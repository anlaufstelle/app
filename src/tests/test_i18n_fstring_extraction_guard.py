"""Guard: keine Uebersetzungsaufrufe INNERHALB von f-String-Ausdruecken (Refs #1933, #1900).

Hintergrund — belegt, nicht vermutet: ``xgettext`` extrahiert ``gettext(...)``
aus einem f-String-Ausdruck erst ab neueren Versionen. Gemessen am selben
Quellbaum:

* lokal ``xgettext (GNU gettext-tools) 0.23.1`` -> Aufruf wird extrahiert
* CI ``ubuntu-24.04`` installiert ``gettext 0.21-14ubuntu2`` -> Aufruf wird
  NICHT extrahiert; der f-String zaehlt als ein einziges Stringliteral

Folge: ``scripts/i18n_catalog.py`` (= ``make i18n-check``) urteilt je nach
Arbeitsplatz anders. Mit gettext 0.21 meldete es genau eine entfernte msgid
(``"Bericht"`` — deren einzige Referenz ``core/reporting/export.py`` in einem
f-String stand) und drei zusaetzliche Referenzaenderungen; mit 0.23.1 war
derselbe Baum sauber.

Gefaehrlich ist nicht nur der rote Check, sondern ``make i18n-update``: es
uebernimmt die Extraktion als Wahrheit und laesst Eintraege weg, die dort
fehlen. Auf einem Rechner mit alter gettext-Version haette der naechste
Katalog-Lauf ``"Bericht"`` samt englischer Uebersetzung ``"Report"`` still
aus dem Katalog geloescht — exakt der Verlustfall, gegen den #1900/#1933
angetreten sind.

Deshalb hier ein toolchain-unabhaengiger Guard: Uebersetzungsaufrufe stehen
vor dem f-String, nicht darin. Der Test braucht selbst KEIN gettext und
laeuft daher auch dort, wo die Katalog-Tests sich wegskippen.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

SRC = Path(__file__).resolve().parent.parent

# Alle Namen, unter denen im Projekt uebersetzt wird — inklusive des ueblichen
# Alias ``_`` (``gettext_lazy as _``). Bewusst namensbasiert: ``xgettext``
# erkennt Uebersetzungsaufrufe ebenfalls nur am Aufrufernamen.
TRANSLATION_CALLABLES = frozenset(
    {
        "_",
        "gettext",
        "gettext_lazy",
        "gettext_noop",
        "ngettext",
        "ngettext_lazy",
        "npgettext",
        "npgettext_lazy",
        "pgettext",
        "pgettext_lazy",
    }
)

# ``.venv``/Build-Artefakte liegen nicht im Git-Baum, koennen aber im
# Arbeitsverzeichnis auftauchen — der Guard soll nur Projektquellen pruefen.
IGNORED_DIRECTORIES = frozenset({".venv", "__pycache__", "migrations", "media", "node_modules", "staticfiles"})


def _called_name(node: ast.Call) -> str | None:
    """Der Name, unter dem aufgerufen wird — ``x()`` wie auch ``mod.x()``."""

    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _python_sources() -> list[Path]:
    return sorted(
        path for path in SRC.rglob("*.py") if not IGNORED_DIRECTORIES.intersection(path.relative_to(SRC).parts)
    )


def _translation_calls_in_fstrings(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and _called_name(inner) in TRANSLATION_CALLABLES:
                findings.append(f"{path.relative_to(SRC.parent)}:{inner.lineno}: {_called_name(inner)}(...)")
    return findings


def test_no_translation_call_inside_an_fstring_expression():
    findings = [finding for path in _python_sources() for finding in _translation_calls_in_fstrings(path)]

    assert not findings, (
        "Uebersetzungsaufruf in einem f-String-Ausdruck — xgettext 0.21 (CI, ubuntu-24.04) sieht ihn nicht, "
        "xgettext 0.23.1 (lokal) schon; 'make i18n-update' wuerde die msgid dort still aus dem Katalog "
        "loeschen. Aufruf vor den f-String ziehen und die Variable einsetzen:\n  " + "\n  ".join(findings)
    )
