"""Rectification decisions lock the person before the request.

The anonymisation (``anonymize_client_now``) and the redaction repair lock the
person row first and write the person's rectification requests afterwards.
``approve_rectification`` and ``reject_rectification`` read and lock both rows
as well; if they took the request first and then waited for the person, two
opposing transactions could deadlock and PostgreSQL would abort one of them.

Checked per service:

* with a real second connection holding the person row, the service waits at
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

from core.models import Client, Facility, Organization, RectificationRequest, User
from core.services.client.rectification import (
    approve_rectification,
    reject_rectification,
    request_rectification,
)
from tests._row_lock_helpers import mitschnitt

CLIENT_TABLE = "core_client"
REQUEST_TABLE = "core_rectificationrequest"
OTHER_FACILITY_MESSAGE = "Antrag gehört zu einer anderen Einrichtung."

_JOIN_TIMEOUT = 30
_BLOCKED_TIMEOUT = 15


def _approve(rr, reviewer):
    return approve_rectification(rr, reviewer, review_note="Evidence checked")


def _reject(rr, reviewer):
    return reject_rectification(rr, reviewer, "Not substantiated")


DECISIONS = {
    "approve": (_approve, RectificationRequest.Status.APPROVED),
    "reject": (_reject, RectificationRequest.Status.REJECTED),
}


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


def _request_row_is_locked(rr_pk):
    """Third connection's view: is the request row currently locked by someone else?"""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            f"SELECT id FROM {REQUEST_TABLE} WHERE id = %s FOR UPDATE SKIP LOCKED",  # noqa: S608 - constant table name
            [str(rr_pk)],
        )
        return cursor.fetchone() is None


@pytest.mark.django_db(transaction=True)
class TestDecisionWaitsAtThePersonWithoutHoldingTheRequest:
    @pytest.fixture
    def committed(self, django_db_setup):
        """Committed rows -- the worker threads use their own connections."""
        org = Organization.objects.create(name="Lock order org")
        facility = Facility.objects.create(organization=org, name="Lock order facility")
        requester = User.objects.create_user(username="lock-order-staff", role=User.Role.STAFF, facility=facility)
        reviewer = User.objects.create_user(username="lock-order-lead", role=User.Role.LEAD, facility=facility)
        person = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Lock-Order-01",
            created_by=requester,
        )
        rr = request_rectification(person, requester, "notes", "Corrected note", "Reason")
        yield person, rr, reviewer
        connections.close_all()

    @pytest.mark.parametrize("decision", sorted(DECISIONS))
    def test_request_row_stays_free_while_the_service_waits_for_the_person(self, committed, decision):
        person, rr, reviewer = committed
        call, expected_status = DECISIONS[decision]

        holder_ready = threading.Event()
        service_started = threading.Event()
        release = threading.Event()
        pids = {}
        errors = {}

        def _hold_person():
            try:
                with transaction.atomic():
                    pids["holder"] = _backend_pid()
                    list(Client.objects.select_for_update().filter(pk=person.pk).values_list("pk", flat=True))
                    holder_ready.set()
                    assert release.wait(timeout=_JOIN_TIMEOUT), "holder was never released"
            except Exception as exc:  # noqa: BLE001 - reported below, not swallowed
                errors["holder"] = exc
            finally:
                holder_ready.set()
                connections.close_all()

        def _decide():
            try:
                pids["service"] = _backend_pid()
                service_started.set()
                call(rr, reviewer)
            except Exception as exc:  # noqa: BLE001 - reported below, not swallowed
                errors["service"] = exc
            finally:
                service_started.set()
                connections.close_all()

        holder = threading.Thread(target=_hold_person, name="hold-person")
        service = threading.Thread(target=_decide, name=f"{decision}-rectification")
        blocked = request_locked = None
        holder.start()
        try:
            assert holder_ready.wait(timeout=_JOIN_TIMEOUT) and "holder" in pids, errors
            service.start()
            assert service_started.wait(timeout=_JOIN_TIMEOUT) and "service" in pids, errors
            blocked = _wait_until_blocked_by(pids["service"], pids["holder"])
            if blocked:
                request_locked = _request_row_is_locked(rr.pk)
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
        rr.refresh_from_db()
        assert rr.status == expected_status
        assert rr.reviewed_by_id == reviewer.pk


@pytest.mark.django_db
class TestLockOrder:
    @pytest.mark.parametrize("decision", sorted(DECISIONS))
    def test_person_is_locked_before_the_request(self, decision, client_identified, staff_user, lead_user):
        call, expected_status = DECISIONS[decision]
        rr = request_rectification(client_identified, staff_user, "notes", "Corrected note", "Reason")

        with mitschnitt() as log:
            call(rr, lead_user)

        order = log.sperrfolge()
        assert CLIENT_TABLE in order and REQUEST_TABLE in order, order
        assert order.index(CLIENT_TABLE) < order.index(REQUEST_TABLE), order
        assert log.sperren(CLIENT_TABLE)[0].traegt(id=[client_identified.pk]), log.sperren(CLIENT_TABLE)
        assert log.sperren(REQUEST_TABLE)[0].traegt(id=[rr.pk]), log.sperren(REQUEST_TABLE)
        rr.refresh_from_db()
        assert rr.status == expected_status

    @pytest.mark.parametrize("decision", sorted(DECISIONS))
    @pytest.mark.parametrize("reviewer_kind", ["foreign", "without_facility"])
    def test_reviewer_facility_is_checked_before_any_row_lock(
        self, decision, reviewer_kind, second_facility, client_identified, staff_user
    ):
        call, _ = DECISIONS[decision]
        reviewer = User.objects.create_user(
            username=f"lock-order-{reviewer_kind}-lead",
            role=User.Role.LEAD,
            facility=second_facility if reviewer_kind == "foreign" else None,
        )
        rr = request_rectification(client_identified, staff_user, "notes", "Corrected note", "Reason")

        with mitschnitt() as log, pytest.raises(ValidationError) as excinfo:
            call(rr, reviewer)

        assert excinfo.value.messages == [OTHER_FACILITY_MESSAGE]
        assert log.sperrfolge() == [], log.sperrfolge()
        rr.refresh_from_db()
        assert rr.status == RectificationRequest.Status.PENDING
        assert rr.reviewed_by_id is None
