"""Refs #2374: the cleanup of stale proposals decides about the event at its locked row.

``cleanup_stale_proposals`` removes open and approved proposals whose event is
gone or in the trash. The selection is read without locks. If the event is
back in the live set by the time the proposal would be deleted, the proposal
is not stale any more and must stay — otherwise the event silently loses its
open or approved proposal.

The restore is a state change committed by a second connection: once after the
selection, and once uncommitted while the cleanup reaches the event, so the
cleanup has to wait for it at the event row. A plain unlocked re-read would
see the old state in the second case.

Lock order per proposal: the event row (the target anchor every decision on a
proposal takes first), then the proposal row, no audit chain. The ways that
lock both take the event first as well, so none of them can close a cycle with
the cleanup; the no-deadlock checks below run the cleanup against a hold and an
approval in both orders.
"""

import threading
import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone

from core.models import Event, LegalHold, RetentionProposal
from core.retention.legal_holds import create_legal_hold
from core.retention.proposals import approve_proposal, cleanup_stale_proposals
from tests._retention_race_helpers import (
    AUDIT_CHAIN,
    AfterFirstRead,
    park_until_the_other_session_waits,
    record_timeline,
    run_threads,
    session_thread,
)
from tests._row_lock_helpers import mitschnitt

_STALE_STATUSES = [RetentionProposal.Status.PENDING, RetentionProposal.Status.APPROVED]


def _move_to_trash(event_id):
    Event.objects.filter(pk=event_id).update(is_deleted=True, deleted_at=timezone.now())


def _restore(event_id):
    Event.objects.filter(pk=event_id).update(is_deleted=False, deleted_at=None, deleted_by=None)


def _status(proposal_pk):
    return RetentionProposal.objects.filter(pk=proposal_pk).values_list("status", flat=True).first()


def _stale_proposal(proposal, status):
    RetentionProposal.objects.filter(pk=proposal.pk).update(status=status)
    _move_to_trash(proposal.target_id)
    return RetentionProposal.objects.get(pk=proposal.pk)


def _second_event(event):
    return Event.objects.create(
        facility=event.facility,
        client=event.client,
        document_type=event.document_type,
        occurred_at=timezone.now(),
        data_json={"dauer": 5},
        created_by=event.created_by,
    )


@pytest.mark.django_db(transaction=True)
class TestAnEventRestoredAfterTheSelectionKeepsItsProposal:
    @pytest.mark.parametrize("status", _STALE_STATUSES)
    def test_restore_committed_after_the_selection(self, retention_proposal, status):
        proposal = _stale_proposal(retention_proposal, status)
        results, errors = {}, {}

        def restore_in_a_second_connection():
            run_threads([session_thread("restore", lambda: _restore(proposal.target_id), results, errors)])

        trigger = AfterFirstRead("core_event", restore_in_a_second_connection)
        with connection.execute_wrapper(trigger):
            removed = cleanup_stale_proposals(proposal.facility)

        assert trigger.fired, "the cleanup never read the events"
        assert not errors, errors
        assert removed == 0
        assert _status(proposal.pk) == status

    @pytest.mark.parametrize("status", _STALE_STATUSES)
    def test_uncommitted_restore_is_waited_for(self, retention_proposal, status):
        proposal = _stale_proposal(retention_proposal, status)
        restored = threading.Event()
        cleanup_session, results, errors = {}, {}, {}

        def restoring():
            with transaction.atomic():
                _restore(proposal.target_id)
                # Commit only once the cleanup waits for the event row.
                park_until_the_other_session_waits(restored, cleanup_session)
            return "restored"

        def cleaning():
            assert restored.wait(timeout=30), "the restore never started"
            return cleanup_stale_proposals(proposal.facility)

        run_threads(
            [
                session_thread("restore", restoring, results, errors),
                session_thread("cleanup", cleaning, results, errors, session=cleanup_session),
            ]
        )

        assert not errors, errors
        assert results == {"restore": "restored", "cleanup": 0}
        assert _status(proposal.pk) == status


@pytest.mark.django_db(transaction=True)
class TestLockOrder:
    def test_each_proposal_is_decided_under_its_event_lock(self, retention_proposal):
        first = _stale_proposal(retention_proposal, RetentionProposal.Status.PENDING)
        other_event = _second_event(Event.objects.get(pk=first.target_id))
        second = RetentionProposal.objects.create(
            facility=first.facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=other_event.pk,
            deletion_due_at=timezone.localdate(),
            retention_category="anonymous",
            status=RetentionProposal.Status.APPROVED,
        )
        _move_to_trash(other_event.pk)
        labels = {"first": first.pk, "second": second.pk}
        by_event = sorted([(first.target_id, "first"), (second.target_id, "second")])

        timeline, removed = record_timeline(first.facility_id, labels, lambda: cleanup_stale_proposals(first.facility))

        expected = []
        for _, label in by_event:
            expected += ["lock core_event", "lock core_retentionproposal", f"{label} -> None"]
        assert timeline.compact() == expected
        assert AUDIT_CHAIN not in timeline.events
        assert removed == 2

    def test_a_restored_event_stops_before_its_proposal_is_locked(self, retention_proposal):
        proposal = _stale_proposal(retention_proposal, RetentionProposal.Status.PENDING)

        def restore_after_the_selection():
            _restore(proposal.target_id)

        with mitschnitt() as recording:
            trigger = AfterFirstRead("core_event", restore_after_the_selection)
            with connection.execute_wrapper(trigger):
                removed = cleanup_stale_proposals(proposal.facility)

        assert removed == 0
        assert recording.sperrfolge() == ["core_event"]
        assert _status(proposal.pk) == RetentionProposal.Status.PENDING


