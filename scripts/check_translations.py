#!/usr/bin/env python3
"""Refs #813 (C-46): CI-Wachhund fuer fuzzy + leere Uebersetzungen.

Liest die ``msgfmt --statistics``-Ausgabe pro ``django.po`` und faellt
bei Werten oberhalb der gepinnten Baseline. Senken sich die Werte,
muss die Baseline mit angepasst werden — sonst ist die Schwelle wieder
zu hoch und der naechste Drift bleibt unsichtbar.

Refs #1348: Zusaetzlich ein Drift-Guard gegen falsch uebernommene
``msgstr``-Werte in der de-po (z. B. per msgmerge-Fuzzy-Matching oder
Copy-Paste): zwei inhaltlich verschiedene ``msgid``s, die auf dieselbe
``msgstr`` zeigen ("Keine Dateien gefunden" UND "Keine gueltigen
Aufgaben gefunden." beide als "Keine Klienten gefunden"). Nur AKTIVE
Eintraege (nicht fuzzy, nicht obsolete) zaehlen — fuzzy-Eintraege
werden von msgfmt beim Kompilieren ohnehin verworfen (kein Live-Bug,
s. test_i18n_catalog.py), obsolete (``#~``) sind totes Katalog-Rauschen.

Bewusst NUR fuer de.po: DE ist die kanonische Quellsprache (ADR-027),
dort ist msgid bereits deutscher Fliesstext und eine korrekte msgstr
faellt entweder mit der msgid zusammen (Identitaets-Uebersetzung) oder
weicht bewusst ab — zwei verschiedene msgids duerfen praktisch nie auf
dieselbe msgstr fallen. In en.po ist eine msgstr-Kollision dagegen der
Normalfall: viele unterschiedliche deutsche Quell-Strings ("Ereignis"
und "Event", "Erstellt von" und "Erstellt von:") uebersetzen korrekt
auf dasselbe englische Wort — dort waere dieser Guard nur Rauschen.
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
#
# 2026-05-12 (v0.12.0): 5-Rollen-Modell + /system/-Bereich (Tier 1+2:
# Health, Lockouts, AuditLog-Export, Maintenance, Retention, VVT,
# Legal-Holds) brachten neue UI-Strings. Der i18n-Sweep #878 hat
# Tier 1/2 abgedeckt, die Restluecke kommt von Form-Labels, Help-
# Texts und Audit-Action-Bezeichnern. Translator-Pass ist Pre-
# Release-Anschlussarbeit.
BASELINES: dict[str, tuple[int, int]] = {
    "src/locale/de/LC_MESSAGES/django.po": (208, 406),
    "src/locale/en/LC_MESSAGES/django.po": (120, 71),
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


# --- Duplicate-msgstr-Drift-Guard (Refs #1348) --------------------------
#
# Nur die de-po wird geprueft (s. Modul-Docstring: DE = Quellsprache,
# msgstr-Kollisionen sind dort so gut wie nie legitim — anders als in
# en.po, wo viele verschiedene deutsche msgids korrekt auf dasselbe
# englische Wort fallen).
DUPLICATE_MSGSTR_CHECK_FILE = "src/locale/de/LC_MESSAGES/django.po"

# Legitime Faelle, in denen zwei verschiedene aktive msgids in der de-po
# bewusst dieselbe msgstr tragen (z. B. Kurzform ohne Doppelpunkt-
# Variante). Value = Menge von frozensets der kollidierenden msgids.
# Ein neuer, nicht gelisteter Kollisions-Cluster faellt den Guard —
# entweder ist es ein echter Drift-Bug (fixen) oder eine bewusste
# Wiederverwendung (hier eintragen, mit Begruendung im Commit).
DUPLICATE_MSGSTR_ALLOWLIST: set[frozenset[str]] = set()


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s


def parse_active_entries(text: str) -> list[tuple[str, str]]:
    """Liefert (msgid, msgstr) fuer aktive Eintraege (nicht fuzzy, nicht obsolete, nicht leer).

    Bewusst ohne externe Abhaengigkeit (kein polib) — kleiner
    stdlib-Parser fuer PO-Bloecke, analog src/tests/test_i18n_catalog.py.
    Plural-Formen (``msgid_plural``/``msgstr[n]``) werden uebersprungen:
    ihre msgstr-Werte sind naturgemaess pro Form verschieden und keine
    Kandidaten fuer diese Drift-Klasse.
    """
    out: list[tuple[str, str]] = []
    for block in text.split("\n\n"):
        lines = block.splitlines()
        if not lines:
            continue
        if any(ln.startswith("#~") for ln in lines):
            continue  # obsolete
        if any(ln.startswith("#,") and "fuzzy" in ln for ln in lines):
            continue  # fuzzy — wird von msgfmt beim Kompilieren verworfen
        if any(ln.startswith("msgid_plural") for ln in lines):
            continue  # Plural-Entry, msgstr[n] nicht vergleichbar
        msgid_parts: list[str] = []
        msgstr_parts: list[str] = []
        current: str | None = None
        for ln in lines:
            if ln.startswith("msgid "):
                current = "id"
                msgid_parts.append(_unquote(ln[len("msgid ") :]))
            elif ln.startswith("msgstr "):
                current = "str"
                msgstr_parts.append(_unquote(ln[len("msgstr ") :]))
            elif ln.startswith('"'):
                if current == "id":
                    msgid_parts.append(_unquote(ln))
                elif current == "str":
                    msgstr_parts.append(_unquote(ln))
            else:
                current = None
        msgid = "".join(msgid_parts)
        msgstr = "".join(msgstr_parts)
        if not msgid or not msgstr:
            continue  # Header-Block bzw. unuebersetzt
        out.append((msgid, msgstr))
    return out


def find_duplicate_msgstr(po: Path, allowlist: set[frozenset[str]]) -> list[str]:
    """Gruppiert aktive Eintraege nach msgstr und meldet nicht erlaubte Kollisionen.

    Eine Kollision ist eine msgstr, unter der mehrere VERSCHIEDENE msgids
    haengen — die Fehlklasse aus #1348 (zwei unterschiedliche UI-Texte
    rendern identisch, weil eine msgstr faelschlich uebernommen wurde).
    """
    by_msgstr: dict[str, set[str]] = {}
    for msgid, msgstr in parse_active_entries(po.read_text(encoding="utf-8")):
        by_msgstr.setdefault(msgstr, set()).add(msgid)

    problems = []
    for msgstr, msgids in by_msgstr.items():
        if len(msgids) < 2:
            continue
        if frozenset(msgids) in allowlist:
            continue
        ids = ", ".join(repr(m) for m in sorted(msgids))
        problems.append(f"{po}: msgstr {msgstr!r} <- {ids}")
    return problems


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

        if rel == DUPLICATE_MSGSTR_CHECK_FILE:
            dupes = find_duplicate_msgstr(po, DUPLICATE_MSGSTR_ALLOWLIST)
            if dupes:
                failures.append(
                    f"{rel}: {len(dupes)} nicht erlaubte msgstr-Kollision(en) "
                    "(verschiedene msgids, identische msgstr — Refs #1348)"
                )
                failures.extend(f"    {d}" for d in dupes)

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
