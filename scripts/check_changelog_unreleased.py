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
``- **Titel** ([#NNN](URL)) — Text.`` mit hoechstens zwei Saetzen.

Calibration
===========
Rules a-f were calibrated against the released blocks ``[0.81.0]`` and
``[0.80.0]``. Two deviations from AGENTS.md showed up there and are part of
the rules on purpose:

* ``### Removed`` and ``### Performance`` are used in practice although
  AGENTS.md does not name them, so the vocabulary below includes them.
* Not every entry carries a ``#NNN`` reference: ``[0.80.0]`` lists a plain
  dependency bump without a ticket. A missing reference is therefore only a
  warning and never blocks (exit code stays 0).

The entry length limit of rule g was calibrated against ``[0.12.0]`` and
``[0.14.0]`` instead (Refs #2704). Those blocks were written and edited in one
pass at release time, and every entry of both meets the limit (see
``dev-ops/release/tests/test_check_changelog_unreleased.py``). ``[0.80.0]``
and ``[0.81.0]`` no longer pass the guard as a whole: they already carry the
long entries rule g is meant to prevent.

Regeln (deterministisch)
========================
a) ``### ``-Unterabschnitte in [Unreleased] nur aus ``ALLOWED_SECTIONS``.
b) Kein leerer Unterabschnitt (Heading ohne mindestens einen Top-Level-Bullet).
c) Ein Eintrag ohne jede ``#NNN``-Referenz erzeugt nur eine Warnung (siehe
   Kalibrierung oben) — keinen Fehler.
d) Keine Duplikat-Bullets: Schluessel = (Menge der ``#NNN``-Referenzen im
   Eintrag, casefold-normalisierter Textanfang bis 60 Zeichen).
e) Fehlende oder leere ``[Unreleased]``-Sektion ist GRUEN (frischer
   Post-Release-Zustand ist legitim).
f) Jede Kategorie hoechstens eine ``### ``-Ueberschrift pro Block (Refs #1975).
   Der Block ``[0.82.0]`` sammelte ``### Fixed`` fuenfmal, ``### Docs``
   fuenfmal, ``### Security`` dreimal: jeder Beitrag haengte seine eigene
   Ueberschrift hinten an, statt den Eintrag unter die vorhandene zu
   sortieren. Geprueft wird auch das nur im offenen Block — veroeffentlichte
   Abschnitte stecken in der Public-History und sind nicht mehr aenderbar.

Entry form (Refs #2704)
=======================
Since v0.20.0 every change writes its own entry, and review rounds kept adding
sentences to it. Entries grew from about 250 to more than 1,000 characters,
one ticket spread over several entries, and merged branches left the sections
in the order of arrival. Rules g-k keep the open block in the form the release
edit expects:

g) The text of an entry after the dash has at most ``MAX_ENTRY_CHARS``
   characters, in the operator-actions section ``MAX_OPERATOR_ENTRY_CHARS``.
   The text is counted as rendered: a Markdown link counts with its label,
   not with its target. An entry without a dash is measured as a whole.
   Indented lines below an entry (nested bullets, wrapped text) count
   towards its limit.
h) One entry per ticket and section. The tickets of an entry are the ``#NNN``
   references before the dash; a mention in the text of another entry does
   not count. The same ticket may still appear in two sections, for example
   as an operator action and as a security entry. The operator-actions
   section is exempt: operators work through it step by step, so one ticket
   may list each required step as its own entry.
i) The sections follow ``SECTION_ORDER``.
j) No blank line between two entries of a section, and none between an
   entry and its nested lines.
k) A blank line before every ``### `` heading.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Reading order of the sections in a release block (rule i). It holds the
# documented vocabulary plus the categories retained from established
# releases. The operator actions come first, as
# src/tests/test_changelog_operator_actions.py requires.
SECTION_ORDER: tuple[str, ...] = (
    "Erforderliche Betreiber-Aktionen",
    "Security",
    "Added",
    "Changed",
    "Deprecated",
    "Removed",
    "Fixed",
    "Performance",
    "Docs",
)
OPERATOR_SECTION = SECTION_ORDER[0]
ALLOWED_SECTIONS: frozenset[str] = frozenset(SECTION_ORDER)

