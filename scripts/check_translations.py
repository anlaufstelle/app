#!/usr/bin/env python3
"""Refs #813 (C-46): CI-Wachhund fuer fuzzy + leere Uebersetzungen.

Liest die ``msgfmt --statistics``-Ausgabe pro ``django.po`` und faellt
bei Werten oberhalb der gepinnten Baseline. Senken sich die Werte,
muss die Baseline mit angepasst werden — sonst ist die Schwelle wieder
zu hoch und der naechste Drift bleibt unsichtbar.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Baseline aus 2026-05-01: zwei Felder pro Sprache (max_fuzzy, max_untranslated).
# Nur senken, niemals erhoehen — Regression bedeutet unbersetzte UI-Strings
# in Produktion.
#
# Refs #814 (C-47): Werte hochgesetzt nach makemessages-Lauf, der die
# Sprachleitlinie-Umstellung (Klient* -> Person*) verarbeitet hat.
# Anschlussarbeit (Translator-Pass): die neuen msgids in de.po
# fertigstellen + en.po neu uebersetzen.
BASELINES: dict[str, tuple[int, int]] = {
    "src/locale/de/LC_MESSAGES/django.po": (153, 242),
    "src/locale/en/LC_MESSAGES/django.po": (57, 29),
}

TRANSLATED_RE = re.compile(r"(\d+)\s+translated")
FUZZY_RE = re.compile(r"(\d+)\s+fuzzy")
UNTRANSLATED_RE = re.compile(r"(\d+)\s+untranslated")


def _extract(pattern: re.Pattern[str], text: str) -> int:
    m = pattern.search(text)
    return int(m.group(1)) if m else 0


def msgfmt_stats(po: Path) -> tuple[int, int, int]:
    proc = subprocess.run(
        ["msgfmt", "--statistics", str(po), "-o", "/dev/null"],
        capture_output=True,
        text=True,
        check=False,
    )
    out = (proc.stdout + proc.stderr).strip()
    return (
        _extract(TRANSLATED_RE, out),
        _extract(FUZZY_RE, out),
        _extract(UNTRANSLATED_RE, out),
    )


def main() -> int:
    failures: list[str] = []
    for rel, (max_fuzzy, max_untranslated) in BASELINES.items():
        po = ROOT / rel
        if not po.exists():
            failures.append(f"{rel}: missing")
            continue
        translated, fuzzy, untranslated = msgfmt_stats(po)
        print(f"{rel}: {translated} translated, {fuzzy} fuzzy, {untranslated} untranslated")
        if fuzzy > max_fuzzy:
            failures.append(f"{rel}: fuzzy {fuzzy} > baseline {max_fuzzy}")
        if untranslated > max_untranslated:
            failures.append(f"{rel}: untranslated {untranslated} > baseline {max_untranslated}")

    if failures:
        print("\nTranslation regression detected:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        print(
            "\nFix the missing strings or — if the new strings are intentional —"
            " update BASELINES in scripts/check_translations.py.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
