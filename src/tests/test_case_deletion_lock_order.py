"""Approving a case deletion request locks the person and the case before the request.

The anonymisation cascades (``anonymize_client`` via the trash run or the
immediate path, and the k-anonymisation bridge of the retention run) lock the
person row first, then the rows of the person's cases, and write the case
deletion requests of these cases afterwards. The case erasure
(``anonymize_case`` behind ``_lock_case_chain``) takes the same order. If
``approve_case_deletion`` took the request first and then waited for the
case, two opposing transactions could deadlock on (request, case) and
PostgreSQL would abort one of them.

Checked:

* with a real second connection holding the person row or the case row, the
  approval waits at that row and does NOT hold the request row meanwhile (a
  third query with ``FOR UPDATE SKIP LOCKED`` still gets the request row);
* the executed row locks name the person, then the case, then the request;
* if the case moves to another person while the approval takes the locks, the
  approval does not lock the new person under the held rows; it refuses with
  the existing "case changed in the meantime" answer and changes nothing;
* a reviewer from another facility (or none) is refused before any row lock.
"""

import threading

import pytest
from django.core.exceptions import ValidationError
from django.db import connections, transaction

from core.models import AuditLog, Case, Client, Facility, Organization, User
from core.models.workitem import DeletionRequest
from core.services.case import approve_case_deletion, request_case_deletion
from tests._row_lock_helpers import mitschnitt
from tests.test_client_deletion_lock_order import (
    _JOIN_TIMEOUT,
    _backend_pid,
    _request_row_is_locked,
    _wait_until_blocked_by,
)

CLIENT_TABLE = Client._meta.db_table
CASE_TABLE = Case._meta.db_table
REQUEST_TABLE = DeletionRequest._meta.db_table
OTHER_FACILITY_MESSAGE = "Antrag gehört zu einer anderen Einrichtung."
CASE_CHANGED_MESSAGE = "Der Fall wurde zwischenzeitlich geändert — bitte die Ansicht neu laden."

_HELD_MODELS = {"person": Client, "case": Case}


def _person(facility, creator, pseudonym):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.QUALIFIED,
        pseudonym=pseudonym,
        created_by=creator,
    )


def _case(facility, person, creator, title):
    return Case.objects.create(facility=facility, client=person, title=title, created_by=creator)


@pytest.mark.django_db(transaction=True)
class TestApprovalWaitsWithoutHoldingTheRequest:
    @pytest.fixture
    def committed(self, django_db_setup):
        """Committed rows -- the worker threads use their own connections."""
        org = Organization.objects.create(name="Case deletion lock order org")
        facility = Facility.objects.create(organization=org, name="Case deletion lock order facility")
        requester = User.objects.create_user(
            username="case-deletion-lock-order-staff", role=User.Role.STAFF, facility=facility
        )
        reviewer = User.objects.create_user(
            username="case-deletion-lock-order-lead",
            role=User.Role.LEAD,
            facility=facility,
            can_confirm_deletion=True,
        )
        yield facility, requester, reviewer
        connections.close_all()

    def _run_while_row_is_held(self, model, held_pk, request_pk, call):
        holder_ready = threading.Event()
        service_started = threading.Event()
        release = threading.Event()
        pids = {}
        errors = {}
        results = {}

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
                results["value"] = call()
            except Exception as exc:  # noqa: BLE001 - reported below, not swallowed
                errors["service"] = exc
            finally:
                service_started.set()
                connections.close_all()

        holder = threading.Thread(target=_hold_row, name="hold-row")
        service = threading.Thread(target=_approve, name="approve-case-request")
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
        return results.get("value")

    @pytest.mark.parametrize("held", ["person", "case"])
    def test_approval_leaves_the_request_row_free_while_waiting(self, committed, held):
        facility, requester, reviewer = committed
        person = _person(facility, requester, f"Case-Lock-{held}")
        case = _case(facility, person, requester, "Case lock order")
        dr = request_case_deletion(case, requester, "Recorded twice")
        held_pk = person.pk if held == "person" else case.pk

        returned = self._run_while_row_is_held(
            _HELD_MODELS[held], held_pk, dr.pk, lambda: approve_case_deletion(dr, reviewer)
        )

        assert returned.pk == case.pk
        dr.refresh_from_db()
        case.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED
        assert dr.reviewed_by_id == reviewer.pk
        assert case.is_deleted
        assert case.deleted_by_id == reviewer.pk


@pytest.mark.django_db
class TestLockOrder:
    def test_approval_locks_person_then_case_then_request(self, case_open, staff_user, lead_user):
        dr = request_case_deletion(case_open, staff_user, "Recorded twice")

        with mitschnitt() as log:
            returned = approve_case_deletion(dr, lead_user)

        order = log.sperrfolge()
        assert {CLIENT_TABLE, CASE_TABLE, REQUEST_TABLE} <= set(order), order
        assert order.index(CLIENT_TABLE) < order.index(CASE_TABLE) < order.index(REQUEST_TABLE), order
        assert log.sperren(CLIENT_TABLE)[0].traegt(id=[case_open.client_id]), log.sperren(CLIENT_TABLE)
        assert log.sperren(CASE_TABLE)[0].traegt(id=[case_open.pk]), log.sperren(CASE_TABLE)
        assert log.sperren(REQUEST_TABLE)[0].traegt(id=[dr.pk]), log.sperren(REQUEST_TABLE)
        assert returned.pk == case_open.pk
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED

    def test_case_moved_to_another_person_during_the_locks_is_refused(self, facility, case_open, staff_user, lead_user):
        original_person_id = case_open.client_id
        other_person = _person(facility, staff_user, "Case-Lock-Other")
        dr = request_case_deletion(case_open, staff_user, "Recorded twice")
        approvals = AuditLog.objects.filter(action=AuditLog.Action.DELETION_APPROVED).count()

        with mitschnitt() as log, pytest.raises(ValidationError) as excinfo:
            # Right after the person lock the case moves to another person, as
            # a merge committed between the unlocked read and the lock would.
            log.nach_sperre(
                CLIENT_TABLE, lambda: Case.objects.filter(pk=case_open.pk).update(client_id=other_person.pk)
            )
            approve_case_deletion(dr, lead_user)

        assert excinfo.value.messages == [CASE_CHANGED_MESSAGE]
        assert log.offene_schranken() == []
        assert all(lock.traegt(id=[original_person_id]) for lock in log.sperren(CLIENT_TABLE)), log.sperren(
            CLIENT_TABLE
        )
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING
        assert dr.reviewed_by_id is None
        assert Case.objects.filter(pk=case_open.pk, is_deleted=False).exists()
        assert AuditLog.objects.filter(action=AuditLog.Action.DELETION_APPROVED).count() == approvals

    @pytest.mark.parametrize("reviewer_kind", ["foreign", "without_facility"])
    def test_reviewer_facility_is_checked_before_any_row_lock(
        self, reviewer_kind, second_facility, case_open, staff_user
    ):
        reviewer = User.objects.create_user(
            username=f"case-deletion-lock-{reviewer_kind}-lead",
            role=User.Role.LEAD,
            facility=second_facility if reviewer_kind == "foreign" else None,
            can_confirm_deletion=True,
        )
        dr = request_case_deletion(case_open, staff_user, "Recorded twice")

        with mitschnitt() as log, pytest.raises(ValidationError) as excinfo:
            approve_case_deletion(dr, reviewer)

        assert excinfo.value.messages == [OTHER_FACILITY_MESSAGE]
        assert log.sperrfolge() == [], log.sperrfolge()
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING
        assert dr.reviewed_by_id is None
