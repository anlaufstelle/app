"""The four-eyes services for events draw the facility boundary of the acting person themselves.

Over HTTP the views already scope the event and the deletion request to the
facility of the logged-in person, and in production the database row security
adds a second line; in the tests it is not active, so the service is the only
boundary. A person from another facility and one without any facility
(fail-closed) are refused before any row lock: nothing is locked across the
tenant boundary, no deletion request is created, decided or handed out, the
event stays as it is, and no audit row is written. The counter-checks with
people of the event's own facility work as before.

Observed behaviour only: message, stored rows, audit rows and the tables
actually locked (taken from the executed SQL, not from its wording).
"""

import pytest
from django.core.exceptions import ValidationError

from core.models import AuditLog, DeletionRequest, Event, User
from core.services.events.deletion import (
    approve_deletion,
    create_or_get_event_deletion_request,
    reject_deletion,
    request_deletion,
)
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

_EVENT_OTHER_FACILITY = "Ereignis gehört zu einer anderen Einrichtung."
_REQUEST_OTHER_FACILITY = "Antrag gehört zu einer anderen Einrichtung."
_REQUESTERS = ["foreign_staff", "facilityless_staff"]
_REVIEWERS = ["foreign_reviewer", "facilityless_reviewer"]


@pytest.fixture
def facilityless_staff():
    return User.objects.create_user(
        username="facilityless-deletion-staff",
        role=User.Role.STAFF,
        facility=None,
        is_staff=True,
    )


@pytest.fixture
def foreign_reviewer(second_facility):
    return User.objects.create_user(
        username="foreign-deletion-reviewer",
        role=User.Role.LEAD,
        facility=second_facility,
        is_staff=True,
        can_confirm_deletion=True,
    )


@pytest.fixture
def facilityless_reviewer():
    return User.objects.create_user(
        username="facilityless-deletion-reviewer",
        role=User.Role.LEAD,
        facility=None,
        is_staff=True,
        can_confirm_deletion=True,
    )


@pytest.fixture
def pending_request(facility, staff_user, sample_event):
    return DeletionRequest.objects.create(
        facility=facility,
        target_type=DeletionRequest.TargetType.EVENT,
        target_id=sample_event.pk,
        status=DeletionRequest.Status.PENDING,
        requested_by=staff_user,
        reason="Probe",
    )


def _traces():
    return DeletionRequest.objects.count(), AuditLog.objects.count()


def _refused_before_any_lock(call, message):
    """Calls the service and checks message, missing traces and missing row locks."""
    before = _traces()
    with mitschnitt() as recording, pytest.raises(ValidationError) as excinfo:
        call()
    assert excinfo.value.messages == [message]
    assert _traces() == before
    assert recording.sperrfolge() == []


def _request_state(deletion_request):
    return DeletionRequest.objects.values("status", "reviewed_by_id", "reviewed_at").get(pk=deletion_request.pk)


class TestRequestDeletion:
    @pytest.mark.parametrize("service", [create_or_get_event_deletion_request, request_deletion])
    @pytest.mark.parametrize("actor", _REQUESTERS)
    def test_foreign_actor_is_refused_before_any_lock(self, request, actor, service, sample_event):
        user = request.getfixturevalue(actor)

        _refused_before_any_lock(lambda: service(sample_event, user, "x"), _EVENT_OTHER_FACILITY)

        assert not DeletionRequest.objects.filter(target_id=sample_event.pk).exists()

    @pytest.mark.parametrize("actor", _REQUESTERS)
    def test_foreign_actor_does_not_receive_the_running_request(self, request, actor, sample_event, pending_request):
        user = request.getfixturevalue(actor)

        _refused_before_any_lock(
            lambda: create_or_get_event_deletion_request(sample_event, user, "x"), _EVENT_OTHER_FACILITY
        )

        assert list(DeletionRequest.objects.filter(target_id=sample_event.pk)) == [pending_request]

    def test_local_actor_requests(self, staff_user, sample_event):
        audit_before = AuditLog.objects.filter(action=AuditLog.Action.DELETION_REQUESTED).count()

        deletion_request, created = create_or_get_event_deletion_request(sample_event, staff_user, "x")

        assert created is True
        assert deletion_request.requested_by == staff_user
        assert deletion_request.status == DeletionRequest.Status.PENDING
        assert AuditLog.objects.filter(action=AuditLog.Action.DELETION_REQUESTED).count() == audit_before + 1


class TestApproveDeletion:
    @pytest.mark.parametrize("actor", _REVIEWERS)
    def test_foreign_reviewer_is_refused_before_any_lock(self, request, actor, sample_event, pending_request):
        reviewer = request.getfixturevalue(actor)
        before = _request_state(pending_request)

        _refused_before_any_lock(lambda: approve_deletion(pending_request, reviewer), _REQUEST_OTHER_FACILITY)

        assert _request_state(pending_request) == before
        assert Event.objects.get(pk=sample_event.pk).is_deleted is False

    def test_local_reviewer_approves(self, lead_user, sample_event, pending_request):
        approve_deletion(pending_request, lead_user)

        assert _request_state(pending_request)["status"] == DeletionRequest.Status.APPROVED
        assert Event.objects.get(pk=sample_event.pk).is_deleted is True


class TestRejectDeletion:
    @pytest.mark.parametrize("actor", _REVIEWERS)
    def test_foreign_reviewer_is_refused_before_any_lock(self, request, actor, sample_event, pending_request):
        reviewer = request.getfixturevalue(actor)
        before = _request_state(pending_request)

        _refused_before_any_lock(lambda: reject_deletion(pending_request, reviewer), _REQUEST_OTHER_FACILITY)

        assert _request_state(pending_request) == before
        assert Event.objects.get(pk=sample_event.pk).is_deleted is False

    def test_local_reviewer_rejects(self, lead_user, sample_event, pending_request):
        reject_deletion(pending_request, lead_user)

        assert _request_state(pending_request)["status"] == DeletionRequest.Status.REJECTED
        assert Event.objects.get(pk=sample_event.pk).is_deleted is False
