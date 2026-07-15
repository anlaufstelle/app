"""Tests fuer den source-hash-Abgleich in ``scripts/check_translation_versions.py``
(Refs #1551, #1552).

Der Guard prueft zusaetzlich zum ``translation-version``-Header, dass der im
EN-Dokument gestempelte ``source-hash``-Marker mit ``git hash-object`` der
DE-Quelle uebereinstimmt. Weicht die Quelle seit dem letzten EN-Sync ab, failt
der Guard **hart** (Exit != 0) — konsistent zum bestehenden
``MAX_MINOR_BEHIND=0``-Gate.

Die Tests rufen das Script per ``subprocess.run`` gegen ein **manipuliertes
Repo-Abbild** in ``tmp_path`` auf. Das Abbild enthaelt fuer JEDEN Eintrag der
(aus dem echten Script importierten) ``TRANSLATED_FILES``-Liste ein gueltiges
EN-Dokument, das auf eine gemeinsame synthetische DE-Quelle zeigt — nur die
Datei unter Test wird pro Szenario verbogen. So triggern die Tests NIE den
echten Baum-Guard und sind unabhaengig davon, ob die realen EN-Stempel schon
gesetzt sind.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

# Meta-Test auf Repo-Filesystem (Script-Kopie + git hash-object). Wie die
# uebrigen scripts/-Guard-Tests markiert (Mutmut-Subprozess-Pfade fehlen, #930).
pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_translation_versions.py"

# Der ``architecture``-Marker deselektiert diesen Meta-Test im Mutmut-Run erst
# NACH dem Sammeln — das Laden des Scripts unten laeuft aber schon beim Import.
# Die mutants/-Kopie enthaelt kein ``scripts/``, der ImportError brach dort das
# Test-Listing und damit den kompletten Mutation-Run ab ("Failed to collect list
# of tests" -> alle Mutanten "not checked"). Refs #930.
if not SCRIPT.exists():  # pragma: no cover - nur im mutants/-Tree
    pytest.skip(
        "scripts/check_translation_versions.py nicht erreichbar — Meta-Test ist nur im echten Repo-Tree sinnvoll",
        allow_module_level=True,
    )

PYPROJECT = '[project]\nname = "x"\nversion = "0.20.0"\n'
DE_SOURCE = "# Quelle\n\nHallo Welt.\n"
UNDER_TEST = "README.en.md"


def _load_translated_files() -> list[str]:
    spec = importlib.util.spec_from_file_location("_cvt", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return list(mod.TRANSLATED_FILES)


TRANSLATED_FILES = _load_translated_files()


def _git_hash(path: Path) -> str:
    out = subprocess.run(
        ["git", "hash-object", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _en_doc(source_hash: str | None, *, include_hash_marker: bool = True) -> str:
    marker = ""
    if include_hash_marker and source_hash is not None:
        marker = f"<!-- source-hash: {source_hash} -->\n"
    return (
        "# Source\n\nHello world.\n\n"
        "<!-- translation-source: source.md -->\n"
        "<!-- translation-version: v0.20.0 -->\n"
        "<!-- translation-date: 2026-07-14 -->\n"
        f"{marker}"
    )


def _mirror(
    dest: Path,
    *,
    de_source: str = DE_SOURCE,
    source_hash: str | None,
    include_hash_marker: bool = True,
) -> Path:
    """Voll-Abbild: pyproject.toml, Script-Kopie, eine gemeinsame DE-Quelle
    ``source.md`` und fuer jeden TRANSLATED_FILES-Eintrag ein EN-Dokument. Alle
    zeigen auf ``source.md``; alle bis auf ``UNDER_TEST`` bekommen einen
    gueltigen Stempel, damit nur die Datei unter Test das Szenario bestimmt.
    """
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "scripts").mkdir(exist_ok=True)
    (dest / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (dest / "scripts" / "check_translation_versions.py").write_text(
        SCRIPT.read_text(encoding="utf-8"), encoding="utf-8"
    )
    # DE-Quelle: fuer die Datei unter Test evtl. abweichender Inhalt (Drift-Test).
    (dest / "source.md").write_text(de_source, encoding="utf-8")
    valid_hash = _git_hash(dest / "source.md")

    for rel in TRANSLATED_FILES:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if rel == UNDER_TEST:
            content = _en_doc(source_hash, include_hash_marker=include_hash_marker)
        else:
            content = _en_doc(valid_hash)
        target.write_text(content, encoding="utf-8")
    return dest


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "check_translation_versions.py")],
        capture_output=True,
        text=True,
        cwd=root,
        check=False,
    )


def test_matching_source_hash_is_green(tmp_path: Path) -> None:
    """Stempel == git hash-object der DE-Quelle -> Exit 0."""
    root = tmp_path / "repo"
    src_hash = _stable_hash(tmp_path, DE_SOURCE)
    _mirror(root, source_hash=src_hash)
    result = _run(root)
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"


def test_short_prefix_hash_is_accepted(tmp_path: Path) -> None:
    """Ein gekuerzter (>=7) Praefix des Voll-Hashes wird toleriert."""
    root = tmp_path / "repo"
    src_hash = _stable_hash(tmp_path, DE_SOURCE)
    _mirror(root, source_hash=src_hash[:8])
    result = _run(root)
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"


def test_changed_source_fails_with_both_hashes(tmp_path: Path) -> None:
    """DE-Quelle nach dem Stempeln geaendert -> Drift -> Exit 1, beide Hashes
    und die Klartext-Fehlermeldung in der Ausgabe."""
    root = tmp_path / "repo"
    stale = _stable_hash(tmp_path, DE_SOURCE)
    changed = "# Quelle\n\nGeaenderter Text.\n"
    _mirror(root, de_source=changed, source_hash=stale)
    result = _run(root)
    combined = result.stdout + result.stderr
    assert result.returncode == 1, combined
    new_hash = _git_hash(root / "source.md")
    assert "DE-Quelle" in combined and "EN-Sync" in combined
    assert stale in combined
    assert new_hash in combined


def test_missing_source_hash_marker_fails(tmp_path: Path) -> None:
    """Fehlender source-hash-Marker -> Exit 1."""
    root = tmp_path / "repo"
    _mirror(root, source_hash=None, include_hash_marker=False)
    result = _run(root)
    combined = result.stdout + result.stderr
    assert result.returncode == 1, combined
    assert "source-hash" in combined


def test_too_short_hash_marker_fails(tmp_path: Path) -> None:
    """Weniger als 7 Hex-Zeichen -> als ungueltig abgewiesen (Exit 1)."""
    root = tmp_path / "repo"
    src_hash = _stable_hash(tmp_path, DE_SOURCE)
    _mirror(root, source_hash=src_hash[:5])
    result = _run(root)
    assert result.returncode == 1, result.stdout + result.stderr


def _stable_hash(base: Path, content: str) -> str:
    """git hash-object eines Inhalts (ueber eine Wegwerf-Datei)."""
    p = base / "_hash_probe.md"
    p.write_text(content, encoding="utf-8")
    return _git_hash(p)


if __name__ == "__main__":
    import tempfile

    failures: list[str] = []

    def _check(name: str, cond: bool) -> None:
        print(f"  [{'OK  ' if cond else 'FAIL'}] {name}")
        if not cond:
            failures.append(name)

    print("Selbsttest source-hash-Guard (ohne pytest):")
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        _mirror(base / "match", source_hash=_stable_hash(base, DE_SOURCE))
        _check("match gruen", _run(base / "match").returncode == 0)

        _mirror(
            base / "drift",
            de_source="# Quelle\n\nGeaendert.\n",
            source_hash=_stable_hash(base, DE_SOURCE),
        )
        _check("drift Exit 1", _run(base / "drift").returncode == 1)

        _mirror(base / "nomarker", source_hash=None, include_hash_marker=False)
        _check("fehlender Marker Exit 1", _run(base / "nomarker").returncode == 1)

    if failures:
        print(f"\n{len(failures)} Selbsttest(s) fehlgeschlagen: {failures}")
        sys.exit(1)
    print("\nAlle Selbsttests gruen.")
