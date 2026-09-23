"""Vokabular-Guard: kein Arbeitsablauf-Jargon in ausgelieferten Dateien (Refs #2349).

Kommentare und Docstrings sollen begruenden, WARUM etwas so gebaut ist —
nicht WER es anfassen durfte. Woerter, die nur innerhalb eines internen
Arbeitsablaufs Bedeutung haben, sind daher in jeder ausgelieferten Datei
fehl am Platz: sie sind fuer Leser des Repos ohne Kontext unverstaendlich
und verraten organisatorische Details, die nicht Teil des Produkts sind.

Einzige Ausnahme ist der Fachbegriff des Offline-Sync-Moduls
(``sync-orchestrator.js`` / ``window.syncOrchestrator`` / „Sync-Orchestrator")
— ein echter Produktbestandteil, kein Arbeitsablauf-Verweis.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]

_PROCESS_WORDS = (
    "lane",
    "standardblock",
    "briefing",
    "besitzliste",
    "tabu",
    "orchestrator",
    "ruling",
    "fix-runde",
)

# Erfasst auch Flexionen/Zusammensetzungen ueber den Wortstamm hinaus — die
# Wortliste selbst ist die einzige Stelle, an der die gebannten Woerter im
# Klartext stehen duerfen. Ausnahme "tabu(?!l)": schliesst voellig andere
# Woerter mit zufaellig gleichem Wortanfang aus (z. B. "Tabulator",
# "TabularInline", "tabular-nums" — nichts davon hat mit dem gebannten Begriff
# zu tun).
_WORD_RE = re.compile(
    r"\btabu(?!l)\w*|\b(?:" + "|".join(re.escape(word) for word in _PROCESS_WORDS if word != "tabu") + r")\w*",
    re.IGNORECASE,
)

# Der Fachbegriff des Sync-Moduls: "sync-orchestrator" (mit Bindestrich, in
# jeder Gross-/Kleinschreibung) wird VOR der Wortsuche aus der Zeile maskiert,
# nicht die ganze Zeile uebersprungen — ein echter Treffer daneben soll
# weiterhin auffallen.
_SYNC_ORCHESTRATOR_RE = re.compile(r"sync-orchestrator", re.IGNORECASE)
_SYNC_ORCHESTRATOR_MASK = "SyncOrchestratorOK"

_DEFAULT_EXTENSIONS = (".py", ".js", ".mjs", ".html", ".txt", ".md", ".json", ".yml", ".yaml", ".css")
_DEFAULT_EXCLUDED_DIR_NAMES = frozenset({"staticfiles", "media", "__pycache__", "node_modules"})

# Der Scanner selbst enthaelt die Wortliste (fuer die Erkennung) und
# Test-Fixture-Zeichenketten, die die Erkennung an genau diesen Woertern
# ueben — beides zwingend noetig, damit der Scanner sich selbst pruefen
# laesst. Die eigene Datei ist deshalb vom Real-Baum-Lauf ausgenommen.
_SELF_RELPATH = "tests/test_process_vocabulary_guard.py"

# Dateien ausserhalb des Aenderungsumfangs dieses Tickets: sie gehoeren zu
# Paketen, die zum Zeitpunkt dieses Sweeps noch nicht integriert waren.
# Muster werden per ``PurePosixPath.match`` gegen den repo-relativen Pfad
# gepruft; sie werden mit dem naechsten Integrationsschritt bereinigt.
_PENDING_FILES = (
    "src/anlaufstelle/settings/base.py",
    "src/core/views/health.py",
    "src/core/checks.py",
    "src/tests/test_ops_*.py",
    "src/tests/test_security_headers_single_source.py",
    "src/tests/test_system_checks.py",
    "src/tests/test_initial_setup_fresh_install*.py",
    "src/tests/test_compose*.py",
    # Zusaetzlich beim Bau dieses Guards gefundene Wortformen (Mehrzahl/
    # zusammengesetzte Form), die ausserhalb des fuer dieses Ticket
    # erhobenen Bestands lagen — werden in einem eigenen Durchgang
    # bereinigt.
    "src/core/services/facility_bootstrap.py",
    "src/tests/test_mutation_pins_097_events_deletion.py",
    "src/tests/test_mutation_pins_097_tally_undo.py",
    "src/tests/test_mutation_pins_099_forms_config_b.py",
    "src/tests/test_mutation_pins_099_forms_config_c.py",
    "src/tests/test_mutation_pins_099_handover.py",
)


class Hit(NamedTuple):
    relpath: str
    lineno: int
    word: str
    line: str


def _is_pending(relpath: str) -> bool:
    posix = Path(relpath).as_posix()
    return any(Path(posix).match(pattern) for pattern in _PENDING_FILES)


def scan_process_vocabulary(
    root: Path,
    *,
    extensions: tuple[str, ...] = _DEFAULT_EXTENSIONS,
    excluded_dir_names: frozenset[str] = _DEFAULT_EXCLUDED_DIR_NAMES,
) -> list[Hit]:
    """Scannt ``root`` rekursiv nach Arbeitsablauf-Jargon.

    Reine Baumsuche ohne Seiteneffekte — Aufrufer entscheiden, ob/wie
    ``_PENDING_FILES`` beruecksichtigt wird (Proben in Tests wollen das
    i. d. R. NICHT, der echte Lauf gegen ``src/`` schon).
    """
    hits: list[Hit] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.suffix not in extensions:
            continue
        relparts = path.relative_to(root).parts
        if any(part in excluded_dir_names for part in relparts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        relpath = path.relative_to(root).as_posix()
        for lineno, line in enumerate(text.splitlines(), start=1):
            masked = _SYNC_ORCHESTRATOR_RE.sub(_SYNC_ORCHESTRATOR_MASK, line)
            for match in _WORD_RE.finditer(masked):
                hits.append(Hit(relpath, lineno, match.group(0), line))
    return hits


class TestScanFunctionAgainstFixtures:
    """Die Scan-Funktion selbst — ausschliesslich gegen ``tmp_path``-Proben,
    nie gegen den echten Baum (Scanner-Tests duerfen keine Proben in den
    echten Baum legen)."""

    def test_finds_case_insensitive_hit_with_flexion(self, tmp_path: Path) -> None:
        (tmp_path / "sample.py").write_text(
            "# Nur STANDARDBLOCKS duerfen das aendern.\n",
            encoding="utf-8",
        )
        hits = scan_process_vocabulary(tmp_path)
        assert [h.word for h in hits] == ["STANDARDBLOCKS"]

    def test_finds_each_word_in_the_list(self, tmp_path: Path) -> None:
        (tmp_path / "sample.py").write_text(
            "\n".join(
                [
                    "# Lane",
                    "# Standardblock",
                    "# Briefing",
                    "# Besitzliste",
                    "# TABU",
                    "# Orchestrator",
                    "# Ruling",
                    "# Fix-Runde",
                ]
            ),
            encoding="utf-8",
        )
        hits = scan_process_vocabulary(tmp_path)
        assert len(hits) == 8

    def test_ignores_sync_orchestrator_compound_forms(self, tmp_path: Path) -> None:
        (tmp_path / "sample.js").write_text(
            "\n".join(
                [
                    "// window.syncOrchestrator laedt das Sync-Modul.",
                    "// siehe sync-orchestrator.js fuer Details.",
                    "// Der SYNC-ORCHESTRATOR ist optional.",
                ]
            ),
            encoding="utf-8",
        )
        hits = scan_process_vocabulary(tmp_path)
        assert hits == []

    def test_bare_orchestrator_next_to_sync_orchestrator_still_flagged(self, tmp_path: Path) -> None:
        (tmp_path / "sample.js").write_text(
            "// Ohne Sync-Orchestrator faellt der Orchestrator auf den Fallback zurueck.\n",
            encoding="utf-8",
        )
        hits = scan_process_vocabulary(tmp_path)
        assert [h.word for h in hits] == ["Orchestrator"]

    def test_ignores_excluded_directories(self, tmp_path: Path) -> None:
        for dirname in ("staticfiles", "media", "__pycache__", "node_modules"):
            sub = tmp_path / dirname
            sub.mkdir()
            (sub / "sample.py").write_text("# TABU\n", encoding="utf-8")
        (tmp_path / "src_sample.py").write_text("# TABU\n", encoding="utf-8")
        hits = scan_process_vocabulary(tmp_path)
        assert [h.relpath for h in hits] == ["src_sample.py"]

    def test_only_scans_configured_extensions(self, tmp_path: Path) -> None:
        (tmp_path / "sample.png").write_bytes(b"TABU")
        (tmp_path / "sample.py").write_text("# TABU\n", encoding="utf-8")
        hits = scan_process_vocabulary(tmp_path)
        assert [h.relpath for h in hits] == ["sample.py"]

    def test_binary_files_are_skipped_without_error(self, tmp_path: Path) -> None:
        (tmp_path / "sample.py").write_bytes(b"\xff\xfe# TABU garbage \x00\x01")
        scan_process_vocabulary(tmp_path)  # nur: darf nicht werfen


class TestRealTreeHasNoUnresolvedProcessVocabulary:
    """Der eigentliche Guard: der ausgelieferte Baum bleibt sauber, damit
    der Bestand aus Ticket 2349 nicht wieder waechst."""

    # deploy/ hat beim Bau dieses Guards 0 Treffer gezeigt und ist deshalb
    # zusaetzlich zu src/ im Scope; docs/, dev-ops/ und scripts/ haben noch
    # bestehende Treffer und bleiben ausserhalb (siehe Bericht).
    _SCAN_ROOTS = ("src", "deploy")

    def test_no_unresolved_process_vocabulary_remains(self) -> None:
        unresolved: list[Hit] = []
        for root_name in self._SCAN_ROOTS:
            root = REPO_ROOT / root_name
            if not root.is_dir():
                continue
            for hit in scan_process_vocabulary(root):
                if root_name == "src" and hit.relpath == _SELF_RELPATH:
                    continue
                relpath = f"{root_name}/{hit.relpath}"
                if _is_pending(relpath):
                    continue
                unresolved.append(hit._replace(relpath=relpath))
        assert unresolved == [], "Arbeitsablauf-Jargon in ausgelieferten Dateien gefunden (Refs #2349):\n" + "\n".join(
            f"{h.relpath}:{h.lineno} [{h.word}] {h.line}" for h in unresolved
        )

    def test_pending_files_patterns_match_something_on_disk(self) -> None:
        """Verwaiste ``_PENDING_FILES``-Muster (Pfad existiert nicht mehr)
        sollen auffallen, statt stillschweigend nutzlos zu bleiben."""
        stale = [pattern for pattern in _PENDING_FILES if not any(REPO_ROOT.glob(pattern))]
        assert stale == [], f"verwaiste _PENDING_FILES-Muster (nichts existiert mehr): {stale}"
