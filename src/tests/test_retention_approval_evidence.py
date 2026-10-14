"""Only evidenced manual or explicitly automatic approvals authorize deletion."""

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from core.models import Event, RetentionProposal
from core.retention.enforcement import enforce_document_type_retention, process_facility_retention
from core.seed.deletions import seed_retention_proposals
from core.services.retention import approve_proposal, defer_proposal
from tests.test_retention_two_person_approval import proposal as proposal

pytestmark = pytest.mark.django_db


@pytest.mark.django_db(transaction=True)
def test_schema_upgrade_keeps_unproven_approval_and_reports_skip(proposal, lead_user, settings_obj, caplog):
    old = [("core", "0189_settings_auditlog_retention_help_text")]
    new = [("core", "0190_retentionproposal_first_approval")]
    executor = MigrationExecutor(connection)
    executor.migrate(old)
    try:
        model = executor.loader.project_state(old).apps.get_model("core", "RetentionProposal")
        model.objects.filter(pk=proposal.pk).update(
            status="approved", approved_by_id=lead_user.pk, approved_at=timezone.now()
        )
    finally:
        MigrationExecutor(connection).migrate(new)
    proposal.refresh_from_db()
    assert proposal.first_approved_by_id is None and proposal.first_approved_at is None
    result = process_facility_retention(proposal.facility, settings_obj, timezone.now(), False)
    assert not Event.objects.get(pk=proposal.target_id).is_deleted
    assert result["approval_blocked"] == 1
    assert "approval evidence" in caplog.text
    assert RetentionProposal.objects.get(pk=proposal.pk).status == RetentionProposal.Status.APPROVED
    output = StringIO()
    call_command("enforce_retention", facility=proposal.facility.name, stdout=output)
    assert "approval evidence" in output.getvalue()
    assert not Event.objects.get(pk=proposal.target_id).is_deleted


@pytest.mark.parametrize("mode", ["manual", "auto"])
def test_proven_approvals_still_delete(proposal, lead_user, admin_user, settings_obj, mode):
    if mode == "manual":
        approve_proposal(proposal, lead_user)
        approve_proposal(proposal, admin_user)
    else:
        settings_obj.retention_auto_approve_after_defer = True
        settings_obj.retention_max_defer_count = 1
        settings_obj.save(update_fields=["retention_auto_approve_after_defer", "retention_max_defer_count"])
        defer_proposal(proposal, lead_user, days=0)
    call_command("enforce_retention", facility=proposal.facility.name, stdout=StringIO())
    assert Event.objects.get(pk=proposal.target_id).is_deleted


@pytest.mark.parametrize("missing", ["first_approved_at", "approved_at", "approved_by"])
def test_incomplete_manual_evidence_blocks_document_type_path(
    proposal, lead_user, admin_user, doc_type_contact, missing
):
    approve_proposal(proposal, lead_user)
    approve_proposal(proposal, admin_user)
    RetentionProposal.objects.filter(pk=proposal.pk).update(**{missing: None})
    doc_type_contact.retention_days = 30
    doc_type_contact.save(update_fields=["retention_days"])
    result = enforce_document_type_retention(proposal.facility, timezone.now(), False)
    assert not Event.objects.get(pk=proposal.target_id).is_deleted
    assert result["count"] == 0 and result["approval_blocked"] == 1


@pytest.mark.parametrize("pool_size", [1, 2])
def test_seed_approved_examples_have_distinct_reviewers(proposal, lead_user, admin_user, pool_size):
    RetentionProposal.objects.filter(pk=proposal.pk).delete()
    users = [lead_user, admin_user][:pool_size]
    with patch("core.seed.deletions.random.choices", return_value=[RetentionProposal.Status.APPROVED]):
        assert seed_retention_proposals(proposal.facility, users, {"retention_proposals": 1}) == (1, 0)
    seeded = RetentionProposal.objects.get(target_id=proposal.target_id)
    if pool_size == 1:
        assert seeded.status == RetentionProposal.Status.PENDING
    else:
        assert seeded.status == RetentionProposal.Status.APPROVED
        assert seeded.first_approved_by_id and seeded.approved_by_id
        assert seeded.first_approved_by_id != seeded.approved_by_id
        assert seeded.first_approved_at and seeded.approved_at


def test_unproven_approval_counted_once_across_strategies(proposal, lead_user, settings_obj, doc_type_contact):
    RetentionProposal.objects.filter(pk=proposal.pk).update(
        status=RetentionProposal.Status.APPROVED, approved_by=lead_user, approved_at=timezone.now()
    )
    doc_type_contact.retention_days = 30
    doc_type_contact.save(update_fields=["retention_days"])
    result = process_facility_retention(proposal.facility, settings_obj, timezone.now(), False)
    assert result["count"] == 0 and result["approval_blocked"] == 1
    assert result.get("failed", 0) == 0
    assert not Event.objects.get(pk=proposal.target_id).is_deleted
    output = StringIO()
    call_command("enforce_retention", facility=proposal.facility.name, stdout=output)
    assert "1 event(s) skipped due to missing approval evidence" in output.getvalue()
    assert "failed=0" in output.getvalue()
