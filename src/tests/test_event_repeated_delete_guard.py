"""Refs #2374: an event is deleted at most once.

``soft_delete_event`` locks the person row and the event row and then reads
the deletion state of the event at the locked row. An event that already lies
in the trash is refused with a domain message and without any write: the
first deletion stamp, the first history entry and the first audit row stay as
they are, and the refusal comes after the two row locks and before the first
write.

The same holds for every caller:

* a stale instance (the page was opened before somebody else deleted);
* a parallel second request that waits on the row locks of the first one and
  only gets them after its commit;
* the direct delete view — the loser of a double POST gets the message on a
  page that still exists, not a server error and not the page of the event
  that is gone;
* the approval of a deletion request whose event was deleted another way —
  refused, and the request stays open so that it can still be rejected or
  withdrawn;
* the tally undo, which locks and checks the event row itself before it calls
  the service and therefore refuses with its own answer.
"""

import threading
import time

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.urls import reverse
from django.utils import timezone

from core.models import Activity, AuditLog, DeletionRequest, Event, EventHistory
from core.services.deletion import withdraw_deletion_request
from core.services.events import approve_deletion, reject_deletion, soft_delete_event
from core.services.events.deletion import create_or_get_event_deletion_request
from tests._retention_race_helpers import JOIN_TIMEOUT, PARK_SECONDS, backend_pid, session_thread, waits_for_a_lock
from tests._row_lock_helpers import mitschnitt

ALREADY_DELETED = "Dieses Ereignis liegt bereits im Papierkorb."


def _counts():
    return AuditLog.objects.count(), Activity.objects.count(), EventHistory.objects.count()


def _trail(event_pk):
    """Everything the first deletion left behind for this event."""
    return {
        "row": Event.objects.filter(pk=event_pk)
        .values("is_deleted", "deleted_at", "deleted_by_id", "updated_at", "data_json")
        .get(),
        "history": list(
            EventHistory.objects.filter(event_id=event_pk, action=EventHistory.Action.DELETE)
            .order_by("pk")
            .values_list("pk", "changed_by_id", "data_before")
        ),
        "audit": list(
            AuditLog.objects.filter(action=AuditLog.Action.DELETE, target_type="Event", target_id=str(event_pk))
            .order_by("pk")
            .values_list("pk", "user_id", "detail")
        ),
    }


def _stale(event):
    """A second instance of the same row, read before the first deletion."""
    return Event.objects.select_related("document_type", "client").get(pk=event.pk)


def _anonymous_event(facility, user, document_type):
    return Event.objects.create(
        facility=facility,
        client=None,
        is_anonymous=True,
        document_type=document_type,
        occurred_at=timezone.now(),
        data_json={"dauer": 5},
        created_by=user,
    )


def _qualified_event(facility, client_qualified, doc_type_contact, staff_user):
    return Event.objects.create(
        facility=facility,
        client=client_qualified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={"dauer": 30, "notiz": "request"},
        created_by=staff_user,
    )


def _assert_refused_without_writes(call, event_pk):
    trail, counts = _trail(event_pk), _counts()
    with pytest.raises(ValidationError) as excinfo:
        call()
    assert excinfo.value.messages == [ALREADY_DELETED]
    assert _trail(event_pk) == trail
    assert _counts() == counts


