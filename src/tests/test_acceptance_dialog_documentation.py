"""Keep dialog acceptance expectations aligned with the implemented UI."""

from pathlib import Path

import pytest


@pytest.mark.architecture
def test_inline_dialog_acceptance_requires_a_new_focus_check():
    root = Path(__file__).resolve().parents[2]
    source = (root / "docs/testing/abnahme-0.98.md").read_text()
    row = next(line for line in source.splitlines() if line.startswith("| A-A11Y-015 |"))
    assert "#2552" in row
    assert "Tab und Shift+Tab" in row
    assert "Escape" in row
    assert "| O |" in row
    assert "Ausnahme A-03" not in row
