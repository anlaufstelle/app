"""Architecture-Guards — Release-Test-Guard (Refs #1137).

Verifiziert ``scripts/verify_release_test_guard.py``: kein ausgelieferter Test
(``src/tests/``) darf hart auf Pfade verweisen, die der Public-/Stage-Release-
Snapshot strippt (``dev-ops/``, ``scripts/dev/``, ``docs/ai/``, ``CLAUDE.md`` …).
Solche Tests laufen im Dev-Tree grün und fallen erst auf der public Stage-CI mit
``FileNotFoundError`` um (#1051/#1047-Fehlklasse).

Die Tests rufen den Guard per ``subprocess.run`` auf (End-to-End: Argumente,
Stderr, Exit-Code) und nutzen ``--tests-dir`` auf ein Tempverzeichnis, damit der
Negativ-Fall isoliert vom echten ``src/tests/``-Baum ist.

WICHTIG (Selbst-Trigger-Schutz): Dieser Test darf den Guard nicht selbst rot
machen, wenn der echte ``make ci``-Lauf ``src/tests/`` scannt. Der einzige
ausgeschlossene Pfad, den wir als *Beispiel* in eine Tempdatei schreiben, wird
darum hier dynamisch zusammengesetzt (siehe ``_excluded_example_path``) — als
einzelnes String-Literal taucht ``dev-ops/`` in dieser Datei NICHT auf, sonst
würde der Guard genau diese Datei melden. Code-String-Literale, die in den Guard-
Scan einfließen, vermeiden den verbotenen Substring (Kommentare/Docstrings wie
dieser zählen nicht).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Meta-Test, der das Repo-Filesystem + ein Script anfasst — wie die übrigen
# architecture-Guards (im Mutmut-Subprozess deselektiert, #930).
pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "verify_release_test_guard.py"
LEAK_SCRIPT = REPO_ROOT / "dev-ops" / "release" / "verify-leak.sh"


def _excluded_example_path() -> str:
    """Ein vom Release gestrippter Pfad — dynamisch gebaut, damit der String
    NICHT als Literal in dieser Test-Datei steht (sonst Selbst-Trigger des
    echten Guard-Laufs über ``src/tests/``)."""
    return "/".join(["dev-" + "ops", "deploy", "some-admin-helper.sh"])


def _skip_reason_prefix() -> str:
    """Der Prefix, den ein gültiger Skip-Reason nennen muss — ebenfalls
    dynamisch (siehe ``_excluded_example_path``)."""
    return "dev-" + "ops/"


def _run(tests_dir: Path, leak_script: Path = LEAK_SCRIPT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--tests-dir",
            str(tests_dir),
            "--leak-script",
            str(leak_script),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )


def test_real_tests_tree_passes() -> None:
    """Smoke gegen den echten ``src/tests/``-Baum — heute MUSS er grün sein."""
    if not LEAK_SCRIPT.exists():
        pytest.skip("dev-ops/ nicht im Public-Snapshot — Guard-Quelle fehlt (Refs #1137)")
    result = _run(REPO_ROOT / "src" / "tests")
    assert result.returncode == 0, (
        f"Release-Test-Guard schlug auf dem echten Baum fehl:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


def test_ungated_excluded_reference_is_detected(tmp_path: Path) -> None:
    """Eine ungegatete Referenz auf einen gestrippten Pfad macht den Guard ROT."""
    if not LEAK_SCRIPT.exists():
        pytest.skip("dev-ops/ nicht im Public-Snapshot — Guard-Quelle fehlt (Refs #1137)")
    excluded = _excluded_example_path()
    offending = tmp_path / "test_offender.py"
    # Realer Fehlfall: Test liest eine vom Release gestrippte Datei OHNE Skip-Gate.
    offending.write_text(
        f"from pathlib import Path\n\n\ndef test_reads_stripped_file():\n    assert Path({excluded!r}).read_text()\n",
        encoding="utf-8",
    )
    result = _run(tmp_path)
    assert result.returncode == 1, (
        f"Guard hätte die ungegatete Referenz melden müssen:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert excluded.split("/")[0] in (result.stdout + result.stderr)


def test_skip_gated_excluded_reference_passes(tmp_path: Path) -> None:
    """Dieselbe Referenz MIT passendem Skip-Gate (Reason nennt den Prefix) ist OK."""
    if not LEAK_SCRIPT.exists():
        pytest.skip("dev-ops/ nicht im Public-Snapshot — Guard-Quelle fehlt (Refs #1137)")
    excluded = _excluded_example_path()
    reason_prefix = _skip_reason_prefix()
    gated = tmp_path / "test_gated.py"
    # Muster aus #1047: .exists()-Guard + pytest.skip, dessen Reason den Prefix nennt.
    gated.write_text(
        "from pathlib import Path\n\nimport pytest\n\n\ndef test_reads_stripped_file():\n"
        f"    p = Path({excluded!r})\n"
        "    if not p.exists():\n"
        f"        pytest.skip({reason_prefix + ' nicht im Public-Snapshot'!r})\n"
        "    assert p.read_text()\n",
        encoding="utf-8",
    )
    result = _run(tmp_path)
    assert result.returncode == 0, (
        f"Skip-gegateter Test sollte grün sein:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


def test_excluded_path_only_in_comment_passes(tmp_path: Path) -> None:
    """Eine bloße Erwähnung in einem Kommentar löst kein FileNotFoundError aus
    und darf den Guard nicht triggern."""
    if not LEAK_SCRIPT.exists():
        pytest.skip("dev-ops/ nicht im Public-Snapshot — Guard-Quelle fehlt (Refs #1137)")
    excluded = _excluded_example_path()
    benign = tmp_path / "test_comment_only.py"
    benign.write_text(
        f"# Hinweis: das Pendant liegt unter {excluded} (dev-only)\n\n\ndef test_noop():\n    assert True\n",
        encoding="utf-8",
    )
    result = _run(tmp_path)
    assert result.returncode == 0, (
        f"Kommentar-Erwähnung sollte den Guard nicht triggern:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


def test_excluded_path_only_in_docstring_passes(tmp_path: Path) -> None:
    """Wie der reale Fall test_authz_audit.py: ein gestrippter Pfad nur im
    Modul-Docstring (Write-Target/Zitat) triggert den Guard nicht."""
    if not LEAK_SCRIPT.exists():
        pytest.skip("dev-ops/ nicht im Public-Snapshot — Guard-Quelle fehlt (Refs #1137)")
    excluded = _excluded_example_path()
    benign = tmp_path / "test_docstring_only.py"
    benign.write_text(
        f'"""Schreibt den Report nach {excluded}."""\n\n\ndef test_noop():\n    assert True\n',
        encoding="utf-8",
    )
    result = _run(tmp_path)
    assert result.returncode == 0, (
        f"Docstring-Erwähnung sollte den Guard nicht triggern:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


def test_excludes_are_single_sourced_from_verify_leak(tmp_path: Path) -> None:
    """Single Source: fehlt die Exclude-Quelle (verify-leak.sh), ist der Guard
    moot und endet grün — er darf die (public) Stage-CI nicht selbst rot machen.
    Belegt zugleich, dass die Liste NICHT im Guard dupliziert ist."""
    excluded = _excluded_example_path()
    offending = tmp_path / "test_offender.py"
    offending.write_text(
        f"from pathlib import Path\n\n\ndef test_reads_stripped_file():\n    assert Path({excluded!r}).read_text()\n",
        encoding="utf-8",
    )
    missing_leak = tmp_path / "no-verify-leak.sh"  # existiert bewusst nicht
    result = _run(tmp_path, leak_script=missing_leak)
    assert result.returncode == 0, (
        f"Ohne Exclude-Quelle muss der Guard grün/moot sein:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
