"""Tests for retention bulk actions (#514) and deferred-follow-up behaviour (#515)."""

import uuid
from datetime import date, timedelta

import pytest

from core.models import AuditLog, RetentionProposal
from core.services.retention import (
    bulk_approve_proposals,
    bulk_defer_proposals,
    bulk_reject_proposals,
    defer_proposal,
    reject_proposal,
)


def _make_proposal(facility, status=RetentionProposal.Status.PENDING, target_id=None):
    return RetentionProposal.objects.create(
        facility=facility,
        target_type=RetentionProposal.TargetType.EVENT,
        target_id=target_id or uuid.uuid4(),
        retention_category="anonymous",
        deletion_due_at=date.today() - timedelta(days=1),
        status=status,
    )


@pytest.mark.django_db
class TestBulkActions:
    def test_bulk_approve_processes_all(self, facility, lead_user):
        ps = [_make_proposal(facility) for _ in range(3)]
        count = bulk_approve_proposals(ps, lead_user)
        assert count == 3
        for p in ps:
            p.refresh_from_db()
            assert p.status == RetentionProposal.Status.APPROVED

    def test_bulk_defer_increments_defer_count(self, facility, lead_user):
        p1 = _make_proposal(facility)
        bulk_defer_proposals([p1], lead_user, days=14)
        p1.refresh_from_db()
        assert p1.status == RetentionProposal.Status.DEFERRED
        assert p1.defer_count == 1
        assert p1.deferred_until == date.today() + timedelta(days=14)

    def test_bulk_reject_marks_all(self, facility, lead_user):
        p1 = _make_proposal(facility)
        bulk_reject_proposals([p1], lead_user)
        p1.refresh_from_db()
        assert p1.status == RetentionProposal.Status.REJECTED


@pytest.mark.django_db
class TestDeferFollowup:
    def test_defer_writes_audit_entry(self, facility, lead_user):
        p = _make_proposal(facility)
        defer_proposal(p, lead_user, days=7)
        entry = AuditLog.objects.filter(target_id=str(p.target_id)).latest("timestamp")
        assert entry.detail.get("category") == "retention_proposal_deferred"
        assert entry.detail.get("defer_count") == 1

    def test_defer_count_accumulates(self, facility, lead_user):
        p = _make_proposal(facility)
        defer_proposal(p, lead_user)
        # Reset to pending to defer again
        p.status = RetentionProposal.Status.PENDING
        p.save(update_fields=["status"])
        defer_proposal(p, lead_user)
        p.refresh_from_db()
        assert p.defer_count == 2

    def test_reject_writes_audit_entry(self, facility, lead_user):
        p = _make_proposal(facility)
        reject_proposal(p, lead_user)
        entry = AuditLog.objects.filter(target_id=str(p.target_id)).latest("timestamp")
        assert entry.detail.get("category") == "retention_proposal_rejected"
