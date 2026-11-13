"""Delta STAT pins for the public operations job summary."""

from datetime import UTC, datetime

import pytest

from core.services import compliance
from core.services.compliance import ComplianceStatus

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 6, 15, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    """Keep all job age calculations independent of the execution date."""
    monkeypatch.setattr("core.services.compliance._clock.now", lambda: NOW)


def test_demo_missing_backup_stays_unknown(settings):
    """Delta STAT: x_operations_job_checks__mutmut_10 reads the wrong setting."""
    settings.DEMO_MODE = True
    settings.BACKUP_DIR = ""

    checks = {check.key: check for check in compliance.operations_job_checks()}

    assert checks["backup_age"].status == ComplianceStatus.UNKNOWN


def test_only_missing_backup_is_escalated_in_operations(settings):
    """Delta STAT: x_operations_job_checks__mutmut_20 reverses the backup match."""
    settings.DEMO_MODE = False
    settings.BACKUP_DIR = ""

    statuses = {check.key: check.status for check in compliance.operations_job_checks()}

    assert statuses == {
        "backup_age": ComplianceStatus.WARNING,
        "retention_last_run": ComplianceStatus.UNKNOWN,
        "audit_chain_last_run": ComplianceStatus.UNKNOWN,
        "snapshot_last_run": ComplianceStatus.UNKNOWN,
        "breach_scan_last_run": ComplianceStatus.UNKNOWN,
    }
