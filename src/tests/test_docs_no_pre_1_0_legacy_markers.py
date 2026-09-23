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


# Begruendete Ausnahmen (Datei:Kontext) — jede Zeile einzeln begruendet, kein
# Pauschal-Opt-out. Ausschliesslich fuer Treffer, die KEINE Vor-1.0-
# Migrations-/Bestandsinstallations-Aussage sind, sondern (a) Code-
# Identifier, die im Text zitiert werden, (b) evergreen-technisches
# Vokabular, das mit der Anlaufstelle-Version nichts zu tun hat, oder (c)
# einen Anker, dessen Umbenennung eine ausserhalb des Sweep-Scopes liegende
# Datei gebrochen haette. Format: relpath -> [(Teilstring der Zeile, Begruendung)].
_ALLOWLIST: dict[str, list[tuple[str, str]]] = {
    "docs/ops-runbook.md": [
        (
            "### 9.6 Rollout der Runtime-/Owner-Rollentrennung auf Bestandsinstallationen (A1.4)",
            "Ueberschrift bewusst unveraendert gelassen: der Anker "
            "'#96-rollout-der-runtime-owner-rollentrennung-auf-bestandsinstallationen-a14' "
            "wird von docs/threat-model.md sowie von der dev-only "
            "Dev-Deployment-Dokumentation referenziert; letztere ist fuer "
            "diesen Sweep nicht im Scope, eine Umbenennung haette dort einen toten "
            "Anker hinterlassen. Der Abschnitt beschreibt zudem eine "
            "Rollentrennungs-Reparatur, die auf JEDER fehlkonfigurierten "
            "Installation auftreten kann (z. B. nach einem Dump/Restore ohne "
            "Init-Script) — nicht ausschliesslich auf Vor-1.0-Bestaenden. "
            "Refs #2205.",
        ),
        (
            "[§ 9.6](#96-rollout-der-runtime-owner-rollentrennung-auf-bestandsinstallationen-a14)",
            "Selbstreferenz auf die oben begruendete unveraenderte Ueberschrift. Refs #2205.",
        ),
    ],
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
