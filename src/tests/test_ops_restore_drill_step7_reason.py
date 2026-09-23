"""Operator guidance when the restore-drill compliance marker fails."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

RESTORE_DRILL = Path(__file__).resolve().parents[2] / "scripts" / "ops" / "restore-drill.sh"


def test_failed_compliance_marker_names_likely_container_cause(tmp_path: Path) -> None:
    script = RESTORE_DRILL.read_text(encoding="utf-8")
    marker = "# Schritt 7: Bei vollem Erfolg den Restore-Test im AuditLog dokumentieren.\n"
    step_seven = script.split(marker, maxsplit=1)[1].split("if [[ ${#DRILL_SKIPPED[@]} -gt 0 ]]", maxsplit=1)[0]
    harness = tmp_path / "step-seven.sh"
    harness.write_text(
        """#!/usr/bin/env bash
set -uo pipefail
DRILL_FAILED=0
DRILL_UNDETERMINED=0
DRILL_SKIPPED=()
LATEST_DB=/backup/anlaufstelle_2026-09-23_120000.sql.gz.enc
DRILL_DB=anlaufstelle_restore_drill
dc() {
    [[ "$*" == "exec -T web python manage.py mark_restore_verified --note "* ]] || return 99
    return 1
}
ok() { echo "OK   - $*"; }
fail() { echo "FAIL - $*"; DRILL_FAILED=1; }
"""
        + step_seven,
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(harness)],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "Schritt 7: mark_restore_verified fehlgeschlagen" in result.stdout
    assert "web-Container nicht laeuft oder nicht healthy ist" in result.stdout
    assert "siehe Runbook" in result.stdout
