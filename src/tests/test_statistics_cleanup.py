"""Retiring an unused statistics cache preserves reports and audit history."""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.cache import cache
from django.core.management import call_command, get_commands
from django.utils import timezone

from core.models import AuditLog
from core.services.audit import audit_event
from core.services.compliance import cron_job_checks
from core.services.system import bypass_replication_triggers

pytestmark = pytest.mark.django_db


def test_no_statistics_refresh_command_registered():
    assert not [name for name in get_commands() if name.startswith("refresh_") and "statistics" in name]


@pytest.mark.parametrize("age_hours", [None, 1, 48], ids=["missing", "recent", "stale"])
def test_compliance_page_has_only_active_jobs(client, super_admin_user, age_hours):
    if age_hours is not None:
        entry = audit_event(AuditLog.Action.MV_REFRESH_COMPLETED, user=None, facility=None, target_type="MVRefreshRun")
        with bypass_replication_triggers():
            AuditLog.objects.filter(pk=entry.pk).update(timestamp=timezone.now() - timedelta(hours=age_hours))
    assert {check.key for check in cron_job_checks()} == {
        "backup_age",
        "retention_last_run",
        "audit_chain_last_run",
        "snapshot_last_run",
        "breach_scan_last_run",
    }
    client.force_login(super_admin_user)
    response = client.get("/system/compliance/")
    assert response.status_code == 200
    assert "Statistik-View-Refresh" not in response.content.decode()


def test_health_does_not_report_retired_job_as_stale(client, settings):
    settings.HEALTH_DETAIL_TOKEN = "statistics-cleanup-health-token"
    entry = audit_event(AuditLog.Action.MV_REFRESH_COMPLETED, user=None, facility=None, target_type="MVRefreshRun")
    with bypass_replication_triggers():
        AuditLog.objects.filter(pk=entry.pk).update(timestamp=timezone.now() - timedelta(hours=48))
    cache.clear()
    try:
        response = client.get("/health/", HTTP_X_HEALTH_TOKEN=settings.HEALTH_DETAIL_TOKEN)
        assert response.status_code == 200
        assert not any(key.startswith("mv_") for key in response.json()["stale_jobs"])
    finally:
        cache.clear()


def test_existing_refresh_audit_entry_is_readable_and_chain_verifies(settings, tmp_path):
    settings.AUDIT_HWM_PATH = str(tmp_path / "audit-hwm.json")
    entry = audit_event(AuditLog.Action.MV_REFRESH_COMPLETED, user=None, facility=None, target_type="MVRefreshRun")
    entry.refresh_from_db()
    assert entry.action == "mv_refresh_completed"
    assert entry.get_action_display()
    original_hash = entry.entry_hash
    output = StringIO()
    call_command("verify_audit_chain", stdout=output)
    entry.refresh_from_db()
    assert entry.entry_hash == original_hash
    assert entry.pk in AuditLog.objects.values_list("pk", flat=True)