# Rule g. Operator actions get more room for the step and the runbook link.
MAX_ENTRY_CHARS = 350
MAX_OPERATOR_ENTRY_CHARS = 600

_SUBSECTION_RE = re.compile(r"^### (.+?)\s*$")
_BULLET_RE = re.compile(r"^- (.*)$")
_ISSUE_REF_RE = re.compile(r"#(\d+)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HEAD_SEPARATOR = " — "
_BOLD_MARK = "**"


def _split_entry(text: str) -> tuple[str, str]:
    """Split an entry into its head (title and ticket links) and its text.

    The head ends at the first dash that stands outside the bold title, so a
    dash inside the title (``**Alpha — beta**``) neither hides the ticket
    reference after it nor adds the rest of the title to the text. An entry
    without a closing bold mark is split at its first dash.
    """
    title_end = 0
    open_mark = text.find(_BOLD_MARK)
    if open_mark != -1:
        close_mark = text.find(_BOLD_MARK, open_mark + len(_BOLD_MARK))
        if close_mark != -1:
            title_end = close_mark + len(_BOLD_MARK)
    split_at = text.find(_HEAD_SEPARATOR, title_end)
    if split_at == -1:
        return "", text
    return text[:split_at], text[split_at + len(_HEAD_SEPARATOR) :]


def entry_text_length(text: str) -> int:
    """Return the length of the entry text after the dash, as rendered.

    A Markdown link counts with its label only. An entry without a dash is
    measured as a whole.
    """
    _head, body = _split_entry(text)
    return len(_LINK_RE.sub(r"\1", body).strip())


def _continuation_length(lines: list[str], entry_index: int, end: int) -> int:
    """Length of the indented lines that continue the entry at ``entry_index``.

    Nested bullets and wrapped lines belong to their entry, so they count
    towards its limit (rule g), measured as rendered like the entry text.
    """
    total = 0
    for idx in range(entry_index + 1, end):
        raw = lines[idx]
        if not raw.strip():
            continue
        if not raw[0].isspace():
            break
        total += len(_LINK_RE.sub(r"\1", raw).strip())
    return total


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
    """Apply rules a-d and f-k to the content of the ``[Unreleased]`` section.

    ``start``/``end`` as returned by :func:`find_unreleased_block`. Returns
    ``(errors, warnings)``, each a list of ``(1-based line number, message)``.
    The messages of rules a-f are German, those of rules g-k English.
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
    # Regel f: je Kategorie alle Ueberschrift-Zeilen, in Lesereihenfolge.
    section_lines: dict[str, list[int]] = {}
    # Rule h: first entry line per (section, ticket).
    seen_tickets: dict[tuple[str, str], int] = {}
    # Rules j and k: what the last non-blank line was, and whether a blank
    # line followed it.
    previous_was_entry = False
    blank_since_previous = False

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

        if not raw.strip():
            blank_since_previous = True
            continue

        heading_match = _SUBSECTION_RE.match(raw)
        if heading_match:
            if not blank_since_previous:
                errors.append((lineno, f"Missing blank line before '{raw.strip()}'."))
            previous_was_entry = False
            blank_since_previous = False
            close_section()
            section_name = heading_match.group(1).strip()
            section_heading_line = lineno
            section_has_bullet = False
            section_lines.setdefault(section_name, []).append(lineno)
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

            if previous_was_entry and blank_since_previous:
                errors.append((lineno, "Blank line inside the list: entries of a section follow each other directly."))
            previous_was_entry = True
            blank_since_previous = False

            text = bullet_match.group(1).rstrip()
            refs = frozenset(_ISSUE_REF_RE.findall(text))
            key = (refs, text[:60].casefold())
            is_duplicate = key in seen_keys
            if is_duplicate:
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

            # A duplicate is already reported above; rule h would only repeat it.
            # Operator actions are exempt: one ticket may list each step on its own.
            if section_name not in (None, OPERATOR_SECTION) and not is_duplicate:
                errors.extend(_second_entries(text, section_name, lineno, seen_tickets))

            limit = MAX_OPERATOR_ENTRY_CHARS if section_name == OPERATOR_SECTION else MAX_ENTRY_CHARS
            length = entry_text_length(text) + _continuation_length(lines, idx, end)
            if length > limit:
                errors.append(
                    (
                        lineno,
                        f"Entry text has {length} characters; the limit is {limit} "
                        "(link targets do not count). Say what changes for users, operators "
                        "or security; edge cases, exit codes and file names belong in the "
                        "commit body, the ticket and the hand-check.",
                    )
                )
            continue

        # Indented lines continue the entry above; anything else ends the list.
        if not raw[0].isspace():
            previous_was_entry = False
        elif previous_was_entry and blank_since_previous:
            errors.append((lineno, "Blank line inside the list: a nested line follows its entry directly."))
        blank_since_previous = False

    close_section()
    errors.extend(_doppelte_kategorien(section_lines))
    errors.extend(_sections_out_of_order(section_lines))
    errors.sort(key=lambda paar: paar[0])
    return errors, warnings


def _second_entries(
    text: str, section_name: str, lineno: int, seen_tickets: dict[tuple[str, str], int]
) -> list[tuple[int, str]]:
    """Rule h: report every ticket of this entry that already has one in the section."""
    head, _body = _split_entry(text)
    findings: list[tuple[int, str]] = []
    for ticket in sorted(set(_ISSUE_REF_RE.findall(head)), key=int):
        first_line = seen_tickets.setdefault((section_name, ticket), lineno)
        if first_line != lineno:
            findings.append(
                (
                    lineno,
                    f"Second entry for #{ticket} in '### {section_name}' (first one in line "
                    f"{first_line}). One entry per ticket and section: merge both into one.",
                )
            )
    return findings


def _sections_out_of_order(section_lines: dict[str, list[int]]) -> list[tuple[int, str]]:
    """Rule i: report every section that comes after one it should precede.

    Unknown sections are left to rule a. A repeated section is judged by its
    first heading; rule f reports the repetition.
    """
    rank = {name: position for position, name in enumerate(SECTION_ORDER)}
    findings: list[tuple[int, str]] = []
    latest: str | None = None
    for name, line_numbers in section_lines.items():
        if name not in rank:
            continue
        if latest is not None and rank[name] < rank[latest]:
            findings.append(
                (
                    line_numbers[0],
                    f"Section '### {name}' is out of order: it belongs before '### {latest}'. "
                    f"Order: {' → '.join(SECTION_ORDER)}.",
                )
            )
        else:
            latest = name
    return findings


def _doppelte_kategorien(section_lines: dict[str, list[int]]) -> list[tuple[int, str]]:
    """Regel f: Kategorien melden, die mehr als eine Ueberschrift haben.

    Gemeldet wird an der ersten Wiederholung — dort steht die Ueberschrift, die
    weg soll. Die Meldung nennt alle Fundstellen, damit das Zusammenfuehren in
    einem Durchgang gelingt. Alle betroffenen Kategorien werden gemeldet, nicht
    nur die erste; sonst wird das Aufraeumen zur Schleife aus Lauf und Fund.
    """
    befunde: list[tuple[int, str]] = []
    for name, zeilen in section_lines.items():
        if len(zeilen) < 2:
            continue
        fundstellen = ", ".join(str(z) for z in zeilen)
        befunde.append(
            (
                zeilen[1],
                f"Abschnitt '### {name}' kommt {len(zeilen)} mal im offenen Block vor "
                f"(Zeilen {fundstellen}). Die Einträge unter die erste Überschrift "
                f"(Zeile {zeilen[0]}) zusammenführen und die weiteren Überschriften "
                "entfernen; die Reihenfolge der Kategorien bleibt dabei unverändert.",
            )
        )
    return befunde


def _token_regex():
    """Token-Regex aus der Single-Source lesen -- oder None (Refs #1974).

    ``dev-ops/release/leak_tokens.py`` liest die Regex dort, wo der
    Release-Build sie ausfuehrt (``verify-leak.sh``). Der Import ist bewusst
    optional: dieses Skript liegt unter ``scripts/`` und ist damit Teil des
    veroeffentlichten Baums, ``dev-ops/`` wird daraus gestrippt. Fehlt das
    Modul, ist der Text ohnehin schon durch den Guard gegangen -- dann gibt es
    hier nichts mehr zu pruefen.
    """
    release_dir = Path(__file__).resolve().parent.parent / "dev-ops" / "release"
    if not (release_dir / "leak_tokens.py").is_file():
        return None
    sys.path.insert(0, str(release_dir))
    try:
        import leak_tokens  # noqa: PLC0415

        return leak_tokens.token_regex()
    except Exception:  # noqa: BLE001 -- ein kaputter Guard darf ci nicht blockieren
        return None
    finally:
        if sys.path and sys.path[0] == str(release_dir):
            sys.path.pop(0)


def check_audit_tokens(lines: list[str], start: int, end: int) -> list[tuple[int, str]]:
    """Audit-Kuerzel im offenen Block melden, bevor der Release-Build daran bricht.

    Der Sanitizer entfernt sie nicht zuverlaessig -- in Markdown-Code-Spans
    trifft ihn kein Ersetzungsmuster --, ``verify-leak.sh`` [2/7] findet sie
    aber im Tree. Ohne diese Pruefung faellt das erst im Build-Dry-Run auf,
    also nach einem Kalt-Rebuild ueber alle Boundaries (Refs #1974).

    Nur der offene Block wird geprueft: veroeffentlichte Abschnitte stecken
    bereits in der Public-History und sind nicht mehr aenderbar.
    """
    regex = _token_regex()
    if regex is None:
        return []
    befunde: list[tuple[int, str]] = []
    for offset, line in enumerate(lines[start:end], start=start):
        treffer = sorted({m.group(0) for m in regex.finditer(line)})
        if treffer:
            befunde.append(
                (
                    offset + 1,
                    f"Audit-Kuerzel im offenen Block: {', '.join(treffer)}. "
                    "Der Release-Build bricht daran ab (verify-leak.sh [2/7]), und der "
                    "Sanitizer entfernt sie hier nicht. Den Eintrag umformulieren, statt "
                    "die Kuerzel bloss zu loeschen -- sonst verliert er seine Aussage.",
                )
            )
    return befunde


def check_changelog(path: Path, *, token_check: bool = True) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
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
    errors, warnings = analyze_unreleased(lines, start, end)
    if token_check:
        errors = [*errors, *check_audit_tokens(lines, start, end)]
        errors.sort(key=lambda paar: paar[0])
    return errors, warnings


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
    parser.add_argument(
        "--kein-token-check",
        action="store_true",
        help=(
            "Die Pruefung auf Audit-Kuerzel ueberspringen (Refs #1974). "
            "Gedacht fuer Baeume ohne dev-ops/, wo die Regex-Quelle fehlt."
        ),
    )
    args = parser.parse_args(argv)

    changelog_path: Path = args.changelog
    if not changelog_path.is_file():
        print(f"FEHLER: Changelog-Datei nicht gefunden: {changelog_path}", file=sys.stderr)
        return 1

    errors, warnings = check_changelog(changelog_path, token_check=not args.kein_token_check)

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
