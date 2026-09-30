"""Approving an event deletion request locks the person and the event before the request.

The anonymisation cascades (``anonymize_client`` via the trash run or the
immediate path, and the k-anonymisation bridge of the retention run) lock the
person row first, then the person's cases and events, and write the deletion
requests of these events afterwards. ``approve_deletion`` used to lock the
request first and then, inside ``soft_delete_event``, the person and the
event. Two opposing transactions could deadlock on (request, person) and
PostgreSQL would abort one of them.

Checked:

* with a real second connection holding the person row or the event row (also
  for an event without a person), the approval waits at that row and does NOT
  hold the request row meanwhile (a third query with ``FOR UPDATE SKIP
  LOCKED`` still gets the request row);
* the executed row locks name the person, then the event, then the request;
* if the event moves to another person while the approval takes the locks, the
  approval does not lock the new person; it refuses with the existing "person
  binding changed" answer and changes nothing;
* a reviewer from another facility (or none) is refused before any row lock.
"""

import threading

import pytest
from django.core.exceptions import ValidationError
from django.db import connections, transaction
from django.utils import timezone

from core.models import AuditLog, Client, DocumentType, Event, Facility, Organization, User
from core.models.workitem import DeletionRequest
from core.services.events import approve_deletion, request_deletion
from tests._row_lock_helpers import mitschnitt
from tests.test_client_deletion_lock_order import (
    _JOIN_TIMEOUT,
    _backend_pid,
    _request_row_is_locked,
    _wait_until_blocked_by,
)

CLIENT_TABLE = Client._meta.db_table
EVENT_TABLE = Event._meta.db_table
REQUEST_TABLE = DeletionRequest._meta.db_table
OTHER_FACILITY_MESSAGE = "Antrag gehört zu einer anderen Einrichtung."
BINDING_CHANGED_MESSAGE = (
    "Die Personenzuordnung dieses Ereignisses wurde zwischenzeitlich geändert — "
    "bitte erneut öffnen und die Löschung wiederholen."
)


def _person(facility, creator, pseudonym):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.QUALIFIED,
        pseudonym=pseudonym,
        created_by=creator,
    )


def _event(facility, person, document_type, creator):
    return Event.objects.create(
        facility=facility,
        client=person,
        is_anonymous=person is None,
        document_type=document_type,
        occurred_at=timezone.now(),
        data_json={"dauer": 30},
        created_by=creator,
    )


@pytest.mark.django_db(transaction=True)
class TestApprovalWaitsWithoutHoldingTheRequest:
    @pytest.fixture
    def committed(self, django_db_setup):
        """Committed rows -- the worker threads use their own connections."""
        org = Organization.objects.create(name="Event deletion lock order org")
        facility = Facility.objects.create(organization=org, name="Event deletion lock order facility")
        requester = User.objects.create_user(
            username="event-deletion-lock-order-staff", role=User.Role.STAFF, facility=facility
        )
        reviewer = User.objects.create_user(
            username="event-deletion-lock-order-lead",
            role=User.Role.LEAD,
            facility=facility,
            can_confirm_deletion=True,
        )
        document_type = DocumentType.objects.create(facility=facility, name="Event lock order contact")
        yield facility, requester, reviewer, document_type
        connections.close_all()

    def _run_while_row_is_held(self, model, held_pk, request_pk, call):
        holder_ready = threading.Event()
        service_started = threading.Event()
        release = threading.Event()
        pids = {}
        errors = {}

        def _hold_row():
            try:
                with transaction.atomic():
                    pids["holder"] = _backend_pid()
                    list(model.objects.select_for_update().filter(pk=held_pk).values_list("pk", flat=True))
                    holder_ready.set()
                    assert release.wait(timeout=_JOIN_TIMEOUT), "holder was never released"
            except Exception as exc:  # noqa: BLE001 - reported below, not swallowed
                errors["holder"] = exc
            finally:
                holder_ready.set()
                connections.close_all()

        def _approve():
            try:
                pids["service"] = _backend_pid()
                service_started.set()
                call()
            except Exception as exc:  # noqa: BLE001 - reported below, not swallowed
                errors["service"] = exc
            finally:
                service_started.set()
                connections.close_all()

        holder = threading.Thread(target=_hold_row, name="hold-row")
        service = threading.Thread(target=_approve, name="approve-event-request")
        blocked = request_locked = None
        holder.start()
        try:
            assert holder_ready.wait(timeout=_JOIN_TIMEOUT) and "holder" in pids, errors
            service.start()
            assert service_started.wait(timeout=_JOIN_TIMEOUT) and "service" in pids, errors
            blocked = _wait_until_blocked_by(pids["service"], pids["holder"])
            if blocked:
                request_locked = _request_row_is_locked(request_pk)
        finally:
            release.set()
            holder.join(timeout=_JOIN_TIMEOUT)
            if service.is_alive() or service.ident is not None:
                service.join(timeout=_JOIN_TIMEOUT)

        assert not holder.is_alive() and not service.is_alive(), "threads did not finish"
        assert not errors, errors
        assert blocked, f"the approval never waited for the {model.__name__} row held by the second transaction"
        assert request_locked is False, (
            f"the approval held the request row while waiting for the {model.__name__} row -- "
            "opposite order to the anonymisation, which can deadlock"
        )

    @pytest.mark.parametrize("held", ["person", "event", "event_without_person"])
    def test_approval_leaves_the_request_row_free_while_waiting(self, committed, held):
        facility, requester, reviewer, document_type = committed
        person = None if held == "event_without_person" else _person(facility, requester, f"Event-Lock-{held}")
        event = _event(facility, person, document_type, requester)
        dr = request_deletion(event, requester, "Recorded twice")
        model, held_pk = (Client, person.pk) if held == "person" else (Event, event.pk)

        self._run_while_row_is_held(model, held_pk, dr.pk, lambda: approve_deletion(dr, reviewer))

        dr.refresh_from_db()
        event.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED
        assert dr.reviewed_by_id == reviewer.pk
        assert event.is_deleted
        assert event.deleted_by_id == reviewer.pk


