"""A merge request whose target changes while the approval locks the persons.

``approve_client_merge`` reads the source and target of the request without a
lock, locks both persons and only then the request. The target
(``merge_into``) stays editable after the request was filed, so another
transaction can point the request at a different person between that read and
the request lock. The approval must then not lock the new person while it
holds the request -- that would reverse the person-before-request order the
anonymisation takes and could break the PK order of the person locks. It
starts over on the current persons instead, and refuses after repeated
changes.

Checked with real second connections:

* the target changes while the approval waits at the source; a third
  transaction holds the new target. The approval waits at the new target and
  does NOT hold the request meanwhile, then merges into the new target;
* the target changes before every person lock. Each person lock is taken
  while the request row is free, and the approval gives up with the existing
  "changed in the meantime" answer; nothing is merged.
"""

import itertools
import threading

import pytest
from django.core.exceptions import ValidationError
from django.db import connections, transaction

from core.models import Client, Facility, Organization, User
from core.models.workitem import DeletionRequest
from core.services.client import merge as merge_service
from core.services.client.merge import approve_client_merge, request_client_merge
from tests.test_client_deletion_lock_order import (
    _JOIN_TIMEOUT,
    _backend_pid,
    _request_row_is_locked,
    _wait_until_blocked_by,
)

CHANGED_MESSAGE = "Der Datensatz wurde zwischenzeitlich bearbeitet. Bitte laden Sie die Seite neu."


class _PersonHolder(threading.Thread):
    """Holds one person row locked in its own transaction until released."""

    def __init__(self, pk):
        super().__init__(name=f"hold-person-{pk}")
        self.pk_to_hold = pk
        self.ready = threading.Event()
        self.release = threading.Event()
        self.pid = None
        self.error = None

    def run(self):
        try:
            with transaction.atomic():
                self.pid = _backend_pid()
                list(Client.objects.select_for_update().filter(pk=self.pk_to_hold).values_list("pk", flat=True))
                self.ready.set()
                assert self.release.wait(timeout=_JOIN_TIMEOUT), "holder was never released"
        except Exception as exc:  # noqa: BLE001 - reported by the test, not swallowed
            self.error = exc
        finally:
            self.ready.set()
            connections.close_all()

    def start_and_wait(self):
        self.start()
        assert self.ready.wait(timeout=_JOIN_TIMEOUT) and self.pid is not None, self.error

    def finish(self):
        self.release.set()
        self.join(timeout=_JOIN_TIMEOUT)


def _in_other_connection(func):
    """Run ``func`` in a worker thread (own connection, own commit) and return its result."""
    outcome = {}

    def _run():
        try:
            outcome["value"] = func()
        except Exception as exc:  # noqa: BLE001 - re-raised below
            outcome["error"] = exc
        finally:
            connections.close_all()

    worker = threading.Thread(target=_run, name="other-connection")
    worker.start()
    worker.join(timeout=_JOIN_TIMEOUT)
    assert not worker.is_alive(), "the other connection did not finish"
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


def _person(facility, creator, pseudonym):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym=pseudonym,
        created_by=creator,
    )


@pytest.fixture
def committed(django_db_setup):
    """Committed rows -- the worker threads use their own connections."""
    org = Organization.objects.create(name="Merge retarget org")
    facility = Facility.objects.create(organization=org, name="Merge retarget facility")
    requester = User.objects.create_user(username="merge-retarget-staff", role=User.Role.STAFF, facility=facility)
    reviewer = User.objects.create_user(
        username="merge-retarget-lead",
        role=User.Role.LEAD,
        facility=facility,
        can_confirm_deletion=True,
    )
    source = _person(facility, requester, "Merge-Retarget-Source")
    old_target = _person(facility, requester, "Merge-Retarget-Old")
    new_target = _person(facility, requester, "Merge-Retarget-New")
    dr = request_client_merge(source, old_target, requester, "Duplicate")
    yield dr, reviewer, source, old_target, new_target
    connections.close_all()


