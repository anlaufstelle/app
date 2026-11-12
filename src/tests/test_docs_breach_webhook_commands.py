"""Webhook installations use the egress-enabled service in copied scan commands."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("document", ["docs/ops-runbook.md", "docs/en/ops-runbook.md"])
@pytest.mark.parametrize("section", ["3.3", "3.4", "6.5b"])
def test_scan_entry_points_explain_webhook_service_selection(document, section):
    text = (ROOT / document).read_text()
    match = re.search(rf"^### {re.escape(section)} .*?(?=^### |\Z)", text, re.M | re.S)
    assert match, (document, section)
    text = match.group()
    assert "BREACH_NOTIFICATION_WEBHOOK_URL" in text, (document, section)
    assert "docker compose --env-file .env exec -T cron python manage.py detect_breaches" in text
    assert "maintenance" in text
    assert ("kein Ausgang" if document.startswith("docs/ops") else "no egress") in text
