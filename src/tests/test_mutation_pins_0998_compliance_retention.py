"""Pins for the retention monitoring marker's success requirement.

Source: mutation delta of the release-0.99.8 checkpoint, 1 mutant.
Groups: a newer non-success marker cannot refresh the public card (1).
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


def test_newer_non_success_retention_marker_does_not_hide_an_outage(settings):
    """The public retention card ages the last success: x__retention_checks__mutmut_25."""
    settings.DEMO_MODE = False
    now = timezone.now()
    success = AuditLog.objects.create(
        action=AuditLog.Action.RETENTION_RUN_COMPLETED,
        timestamp=now - timedelta(days=5),
        detail={"job_run": SUCCESSFUL_JOB_RUN},
    )
    newer = AuditLog.objects.create(
        action=AuditLog.Action.RETENTION_RUN_COMPLETED,
        timestamp=now,
        detail={"job_run": {**SUCCESSFUL_JOB_RUN, "status": "failed"}},
    )
    assert newer.timestamp > success.timestamp

    with patch("core.services.compliance._clock.now", return_value=now):
        card = next(card for card in cron_job_checks() if card.key == "retention_last_run")

    assert card.status == ComplianceStatus.CRITICAL
    assert card.detail == _("Letzter Lauf: %(timestamp)s") % {"timestamp": f"{success.timestamp:%Y-%m-%d %H:%M}"}