@pytest.mark.django_db
class TestServiceRefusesARepeatedDelete:
    def test_stale_instance_of_a_person_event(self, sample_event, staff_user, lead_user):
        stale = _stale(sample_event)
        soft_delete_event(sample_event, staff_user)

        _assert_refused_without_writes(lambda: soft_delete_event(stale, lead_user), sample_event.pk)

        row = _trail(sample_event.pk)
        assert row["row"]["deleted_by_id"] == staff_user.pk
        assert len(row["history"]) == 1
        assert len(row["audit"]) == 1

    def test_stale_instance_of_an_anonymous_event(self, facility, staff_user, lead_user, doc_type_contact):
        event = _anonymous_event(facility, staff_user, doc_type_contact)
        stale = _stale(event)
        soft_delete_event(event, staff_user)

        _assert_refused_without_writes(lambda: soft_delete_event(stale, lead_user), event.pk)

    def test_same_instance_called_twice(self, sample_event, staff_user):
        soft_delete_event(sample_event, staff_user)

        _assert_refused_without_writes(lambda: soft_delete_event(sample_event, staff_user), sample_event.pk)

    def test_refusal_comes_after_both_row_locks_and_before_any_write(self, sample_event, staff_user, lead_user):
        stale = _stale(sample_event)
        soft_delete_event(sample_event, staff_user)

        with mitschnitt() as recording, pytest.raises(ValidationError):
            soft_delete_event(stale, lead_user)

        assert recording.sperrfolge() == ["core_client", "core_event"]
        writes = [q.sql for q in recording if q.sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
        assert writes == []

    def test_first_delete_is_unchanged(self, sample_event, staff_user):
        with mitschnitt() as recording:
            soft_delete_event(sample_event, staff_user)

        assert recording.sperrfolge()[:2] == ["core_client", "core_event"]
        trail = _trail(sample_event.pk)
        assert trail["row"]["is_deleted"] is True
        assert trail["row"]["deleted_by_id"] == staff_user.pk
        assert len(trail["history"]) == 1
        assert len(trail["audit"]) == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
def test_parallel_second_delete_waits_for_the_first_and_is_refused(sample_event, staff_user, lead_user):
    """The second session reads the deletion state only after it got the row locks.

    The first session deletes and keeps its transaction open until the second
    one waits on a row lock; only then it commits. A check before the locks
    would still see the live event and delete it a second time.
    """
    event_pk = sample_event.pk
    stale = _stale(sample_event)
    main_pid = backend_pid()
    first_holds_the_locks = threading.Event()
    results, errors = {}, {}

    def first_delete():
        with transaction.atomic():
            soft_delete_event(Event.objects.get(pk=event_pk), staff_user)
            first_holds_the_locks.set()
            deadline = time.monotonic() + PARK_SECONDS
            while time.monotonic() < deadline and not waits_for_a_lock(main_pid):
                time.sleep(0.05)
        return "deleted"

    first = session_thread("first", first_delete, results, errors)
    first.start()
    try:
        assert first_holds_the_locks.wait(JOIN_TIMEOUT), "the first session did not delete"
        with pytest.raises(ValidationError) as excinfo:
            soft_delete_event(stale, lead_user)
    finally:
        first.join(timeout=JOIN_TIMEOUT)
    assert not first.is_alive()
    assert errors == {}
    assert results == {"first": "deleted"}
    assert excinfo.value.messages == [ALREADY_DELETED]

    trail = _trail(event_pk)
    assert trail["row"]["deleted_by_id"] == staff_user.pk
    assert [entry[1] for entry in trail["history"]] == [staff_user.pk]
    assert [entry[1] for entry in trail["audit"]] == [staff_user.pk]
    connection.close()


@pytest.mark.django_db
class TestDirectDeleteView:
    def test_loser_of_a_double_post_gets_the_message_on_a_live_page(
        self, client, monkeypatch, sample_event, staff_user
    ):
        """Both requests loaded the live event before the first one committed."""
        stale = _stale(sample_event)
        soft_delete_event(sample_event, staff_user)
        client.force_login(staff_user)
        trail, counts = _trail(sample_event.pk), _counts()
        monkeypatch.setattr("core.views.events.get_visible_event_or_404", lambda *args, **kwargs: stale)

        response = client.post(reverse("core:event_delete", kwargs={"pk": sample_event.pk}), {"reason": ""})

        assert response.status_code == 302
        assert [str(m) for m in get_messages(response.wsgi_request)] == [ALREADY_DELETED]
        assert response.url == reverse("core:zeitstrom")
        assert _trail(sample_event.pk) == trail
        assert _counts() == counts
        monkeypatch.undo()
        assert client.get(response.url).status_code == 200


@pytest.mark.django_db
class TestApprovalOfARequestForAnAlreadyDeletedEvent:
    """The event of an open request was deleted another way in the meantime."""

    @pytest.fixture
    def open_request(self, facility, client_qualified, doc_type_contact, staff_user, lead_user):
        event = _qualified_event(facility, client_qualified, doc_type_contact, staff_user)
        request, created = create_or_get_event_deletion_request(event, staff_user, "Recorded twice")
        assert created
        soft_delete_event(event, lead_user)
        return request, event

    def test_approval_is_refused_and_the_request_stays_open(self, open_request, lead_user):
        request, event = open_request
        approvals = AuditLog.objects.filter(action=AuditLog.Action.DELETION_APPROVED).count()

        _assert_refused_without_writes(lambda: approve_deletion(request, lead_user), event.pk)

        request.refresh_from_db()
        assert request.status == DeletionRequest.Status.PENDING
        assert request.reviewed_by_id is None
        assert AuditLog.objects.filter(action=AuditLog.Action.DELETION_APPROVED).count() == approvals

    def test_rejection_stays_possible(self, open_request, lead_user):
        request, event = open_request
        with pytest.raises(ValidationError):
            approve_deletion(request, lead_user)

        reject_deletion(request, lead_user)

        request.refresh_from_db()
        assert request.status == DeletionRequest.Status.REJECTED
        assert AuditLog.objects.filter(action=AuditLog.Action.DELETION_REJECTED, target_id=str(request.pk)).count() == 1

    def test_withdrawal_stays_possible(self, open_request, lead_user, staff_user):
        request, event = open_request
        with pytest.raises(ValidationError):
            approve_deletion(request, lead_user)

        withdraw_deletion_request(request, staff_user)

        request.refresh_from_db()
        assert request.status == DeletionRequest.Status.WITHDRAWN

    def test_review_view_answers_with_the_message_and_keeps_the_request_decidable(
        self, client, open_request, lead_user
    ):
        request, event = open_request
        trail = _trail(event.pk)
        client.force_login(lead_user)
        url = reverse("core:deletion_review", kwargs={"pk": request.pk})

        response = client.post(url, {"action": "approve"})

        assert response.status_code == 302
        assert response.url == reverse("core:deletion_request_list")
        assert [str(m) for m in get_messages(response.wsgi_request)] == [ALREADY_DELETED]
        assert _trail(event.pk) == trail
        request.refresh_from_db()
        assert request.status == DeletionRequest.Status.PENDING

        assert client.get(url).status_code == 200
        response = client.post(url, {"action": "reject"})
        assert response.status_code == 302
        request.refresh_from_db()
        assert request.status == DeletionRequest.Status.REJECTED


@pytest.mark.django_db
def test_tally_undo_of_an_event_deleted_by_someone_else_is_denied_without_writes(
    facility, staff_user, lead_user, doc_type_contact
):
    """The undo locks the event row and checks the deletion state before it calls the service."""
    from core.services.events import create_event
    from core.services.events.tally_undo import TallyUndoDenied, mint_tally_undo_token, undo_tally_event

    event = create_event(
        facility=facility,
        user=staff_user,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={},
        is_anonymous=True,
        idempotency_key="repeated-delete-tally",
        created_via=Event.CreatedVia.TALLY,
    )
    token = mint_tally_undo_token(event)
    soft_delete_event(_stale(event), lead_user)
    trail, counts = _trail(event.pk), _counts()

    with pytest.raises(TallyUndoDenied):
        undo_tally_event(facility, staff_user, token)

    assert _trail(event.pk) == trail
    assert _counts() == counts