@pytest.mark.django_db
class TestLockOrder:
    def test_approval_locks_person_then_event_then_request(self, sample_event, staff_user, lead_user):
        dr = request_deletion(sample_event, staff_user, "Recorded twice")

        with mitschnitt() as log:
            approve_deletion(dr, lead_user)

        order = log.sperrfolge()
        assert {CLIENT_TABLE, EVENT_TABLE, REQUEST_TABLE} <= set(order), order
        assert order.index(CLIENT_TABLE) < order.index(EVENT_TABLE) < order.index(REQUEST_TABLE), order
        assert log.sperren(CLIENT_TABLE)[0].traegt(id=[sample_event.client_id]), log.sperren(CLIENT_TABLE)
        assert log.sperren(EVENT_TABLE)[0].traegt(id=[sample_event.pk]), log.sperren(EVENT_TABLE)
        assert log.sperren(REQUEST_TABLE)[0].traegt(id=[dr.pk]), log.sperren(REQUEST_TABLE)
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED

    def test_event_without_person_locks_the_event_before_the_request(
        self, facility, doc_type_contact, staff_user, lead_user
    ):
        event = _event(facility, None, doc_type_contact, staff_user)
        dr = request_deletion(event, staff_user, "Recorded twice")

        with mitschnitt() as log:
            approve_deletion(dr, lead_user)

        order = log.sperrfolge()
        assert CLIENT_TABLE not in order, order
        assert {EVENT_TABLE, REQUEST_TABLE} <= set(order), order
        assert order.index(EVENT_TABLE) < order.index(REQUEST_TABLE), order
        assert log.sperren(EVENT_TABLE)[0].traegt(id=[event.pk]), log.sperren(EVENT_TABLE)
        event.refresh_from_db()
        assert event.is_deleted

    def test_event_moved_to_another_person_during_the_locks_is_refused(
        self, facility, sample_event, staff_user, lead_user
    ):
        original_person_id = sample_event.client_id
        other_person = _person(facility, staff_user, "Event-Lock-Other")
        dr = request_deletion(sample_event, staff_user, "Recorded twice")
        approvals = AuditLog.objects.filter(action=AuditLog.Action.DELETION_APPROVED).count()

        with mitschnitt() as log, pytest.raises(ValidationError) as excinfo:
            # Right after the person lock the event moves to another person, as
            # a merge committed between the unlocked read and the lock would.
            log.nach_sperre(
                CLIENT_TABLE, lambda: Event.objects.filter(pk=sample_event.pk).update(client_id=other_person.pk)
            )
            approve_deletion(dr, lead_user)

        assert excinfo.value.messages == [BINDING_CHANGED_MESSAGE]
        assert log.offene_schranken() == []
        assert all(lock.traegt(id=[original_person_id]) for lock in log.sperren(CLIENT_TABLE)), log.sperren(
            CLIENT_TABLE
        )
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING
        assert dr.reviewed_by_id is None
        assert Event.objects.filter(pk=sample_event.pk, is_deleted=False).exists()
        assert AuditLog.objects.filter(action=AuditLog.Action.DELETION_APPROVED).count() == approvals

    @pytest.mark.parametrize("reviewer_kind", ["foreign", "without_facility"])
    def test_reviewer_facility_is_checked_before_any_row_lock(
        self, reviewer_kind, second_facility, sample_event, staff_user
    ):
        reviewer = User.objects.create_user(
            username=f"event-deletion-lock-{reviewer_kind}-lead",
            role=User.Role.LEAD,
            facility=second_facility if reviewer_kind == "foreign" else None,
            can_confirm_deletion=True,
        )
        dr = request_deletion(sample_event, staff_user, "Recorded twice")

        with mitschnitt() as log, pytest.raises(ValidationError) as excinfo:
            approve_deletion(dr, reviewer)

        assert excinfo.value.messages == [OTHER_FACILITY_MESSAGE]
        assert log.sperrfolge() == [], log.sperrfolge()
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING
        assert dr.reviewed_by_id is None
