"""Backup and restore cards on the public demo (Refs #2414).

The demo holds only synthetic data and regenerates it every hour; it has no
backup timer, and the hourly ``seed --flush`` would delete any restore proof.
The operator hints ("activate backup.sh", "run mark_restore_verified") sent
demo visitors to steps that do not exist there. With ``DEMO_MODE`` both cards
explain that instead — and stay ``unknown``: no evidence, no green.
Without ``DEMO_MODE`` nothing changes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from django.urls import reverse

from core.models import AuditLog
from core.services import compliance
from core.services.audit import audit_event
from core.services.compliance import ComplianceStatus, backup

_FRESH_BACKUP = {"path": "/var/backups/anl/db.sql.gz", "age_hours": 2.0, "mtime": datetime.now(tz=UTC)}
_OPERATOR_STEPS = ("backup.sh", "mark_restore_verified", "BACKUP_DIR")


def _card(checks, key):
    cards = [c for c in checks if c.key == key]
    assert len(cards) == 1
    return cards[0]


def _no_operator_steps(check):
    text = " ".join(filter(None, (check.message, check.detail, check.action_hint)))
    return not any(step in text for step in _OPERATOR_STEPS)


class TestBackupCardInDemoMode:
    def test_demo_backup_card_explains_and_stays_unknown(self, settings):
        settings.DEMO_MODE = True
        with patch("core.services.system.health.last_backup_info", return_value=None):
            card = _card(backup._backup_checks(), "backup_age")

        assert card.status == ComplianceStatus.UNKNOWN
        assert card.message == "In der Demo nicht eingerichtet: die Daten werden stündlich neu erzeugt."
        assert card.action_hint is None
        assert _no_operator_steps(card)

    def test_demo_backup_card_is_never_green(self, settings):
        """Even a file in BACKUP_DIR is no proof on the demo — the data it holds is gone within the hour."""
        settings.DEMO_MODE = True
        with patch("core.services.system.health.last_backup_info", return_value=_FRESH_BACKUP):
            card = _card(backup._backup_checks(), "backup_age")

        assert card.status == ComplianceStatus.UNKNOWN

    def test_without_demo_mode_backup_card_is_unchanged(self, settings):
        settings.DEMO_MODE = False
        with patch("core.services.system.health.last_backup_info", return_value=None):
            card = _card(backup._backup_checks(), "backup_age")

        assert card.status == ComplianceStatus.UNKNOWN
        assert card.message == "Kein Backup gefunden oder BACKUP_DIR nicht konfiguriert."
        assert card.action_hint == "settings.BACKUP_DIR pruefen, Cron-Job 'backup.sh' aktivieren."

    def test_without_demo_mode_fresh_backup_stays_green(self, settings):
        settings.DEMO_MODE = False
        with patch("core.services.system.health.last_backup_info", return_value=_FRESH_BACKUP):
            card = _card(backup._backup_checks(), "backup_age")

        assert card.status == ComplianceStatus.OK


@pytest.mark.django_db
class TestRestoreCardInDemoMode:
    def test_demo_restore_card_explains_and_stays_unknown(self, settings):
        settings.DEMO_MODE = True

        card = _card(backup._restore_checks(), "restore_verified")

        assert card.status == ComplianceStatus.UNKNOWN
        assert card.message == "In der Demo nicht eingerichtet: die Daten werden stündlich neu erzeugt."
        assert card.action_hint is None
        assert _no_operator_steps(card)

    def test_demo_restore_card_ignores_a_marker(self, settings):
        """A restore proof on the demo would be a false statement (Refs #2414)."""
        settings.DEMO_MODE = True
        audit_event(AuditLog.Action.RESTORE_VERIFIED, user=None, facility=None, target_type="RestoreVerification")

        card = _card(backup._restore_checks(), "restore_verified")

        assert card.status == ComplianceStatus.UNKNOWN

    def test_without_demo_mode_restore_card_is_unchanged(self, settings):
        settings.DEMO_MODE = False

        card = _card(backup._restore_checks(), "restore_verified")

        assert card.status == ComplianceStatus.UNKNOWN
        assert card.message == "Noch nie ein Restore-Test dokumentiert."
        assert card.action_hint == "manage.py mark_restore_verified --note '...' nach jedem Test ausfuehren."

    def test_without_demo_mode_marker_turns_restore_card_green(self, settings):
        settings.DEMO_MODE = False
        audit_event(AuditLog.Action.RESTORE_VERIFIED, user=None, facility=None, target_type="RestoreVerification")

        assert _card(backup._restore_checks(), "restore_verified").status == ComplianceStatus.OK


class TestOperationsViewInDemoMode:
    """``/system/`` lifts "no backup" to a warning — not on the demo, where it is by design."""

    def test_demo_backup_stays_unknown_in_operations_view(self, settings):
        settings.DEMO_MODE = True
        with (
            patch("core.services.system.health.last_backup_info", return_value=None),
            patch.object(compliance, "cron_job_checks", lambda: backup._backup_checks()),
        ):
            card = _card(compliance.operations_job_checks(), "backup_age")

        assert card.status == ComplianceStatus.UNKNOWN

    def test_without_demo_mode_operations_view_still_warns(self, settings):
        settings.DEMO_MODE = False
        with (
            patch("core.services.system.health.last_backup_info", return_value=None),
            patch.object(compliance, "cron_job_checks", lambda: backup._backup_checks()),
        ):
            card = _card(compliance.operations_job_checks(), "backup_age")

        assert card.status == ComplianceStatus.WARNING


@pytest.mark.django_db
def test_demo_dashboard_shows_no_operator_steps(client, super_admin_user, settings):
    settings.DEMO_MODE = True
    client.force_login(super_admin_user)

    with (
        patch("core.services.system.health.last_backup_info", return_value=None),
        patch(
            "core.views.system.compliance.aggregate_checks",
            lambda: backup._backup_checks() + backup._restore_checks(),
        ),
    ):
        response = client.get(reverse("core:system_compliance"))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert 'data-testid="compliance-check-backup_age" data-status="unknown"' in content
    assert 'data-testid="compliance-check-restore_verified" data-status="unknown"' in content
    assert "In der Demo nicht eingerichtet" in content
    assert "backup.sh" not in content
    assert "mark_restore_verified" not in content
