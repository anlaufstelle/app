"""Tests fuer ``scripts/verify_test_matrix_drift.py`` (Refs #922 / #923).

Das Script verifiziert, dass alle in der Manual-Test-Matrix referenzierten
Test-Files tatsaechlich in ``src/tests/`` oder ``src/tests/e2e/``
existieren. Refs #1071 Block B: die Matrix ist in Hub + Sektions-Dateien
(``manual-test-matrix-a.md`` … ``-d.md``) gesplittet; das Script scannt
per Default alle. Wenn die Matrix ein File behauptet, das nicht
existiert, soll der Drift-Check den CI-Build mit Exit-Code != 0 stoppen.

Die Tests rufen das Script per ``subprocess.run`` auf, damit das
End-to-End-Verhalten (Argumente, Stderr, Exit-Code) abgedeckt ist.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Meta-Tests, die auf das Repo-Filesystem zugreifen (``docs/`` + ``scripts/``).
# Im Mutmut-Subprozess läuft pytest aus ``mutants/`` und diese Pfade fehlen,
# daher als ``architecture`` markiert und im Mutmut-Run deselektiert (#930).
pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "verify_test_matrix_drift.py"


def _run(matrix_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--matrix", str(matrix_path)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )


def test_real_matrix_has_no_drift() -> None:
    """Smoke gegen die echte Matrix — heute MUSS sie konsistent sein.

    Refs #1071 Block B: ohne ``--matrix`` scannt das Script per Default Hub +
    alle Sektions-Dateien; die TC→Test-Referenzen leben in den Sektionen.
    """
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode == 0, (
        f"Drift-Check schlug auf der echten Matrix fehl:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


def test_missing_file_is_detected(tmp_path: Path) -> None:
    """Eine Matrix mit einer Referenz auf ein nicht-existentes File faillt."""
    fake_matrix = tmp_path / "matrix.md"
    fake_matrix.write_text(
        "# Fake Matrix\n\n"
        "| TC | Titel | E2E |\n"
        "|----|-------|-----|\n"
        "| FAKE-01 | irgendwas | `test_phantom_does_not_exist.py` |\n"
    )
    result = _run(fake_matrix)
    assert result.returncode != 0
    assert "test_phantom_does_not_exist.py" in (result.stdout + result.stderr)


def test_existing_file_in_tests_or_e2e_passes(tmp_path: Path) -> None:
    """Files in src/tests/ ODER src/tests/e2e/ sind beide gueltig."""
    fake_matrix = tmp_path / "matrix.md"
    # test_dashboard.py existiert sowohl in src/tests/ als auch in src/tests/e2e/.
    fake_matrix.write_text(
        "# Fake Matrix\n\n"
        "| TC | Titel | E2E |\n"
        "|----|-------|-----|\n"
        "| FAKE-02 | dashboard-test | `test_dashboard.py` |\n"
    )
    result = _run(fake_matrix)
    assert result.returncode == 0, (
        f"Drift-Check sollte fuer existentes File gruen sein:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


def test_em_dash_placeholder_is_ignored(tmp_path: Path) -> None:
    """Zeilen mit ``—`` als E2E-Platzhalter werden nicht als File-Ref gewertet."""
    fake_matrix = tmp_path / "matrix.md"
    fake_matrix.write_text(
        "# Fake Matrix\n\n| TC | Titel | E2E |\n|----|-------|-----|\n| FAKE-03 | noch nicht automatisiert | — |\n"
    )
    result = _run(fake_matrix)
    assert result.returncode == 0


def test_multiple_files_in_one_cell(tmp_path: Path) -> None:
    """Mehrere kommagetrennte Test-Files in einer Zelle werden alle geprueft."""
    fake_matrix = tmp_path / "matrix.md"
    fake_matrix.write_text(
        "# Fake Matrix\n\n"
        "| TC | Titel | E2E |\n"
        "|----|-------|-----|\n"
        "| FAKE-04 | mehrfach | `test_dashboard.py`, `test_phantom_xyz.py` |\n"
    )
    result = _run(fake_matrix)
    assert result.returncode != 0
    assert "test_phantom_xyz.py" in (result.stdout + result.stderr)


def test_invalid_matrix_path_fails(tmp_path: Path) -> None:
    """Nicht existente Matrix-Datei → Exit != 0 mit sprechender Fehlermeldung."""
    missing = tmp_path / "does-not-exist.md"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--matrix", str(missing)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode != 0
    assert "does-not-exist.md" in (result.stdout + result.stderr)


@pytest.mark.parametrize(
    "filename",
    ["test_workflow_complete.py", "test_offline_apis.py", "test_audit.py"],
)
def test_known_e2e_files_resolve(tmp_path: Path, filename: str) -> None:
    """Stichprobe: bekannte E2E-Files aus der Matrix existieren."""
    fake_matrix = tmp_path / "matrix.md"
    fake_matrix.write_text(f"| TC | Titel | E2E |\n|----|-------|-----|\n| FAKE | x | `{filename}` |\n")
    result = _run(fake_matrix)
    assert result.returncode == 0, f"{filename} sollte aufloesbar sein:\n{result.stderr}"
