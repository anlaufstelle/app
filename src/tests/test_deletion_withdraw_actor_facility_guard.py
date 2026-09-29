"""Refs #2374: withdrawing a deletion request checks the facility of the acting person first.

``withdraw_deletion_request`` refuses a person outside the facility of the
request — and a person without any facility (fail-closed) — before it locks the
request row: no row lock, no status change, no audit row. Identity and status
are still checked afterwards at the locked row. The service serves every kind
of request (event, person, case, merge); the own, open request of a person in
the same facility is withdrawn for each of them with exactly one audit row.

The withdraw view loads the own request scoped to the current facility; a
refusal of the service still ends there with a message, not with a server
error.
"""

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.models import AuditLog, Client, DeletionRequest, User
from core.services.deletion import withdraw_deletion_request
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

OTHER_FACILITY = "Antrag gehört zu einer anderen Einrichtung."
NOT_THE_REQUESTER = "Nur die antragstellende Person kann einen Löschantrag zurückziehen."
ALREADY_WITHDRAWN = "Dieser Löschantrag wurde bereits zurückgezogen."
KINDS = ("event", "client", "case", "merge")
DETAIL_KEY = {"event": "target_event", "client": "target_client", "case": "target_case", "merge": "target_client"}


@pytest.fixture
def facilityless_requester(staff_user):
    User.objects.filter(pk=staff_user.pk).update(facility=None)
    return User.objects.get(pk=staff_user.pk)


@pytest.fixture
def moved_requester(staff_user, second_facility):
    User.objects.filter(pk=staff_user.pk).update(facility=second_facility)
    return User.objects.get(pk=staff_user.pk)


@pytest.fixture
def open_request(request, facility, staff_user, sample_event, client_identified, case_open):
    """An open request of ``staff_user`` for the kind named by the test parameter."""
    kind = getattr(request, "param", "event")
    target_type, target_id, merge_into = {
        "event": (DeletionRequest.TargetType.EVENT, sample_event.pk, None),
        "client": (DeletionRequest.TargetType.CLIENT, client_identified.pk, None),
        "case": (DeletionRequest.TargetType.CASE, case_open.pk, None),
        "merge": (
            DeletionRequest.TargetType.CLIENT,
            client_identified.pk,
            Client.objects.create(
                facility=facility,
                contact_stage=Client.ContactStage.IDENTIFIED,
                pseudonym="Merge-Ziel-01",
                created_by=staff_user,
            ),
        ),
    }[kind]
    return DeletionRequest.objects.create(
        facility=facility,
        target_type=target_type,
        target_id=target_id,
        merge_into=merge_into,
        reason="Versehentlich gestellt",
        requested_by=staff_user,
    )


def _withdrawals(deletion_request):
    return AuditLog.objects.filter(
        action=AuditLog.Action.DELETION_WITHDRAWN, target_type="DeletionRequest", target_id=str(deletion_request.pk)
    )


def _assert_refused_before_any_lock(deletion_request, user, message):
    audits = AuditLog.objects.count()
    with mitschnitt() as recording, pytest.raises(ValidationError) as excinfo:
        withdraw_deletion_request(deletion_request, user)
    assert excinfo.value.messages == [message]
    assert recording.sperrfolge() == []
    row = DeletionRequest.objects.get(pk=deletion_request.pk)
    assert row.status == DeletionRequest.Status.PENDING
    assert row.reviewed_at is None
    assert AuditLog.objects.count() == audits


@pytest.mark.parametrize("open_request", KINDS, indirect=True)
class TestRefusedBeforeTheRowLock:
    def test_requester_moved_to_another_facility(self, open_request, moved_requester):
        _assert_refused_before_any_lock(open_request, moved_requester, OTHER_FACILITY)

    def test_requester_without_facility(self, open_request, facilityless_requester):
        _assert_refused_before_any_lock(open_request, facilityless_requester, OTHER_FACILITY)

    def test_person_of_another_facility(self, open_request, foreign_staff):
        _assert_refused_before_any_lock(open_request, foreign_staff, OTHER_FACILITY)


@pytest.mark.parametrize("open_request", KINDS, indirect=True)
def test_own_open_request_in_the_own_facility_is_withdrawn(request, open_request, staff_user):
    kind = request.node.callspec.params["open_request"]

    with mitschnitt() as recording:
        withdrawn = withdraw_deletion_request(open_request, staff_user)

    assert withdrawn.pk == open_request.pk
    assert recording.sperrfolge()[0] == "core_deletionrequest"
    row = DeletionRequest.objects.get(pk=open_request.pk)
    assert row.status == DeletionRequest.Status.WITHDRAWN
    assert row.reviewed_at is not None
    assert row.reviewed_by_id is None
    audits = list(_withdrawals(open_request).values_list("user_id", "detail"))
    assert len(audits) == 1
    assert audits[0][0] == staff_user.pk
    assert audits[0][1][DETAIL_KEY[kind]] == str(open_request.target_id)
    if kind == "merge":
        assert audits[0][1]["merge_into"] == str(open_request.merge_into_id)


class TestStillCheckedAtTheLockedRow:
    def test_another_person_of_the_same_facility(self, open_request, lead_user):
        audits = AuditLog.objects.count()
        with mitschnitt() as recording, pytest.raises(ValidationError) as excinfo:
            withdraw_deletion_request(open_request, lead_user)

        assert excinfo.value.messages == [NOT_THE_REQUESTER]
        assert recording.sperrfolge() == ["core_deletionrequest"]
        assert AuditLog.objects.count() == audits

    def test_stale_instance_of_a_request_withdrawn_in_the_meantime(self, open_request, staff_user):
        stale = DeletionRequest.objects.get(pk=open_request.pk)
        withdraw_deletion_request(open_request, staff_user)

        with pytest.raises(ValidationError) as excinfo:
            withdraw_deletion_request(stale, staff_user)

        assert excinfo.value.messages == [ALREADY_WITHDRAWN]
        assert _withdrawals(open_request).count() == 1


class TestWithdrawView:
    def test_refusal_of_the_service_ends_with_a_message(self, client, monkeypatch, open_request, staff_user):
        """The person leaves the facility between the scoped lookup and the service call."""
        from core.views import event_deletion

        scoped = event_deletion.get_scoped_object

        def lookup_then_leave(model, request, **kwargs):
            found = scoped(model, request, **kwargs)
            request.user.facility_id = None
            return found

        monkeypatch.setattr(event_deletion, "get_scoped_object", lookup_then_leave)
        client.force_login(staff_user)
        audits = AuditLog.objects.count()

        response = client.post(reverse("core:deletion_withdraw", kwargs={"pk": open_request.pk}))

        assert response.status_code == 302
        assert [str(m) for m in get_messages(response.wsgi_request)] == [OTHER_FACILITY]
        assert DeletionRequest.objects.get(pk=open_request.pk).status == DeletionRequest.Status.PENDING
        assert AuditLog.objects.count() == audits

    def test_moved_requester_does_not_reach_the_service(self, client, open_request, staff_user, second_facility):
        client.force_login(staff_user)
        User.objects.filter(pk=staff_user.pk).update(facility=second_facility)
        audits = AuditLog.objects.count()

        response = client.post(reverse("core:deletion_withdraw", kwargs={"pk": open_request.pk}))

        assert response.status_code == 404
        assert DeletionRequest.objects.get(pk=open_request.pk).status == DeletionRequest.Status.PENDING
        assert AuditLog.objects.count() == audits
