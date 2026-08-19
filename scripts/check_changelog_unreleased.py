#!/usr/bin/env python3
"""CI-Guard: Struktur-Hygiene der ``## [Unreleased]``-Sektion in CHANGELOG.md.

Hintergrund (Refs #1953)
=========================
Die CHANGELOG-Hygiene wurde beim Release-Schnitt v0.81.0 als Batch nachgeholt
(Duplikat-Paare, fehlende ``### ``-Sektionen). Dieser Guard haelt
``[Unreleased]`` laufend sauber, statt das am Release-Cut nachzuholen — er
laeuft in ``make ci`` und prueft nur den offenen Bereich, niemals bereits
vergebene Versionsbloecke.

Konvention: AGENTS.md (Abschnitt "Coding-Regeln", CHANGELOG-Eintrag-Regel) —
``### Kategorie`` aus Security/Added/Changed/Fixed/Docs, Format
``- **Titel** ([#NNN](URL)) — ein Satz.``.

Kalibrierung gegen gelebte Praxis
==================================
Die Regeln unten wurden gegen die beiden zuletzt abgenommenen Bloecke
``[0.81.0]`` und ``[0.80.0]`` in CHANGELOG.md kalibriert: beide MUESSEN diesen
Guard bestehen, wenn ihr Inhalt (hypothetisch) unter ``[Unreleased]`` stuende
(siehe ``dev-ops/release/tests/test_check_changelog_unreleased.py``, Test
``test_real_released_blocks_would_pass_the_guard``). Zwei Abweichungen von
AGENTS.md wurden dabei sichtbar und sind bewusst in den Regeln beruecksichtigt:

* ``### Removed`` und ``### Performance`` werden in der Praxis verwendet,
  obwohl AGENTS.md nur Security/Added/Changed/Fixed/Docs nennt -> im
  Vokabular unten (``ALLOWED_SECTIONS``) mit aufgenommen.
* Nicht jeder Eintrag traegt eine ``#NNN``-Referenz — ``[0.80.0]`` enthaelt
  unter ``### Fixed`` den reinen Dependency-Bump "cryptography auf 50.0.0
  angehoben" ganz ohne Ticket-Bezug. Ein fehlender Issue-Link ist deshalb nur
  ein Hinweis (Warnung), kein Blocker (Exit bleibt 0).

Regeln (deterministisch, kein Stil-Linting)
============================================
a) ``### ``-Unterabschnitte in [Unreleased] nur aus ``ALLOWED_SECTIONS``.
b) Kein leerer Unterabschnitt (Heading ohne mindestens einen Top-Level-Bullet).
c) Ein Eintrag ohne jede ``#NNN``-Referenz erzeugt nur eine Warnung (siehe
   Kalibrierung oben) — keinen Fehler.
d) Keine Duplikat-Bullets: Schluessel = (Menge der ``#NNN``-Referenzen im
   Eintrag, casefold-normalisierter Textanfang bis 60 Zeichen).
e) Fehlende oder leere ``[Unreleased]``-Sektion ist GRUEN (frischer
   Post-Release-Zustand ist legitim).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Beobachtetes Vokabular: Security/Added/Changed/Fixed/Docs stehen so in
# AGENTS.md; Removed und Performance sind gelebte Praxis (siehe Docstring
# oben) und deshalb mit aufgenommen. Deprecated ist Keep-a-Changelog-Standard
# und bislang ungenutzt, aber als Minimum vorgesehen.
ALLOWED_SECTIONS: frozenset[str] = frozenset(
    {
        "Added",
        "Changed",
        "Deprecated",
        "Removed",
        "Fixed",
        "Security",
        "Docs",
        "Performance",
    }
)

_SUBSECTION_RE = re.compile(r"^### (.+?)\s*$")
_BULLET_RE = re.compile(r"^- (.*)$")
_ISSUE_REF_RE = re.compile(r"#(\d+)")


def find_unreleased_block(lines: list[str]) -> tuple[int, int] | None:
    """Grenzen der ``[Unreleased]``-Sektion ermitteln.

    Gibt ``(start, end)`` als 0-basierte Zeilenindizes zurueck, wobei
    ``start`` die Heading-Zeile selbst ist und ``end`` (exklusiv) der Beginn
    des naechsten ``## [``-Blocks bzw. das Dateiende. ``None``, wenn keine
    ``## [Unreleased]``-Heading existiert (Regel e: dann global gruen).
    """
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "## [Unreleased]":
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## ["):
            end = j
            break
    return start, end


def analyze_unreleased(lines: list[str], start: int, end: int) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Regeln a-d auf den Inhalt der ``[Unreleased]``-Sektion anwenden.

    ``start``/``end`` wie von :func:`find_unreleased_block` geliefert.
    Gibt ``(errors, warnings)`` zurueck, je eine Liste aus
    ``(1-basierte Zeilennummer, deutsche Meldung)``.
    """
    errors: list[tuple[int, str]] = []
    warnings: list[tuple[int, str]] = []

    section_name: str | None = None
    # Nur gueltig, solange section_name gesetzt ist (beide werden zusammen
    # beim Betreten eines Abschnitts zugewiesen) — der Platzhalter 0 wird nie
    # gelesen, bevor section_name aus dem None-Zustand heraus ist.
    section_heading_line = 0
    section_has_bullet = False
    seen_keys: dict[tuple[frozenset[str], str], int] = {}

    def close_section() -> None:
        if section_name is not None and not section_has_bullet:
            errors.append(
                (
                    section_heading_line,
                    f"Leerer Abschnitt '### {section_name}' ohne Eintrag.",
                )
            )

    for idx in range(start + 1, end):
        raw = lines[idx]
        lineno = idx + 1

        heading_match = _SUBSECTION_RE.match(raw)
        if heading_match:
            close_section()
            section_name = heading_match.group(1).strip()
            section_heading_line = lineno
            section_has_bullet = False
            if section_name not in ALLOWED_SECTIONS:
                allowed = ", ".join(sorted(ALLOWED_SECTIONS))
                errors.append(
                    (
                        lineno,
                        f"Unbekannter Abschnitt '### {section_name}' — erlaubt: {allowed}.",
                    )
                )
            continue

        bullet_match = _BULLET_RE.match(raw)
        if bullet_match:
            if section_name is None:
                errors.append((lineno, "Eintrag außerhalb eines '### '-Abschnitts."))
            else:
                section_has_bullet = True

            text = bullet_match.group(1).rstrip()
            refs = frozenset(_ISSUE_REF_RE.findall(text))
            key = (refs, text[:60].casefold())
            if key in seen_keys:
                errors.append(
                    (
                        lineno,
                        f"Möglicher Duplikat-Eintrag zu Zeile {seen_keys[key]} "
                        "(gleiche #-Referenzen + gleicher Textanfang).",
                    )
                )
            else:
                seen_keys[key] = lineno

            if not refs:
                warnings.append(
                    (
                        lineno,
                        "Eintrag ohne #NNN-Referenz — nicht blockierend "
                        "(gelebte Praxis, z. B. reine Dependency-Bumps ohne Ticket).",
                    )
                )
            continue

    close_section()
    return errors, warnings


