"""Refs #2374: a hold on an older approved proposal and a decision on a newer proposal of the same target.

``APPROVED`` is not part of the active proposal set: once a proposal is
approved, the next proposal run may create a new ``PENDING`` proposal for the
same event (``create_proposal``). A hold on the older, approved proposal
moves it back into the active set (``HELD``) — and the database checks the
uniqueness of the active set (``unique_active_retention_proposal``) against
the newer proposal. If that check runs after the hold already holds the
facility's audit chain, it waits for a transaction that is deciding the newer
proposal, while that transaction waits for the audit chain: a deadlock.

Every way that changes a proposal's status therefore follows one order:
target anchors (person -> event), then every proposal of the target in
primary-key order, then the status write including its uniqueness check,
and only then the audit chain. A newer proposal that is still active refuses
the hold before anything is written.
"""

import threading
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, LegalHold, RetentionProposal
from core.retention.legal_holds import create_legal_hold, dismiss_legal_hold
from core.retention.proposals import (
    approve_proposal,
    create_proposal,
    defer_proposal,
    reactivate_deferred_proposals,
    reject_proposal,
)
from tests._retention_race_helpers import (
    AUDIT_CHAIN,
    AfterOwnStatusChange,
    immediately_lockable,
    park_until_the_other_session_waits,
    record_timeline,
    run_threads,
    session_thread,
)
from tests._row_lock_helpers import mitschnitt

OTHER_ACTIVE_PROPOSAL_TEXT = "weiterer offener Löschvorschlag"
EXISTING_HOLD_TEXT = "existiert bereits ein aktiver Hold"


def _two_generations(older, user):
    """An approved older proposal and a newer pending proposal of the same event, via the public services."""
    approve_proposal(older, user)
    current, created = create_proposal(
        older.facility,
        older.target_type,
        older.target_id,
        older.deletion_due_at,
        {},
        older.retention_category,
    )
    assert created, "precondition: an approved proposal leaves room for a new active proposal"
    return RetentionProposal.objects.get(pk=older.pk), current


def _fresh(proposal):
    return RetentionProposal.objects.get(pk=proposal.pk)


def _active_holds(proposal):
    return LegalHold.objects.filter(target_id=proposal.target_id, dismissed_at__isnull=True)


def _hold_audit_rows(proposal):
    return AuditLog.objects.filter(
        action=AuditLog.Action.LEGAL_HOLD,
        target_id=str(proposal.target_id),
        detail__category="legal_hold_created",
    )


@pytest.mark.django_db(transaction=True)
class TestEveryStatusChangeWithTwoGenerationsPrecedesTheAuditChain:
    """Observed order per way, with an approved older and a newer proposal of the same event."""

    def test_hold_locks_both_proposals_and_writes_the_status_before_the_audit_chain(
        self, retention_proposal, lead_user
    ):
        older, current = _two_generations(retention_proposal, lead_user)
        reject_proposal(current, lead_user)
        free_at_audit_chain = []

        timeline, hold = record_timeline(
            older.facility_id,
            {"older": older.pk, "current": current.pk},
            lambda: create_legal_hold(_fresh(older), lead_user, "court proceedings"),
            on_audit_chain=lambda: free_at_audit_chain.extend(
                immediately_lockable(
                    [("older", RetentionProposal, older.pk), ("current", RetentionProposal, current.pk)]
                )
            ),
        )

        assert timeline.compact() == [
            "lock core_client",
            "lock core_event",
            "lock core_retentionproposal",
            "older -> held",
            AUDIT_CHAIN,
        ]
        # Both proposals of the target are locked once the audit chain is held.
        assert free_at_audit_chain == []
        assert isinstance(hold, LegalHold)
        assert _fresh(older).status == RetentionProposal.Status.HELD

    @pytest.mark.parametrize(
        ("way", "expected"),
        [
            (
                "approve",
                ["lock core_client", "lock core_event", "lock core_retentionproposal", "current -> approved"],
            ),
            ("defer", ["lock core_retentionproposal", "current -> deferred"]),
            ("reject", ["lock core_retentionproposal", "current -> rejected"]),
            ("reactivate", ["lock core_retentionproposal", "current -> pending"]),
            ("dismiss_hold", ["lock core_legalhold", "current -> pending"]),
        ],
    )
    def test_decisions_on_the_newer_proposal_write_their_status_before_the_audit_chain(
        self, retention_proposal, lead_user, way, expected
    ):
        older, current = _two_generations(retention_proposal, lead_user)
        facility = older.facility
        if way == "reactivate":
            defer_proposal(current, lead_user)
            RetentionProposal.objects.filter(pk=current.pk).update(
                deferred_until=timezone.localdate() - timedelta(days=1)
            )
        hold = create_legal_hold(_fresh(current), lead_user, "proceedings") if way == "dismiss_hold" else None
        ways = {
            "approve": lambda: approve_proposal(_fresh(current), lead_user),
            "defer": lambda: defer_proposal(_fresh(current), lead_user),
            "reject": lambda: reject_proposal(_fresh(current), lead_user),
            "reactivate": lambda: reactivate_deferred_proposals(facility),
            "dismiss_hold": lambda: dismiss_legal_hold(LegalHold.objects.get(pk=hold.pk), lead_user),
        }

        timeline, _ = record_timeline(older.facility_id, {"older": older.pk, "current": current.pk}, ways[way])

        assert timeline.compact() == [*expected, AUDIT_CHAIN]
        assert _fresh(older).status == RetentionProposal.Status.APPROVED


