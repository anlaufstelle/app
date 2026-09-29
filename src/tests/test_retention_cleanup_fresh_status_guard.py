"""Refs #2374: the cleanup of stale proposals decides at the row it deletes, not at its earlier selection.

``cleanup_stale_proposals`` selects open and approved proposals whose event is
gone and deletes them afterwards. Between the selection and the deletion a
hold, a deferral or a rejection can change such a proposal. A held proposal
must stay — its card on the retention dashboard shows the hold — and a
decided one is no longer the cleanup's business either.

The change is committed by a second connection right after the cleanup first
reads the events (observed via the query plan, not the wording). The
counter-check keeps the case the cleanup exists for: an approval in between
leaves the proposal stale, and it is removed.
"""

import pytest
from django.db import connection
from django.utils import timezone

from core.models import Event, LegalHold, RetentionProposal
from core.retention.legal_holds import create_legal_hold
from core.retention.proposals import (
    approve_proposal,
    cleanup_stale_proposals,
    defer_proposal,
    reject_proposal,
)
from tests._retention_race_helpers import AfterFirstRead, run_threads, session_thread

_CHANGES = {
    "hold": lambda proposal, user: create_legal_hold(proposal, user, "Verfahren"),
    "defer": lambda proposal, user: defer_proposal(proposal, user),
    "reject": lambda proposal, user: reject_proposal(proposal, user),
    "approve": lambda proposal, user: approve_proposal(proposal, user),
}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("change", "kept_status"),
    [
        ("hold", RetentionProposal.Status.HELD),
        ("defer", RetentionProposal.Status.DEFERRED),
        ("reject", RetentionProposal.Status.REJECTED),
        ("approve", None),
    ],
)
def test_cleanup_keeps_a_proposal_changed_after_its_selection(retention_proposal, lead_user, change, kept_status):
    Event.objects.filter(pk=retention_proposal.target_id).update(is_deleted=True, deleted_at=timezone.now())
    results, errors = {}, {}

    def change_in_a_second_connection():
        run_threads(
            [
                session_thread(
                    change,
                    lambda: _CHANGES[change](RetentionProposal.objects.get(pk=retention_proposal.pk), lead_user),
                    results,
                    errors,
                )
            ]
        )

    trigger = AfterFirstRead("core_event", change_in_a_second_connection)
    with connection.execute_wrapper(trigger):
        removed = cleanup_stale_proposals(retention_proposal.facility)

    assert trigger.fired, "the cleanup never read the events"
    assert not errors, errors
    assert not isinstance(results.get(change), Exception), results
    remaining = RetentionProposal.objects.filter(pk=retention_proposal.pk).values_list("status", flat=True).first()
    if kept_status is None:
        # Still stale after the approval: removed as before.
        assert removed == 1
        assert remaining is None
    else:
        assert removed == 0
        assert remaining == kept_status
    if change == "hold":
        assert LegalHold.objects.filter(target_id=retention_proposal.target_id, dismissed_at__isnull=True).count() == 1