def check_changelog(path: Path) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Datei laden und Regeln anwenden. Gibt ``(errors, warnings)`` zurueck.

    Leere Listen fuer beides, wenn keine ``[Unreleased]``-Sektion existiert
    oder sie leer ist (Regel e).
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    block = find_unreleased_block(lines)
    if block is None:
        return [], []
    start, end = block
    if not any(lines[i].strip() for i in range(start + 1, end)):
        return [], []
    return analyze_unreleased(lines, start, end)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prüft die '## [Unreleased]'-Sektion von CHANGELOG.md auf Struktur-Hygiene."
    )
    parser.add_argument(
        "--changelog",
        type=Path,
        default=Path("CHANGELOG.md"),
        help="Pfad zur CHANGELOG.md (Default: CHANGELOG.md im aktuellen Verzeichnis).",
    )
    args = parser.parse_args(argv)

    changelog_path: Path = args.changelog
    if not changelog_path.is_file():
        print(f"FEHLER: Changelog-Datei nicht gefunden: {changelog_path}", file=sys.stderr)
        return 1

    errors, warnings = check_changelog(changelog_path)

    for lineno, msg in warnings:
        print(f"Hinweis (Zeile {lineno}): {msg}")

    if errors:
        for lineno, msg in errors:
            print(f"FEHLER (Zeile {lineno}): {msg}", file=sys.stderr)
        print(f"\n{len(errors)} Problem(e) in '## [Unreleased]' gefunden.", file=sys.stderr)
        return 1

    print("'## [Unreleased]' ist sauber (oder aktuell leer).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
