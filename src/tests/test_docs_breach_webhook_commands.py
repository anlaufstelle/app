"""Webhook installations use the egress-enabled service in copied scan commands."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture
ROOT = Path(__file__).resolve().parents[2]
# The release build strips the development tree's ops directory and the
# release:strip blocks of the runbooks; section 3.4 (dev systemd timers) is one
# of them, so it only exists in the development tree.
_OPS_DIR = "dev-" + "ops"
_DEV_ONLY_SECTIONS = {"3.4"}


def _section(document: str, section: str) -> str:
    text = (ROOT / document).read_text()
    match = re.search(rf"^### {re.escape(section)} .*?(?=^### |\Z)", text, re.M | re.S)
    if match is None and section in _DEV_ONLY_SECTIONS and not (ROOT / _OPS_DIR).is_dir():
        pytest.skip(f"{document} section {section} is stripped from the public tree")
    assert match, (document, section)
    return match.group()


@pytest.mark.parametrize("document", ["docs/ops-runbook.md", "docs/en/ops-runbook.md"])
@pytest.mark.parametrize("section", ["3.3", "3.4", "6.5b"])
def test_scan_entry_points_explain_webhook_service_selection(document, section):
    text = _section(document, section)
    assert "BREACH_NOTIFICATION_WEBHOOK_URL" in text, (document, section)
    assert "docker compose --env-file .env exec -T cron python manage.py detect_breaches" in text
    assert "maintenance" in text
    assert ("kein Ausgang" if document.startswith("docs/ops") else "no egress") in text


@pytest.mark.parametrize("document", ["docs/ops-runbook.md", "docs/en/ops-runbook.md"])
@pytest.mark.parametrize("section", ["3.3", "3.4"])
def test_webhook_scheduler_guidance_matches_the_stack(document, section):
    block = _section(document, section)
    guidance = next(line for line in block.splitlines() if line.startswith("**Webhook"))
    assert "deploy/crontab" in guidance
    assert ("stündlich" if document.startswith("docs/ops") else "hourly") in guidance
    if section == "3.4":
        assert ("keinen `cron`-Sidecar" if document.startswith("docs/ops") else "no `cron` sidecar") in guidance
        assert ("Prod-Stack" if document.startswith("docs/ops") else "production stack") in guidance
        assert "§ 6.5b" in guidance
        assert not re.search(r"Breach-Timer.*weglassen|omit.*breach timer", block, re.I)
    else:
        # A running sidecar already schedules the scan; adding a host job duplicates it.
        assert ("weglassen" if document.startswith("docs/ops") else "omit") in guidance