@pytest.mark.django_db
class TestARemainingActiveProposalRefusesTheHold:
    @pytest.mark.parametrize(
        ("current_status", "message"),
        [
            (RetentionProposal.Status.PENDING, OTHER_ACTIVE_PROPOSAL_TEXT),
            (RetentionProposal.Status.DEFERRED, OTHER_ACTIVE_PROPOSAL_TEXT),
            (RetentionProposal.Status.HELD, EXISTING_HOLD_TEXT),
        ],
    )
    def test_refused_before_any_write(self, retention_proposal, lead_user, current_status, message):
        older, current = _two_generations(retention_proposal, lead_user)
        if current_status == RetentionProposal.Status.DEFERRED:
            defer_proposal(current, lead_user)
        elif current_status == RetentionProposal.Status.HELD:
            create_legal_hold(current, lead_user, "proceedings")
        holds_before = list(_active_holds(older).values_list("pk", flat=True))
        audit_rows_before = _hold_audit_rows(older).count()

        with pytest.raises(ValidationError, match=message):
            with transaction.atomic():
                create_legal_hold(_fresh(older), lead_user, "second hold")

        assert _fresh(older).status == RetentionProposal.Status.APPROVED
        assert _fresh(current).status == current_status
        assert list(_active_holds(older).values_list("pk", flat=True)) == holds_before
        assert _hold_audit_rows(older).count() == audit_rows_before

    def test_hold_request_answers_409_with_the_message(self, client, retention_proposal, lead_user):
        older, current = _two_generations(retention_proposal, lead_user)
        client.force_login(lead_user)

        response = client.post(reverse("core:retention_hold", args=[older.pk]), {"reason": "proceedings"})

        assert response.status_code == 409
        assert OTHER_ACTIVE_PROPOSAL_TEXT in response.content.decode()
        assert _fresh(older).status == RetentionProposal.Status.APPROVED
        assert _fresh(current).status == RetentionProposal.Status.PENDING
        assert not _active_holds(older).exists()
        assert not _hold_audit_rows(older).exists()


@pytest.mark.django_db(transaction=True)
class TestHoldOnTheOlderAndDecisionOnTheNewerProposalDoNotDeadlock:
    @pytest.mark.parametrize(
        ("decide", "status_after"),
        [(reject_proposal, RetentionProposal.Status.REJECTED), (defer_proposal, RetentionProposal.Status.DEFERRED)],
        ids=["reject", "defer"],
    )
    @pytest.mark.parametrize("first", ["decision_first", "hold_first"])
    def test_both_outcomes_allowed_no_deadlock(self, retention_proposal, lead_user, decide, status_after, first):
        older, current = _two_generations(retention_proposal, lead_user)
        signal = threading.Event()
        hold_session, decision_session, results, errors = {}, {}, {}, {}

        def deciding():
            if first == "decision_first":
                # Park right after the own status write, before the audit row.
                wrapper = AfterOwnStatusChange(
                    current.pk, lambda: park_until_the_other_session_waits(signal, hold_session)
                )
                with connection.execute_wrapper(wrapper):
                    decide(_fresh(current), lead_user)
            else:
                assert signal.wait(timeout=30), "the hold never reached the proposals"
                decide(_fresh(current), lead_user)
            return "ok"

        def holding():
            if first == "hold_first":
                # Park right after the first proposal row lock of the hold.
                with mitschnitt() as recording:
                    recording.nach_sperre(
                        "core_retentionproposal",
                        lambda: park_until_the_other_session_waits(signal, decision_session),
                    )
                    return create_legal_hold(_fresh(older), lead_user, "proceedings")
            assert signal.wait(timeout=30), "the decision never wrote its status"
            # The same bracket as ``RetentionHoldView.post``.
            with transaction.atomic():
                return create_legal_hold(_fresh(older), lead_user, "proceedings")

        run_threads(
            [
                session_thread("decision", deciding, results, errors, session=decision_session),
                session_thread("hold", holding, results, errors, session=hold_session),
            ]
        )

        assert not errors, errors
        assert results["decision"] == "ok", results
        assert _fresh(current).status == status_after
        if isinstance(results["hold"], LegalHold):
            assert _fresh(older).status == RetentionProposal.Status.HELD
            assert _active_holds(older).count() == 1
            assert _hold_audit_rows(older).count() == 1
        else:
            assert isinstance(results["hold"], ValidationError), results
            assert _fresh(older).status == RetentionProposal.Status.APPROVED
            assert not _active_holds(older).exists()
            assert not _hold_audit_rows(older).exists()
