"""Automatic approval respects committed deferral changes (Refs #2524)."""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from core.models import AuditLog, RetentionProposal
from core.retention.proposals import approve_proposal, create_proposals_for_facility, defer_proposal
from core.services.events.crud import create_event
from tests._retention_approval_helpers import prime_first_approval
from tests._retention_race_helpers import AfterFirstRead, run_threads, session_thread

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def due_proposal(facility, settings_obj, client_qualified, lead_user, doc_type_contact):
    settings_obj.retention_qualified_days = 30
    settings_obj.retention_max_defer_count = 1
    settings_obj.retention_auto_approve_after_defer = True
    settings_obj.save()

    def create():
        event = create_event(
            facility,
            lead_user,
            doc_type_contact,
            timezone.now() - timedelta(days=45),
            {"dauer": 20},
            client=client_qualified,
        )
        create_proposals_for_facility(facility, settings_obj, timezone.now())
        proposal = RetentionProposal.objects.get(facility=facility, target_id=event.pk)
        with patch("django.utils.timezone.now", return_value=timezone.now() - timedelta(days=2)):
            defer_proposal(proposal, lead_user, days=1)
        return event, proposal

    return create


def _snapshot(proposal):
    proposal.refresh_from_db()
    return (
        proposal.status,
        proposal.deferred_until,
        proposal.defer_count,
        proposal.approved_at,
        proposal.approved_by_id,
    )


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("proposal_count", [1, 2])
def test_command_keeps_concurrently_renewed_deferral(
    due_proposal, facility, settings_obj, lead_user, caplog, enabled, proposal_count
):
    event, proposal = due_proposal()
    other_event = due_proposal()[0] if proposal_count == 2 else None
    settings_obj.retention_auto_approve_after_defer = enabled
    settings_obj.save(update_fields=["retention_auto_approve_after_defer"])
    results, errors = {}, {}

    def renew():
        current = RetentionProposal.objects.get(pk=proposal.pk)
        defer_proposal(current, lead_user, days=30)
        return _snapshot(current)

    def concurrent_change():
        run_threads([session_thread("renew-deferral", renew, results, errors)])

    trigger = AfterFirstRead("core_retentionproposal", concurrent_change)
    with connection.execute_wrapper(trigger):
        call_command("enforce_retention", facility=facility.name, stdout=StringIO())

    assert trigger.fired
    assert not errors, errors
    assert results["renew-deferral"] == (
        RetentionProposal.Status.DEFERRED,
        timezone.localdate() + timedelta(days=30),
        2,
        None,
        None,
    )
    event.refresh_from_db()
    assert not event.is_deleted
    assert event.data_json == {"dauer": 20}
    assert _snapshot(proposal) == results["renew-deferral"]
    decisions = AuditLog.objects.filter(facility=facility, target_id=str(event.pk))
    assert not decisions.filter(detail__category="retention_proposal_auto_approved").exists()
    assert not decisions.filter(detail__category="retention_proposal_reactivated").exists()
    assert decisions.filter(detail__category="retention_proposal_auto_approve_skipped").count() == int(enabled)
    if enabled:
        assert any("1" in record.getMessage() and "skipped" in record.getMessage() for record in caplog.records)
    if other_event is not None:
        other_event.refresh_from_db()
        assert other_event.is_deleted is enabled


def test_manual_approval_of_a_future_deferral_remains_allowed(due_proposal, facility, lead_user):
    event, proposal = due_proposal()
    defer_proposal(proposal, lead_user, days=30)
    assert proposal.deferred_until > timezone.localdate()
    prime_first_approval(proposal)
    approve_proposal(proposal, lead_user)
    call_command("enforce_retention", facility=facility.name, stdout=StringIO())
    event.refresh_from_db()
    assert event.is_deleted
    assert (
        AuditLog.objects.filter(
            facility=facility, target_id=str(event.pk), detail__category="retention_proposal_approved"
        ).count()
        == 1
    )


@pytest.mark.parametrize(
    "changed_fields",
    [
        {"status": RetentionProposal.Status.PENDING},
        {"deferred_until": None},
        {"defer_count": 0},
    ],
    ids=["pending-review", "missing-date", "below-threshold"],
)
def test_command_rechecks_auto_approval_eligibility(due_proposal, facility, changed_fields):
    event, proposal = due_proposal()
    results, errors = {}, {}

    def update_eligibility():
        current = RetentionProposal.objects.get(pk=proposal.pk)
        for field, value in changed_fields.items():
            setattr(current, field, value)
        current.save(update_fields=list(changed_fields))
        return _snapshot(current)

    def concurrent_change():
        run_threads([session_thread("change-eligibility", update_eligibility, results, errors)])

    trigger = AfterFirstRead("core_retentionproposal", concurrent_change)
    with connection.execute_wrapper(trigger):
        call_command("enforce_retention", facility=facility.name, stdout=StringIO())

    assert trigger.fired
    assert not errors, errors
    event.refresh_from_db()
    assert not event.is_deleted
    assert event.data_json == {"dauer": 20}
    assert _snapshot(proposal) == results["change-eligibility"]
    decisions = AuditLog.objects.filter(facility=facility, target_id=str(event.pk))
    assert not decisions.filter(detail__category="retention_proposal_auto_approved").exists()
    assert decisions.filter(detail__category="retention_proposal_auto_approve_skipped").count() == 1


def test_stored_zero_threshold_does_not_auto_approve_uncounted_deferral(due_proposal, facility, settings_obj):
    event, proposal = due_proposal()
    type(settings_obj).objects.filter(pk=settings_obj.pk).update(retention_max_defer_count=0)
    RetentionProposal.objects.filter(pk=proposal.pk).update(defer_count=0)

    call_command("enforce_retention", facility=facility.name, stdout=StringIO())

    event.refresh_from_db()
    assert not event.is_deleted
    proposal.refresh_from_db()
    assert proposal.approved_at is None
    assert proposal.auto_approved_at is None
    decisions = AuditLog.objects.filter(facility=facility, target_id=str(event.pk))
    assert not decisions.filter(detail__category="retention_proposal_auto_approved").exists()
