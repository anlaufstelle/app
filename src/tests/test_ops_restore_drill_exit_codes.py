"""Exit-Codes des Restore-Drills: Lücken sind kein Erfolg (#2316).

Ein Lauf mit übersprungenen Pflichtschritten (Werkskonfiguration ohne
Ankerpfad) endete mit Exit 0 und einer Erfolgszeile — die Alarmregel des
Runbooks (`Exit != 0`) schlug deshalb nie an. Die Fälle nutzen die
Drill-Sandbox aus ``test_ops_backup_hwm``.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

import pytest

from tests.test_ops_backup_hwm import AUDIT_KEY, _restore_sandbox

pytestmark = pytest.mark.architecture

SKIPPED_EXIT_CODE = 2
SUCCESS_LINE = "Drill erfolgreich abgeschlossen"
GAP_LINE = "kein vollstaendiger Nachweis"


def _valid_hwm_payload() -> str:
    signature = hmac.new(AUDIT_KEY.encode(), b"{}", hashlib.sha256).hexdigest()
    return f'{{"version":1,"chains":{{}},"sig":"{signature}"}}'


def test_run_with_skipped_mandatory_step_is_not_reported_as_success(tmp_path: Path) -> None:
    _, _, result = _restore_sandbox(tmp_path, hwm_payload=None, hwm_configured=False)

    assert result.returncode == SKIPPED_EXIT_CODE, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "SKIP - Schritt 2b: Audit-Anker NICHT geprueft" in result.stdout
    assert GAP_LINE in result.stdout
    assert SUCCESS_LINE not in result.stdout


def test_complete_run_still_reports_success_with_exit_zero(tmp_path: Path) -> None:
    _, _, result = _restore_sandbox(tmp_path, hwm_payload=_valid_hwm_payload())

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert SUCCESS_LINE in result.stdout
    assert GAP_LINE not in result.stdout


def test_failed_step_keeps_exit_code_one(tmp_path: Path) -> None:
    _, _, result = _restore_sandbox(tmp_path, hwm_payload='{"version":1,"chains":{},"sig":"manipuliert"}')

    assert result.returncode == 1, f"STDOUT:\n{result.stdout}"
    assert SUCCESS_LINE not in result.stdout


def test_runbook_and_dr_doc_name_the_gap_exit_code() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    runbook = (repo_root / "docs" / "ops-runbook.md").read_text(encoding="utf-8")
    dr_doc = (repo_root / "docs" / "disaster-recovery.md").read_text(encoding="utf-8")

    for text in (runbook, dr_doc):
        assert "Exit-Code 2" in text or "Exit 2" in text
