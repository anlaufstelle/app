"""The release checklist must describe the release tooling the way the code behaves."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture
ROOT = Path(__file__).resolve().parents[2]
CHECKLIST = ROOT / "docs/dev/release-checklist.md"

if not CHECKLIST.exists():
    pytest.skip("docs/dev/, scripts/dev/ not in the public snapshot", allow_module_level=True)


def _checklist() -> str:
    return CHECKLIST.read_text(encoding="utf-8")


def _fenced_blocks(text: str) -> list[str]:
    return re.findall(r"```[a-z]*\n(.*?)```", text, re.DOTALL)


def test_checklist_documents_the_budget_confirmation_for_runs_without_a_terminal():
    text = _checklist()
    script = (ROOT / "scripts/dev/probe-stage.sh").read_text(encoding="utf-8")
    assert "--budget-confirmed" in script
    assert "--budget-confirmed" in text
    assert "`--yes` deckt sie bewusst nicht ab" in text
    assert "Settings → Billing" in text


def test_checklist_describes_the_approval_line_of_the_list_runner():
    text = _checklist()
    for needle in ("ROLE=listenfahrer", "EXPECT_GID", "FREIGABE <Liste> <Label> <UTC> <Wortlaut>", "--streng"):
        assert needle in text, needle
    assert "vier Stunden" in text
    assert "einmal" in text


def test_checklist_covers_the_actions_policy_steps_of_the_release_run():
    text = _checklist()
    guard = (ROOT / "src/tests/test_actions_policy_guard.py").read_text(encoding="utf-8")
    allowed = re.search(r"ALLOWED_THIRD_PARTY = frozenset\(\s*\{(.*?)\}", guard, re.DOTALL)
    assert allowed, "allow-list not found in the actions policy guard"
    for action in re.findall(r'"([\w./-]+)"', allowed.group(1)):
        assert f"`{action}`" in text, f"allowed third-party action not named: {action}"
    for needle in (
        "sha_pinning_required",
        "ALLOWED_THIRD_PARTY",
        "GH013",
        "repos/anlaufstelle/stage/actions/permissions",
    ):
        assert needle in text, needle


def test_the_actions_policy_fallback_is_prose_and_never_a_command_block():
    text = _checklist()
    assert "allowed_actions=all" in text
    for block in _fenced_blocks(text):
        assert "allowed_actions=all" not in block
        assert "-X PUT" not in block


def test_checklist_names_the_grace_period_of_the_third_party_image_scan():
    text = _checklist()
    scanner = (ROOT / "scripts/scan_images.py").read_text(encoding="utf-8")
    days = re.search(r"^GRACE_DAYS = (\d+)$", scanner, re.MULTILINE)
    assert days, "grace period constant not found"
    assert f"{days.group(1)} Tage" in text
    assert "grace until" in text
    assert "grace n (not blocking)" in text
    assert "Eine Ausnahme gibt es nur für Fremdimages, über einen Eintrag" not in text


def _section(text: str, start: str, end: str) -> str:
    """Text from the heading line starting with ``start`` up to the next heading starting with ``end``."""
    begin = text.index(start)
    return text[begin : text.index(end, begin + len(start))]


def test_ops_gate_reads_the_audit_verify_timer_result_and_never_runs_the_check_itself():
    section = _section(_checklist(), "## 4. Ops-Gate", "### 4.1 ")
    items = [line for line in section.splitlines() if line.startswith("- [ ]") and "audit-verify" in line]
    assert len(items) == 1, items
    item = items[0]
    command = (
        "systemctl show anlaufstelle-audit-verify.service "
        "-p LoadState -p Result -p ExecMainStatus -p ExecMainExitTimestamp"
    )
    assert f"`{command}`" in item
    # a oneshot unit never sets ActiveEnterTimestamp after a successful run: it must not be a criterion
    assert "-p ActiveEnterTimestamp" not in item
    assert "`ActiveEnterTimestamp` zählt nicht" in item
    # target values, fail-closed on a missing unit, an empty or stale timestamp and a disabled timer
    for target in ("`LoadState=loaded`", "`Result=success`", "`ExecMainStatus=0`", "`ExecMainExitTimestamp`"):
        assert target in item, target
    assert "jünger als 26 h" in item
    assert "`systemctl is-enabled anlaufstelle-audit-verify.timer`" in item
    assert "`enabled`" in item
    assert "nicht geprüft" in item
    assert "nicht grün" in item
    assert "nicht von Hand" in item
    # the unit named in the checklist is the one the timer installer creates
    installer_path = ROOT / "dev-ops/deploy/install-timers.sh"
    if not installer_path.exists():
        pytest.skip("dev-ops/deploy/install-timers.sh not in the public snapshot")
    installer = installer_path.read_text(encoding="utf-8")
    assert 'install_timer audit-verify "' in installer
    assert '"$ADMIN_RUN verify_audit_chain"' in installer
    # the read-only command is the only shell command of the line: no run of the management command
    assert "manage.py verify_audit_chain" not in item


def test_list_runner_section_says_the_tool_belongs_to_the_release_management_not_the_repo():
    section = _section(_checklist(), "### 1.7 ", "## 2. ")
    assert "Werkzeug der Wellenverwaltung, nicht Teil des Repos" in section
