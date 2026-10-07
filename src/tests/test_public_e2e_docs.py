"""Public E2E instructions must use the locked resource-owning targets."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture
ROOT = Path(__file__).resolve().parents[2]


def findings(root):
    errors = []
    runbook = (root / "docs/e2e-runbook.md").read_text()
    if "pkill" in runbook:
        errors.append("unowned process termination")
    if "make e2e-server" not in runbook or "CI_LOCK" not in runbook:
        errors.append("missing locked manual server")
    for relative in ["docs/screenshots.md", "docs/screenshots.en.md"]:
        text = (root / relative).read_text()
        if "make seed --scale" in text:
            errors.append(f"{relative}: invalid make option")
        if "make docs-screens" not in text or "seed --flush" not in text or "8844" not in text:
            errors.append(f"{relative}: missing screenshot prerequisites")
    return errors


def test_public_e2e_instructions_use_safe_targets():
    assert findings(ROOT) == []


def test_rejects_unlocked_and_invalid_examples(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/e2e-runbook.md").write_text("pkill -f 'gunicorn.*8844'\n")
    for relative in ["docs/screenshots.md", "docs/screenshots.en.md"]:
        (tmp_path / relative).write_text("make seed --scale=medium\n")
    errors = findings(tmp_path)
    assert "unowned process termination" in errors
    assert sum("invalid make option" in error for error in errors) == 2
