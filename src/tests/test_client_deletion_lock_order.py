"""Approving a person's deletion or merge request locks the person before the request.

The anonymisation cascades (``anonymize_client`` and the k-anonymisation bridge
of the retention run) lock the person row first and write the person's
deletion requests afterwards. The retention run also reaches persons that are
not in the trash (all their events deleted), so a pending deletion or merge
request can meet it. ``approve_client_deletion`` and ``approve_client_merge``
lock both rows as well; if they took the request first and then waited for
the person, two opposing transactions could deadlock and PostgreSQL would
abort one of them.

Checked per service:

* with a real second connection holding a person row, the service waits at
  the person and does NOT hold the request row meanwhile (a third query with
  ``FOR UPDATE SKIP LOCKED`` still gets the request row);
* the executed row locks name the person before the request;
* a reviewer from another facility (or none) is refused before any row lock.
"""

import threading
import time

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, connections, transaction

from core.models import Client, Facility, Organization, User
from core.models.workitem import DeletionRequest
from core.services.client import approve_client_deletion, request_client_deletion
from core.services.client.merge import approve_client_merge, request_client_merge
from tests._row_lock_helpers import mitschnitt

CLIENT_TABLE = Client._meta.db_table
REQUEST_TABLE = DeletionRequest._meta.db_table
OTHER_FACILITY_MESSAGE = "Antrag gehört zu einer anderen Einrichtung."

_JOIN_TIMEOUT = 30
_BLOCKED_TIMEOUT = 15


def _backend_pid():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        return cursor.fetchone()[0]


def _wait_until_blocked_by(waiter_pid, holder_pid):
    """Poll until ``waiter_pid`` waits for a lock held by ``holder_pid``."""
    deadline = time.monotonic() + _BLOCKED_TIMEOUT
    with connection.cursor() as cursor:
        while time.monotonic() < deadline:
            cursor.execute("SELECT %s = ANY(pg_blocking_pids(%s))", [holder_pid, waiter_pid])
            if cursor.fetchone()[0]:
                return True
            time.sleep(0.02)
    return False


def _request_row_is_locked(request_pk):
    """Third connection's view: is the request row currently locked by someone else?"""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            f"SELECT id FROM {REQUEST_TABLE} WHERE id = %s FOR UPDATE SKIP LOCKED",  # noqa: S608 - model table name
            [str(request_pk)],
        )
        return cursor.fetchone() is None


def _person(facility, creator, pseudonym):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym=pseudonym,
        created_by=creator,
    )


@pytest.mark.django_db(transaction=True)
class TestApprovalWaitsAtThePersonWithoutHoldingTheRequest:
    @pytest.fixture
    def committed(self, django_db_setup):
        """Committed rows -- the worker threads use their own connections."""
        org = Organization.objects.create(name="Deletion lock order org")
        facility = Facility.objects.create(organization=org, name="Deletion lock order facility")
        requester = User.objects.create_user(
            username="deletion-lock-order-staff", role=User.Role.STAFF, facility=facility
        )
        reviewer = User.objects.create_user(
            username="deletion-lock-order-lead",
            role=User.Role.LEAD,
            facility=facility,
            can_confirm_deletion=True,
        )
        yield facility, requester, reviewer
        connections.close_all()

    def _run_while_person_is_held(self, held_pk, request_pk, call):
        holder_ready = threading.Event()
        service_started = threading.Event()
        release = threading.Event()
        pids = {}
        errors = {}
        results = {}

        def _hold_person():
            try:
                with transaction.atomic():
                    pids["holder"] = _backend_pid()
                    list(Client.objects.select_for_update().filter(pk=held_pk).values_list("pk", flat=True))
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
                results["value"] = call()
            except Exception as exc:  # noqa: BLE001 - reported below, not swallowed
                errors["service"] = exc
            finally:
                service_started.set()
                connections.close_all()

        holder = threading.Thread(target=_hold_person, name="hold-person")
        service = threading.Thread(target=_approve, name="approve-request")
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
        assert blocked, "the service never waited for the person row held by the second transaction"
        assert request_locked is False, (
            "the service held the request row while waiting for the person -- "
            "opposite order to the anonymisation, which can deadlock"
        )
        return results.get("value")

    def test_deletion_approval_leaves_the_request_row_free_while_waiting(self, committed):
        facility, requester, reviewer = committed
        person = _person(facility, requester, "Deletion-Lock-01")
        dr = request_client_deletion(person, requester, "No longer needed")

        returned = self._run_while_person_is_held(person.pk, dr.pk, lambda: approve_client_deletion(dr, reviewer))

        assert returned.pk == person.pk
        dr.refresh_from_db()
        person.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED
        assert dr.reviewed_by_id == reviewer.pk
        assert person.is_deleted

    @pytest.mark.parametrize("held_side", ["source", "target"])
    def test_merge_approval_leaves_the_request_row_free_while_waiting(self, committed, held_side):
        facility, requester, reviewer = committed
        source = _person(facility, requester, "Merge-Lock-Source")
        target = _person(facility, requester, "Merge-Lock-Target")
        dr = request_client_merge(source, target, requester, "Duplicate")
        held = source if held_side == "source" else target

        returned = self._run_while_person_is_held(held.pk, dr.pk, lambda: approve_client_merge(dr, reviewer))

        assert returned.pk == target.pk
        dr.refresh_from_db()
        source.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED
        assert dr.reviewed_by_id == reviewer.pk
        assert source.merged_into_id == target.pk
        assert source.is_deleted


