"""Pins for successful installation markers in public cron monitoring.

Source: mutation delta of the release-0.99.8 checkpoint, 2 mutants.
Groups: success status filter (1), unused marker detail preloading (1, not pinned).
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from django.utils.translation import gettext as _

from core.models import AuditLog
from core.services.compliance import ComplianceStatus, cron_job_checks
from tests._job_run_helpers import SUCCESSFUL_JOB_RUN

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "action,key,age",
    [
        (AuditLog.Action.SNAPSHOT_RUN_COMPLETED, "snapshot_last_run", timedelta(days=80)),
        (AuditLog.Action.BREACH_SCAN_COMPLETED, "breach_scan_last_run", timedelta(hours=30)),
    ],
    ids=["snapshot", "breach"],
)
def test_newer_non_success_marker_never_refreshes_the_public_card(action, key, age):
    """Only successful installation runs set the card age: x__latest__mutmut_14."""
    now = timezone.now()
    success = AuditLog.objects.create(
        action=action,
        timestamp=now - age,
        detail={"job_run": SUCCESSFUL_JOB_RUN},
    )
    newer = AuditLog.objects.create(
        action=action,
        timestamp=now,
        detail={"job_run": {**SUCCESSFUL_JOB_RUN, "status": "failed"}},
    )
    assert newer.timestamp > success.timestamp

    with patch("core.services.compliance._clock.now", return_value=now):
        card = next(card for card in cron_job_checks() if card.key == key)

    assert card.status == ComplianceStatus.CRITICAL
    message = "Letzter Lauf: %(timestamp)s" if key == "snapshot_last_run" else "Letzter Scan: %(timestamp)s"
    assert card.detail == _(message) % {"timestamp": f"{success.timestamp:%Y-%m-%d %H:%M}"}