@pytest.mark.django_db(transaction=True)
class TestMergeApprovalWithAChangedTarget:
    def test_the_new_target_is_locked_without_holding_the_request(self, committed):
        dr, reviewer, source, old_target, new_target = committed
        source_holder = _PersonHolder(source.pk)
        new_target_holder = _PersonHolder(new_target.pk)
        service_started = threading.Event()
        pids, errors, results = {}, {}, {}

        def _approve():
            try:
                pids["service"] = _backend_pid()
                service_started.set()
                results["value"] = approve_client_merge(dr, reviewer)
            except Exception as exc:  # noqa: BLE001 - reported below, not swallowed
                errors["service"] = exc
            finally:
                service_started.set()
                connections.close_all()

        service = threading.Thread(target=_approve, name="approve-merge")
        blocked_at_source = blocked_at_new_target = request_locked = None
        source_holder.start_and_wait()
        try:
            service.start()
            assert service_started.wait(timeout=_JOIN_TIMEOUT) and "service" in pids, errors
            # The approval has read source and old target and waits at the source.
            blocked_at_source = _wait_until_blocked_by(pids["service"], source_holder.pid)
            if blocked_at_source:
                # Committed at once (no surrounding transaction in this test).
                DeletionRequest.objects.filter(pk=dr.pk).update(merge_into=new_target)
                new_target_holder.start_and_wait()
                source_holder.finish()
                blocked_at_new_target = _wait_until_blocked_by(pids["service"], new_target_holder.pid)
                if blocked_at_new_target:
                    request_locked = _request_row_is_locked(dr.pk)
        finally:
            source_holder.finish()
            if new_target_holder.ident is not None:
                new_target_holder.finish()
            if service.ident is not None:
                service.join(timeout=_JOIN_TIMEOUT)

        assert not service.is_alive(), "the approval did not finish"
        assert source_holder.error is None and new_target_holder.error is None
        assert not errors, errors
        assert blocked_at_source, "the approval never waited for the source held by the second transaction"
        assert blocked_at_new_target, "the approval never waited for the new target held by the third transaction"
        assert request_locked is False, (
            "the approval held the request row while waiting for the new target -- "
            "a person locked after the request, opposite to the anonymisation"
        )
        assert results["value"].pk == new_target.pk
        dr.refresh_from_db()
        source.refresh_from_db()
        old_target.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED
        assert dr.merge_into_id == new_target.pk
        assert source.merged_into_id == new_target.pk
        assert source.is_deleted
        assert not old_target.is_deleted
        assert old_target.merged_into_id is None

    def test_a_target_that_keeps_changing_is_refused(self, committed, monkeypatch):
        dr, reviewer, source, old_target, new_target = committed
        next_target = itertools.cycle([new_target.pk, old_target.pk])
        request_free_at_person_lock = []
        real_lock_clients = merge_service.lock_clients

        def _retarget_if_request_is_free(target_pk):
            with transaction.atomic():
                free = DeletionRequest.objects.select_for_update(skip_locked=True).filter(pk=dr.pk)
                if not list(free.values_list("pk", flat=True)):
                    return False
                DeletionRequest.objects.filter(pk=dr.pk).update(merge_into_id=target_pk)
                return True

        def _lock_clients_after_a_retarget(client_refs, **kwargs):
            target_pk = next(next_target)
            request_free_at_person_lock.append(_in_other_connection(lambda: _retarget_if_request_is_free(target_pk)))
            return real_lock_clients(client_refs, **kwargs)

        monkeypatch.setattr(merge_service, "lock_clients", _lock_clients_after_a_retarget)

        with pytest.raises(ValidationError) as excinfo:
            approve_client_merge(dr, reviewer)

        assert excinfo.value.messages == [CHANGED_MESSAGE]
        assert len(request_free_at_person_lock) >= 2, "the approval did not start over on the current persons"
        assert all(request_free_at_person_lock), (
            "a person was locked while the approval held the request row",
            request_free_at_person_lock,
        )
        dr.refresh_from_db()
        source.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING
        assert dr.reviewed_by_id is None
        assert source.merged_into_id is None
        assert not source.is_deleted