@pytest.mark.django_db
class TestLockOrder:
    def test_deletion_approval_locks_the_person_before_the_request(self, client_identified, staff_user, lead_user):
        dr = request_client_deletion(client_identified, staff_user, "No longer needed")

        with mitschnitt() as log:
            approve_client_deletion(dr, lead_user)

        order = log.sperrfolge()
        assert CLIENT_TABLE in order and REQUEST_TABLE in order, order
        assert order.index(CLIENT_TABLE) < order.index(REQUEST_TABLE), order
        assert log.sperren(CLIENT_TABLE)[0].traegt(id=[client_identified.pk]), log.sperren(CLIENT_TABLE)
        assert log.sperren(REQUEST_TABLE)[0].traegt(id=[dr.pk]), log.sperren(REQUEST_TABLE)
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED

    def test_merge_approval_locks_both_persons_before_the_request(
        self, facility, client_identified, staff_user, lead_user
    ):
        target = _person(facility, staff_user, "Merge-Lock-Order-Target")
        dr = request_client_merge(client_identified, target, staff_user, "Duplicate")

        with mitschnitt() as log:
            approve_client_merge(dr, lead_user)

        order = log.sperrfolge()
        assert CLIENT_TABLE in order and REQUEST_TABLE in order, order
        assert order.index(CLIENT_TABLE) < order.index(REQUEST_TABLE), order
        assert log.sperren(CLIENT_TABLE)[0].traegt(id=[client_identified.pk, target.pk]), log.sperren(CLIENT_TABLE)
        assert log.sperren(REQUEST_TABLE)[0].traegt(id=[dr.pk]), log.sperren(REQUEST_TABLE)
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED

    @pytest.mark.parametrize("kind", ["deletion", "merge"])
    @pytest.mark.parametrize("reviewer_kind", ["foreign", "without_facility"])
    def test_reviewer_facility_is_checked_before_any_row_lock(
        self, kind, reviewer_kind, facility, second_facility, client_identified, staff_user
    ):
        reviewer = User.objects.create_user(
            username=f"deletion-lock-{reviewer_kind}-lead",
            role=User.Role.LEAD,
            facility=second_facility if reviewer_kind == "foreign" else None,
            can_confirm_deletion=True,
        )
        if kind == "deletion":
            dr = request_client_deletion(client_identified, staff_user, "No longer needed")
            call = approve_client_deletion
        else:
            target = _person(facility, staff_user, "Merge-Lock-Foreign-Target")
            dr = request_client_merge(client_identified, target, staff_user, "Duplicate")
            call = approve_client_merge

        with mitschnitt() as log, pytest.raises(ValidationError) as excinfo:
            call(dr, reviewer)

        assert excinfo.value.messages == [OTHER_FACILITY_MESSAGE]
        assert log.sperrfolge() == [], log.sperrfolge()
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING
        assert dr.reviewed_by_id is None