@pytest.mark.django_db
class TestTheCleanupStillRemovesStaleProposals:
    @pytest.mark.parametrize("status", _STALE_STATUSES)
    def test_proposal_of_an_event_in_the_trash_is_removed(self, retention_proposal, status):
        proposal = _stale_proposal(retention_proposal, status)

        assert cleanup_stale_proposals(proposal.facility) == 1
        assert _status(proposal.pk) is None

    def test_proposal_without_any_event_is_removed(self, retention_proposal):
        orphan = RetentionProposal.objects.create(
            facility=retention_proposal.facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=uuid.uuid4(),
            deletion_due_at=timezone.localdate(),
            retention_category="anonymous",
        )

        assert cleanup_stale_proposals(orphan.facility) == 1
        assert _status(orphan.pk) is None
        assert _status(retention_proposal.pk) == RetentionProposal.Status.PENDING

    @pytest.mark.parametrize(
        "status",
        [RetentionProposal.Status.HELD, RetentionProposal.Status.DEFERRED, RetentionProposal.Status.REJECTED],
    )
    def test_decided_proposal_of_an_event_in_the_trash_stays(self, retention_proposal, status):
        proposal = _stale_proposal(retention_proposal, status)

        assert cleanup_stale_proposals(proposal.facility) == 0
        assert _status(proposal.pk) == status


_OTHER_WAYS = {
    "hold": lambda proposal, user: create_legal_hold(proposal, user, "proceedings"),
    "approve": lambda proposal, user: approve_proposal(proposal, user),
}


@pytest.mark.django_db(transaction=True)
class TestNoDeadlockWithTheWaysThatLockTheEvent:
    @pytest.mark.parametrize("way", sorted(_OTHER_WAYS))
    def test_cleanup_first(self, retention_proposal, lead_user, way):
        proposal = _stale_proposal(retention_proposal, RetentionProposal.Status.PENDING)
        signal = threading.Event()
        cleanup_session, way_session, results, errors = {}, {}, {}, {}

        def cleaning():
            # Park right after the own event row lock.
            with mitschnitt() as recording:
                recording.nach_sperre("core_event", lambda: park_until_the_other_session_waits(signal, way_session))
                return cleanup_stale_proposals(proposal.facility)

        def deciding():
            assert signal.wait(timeout=30), "the cleanup never locked the event"
            return _OTHER_WAYS[way](RetentionProposal.objects.get(pk=proposal.pk), lead_user)

        run_threads(
            [
                session_thread("cleanup", cleaning, results, errors, session=cleanup_session),
                session_thread(way, deciding, results, errors, session=way_session),
            ]
        )

        assert not errors, errors
        assert results["cleanup"] == 1
        # The proposal is gone once the other way gets the event row.
        assert isinstance(results[way], ValidationError), results
        assert _status(proposal.pk) is None
        assert not LegalHold.objects.filter(target_id=proposal.target_id).exists()

    @pytest.mark.parametrize(
        ("way", "removed", "status_after"),
        [("approve", 1, None), ("hold", 0, RetentionProposal.Status.HELD)],
    )
    def test_other_way_first(self, retention_proposal, lead_user, way, removed, status_after):
        proposal = _stale_proposal(retention_proposal, RetentionProposal.Status.PENDING)
        signal = threading.Event()
        cleanup_session, way_session, results, errors = {}, {}, {}, {}

        def deciding():
            # Park right after the own proposal row lock (event and person are held).
            with mitschnitt() as recording:
                recording.nach_sperre(
                    "core_retentionproposal", lambda: park_until_the_other_session_waits(signal, cleanup_session)
                )
                _OTHER_WAYS[way](RetentionProposal.objects.get(pk=proposal.pk), lead_user)
            return "ok"

        def cleaning():
            assert signal.wait(timeout=30), f"{way} never locked the proposal"
            return cleanup_stale_proposals(proposal.facility)

        run_threads(
            [
                session_thread(way, deciding, results, errors, session=way_session),
                session_thread("cleanup", cleaning, results, errors, session=cleanup_session),
            ]
        )

        assert not errors, errors
        assert results == {way: "ok", "cleanup": removed}
        assert _status(proposal.pk) == status_after
