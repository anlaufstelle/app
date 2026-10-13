"""Only complete installation runs refresh operational success timestamps."""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from core.models import AuditLog, Settings
from core.services.compliance import ComplianceStatus
from core.services.compliance.cron import _breach_scan_checks, _snapshot_checks
from core.services.compliance.retention import _retention_checks
from core.views.health import _check_stale_jobs

pytestmark = pytest.mark.django_db

JOBS = [
    ("create_statistics_snapshots", AuditLog.Action.SNAPSHOT_RUN_COMPLETED, _snapshot_checks, "snapshot_last_run"),
    ("enforce_retention", AuditLog.Action.RETENTION_RUN_COMPLETED, _retention_checks, "retention_last_run"),
    ("detect_breaches", AuditLog.Action.BREACH_SCAN_COMPLETED, _breach_scan_checks, "breach_scan_last_run"),
]


def _old_success(action):
    return AuditLog.objects.create(
        action=action,
        facility=None,
        timestamp=timezone.now() - timedelta(days=90),
        detail={"job_run": {"scope": "installation", "status": "success", "processed": 2, "skipped": 0, "failed": 0}},
    )


@pytest.fixture
def second_settings(second_facility):
    return Settings.objects.create(facility=second_facility)


@pytest.fixture
def job_boundaries():
    with (
        patch(
            "core.management.commands.create_statistics_snapshots.create_or_update_snapshot", return_value=None
        ) as snapshot,
        patch("core.management.commands.detect_breaches.run_all_detections", return_value=[]) as breach,
        patch("core.management.commands.detect_breaches.run_system_detections", return_value=[]) as system,
    ):
        yield snapshot, breach, system


@pytest.mark.parametrize("command,action,checker,key", JOBS)
def test_facility_run_preserves_last_global_success(
    facility, second_facility, settings_obj, job_boundaries, command, action, checker, key
):
    old = _old_success(action)
    call_command(command, facility=facility.name, stdout=StringIO())
    assert list(AuditLog.objects.filter(action=action).values_list("pk", flat=True)) == [old.pk]
    assert checker()[0].status == ComplianceStatus.CRITICAL
    assert key in _check_stale_jobs()
    if command == "detect_breaches":
        job_boundaries[2].assert_not_called()


@pytest.mark.parametrize("failure_count", [1, 2])
def test_snapshot_failures_do_not_refresh_success(
    facility, second_facility, settings_obj, second_settings, job_boundaries, failure_count
):
    old = _old_success(AuditLog.Action.SNAPSHOT_RUN_COMPLETED)
    snapshot = job_boundaries[0]
    snapshot.side_effect = (
        [RuntimeError("snapshot unavailable"), None] if failure_count == 1 else RuntimeError("snapshot unavailable")
    )
    with pytest.raises(CommandError):
        call_command("create_statistics_snapshots", stdout=StringIO(), stderr=StringIO())
    assert AuditLog.objects.filter(action=AuditLog.Action.SNAPSHOT_RUN_COMPLETED).count() == 1
    assert _snapshot_checks()[0].status == ComplianceStatus.CRITICAL
    assert "snapshot_last_run" in _check_stale_jobs()
    assert snapshot.call_count == 2
    assert AuditLog.objects.get(pk=old.pk).timestamp == old.timestamp


@pytest.mark.parametrize("command,action,checker,key", JOBS[:2])
def test_missing_settings_does_not_cover_skipped_facility(
    facility, second_facility, settings_obj, job_boundaries, command, action, checker, key
):
    _old_success(action)
    out = StringIO()
    call_command(command, stdout=out)
    assert AuditLog.objects.filter(action=action).count() == 1
    assert "processed=1" in out.getvalue()
    assert "skipped=1" in out.getvalue()
    assert checker()[0].status == ComplianceStatus.CRITICAL


@pytest.mark.parametrize("command,action,checker,key", JOBS)
def test_complete_run_refreshes_success(
    facility, second_facility, settings_obj, second_settings, job_boundaries, command, action, checker, key
):
    call_command(command, stdout=StringIO())
    entry = AuditLog.objects.get(action=action)
    assert entry.facility is None
    assert entry.detail["job_run"] == {
        "scope": "installation",
        "status": "success",
        "processed": 2,
        "skipped": 0,
        "failed": 0,
    }
    assert checker()[0].status == ComplianceStatus.OK
    if command == "detect_breaches":
        job_boundaries[2].assert_called_once()


@pytest.mark.parametrize("command,action,checker,key", JOBS)
def test_legacy_marker_does_not_prove_full_success(command, action, checker, key):
    AuditLog.objects.create(action=action, facility=None, detail={})
    assert checker()[0].status == ComplianceStatus.UNKNOWN


@pytest.mark.parametrize(
    "command,options,action",
    [
        (JOBS[0][0], {"dry_run": True}, JOBS[0][1]),
        (JOBS[1][0], {"dry_run": True}, JOBS[1][1]),
        (JOBS[1][0], {"propose": True}, JOBS[1][1]),
    ],
)
def test_preview_does_not_create_success(facility, settings_obj, job_boundaries, command, options, action):
    call_command(command, stdout=StringIO(), **options)
    assert not AuditLog.objects.filter(action=action).exists()


@pytest.mark.parametrize("command,action,checker,key", JOBS)
@pytest.mark.parametrize("invalid", ["facility", "scope", "failed", "skipped"])
def test_partial_evidence_cannot_override_old_global_success(facility, command, action, checker, key, invalid):
    old = _old_success(action)
    evidence = {"scope": "installation", "status": "success", "processed": 1, "skipped": 0, "failed": 0}
    if invalid == "scope":
        evidence["scope"] = "facility"
    elif invalid in {"failed", "skipped"}:
        evidence[invalid] = 1
    AuditLog.objects.create(
        action=action, facility=facility if invalid == "facility" else None, detail={"job_run": evidence}
    )
    assert checker()[0].status == ComplianceStatus.CRITICAL
    assert key in _check_stale_jobs()
    assert AuditLog.objects.get(pk=old.pk).timestamp == old.timestamp
