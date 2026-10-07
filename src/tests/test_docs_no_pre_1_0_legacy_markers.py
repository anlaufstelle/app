"""Architecture-Guards — keine Vor-1.0-Migrations-/Legacy-Hinweise in der
oeffentlichen Doku (Refs #2205).

v1.0.0 ist die erste oeffentliche Version. Es gibt keine Installationen auf
Vor-1.0-Staenden, die eine Migrationsanleitung brauchen. Passagen der Form
``Updates von < 0.23``, ``Bestandsinstallation``, ``Migrationsschritte``,
``seit v0.x``/``since v0.x`` oder ``Legacy``/``legacy`` beschreiben eine
Historie, die fuer Leser der 1.0-Doku nicht existiert. Historie bleibt im
``CHANGELOG.md`` und in den ADRs (bewusst nicht gescannt).

Gescannt werden nur die oeffentlichen Kern-Dokumente auf oberster Ebene:
``docs/*.md`` (nicht rekursiv — ``docs/adr/``, ``docs/archive/``,
``docs/testing/``, ``docs/dev/`` bleiben damit automatisch aussen vor),
``docs/en/*.md``, ``docs/compliance/**/*.md``, ``README.md``, ``README.en.md``,
``CONTRIBUTING.md``, ``CONTRIBUTING.en.md``, ``SECURITY.md``.

Die Scan-Funktion nimmt das Wurzelverzeichnis als Parameter; die
Funktionstests unten proben ausschliesslich gegen ``tmp_path`` (nie gegen den
echten Baum), der Repo-weite Nachweis laeuft in einem eigenen Test gegen
``REPO_ROOT``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]

# Reihenfolge = Meldereihenfolge im Assertion-Text, nicht relevant fuer den Scan.
_PATTERNS: dict[str, re.Pattern[str]] = {
    "Updates von <": re.compile(r"Updates von <"),
    "Bestandsinstallation": re.compile(r"Bestandsinstallation", re.IGNORECASE),
    "Migrationsschritte": re.compile(r"Migrationsschritte"),
    "seit v0.": re.compile(r"\bseit v0\.", re.IGNORECASE),
    "since v0.": re.compile(r"\bsince v0\.", re.IGNORECASE),
    "Legacy": re.compile(r"legacy", re.IGNORECASE),
}


# Second pattern list (Refs #2447): phrases that narrate a change history or a
# pre-existing data stock ("since 0.97", "since #1751", "Bestandsmigration",
# "an earlier version of this text was wrong"). Public docs describe the
# current state; history lives in CHANGELOG.md and dated ADR addenda.
# These patterns are enforced on the real tree for all public core documents
# (same file set as the legacy markers); quoted literal UI/PDF strings are
# the only exceptions (_HISTORY_ALLOWLIST).
_HISTORY_PATTERNS: dict[str, re.Pattern[str]] = {
    "seit/bis/vor 0.x": re.compile(r"\b(?:[Ss]eit|[Bb]is|[Vv]or|[Ss]ince|[Uu]ntil|[Bb]efore) (?:[Vv]ersion )?v?0\.\d"),
    "Stand v0.": re.compile(r"\bStand:? v?0\.\d"),
    "release-0.": re.compile(r"\brelease-0\.\d"),
    "ab v0.": re.compile(r"\b(?:[Aa]b|[Ff]rom) v0\.\d"),
    "seit #": re.compile(r"\b(?:[Ss]eit|[Bb]is|[Nn]ach|[Ss]ince|[Uu]ntil) \[?#\d"),
    "Bestandsmigration": re.compile(r"Bestandsmigration|\bBestands-"),
    "Alt-Bestand": re.compile(r"\bAlt-(?:Bestand|[Zz]eilen|[Dd]aten|Snapshot)|\bAlt(?:bestand|zeilen|daten)\b"),
    "Welle": re.compile(r"\b(?:[Dd]ieser|[Ss]eit|[Nn]ach|[Vv]or) Welle\b|\b(?:[Ss]ince|[Tt]his) [Ww]ave\b"),
    "frühere Fassung": re.compile(r"\b[Ff]rühere[n]? Fassung|\b[Dd]ie frühere\b|\b[Ee]ine frühere\b"),
    "Verhaltenswechsel": re.compile(r"Verhaltenswechsel"),
    "seit Milestone": re.compile(r"\b(?:[Ss]eit|[Vv]or|[Bb]is|[Mm]it) (?:Milestone|M)\s?\d"),
    "Bis zum Update": re.compile(
        r"\b(?:[Bb]is zum|[Vv]or dem|[Ss]eit dem) Update\b|\b(?:[Uu]ntil|[Bb]efore) the update\b"
    ),
    "Vor dieser Version": re.compile(
        r"\b(?:[Vv]or|[Ss]eit|[Bb]is zu) dieser Version\b|\b(?:[Bb]efore|[Ss]ince|[Uu]ntil) this version\b"
    ),
    "wie bisher": re.compile(r"\b[Ww]ie bisher\b"),
    # Narrowed to wording about an earlier text/state; "der ursprüngliche
    # Request-Payload" (the original input) is current-state wording.
    "ursprünglich": re.compile(
        r"\b[Uu]rsprünglich(?:e[nmrs]?)? (?:Fassung|Version|Formulierung|Text|Zusage|enthalten)"
    ),
    "seit A<n>.<n>": re.compile(r"\b(?:[Ss]eit|[Ss]ince|[Bb]is|[Uu]ntil) A\d\.\d"),
    "Reihenfolge-Umkehr": re.compile(r"[Rr]eihenfolge-Umkehr|\b(?:ordering|order) reversal\b"),
    "früherer Zustand": re.compile(
        r"\b[Ff]rüher(?:e[nmrs]?)? (?:stillen?|Fail|Verhalten|Zustand)|"
        r"\b(?:earlier|former|previous) (?:silent|fail-open|behaviou?r)\b"
    ),
    "Synchronization update": re.compile(r"\bSynchroni[sz]ation update\b", re.IGNORECASE),
}

# Literal interface/PDF strings that the documents quote verbatim and that
# cannot be reworded without changing the product text (.po / PDF labels).
# Format: relpath -> [(substring of the line, reason)].
_HISTORY_ALLOWLIST: dict[str, list[tuple[str, str]]] = {
    "docs/admin-guide.md": [
        (
            "Frühere Fassungen ihrer Ereignis-Dokumentation",
            "Quotes the German success message of the anonymization action verbatim.",
        ),
        ("vor 0.96 nicht erfasst", "Quotes the literal placeholder printed in the PDF export."),
    ],
    "docs/en/admin-guide.md": [
        (
            "Frühere Fassungen ihrer Ereignis-Dokumentation",
            "Quotes the German success message of the anonymization action verbatim.",
        ),
        ("vor 0.96 nicht erfasst", "Quotes the literal placeholder printed in the PDF export."),
    ],
}


@dataclass(frozen=True)
class Hit:
    relpath: str
    lineno: int
    pattern: str
    line: str


def _scanned_files(root: Path) -> list[Path]:
    """Die oeffentlichen Kern-Dokumente unterhalb ``root`` — bewusst nicht
    rekursiv fuer ``docs/`` und ``docs/en/`` (haelt ``docs/adr/``,
    ``docs/archive/``, ``docs/testing/``, ``docs/dev/`` automatisch aussen
    vor, ohne sie explizit auszuschliessen)."""
    candidates: list[Path] = []
    candidates.extend(sorted((root / "docs").glob("*.md")))
    candidates.extend(sorted((root / "docs" / "en").glob("*.md")))
    candidates.extend(sorted((root / "docs" / "compliance").rglob("*.md")))
    for rel in ("README.md", "README.en.md", "CONTRIBUTING.md", "CONTRIBUTING.en.md", "SECURITY.md"):
        candidate = root / rel
        if candidate.exists():
            candidates.append(candidate)
    return [p for p in candidates if p.is_file()]


def scan_markers(root: Path) -> list[Hit]:
    """Alle Marker-Treffer in den oeffentlichen Kern-Dokumenten unter
    ``root`` — ohne Allowlist-Filterung (die wendet der aufrufende Test an)."""
    hits: list[Hit] = []
    for path in _scanned_files(root):
        relpath = str(path.relative_to(root))
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for name, pattern in _PATTERNS.items():
                if pattern.search(line):
                    hits.append(Hit(relpath=relpath, lineno=lineno, pattern=name, line=line.strip()))
    return hits


# Hard-wrapped prose of the runbooks is checked across line breaks as well.
_WRAP_SCOPE = ("docs/ops-runbook.md", "docs/en/ops-runbook.md")


def _strip_quotes(line: str, relpath: str) -> str:
    """``line`` without the verbatim-quoted strings allowlisted for ``relpath``;
    other history wording on the same line stays visible to the scan."""
    for needle, _ in _HISTORY_ALLOWLIST.get(relpath, []):
        line = line.replace(needle, " ")
    return line


def scan_history_markers(
    root: Path, relpaths: tuple[str, ...] | None = None, *, apply_allowlist: bool = True
) -> list[Hit]:
    """History-pattern hits under ``root``: in ``relpaths`` if given, else in
    all public core documents (same file set as :func:`scan_markers`).

    With ``apply_allowlist`` the verbatim-quoted strings of
    ``_HISTORY_ALLOWLIST`` are removed from the line before matching, so only
    the quoted span is exempt. In the operations runbooks (``_WRAP_SCOPE``) a
    phrase wrapped over a line break (the match starts on one line and ends on
    the next) is reported on its first line."""
    if relpaths is None:
        paths = _scanned_files(root)
    else:
        paths = [root / rel for rel in relpaths if (root / rel).is_file()]
    hits: list[Hit] = []
    for path in paths:
        relpath = str(path.relative_to(root))
        lines = path.read_text(encoding="utf-8").splitlines()
        for lineno, line in enumerate(lines, start=1):
            checked = _strip_quotes(line, relpath) if apply_allowlist else line
            following = lines[lineno].lstrip("> \t") if lineno < len(lines) and relpath in _WRAP_SCOPE else ""
            joined = f"{checked.rstrip()} {following}"
            for name, pattern in _HISTORY_PATTERNS.items():
                if pattern.search(checked) or any(
                    m.start() < len(checked.rstrip()) < m.end() for m in pattern.finditer(joined)
                ):
                    hits.append(Hit(relpath=relpath, lineno=lineno, pattern=name, line=line.strip()))
    return hits


# Begruendete Ausnahmen (Datei:Kontext) — jede Zeile einzeln begruendet, kein
# Pauschal-Opt-out. Ausschliesslich fuer Treffer, die KEINE Vor-1.0-
# Migrations-/Bestandsinstallations-Aussage sind, sondern (a) Code-
# Identifier, die im Text zitiert werden, (b) evergreen-technisches
# Vokabular, das mit der Anlaufstelle-Version nichts zu tun hat, oder (c)
# einen Anker, dessen Umbenennung eine ausserhalb des Sweep-Scopes liegende
# Datei gebrochen haette. Format: relpath -> [(Teilstring der Zeile, Begruendung)].
_ALLOWLIST: dict[str, list[tuple[str, str]]] = {
    "docs/security-notes.md": [
        (
            "Legacy-Wert",
            "Bezeichnet ein Datenformat (Idempotenz-Fingerprint ohne "
            "'h1:'-Praefix), kein Vor-1.0-Installationsstand — das Format "
            "bleibt fuer jede zukuenftige Schluesselrotation relevant, "
            "unabhaengig vom Release-Stand. Refs #2205.",
        ),
        (
            "_LEGACY_VIEW_Q",
            "Zitiert den tatsaechlichen Python-Bezeichner aus dem Code "
            "(Abwertungs-Lesepfad fuer ExternalReport-Freigaben) — kein "
            "Prosa-Legacy-Hinweis. Refs #2205.",
        ),
        (
            "legacy_redacted",
            "Zitiert den tatsaechlichen Marker-Wert aus Migration 0163 "
            "(core_activity.display_context) — Code-Identifier, keine "
            "Vor-1.0-Migrationsanleitung. Refs #2205.",
        ),
    ],
}


def _is_allowlisted(hit: Hit) -> str | None:
    for needle, reason in _ALLOWLIST.get(hit.relpath, []):
        if needle in hit.line:
            return reason
    return None


def _format_hits(hits: list[Hit]) -> str:
    return "\n".join(f"{h.relpath}:{h.lineno} [{h.pattern}] {h.line}" for h in hits)


class TestScanFunctionAgainstFixtures:
    """Die Scan-Funktion selbst — ausschliesslich gegen ``tmp_path``-Proben,
    nie gegen den echten Baum (Lehre: Scanner-Tests duerfen keine Proben in
    den echten Baum legen)."""

    def _make_tree(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "en").mkdir()
        (tmp_path / "docs" / "adr").mkdir()
        (tmp_path / "docs" / "compliance").mkdir()

    def test_finds_each_pattern_in_top_level_docs(self, tmp_path: Path) -> None:
        self._make_tree(tmp_path)
        (tmp_path / "docs" / "sample.md").write_text(
            "\n".join(
                [
                    "Betreiber-Aktion bei Updates von < 0.23 noetig.",
                    "Gilt fuer jede Bestandsinstallation.",
                    "Details und Migrationsschritte folgen.",
                    "Seit v0.12 gibt es diese Rolle.",
                    "Since v0.11 tested in CI.",
                    "Der Legacy-Pfad ist ausgeschlossen.",
                ]
            ),
            encoding="utf-8",
        )
        hits = scan_markers(tmp_path)
        found_patterns = {h.pattern for h in hits}
        assert found_patterns == set(_PATTERNS), f"nicht alle Muster erkannt: {_format_hits(hits)}"

    def test_ignores_adr_and_archive_and_dev_and_testing(self, tmp_path: Path) -> None:
        self._make_tree(tmp_path)
        (tmp_path / "docs" / "archive").mkdir()
        (tmp_path / "docs" / "dev").mkdir()
        (tmp_path / "docs" / "testing").mkdir()
        marker = "Legacy-Pfad, seit v0.1 Bestandsinstallation"
        (tmp_path / "docs" / "adr" / "001-foo.md").write_text(marker, encoding="utf-8")
        (tmp_path / "docs" / "archive" / "old.md").write_text(marker, encoding="utf-8")
        (tmp_path / "docs" / "dev" / "hand-checks.md").write_text(marker, encoding="utf-8")
        (tmp_path / "docs" / "testing" / "runs.md").write_text(marker, encoding="utf-8")
        (tmp_path / "CHANGELOG.md").write_text(marker, encoding="utf-8")
        hits = scan_markers(tmp_path)
        assert hits == [], f"gescannte Nicht-Kern-Dokumente haetten leer bleiben muessen: {_format_hits(hits)}"

    def test_scans_readme_contributing_security_and_compliance(self, tmp_path: Path) -> None:
        self._make_tree(tmp_path)
        (tmp_path / "README.md").write_text("Bestandsinstallation", encoding="utf-8")
        (tmp_path / "README.en.md").write_text("since v0.1", encoding="utf-8")
        (tmp_path / "CONTRIBUTING.md").write_text("Migrationsschritte", encoding="utf-8")
        (tmp_path / "CONTRIBUTING.en.md").write_text("Legacy", encoding="utf-8")
        (tmp_path / "SECURITY.md").write_text("Updates von < 1.0", encoding="utf-8")
        (tmp_path / "docs" / "compliance" / "sub").mkdir()
        (tmp_path / "docs" / "compliance" / "sub" / "nested.md").write_text("seit v0.5", encoding="utf-8")
        hits = scan_markers(tmp_path)
        relpaths = {h.relpath for h in hits}
        assert relpaths == {
            "README.md",
            "README.en.md",
            "CONTRIBUTING.md",
            "CONTRIBUTING.en.md",
            "SECURITY.md",
            "docs/compliance/sub/nested.md",
        }, _format_hits(hits)

    def test_clean_tree_reports_nothing(self, tmp_path: Path) -> None:
        self._make_tree(tmp_path)
        (tmp_path / "docs" / "sample.md").write_text(
            "Die Doku beschreibt den Ist-Stand ohne Versions-Marker.", encoding="utf-8"
        )
        assert scan_markers(tmp_path) == []


class TestRealDocsTreeIsClean:
    """Repo-weiter Nachweis: nach dem Vor-1.0-Altlasten-Sweep (Refs #2205)
    hat die oeffentliche Kern-Doku ausserhalb der begruendeten Allowlist
    keinen Treffer mehr."""

    def test_no_unallowlisted_markers_remain(self) -> None:
        hits = scan_markers(REPO_ROOT)
        unresolved = [h for h in hits if _is_allowlisted(h) is None]
        assert unresolved == [], (
            "Vor-1.0-Migrations-/Legacy-Marker in der oeffentlichen Kern-Doku "
            f"gefunden (Refs #2205):\n{_format_hits(unresolved)}\n"
            "Entweder die Passage bereinigen (Grundsatz: 1.0 ist die erste "
            "oeffentliche Version, Historie gehoert ins CHANGELOG/ADR) oder, "
            "falls es sich nachweislich NICHT um eine Vor-1.0-"
            "Migrationsanleitung handelt, mit Begruendung in _ALLOWLIST "
            "aufnehmen."
        )

    def test_allowlist_entries_still_match_something(self) -> None:
        """Verwaiste Allowlist-Eintraege (Text laengst entfernt) sollen
        auffallen, statt stillschweigend nutzlos zu bleiben."""
        hits = scan_markers(REPO_ROOT)
        by_file: dict[str, list[Hit]] = {}
        for hit in hits:
            by_file.setdefault(hit.relpath, []).append(hit)
        stale: list[str] = []
        for relpath, entries in _ALLOWLIST.items():
            for needle, _reason in entries:
                if not any(needle in hit.line for hit in by_file.get(relpath, [])):
                    stale.append(f"{relpath}: {needle!r}")
        assert stale == [], f"verwaiste Allowlist-Eintraege (nichts trifft mehr zu): {stale}"


class TestHistoryScanAgainstFixtures:
    """History patterns (Refs #2447) — probes only in ``tmp_path``."""

    def test_finds_each_history_pattern(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "sample.md").write_text(
            "\n".join(
                [
                    "Das gilt seit 0.97 für alle Exporte.",
                    "Stand: v0.15 der Doku.",
                    "Eingeplant für `release-0.85`.",
                    "**Status:** Aktiv ab v0.11.",
                    "Seit [#1751](https://example.org/1751) schreibt der Pfad nichts mehr.",
                    "Die Bestandsmigration läuft einmalig.",
                    "Der Alt-Bestand bleibt lesbar.",
                    "Das gilt seit dieser Welle.",
                    "Eine frühere Fassung dieses Abschnitts war falsch.",
                    "Verhaltenswechsel (2026-08):",
                    "Umgesetzt seit Milestone 24.",
                    "Bis zum Update war der Schutz unvollständig.",
                    "Vor dieser Version gab es keinen Guard.",
                    "Der Export funktioniert wie bisher.",
                    "Seit A1.4 ist der Owner die Admin-Rolle.",
                    "Das gilt seit der Reihenfolge-Umkehr.",
                    "Das verhindert den früheren stillen Fail-open.",
                    "Die ursprüngliche Fassung gilt.",
                    "Synchronization update 2026-10-06: current wording.",
                ]
            ),
            encoding="utf-8",
        )
        hits = scan_history_markers(tmp_path)
        assert {h.pattern for h in hits} == set(_HISTORY_PATTERNS), _format_hits(hits)

    def test_current_state_wording_is_not_flagged(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "sample.md").write_text(
            "\n".join(
                [
                    "Die Prüfung erkennt drei Fälle ([#1070](https://example.org/1070)).",
                    "Erneute Einrichtung verlangt den Bestandsfaktor.",
                    "Der Wert liegt bei 0.5 Sekunden.",
                    "Die Version steht im Kopf der Datei.",
                ]
            ),
            encoding="utf-8",
        )
        assert scan_history_markers(tmp_path) == []

    def test_relpaths_limit_the_scan(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "mine.md").write_text("Seit 0.90 gilt das.", encoding="utf-8")
        (tmp_path / "docs" / "other.md").write_text("Seit 0.90 gilt das.", encoding="utf-8")
        hits = scan_history_markers(tmp_path, ("docs/mine.md", "docs/missing.md"))
        assert [h.relpath for h in hits] == ["docs/mine.md"]


# Wording examples named in issue #2447, written independently of
# _HISTORY_PATTERNS so that a pattern left out of the list makes this red.
TICKET_HISTORY_EXAMPLES: tuple[str, ...] = (
    "Das gilt seit 0.97 für alle Exporte.",
    "Der Alt-Bestand bleibt lesbar.",
    "Stand: v0.15 der Doku.",
    "Das galt vor 0.99 nicht.",
    "Seit #1751 schreibt der Pfad nichts mehr.",
    "Bis #1200 fehlte die Prüfung.",
    "Das blieb bis 0.89 so.",
    "Seit 0.90 gilt das.",
    "Das galt bis v0.95.0.",
    "Bestands-Sessions laufen weiter.",
    "Die Bestandsmigration läuft einmalig.",
    "Die Alt-Zeilen bleiben stehen.",
    "Das gilt seit dieser Welle.",
    "Seit Welle " + "3 prüft der Guard das.",  # split: the release tree must not carry the track code
    "Since Wave 3 the guard checks this.",
    "since wave 3 the guard checks this.",
    "Eine frühere Fassung dieses Abschnitts war falsch.",
    "Die frühere Prüfung fehlte.",
    "Bis zum Update war der Schutz unvollständig.",
    "Vor dieser Version gab es keinen Guard.",
    "Before this version there was no guard.",
    "Der Export funktioniert wie bisher.",
    "Die ursprüngliche Fassung gilt.",
    "Stand v0.15 der Doku.",
    "Eingeplant für release-0.85.",
    "Umgesetzt seit M24.",
    "Geplant vor Milestone 25.",
    "Synchronization update 2026-10-06: current wording.",
    "Seit A1.4 ist der Owner die Admin-Rolle.",
    "Since A1.4, the check additionally verifies ownership.",
    "Das gilt seit der Reihenfolge-Umkehr in der Migration.",
    "Das verhindert den früheren stillen Fail-open.",
    "This prevents the earlier silent fail-open.",
)


class TestTicketHistoryExamples:
    """Every example from issue #2447 is flagged (Refs #2447)."""

    @pytest.mark.parametrize("sentence", TICKET_HISTORY_EXAMPLES)
    def test_ticket_example_is_flagged(self, sentence: str, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "sample.md").write_text(sentence, encoding="utf-8")
        assert scan_history_markers(tmp_path), f"history wording not flagged: {sentence!r}"


def _quote_is_needed(relpath: str, needle: str) -> bool:
    """True if some line quotes ``needle`` and carries a history hit only
    because of that quote (removing the quote removes the hit)."""
    raw = scan_history_markers(REPO_ROOT, (relpath,), apply_allowlist=False)
    kept = scan_history_markers(REPO_ROOT, (relpath,))
    return len([h for h in raw if needle in h.line]) > len([h for h in kept if needle in h.line])


class TestAllowlistIsLimitedToTheQuote:
    """An allowlisted quote exempts only its own span (Refs #2447)."""

    def test_other_history_wording_on_the_same_line_is_still_flagged(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        quote = _HISTORY_ALLOWLIST["docs/admin-guide.md"][0][0]
        line = f"Die Meldung lautet „{quote}“ und gilt seit 0.90 für alle Exporte."
        (tmp_path / "docs" / "admin-guide.md").write_text(line, encoding="utf-8")
        hits = scan_history_markers(tmp_path)
        assert [h.pattern for h in hits] == ["seit/bis/vor 0.x"]

    def test_quote_alone_is_not_flagged(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        quote = _HISTORY_ALLOWLIST["docs/admin-guide.md"][0][0]
        (tmp_path / "docs" / "admin-guide.md").write_text(f"Die Meldung lautet „{quote}“.", encoding="utf-8")
        assert scan_history_markers(tmp_path) == []


class TestWrappedHistoryPhrases:
    """History wording split over two lines is found (Refs #2447)."""

    @pytest.mark.parametrize(
        "text",
        [
            "are safeguarded since\n[#1746](https://example.invalid/issues/1746) by a check.",
            "> Das gilt seit\n> [#1751](https://example.invalid/issues/1751).",
            "The step runs since\n#1746 as the first line.",
        ],
    )
    def test_wrapped_phrase_is_flagged(self, text: str, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "ops-runbook.md").write_text(text, encoding="utf-8")
        assert scan_history_markers(tmp_path)


class TestPublicDocsCarryNoHistoryWording:
    """The public core documents carry no history phrasing (Refs #2447)."""

    def test_no_history_markers_in_public_docs(self) -> None:
        hits = scan_history_markers(REPO_ROOT)
        assert hits == [], (
            "History or legacy-data wording in public docs (Refs #2447):\n"
            f"{_format_hits(hits)}\n"
            "Describe the current state; history belongs in CHANGELOG.md or a dated ADR addendum."
        )

    def test_history_allowlist_entries_still_match_something(self) -> None:
        stale = [
            (relpath, needle)
            for relpath, entries in _HISTORY_ALLOWLIST.items()
            for needle, _ in entries
            if not _quote_is_needed(relpath, needle)
        ]
        assert stale == [], f"_HISTORY_ALLOWLIST entries without a matching hit: {stale}"
