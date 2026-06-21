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

import importlib.util
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


def _load_guard_module():
    """Den Guard als Modul laden, um seine Parser-Helfer direkt zu pinnen.

    Liegt unter ``scripts/`` (kein Package); per ``importlib`` aus dem Pfad
    geladen statt importiert."""
    spec = importlib.util.spec_from_file_location("verify_release_test_guard", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _strip_line(path: str, flag: str = "-rf") -> str:
    """Eine kanonische Strip-Zeile ``  rm <flag> "$RELEASE_DIR/<path>"`` bauen.

    Bewusst zur Laufzeit zusammengesetzt mit NEUTRALEN Platzhalter-Pfaden (z.B.
    ``some/strip-target``), nie mit einem real gestrippten Pfad — sonst stünde ein
    Public-ausgeschlossener Prefix (``CLAUDE.md``, ``dev-ops/`` …) als Literal in
    dieser ausgelieferten Test-Datei und der echte Guard-Lauf würde GENAU diese
    Datei melden (Selbst-Trigger, siehe Modul-Docstring). ``$RELEASE_DIR`` wird aus
    Teilstücken gefügt, damit auch das Quoting-/Klammer-Pinning unten ohne reale
    Prefixe auskommt."""
    var = "$" + "RELEASE_DIR"
    return f'  rm {flag}  "{var}/{path}"'


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


# ── Strip-Listen-Parser-Kontrakt (Refs #1189, #1203) ───────────────────────
class TestStripLineParserContract:
    """Pinnt den Kontrakt, den der ``rm``-Block in build-release.sh und der
    Sync-Guard-Parser (``_RM_STRIP_LINE`` / ``parse_stripped_paths_from_build_release``)
    teilen: genau EIN gequoteter Pfad pro Zeile, Form ``rm -rf "$RELEASE_DIR/…"``
    bzw. ``rm -f "$RELEASE_DIR/…"``.

    Der Parser ist bewusst eng (Refs #1189): eine künftige Strip-Zeile, die
    unquoted ist, ``${RELEASE_DIR}``-Klammern nutzt oder zwei Pfade in EINER Zeile
    listet, wäre für den Sync-Check unsichtbar (begrenzter False-Negative). Diese
    Tests dokumentieren genau diese Grenze, damit ein versehentlicher Reformat des
    rm-Blocks NICHT still am Sync-Guard vorbeiläuft — der Kontrakt-Kommentar in
    build-release.sh verweist hierauf."""

    def test_documented_format_is_parsed(self) -> None:
        """Die kanonische Form (``-rf`` und ``-f``) wird als Strip-Pfad geparst."""
        guard = _load_guard_module()
        assert guard._RM_STRIP_LINE.match(_strip_line("some/strip-target", "-rf"))
        assert guard._RM_STRIP_LINE.match(_strip_line("placeholder.md", "-f"))
        match = guard._RM_STRIP_LINE.match(_strip_line("some/nested/target", "-rf"))
        assert match is not None and match.group("path") == "some/nested/target"

    def test_contract_breaking_strip_line_is_not_recognised(self) -> None:
        """Kontraktbruch-Formen matchen NICHT — sie wären für den Sync-Guard
        unsichtbar. Schlägt dieser Test fehl, wurde der Parser erweitert: dann den
        Kontrakt-Kommentar in build-release.sh anpassen (Refs #1189, #1203).

        Pfade sind neutrale Platzhalter (siehe ``_strip_line``); ``$RELEASE_DIR``
        wird gefügt, damit kein Public-Prefix als Literal in dieser Datei steht."""
        guard = _load_guard_module()
        var = "$" + "RELEASE_DIR"
        breakers = {
            "unquoted": f"  rm -rf {var}/some/strip-target",
            "geklammertes ${RELEASE_DIR}": f'  rm -rf "${{{"RELEASE_DIR"}}}/some/strip-target"',
            "zwei Pfade in einer Zeile": f'  rm -rf "{var}/a" "{var}/b"',
        }
        for why, line in breakers.items():
            assert guard._RM_STRIP_LINE.match(line) is None, (
                f"Strip-Zeile mit {why} wird unerwartet geparst — Parser/Kontrakt driften "
                "(siehe Kontrakt-Kommentar am rm-Block in build-release.sh, Refs #1203)."
            )

    def test_parser_picks_up_strip_paths_in_skript_order(self, tmp_path: Path) -> None:
        """``parse_stripped_paths_from_build_release`` liest aus einem Mini-Skript
        genau die gequoteten Pfade in Reihenfolge — und ignoriert die kontrakt-
        brechende (unquoted) Zeile (sie bleibt unsichtbar)."""
        guard = _load_guard_module()
        var = "$" + "RELEASE_DIR"
        fake_build = tmp_path / "build-release.sh"
        fake_build.write_text(
            "#!/usr/bin/env bash\n"
            + _strip_line("some/strip-target", "-rf")
            + "\n"
            + _strip_line("placeholder.md", "-f")
            + "\n"
            + f"  rm -rf {var}/some/unquoted-invisible\n",  # unquoted ⇒ vom Parser ignoriert
            encoding="utf-8",
        )
        paths = guard.parse_stripped_paths_from_build_release(fake_build)
        assert paths == ["some/strip-target", "placeholder.md"]
