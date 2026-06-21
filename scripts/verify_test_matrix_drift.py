#!/usr/bin/env python3
"""Verify that every test-file referenced in ``manual-test-matrix.md`` exists.

Die Manual-Test-Matrix ist die Single-Source-of-Truth für unsere
TC → E2E/Unit-Test-Zuordnung. Refs #1071 Block B: sie ist in einen Hub
(``docs/testing/manual-test-matrix.md``) und vier Sektions-Dateien
(``manual-test-matrix-a.md`` … ``-d.md``) gesplittet; die TC→Test-
Referenzen leben in den Sektions-Dateien. Wenn ein dort behauptetes File
nicht existiert (Refactor, Rename, Drift), gibt dieses Script einen
Exit-Code != 0 und listet die Lücken auf — gedacht als CI-Step vor
``pytest``.

Stdlib-only. Aufruf::

    python scripts/verify_test_matrix_drift.py              # Hub + alle Sektionen
    python scripts/verify_test_matrix_drift.py --matrix a.md b.md

Refs #922, #923.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTING_DIR = ROOT / "docs" / "testing"
# Refs #1071 Block B: Die Matrix ist in Hub + Sektions-Dateien gesplittet.
# Die TC→Test-Referenzen leben in den Sektions-Dateien; der Hub trägt nur
# Front-matter + Anhänge. Standardmäßig alle scannen, damit das CI-Gate
# weiterhin sämtliche referenzierten Test-Files prüft.
DEFAULT_MATRIX = (
    TESTING_DIR / "manual-test-matrix.md",
    TESTING_DIR / "manual-test-matrix-a.md",
    TESTING_DIR / "manual-test-matrix-b.md",
    TESTING_DIR / "manual-test-matrix-c.md",
    TESTING_DIR / "manual-test-matrix-d.md",
)
SEARCH_DIRS = (ROOT / "src" / "tests", ROOT / "src" / "tests" / "e2e")

# `test_<name>.py` in Backticks. Keine Slashes — die Matrix referenziert nur
# den Basename, ohne Verzeichnis. Mehrfach pro Zelle, komma-getrennt.
TEST_REF = re.compile(r"`(test_[a-z0-9_]+\.py)`")


def extract_refs(text: str) -> set[str]:
    """Alle ``test_*.py``-Basenames aus dem Markdown-Text."""
    return set(TEST_REF.findall(text))


def find_missing(refs: set[str], search_dirs: tuple[Path, ...] = SEARCH_DIRS) -> set[str]:
    """Basenames aus ``refs``, die in keinem der ``search_dirs`` existieren."""
    existing: set[str] = set()
    for directory in search_dirs:
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.is_file() and path.suffix == ".py":
                existing.add(path.name)
    return refs - existing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--matrix",
        type=Path,
        nargs="+",
        default=list(DEFAULT_MATRIX),
        help=("Pfad(e) zur Manual-Test-Matrix (default: Hub + Sektions-Dateien manual-test-matrix*.md)"),
    )
    args = parser.parse_args(argv)

    matrix_paths: list[Path] = args.matrix
    refs: set[str] = set()
    for matrix_path in matrix_paths:
        if not matrix_path.is_file():
            print(f"FEHLER: Matrix-Datei nicht gefunden: {matrix_path}", file=sys.stderr)
            return 2
        refs |= extract_refs(matrix_path.read_text(encoding="utf-8"))

    if not refs:
        joined = ", ".join(str(p) for p in matrix_paths)
        print(f"WARNUNG: Keine `test_*.py`-Referenzen in {joined} gefunden.", file=sys.stderr)
        return 0

    missing = find_missing(refs)
    if missing:
        print(
            f"FEHLER: Die Matrix verweist auf {len(missing)} nicht-existente Test-Files:",
            file=sys.stderr,
        )
        for name in sorted(missing):
            print(f"  - {name}", file=sys.stderr)
        print(
            "\nTipp: die Sektions-Dateien 'docs/testing/manual-test-matrix-*.md' aktualisieren "
            "oder die fehlenden Files in 'src/tests/' bzw. 'src/tests/e2e/' anlegen.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {len(refs)} Test-File-Referenzen, alle existieren.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
