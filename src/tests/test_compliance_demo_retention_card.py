"""Retention card on the public demo (Refs #2413).

The demo reset never runs ``enforce_retention``: on a fresh ``--scale=demo``
seed it would soft-delete the seeded anonymous events beyond the 90-day
period and change the demo areas every hour. The card therefore can never
show a real run there. With ``DEMO_MODE`` it explains that instead of pointing
visitors at the cron job and the runbook — and stays ``unknown``: no real run,
no green. Without ``DEMO_MODE`` nothing changes.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from core.models import AuditLog
from core.services import compliance
from core.services.audit import audit_event
from core.services.compliance import ComplianceStatus, retention
from core.services.compliance._types import ComplianceCategory
from tests._job_run_helpers import SUCCESSFUL_JOB_RUN

_DEMO_TEXT = "In der Demo nicht eingerichtet: der Löschlauf würde die stündlich neu erzeugten Beispieldaten löschen."
_OPERATOR_STEPS = ("enforce_retention", "Cron", "Runbook", "crontab")


def _card(checks, key):
    cards = [c for c in checks if c.key == key]
    assert len(cards) == 1
    return cards[0]


def _no_operator_steps(check):
    text = " ".join(filter(None, (check.message, check.detail, check.action_hint)))
    return not any(step in text for step in _OPERATOR_STEPS)


def _record_retention_run():
    audit_event(
        AuditLog.Action.RETENTION_RUN_COMPLETED,
        user=None,
        facility=None,
        target_type="RetentionRun",
        detail={"job_run": SUCCESSFUL_JOB_RUN},
    )


@pytest.mark.django_db
class TestRetentionCardInDemoMode:
    def test_demo_retention_card_explains_and_stays_unknown(self, settings):
        settings.DEMO_MODE = True

        card = _card(retention._retention_checks(), "retention_last_run")

        assert card.status == ComplianceStatus.UNKNOWN
        assert card.message == _DEMO_TEXT
        assert card.detail is None
        assert card.action_hint is None
        assert card.label == "Retention-Lauf"
        assert card.category == ComplianceCategory.RETENTION
        assert _no_operator_steps(card)

    def test_demo_retention_card_ignores_a_run_marker(self, settings):
        """The demo reset does not run the job; a marker there is no proof for the visitor."""
        settings.DEMO_MODE = True
        _record_retention_run()

        card = _card(retention._retention_checks(), "retention_last_run")

        assert card.status == ComplianceStatus.UNKNOWN
        assert card.message == _DEMO_TEXT

    def test_demo_retention_card_stays_unknown_in_operations_view(self, settings):
        settings.DEMO_MODE = True

        card = _card(compliance.operations_job_checks(), "retention_last_run")

        assert card.status == ComplianceStatus.UNKNOWN
        assert card.message == _DEMO_TEXT


@pytest.mark.django_db
class TestRetentionCardWithoutDemoMode:
    def test_missing_run_keeps_the_operator_hint(self, settings):
        settings.DEMO_MODE = False

        card = _card(retention._retention_checks(), "retention_last_run")

        assert card.status == ComplianceStatus.UNKNOWN
        assert card.message == "Noch kein RETENTION_RUN_COMPLETED-Eintrag — Cron lief vielleicht nie."
        assert card.action_hint == "enforce_retention-Cron prüfen (Ops-Runbook §6)."

    def test_fresh_run_turns_the_card_green(self, settings):
        settings.DEMO_MODE = False
        _record_retention_run()

        assert _card(retention._retention_checks(), "retention_last_run").status == ComplianceStatus.OK


@pytest.mark.django_db
def test_demo_dashboard_shows_the_retention_demo_text(client, super_admin_user, settings):
    settings.DEMO_MODE = True
    client.force_login(super_admin_user)

    response = client.get(reverse("core:system_compliance"))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert 'data-testid="compliance-check-retention_last_run" data-status="unknown"' in content
    assert _DEMO_TEXT in content
    assert "enforce_retention-Cron" not in content
