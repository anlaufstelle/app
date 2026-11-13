"""Delta STAT pins for the published failed breach-scan check."""

from datetime import UTC, datetime, timedelta

import pytest
from django.utils.translation import gettext as _

from core.models import AuditLog
from core.services.compliance import ComplianceCheck, ComplianceStatus, cron_job_checks
from core.services.compliance._types import ComplianceCategory
from tests._job_run_helpers import SUCCESSFUL_JOB_RUN

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 6, 15, 12, tzinfo=UTC)


def test_failed_full_scan_publishes_all_diagnostic_fields(monkeypatch):
    """Delta STAT: preserve the complete failed-scan check.

    x__breach_scan_checks__mutmut_38 removes its label.
    x__breach_scan_checks__mutmut_39 removes its category.
    x__breach_scan_checks__mutmut_43 and x__breach_scan_checks__mutmut_50
    remove the operator action hint.
    """
    monkeypatch.setattr("core.services.compliance._clock.now", lambda: NOW)
    scanned_at = NOW - timedelta(hours=1)
    AuditLog.objects.create(
        action=AuditLog.Action.BREACH_SCAN_COMPLETED,
        timestamp=NOW - timedelta(hours=2),
        detail={"job_run": SUCCESSFUL_JOB_RUN},
    )
    AuditLog.objects.create(
        action=AuditLog.Action.BREACH_SCAN_COMPLETED,
        timestamp=scanned_at,
        detail={"job_run": {**SUCCESSFUL_JOB_RUN, "status": "failed", "failed": 1}},
    )

    checks = [check for check in cron_job_checks() if check.key == "breach_scan_last_run"]

    assert checks == [
        ComplianceCheck(
            key="breach_scan_last_run",
            label=_("Breach-Detection-Scan"),
            category=ComplianceCategory.BACKGROUND_JOBS,
            status=ComplianceStatus.CRITICAL,
            message=_("Letzter vollständiger Breach-Scan fehlgeschlagen — cron-Logs und Zustellzeilen prüfen."),
            detail=_("Letzter Scan: %(timestamp)s") % {"timestamp": "2026-06-15 11:00"},
            action_hint=_("docker compose -f docker-compose.prod.yml logs cron prüfen (Ops-Runbook § 3.2)."),
        )
    ]
