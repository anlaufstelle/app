"""Retention cleanup through the command and public services (Refs #2524)."""

from datetime import timedelta
from io import StringIO
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.models import AuditLog, Case, DeletionRequest, LegalHold
from core.retention.audit_pruning import prune_auditlog
from core.services.audit.chain import verify_chain
from tests._factories import make_event

pytestmark = pytest.mark.django_db


def old_audit(facility, now, offset, *, target_type="", target_id=""):
    return AuditLog.objects.create(
        facility=facility,
        action=AuditLog.Action.LOGIN,
        timestamp=now - timedelta(days=800 - offset),
        target_type=target_type,
        target_id=str(target_id),
    )


@pytest.mark.parametrize("hold_type", ["Client", "Case", "Event"])
def test_audit_hold_survivors_keep_authenticated_chain(
    facility, settings_obj, client_identified, doc_type_contact, staff_user, hold_type
):
    now = timezone.now()
    case = Case.objects.create(facility=facility, client=client_identified, title="Held case")
    event = make_event(
        facility=facility,
        document_type=doc_type_contact,
        created_by=staff_user,
        client=client_identified,
        case=case,
        occurred_at=now,
        data_json={},
    )
    targets = {"Client": client_identified.pk, "Case": case.pk, "Event": event.pk}
    before = old_audit(facility, now, 0)
    held = [
        old_audit(facility, now, i + 1, target_type=kind, target_id=pk) for i, (kind, pk) in enumerate(targets.items())
    ]
    after = old_audit(facility, now, 5)
    LegalHold.objects.create(
        facility=facility, target_type=hold_type, target_id=targets[hold_type], reason="Evidence", created_by=staff_user
    )

    prune_auditlog(facility, settings_obj, now=now)

    assert not AuditLog.objects.filter(pk__in=[before.pk, after.pk]).exists()
    assert AuditLog.objects.filter(pk=held[list(targets).index(hold_type)].pk).exists()
    if hold_type == "Client":
        assert AuditLog.objects.filter(pk__in=[row.pk for row in held]).count() == 3
    result = verify_chain(facility)
    assert result.ok, result.reason


@pytest.mark.parametrize("disabled,dry_run", [(False, False), (True, False), (False, True)])
def test_command_prunes_system_chain_with_separate_period(settings, facility, settings_obj, disabled, dry_run):
    settings.AUDITLOG_SYSTEM_RETENTION_MONTHS = 0 if disabled else 24
    now = timezone.now()
    old = old_audit(None, now, 0)
    exempt = AuditLog.objects.create(
        facility=None, action=AuditLog.Action.SECURITY_VIOLATION, timestamp=now - timedelta(days=799)
    )
    recent = AuditLog.objects.create(facility=None, action=AuditLog.Action.LOGIN)
    settings_obj.auditlog_retention_months = 0
    settings_obj.save()

    call_command("enforce_retention", dry_run=dry_run, stdout=StringIO())

    assert AuditLog.objects.filter(pk=old.pk).exists() == (disabled or dry_run)
    assert AuditLog.objects.filter(pk__in=[exempt.pk, recent.pk]).count() == 2
    result = verify_chain(None)
    assert result.ok, result.reason


@pytest.mark.parametrize("status", ["approved", "rejected", "withdrawn"])
@pytest.mark.parametrize("stage_downgrade", [False, True])
def test_command_prunes_only_completed_requests_after_configured_period(
    facility, settings_obj, staff_user, status, stage_downgrade
):
    settings_obj.deletion_request_retention_days = 7
    settings_obj.save()
    now = timezone.now()

    def request(*, status, age, completed=True):
        return DeletionRequest.objects.create(
            facility=facility,
            target_type="Client",
            target_id=uuid4(),
            requested_by=staff_user,
            reason="Review",
            stage_downgrade=stage_downgrade,
            status=status,
            reviewed_at=now - timedelta(days=age) if completed else None,
        )

    expired = request(status=status, age=8)
    recent = request(status=status, age=6)
    unstamped = request(status=status, age=100, completed=False)
    pending = request(status="pending", age=100)
    audit = AuditLog.objects.create(
        facility=facility, action=AuditLog.Action.DELETION_APPROVED, target_id=str(expired.pk)
    )
    call_command("enforce_retention", dry_run=True, stdout=StringIO())
    assert DeletionRequest.objects.filter(pk=expired.pk).exists()

    call_command("enforce_retention", stdout=StringIO())

    assert not DeletionRequest.objects.filter(pk=expired.pk).exists()
    assert DeletionRequest.objects.filter(pk__in=[recent.pk, unstamped.pk, pending.pk]).count() == 3
    assert AuditLog.objects.filter(pk=audit.pk).exists()
