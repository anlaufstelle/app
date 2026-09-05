"""axe-core-Injection und Auswertung fuer das WCAG-Gate (Refs #1059).

Warum eine eigene Datei und kein Node-Stack: Das Ticket verlangt ausdruecklich
keine neuen Node-Pakete und keine Aenderung an ``requirements-dev.txt``.
axe-core liegt deshalb als vendorte Datei unter
``src/static/js/vendor/axe.min.js`` (THIRDPARTY-LICENSES.md, Abschnitt
„Vendored JavaScript") und wird hier in die laufende Playwright-Seite geladen.

**Injection-Weg ist CSP-diktiert.** ``settings.e2e`` erbt ueber ``dev.py`` die
Produktions-CSP ``script-src 'self'`` (``settings/base.py:618``); kein
E2E-Kontext setzt ``bypass_csp``. Ein ``page.add_script_tag(path=...)`` waere
ein Inline-Script und wuerde geblockt. Genutzt wird deshalb die
**same-origin-URL** ``{base_url}/static/js/vendor/axe.min.js`` — im Dev-/E2E-
Kontext bedient WhiteNoise Statics ueber die Finder
(``WHITENOISE_USE_FINDERS = True``, ``dev.py:57-58``), der ungehashte Pfad
existiert also, obwohl die Storage grundsaetzlich eine Manifest-Storage ist.

**Kein ``'unsafe-eval'`` noetig.** Die vendorte Datei enthaelt 0x ``eval(`` und
0x ``document.write``; die sechs ``new Function``-Stellen liegen ausschliesslich
in Pfaden, die ein reiner ``axe.run()`` nicht betritt (doT-Kompilierung nur bei
``axe.configure({locale})``, Metadata-Function-Deserialisierung nur bei
Custom-Checks, ein Arity-Polyfill-Fallback fuer Engines ohne konfigurierbares
``Function.length``). Genau deshalb wird hier **keine** Locale geladen und
**nicht** konfiguriert — die englischen Default-Messages sind im Bundle bereits
kompilierte Funktionen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - nur fuer Typpruefer
    from playwright.sync_api import Page

# Pfad der vendorten Datei, relativ zur ``base_url`` des E2E-Servers.
AXE_SCRIPT_PATH = "/static/js/vendor/axe.min.js"

# WCAG 2.1 AA = die vier Tag-Familien. Bewusst OHNE ``best-practice`` und ohne
# ``wcag2aaa``: das Ticket zielt auf AA, und Best-Practice-Regeln sind
# Empfehlungen ohne normativen Rang — sie im Gate zu fuehren wuerde die
# Baseline mit Nicht-Verstoessen fluten.
AXE_TAGS = ("wcag2a", "wcag2aa", "wcag21a", "wcag21aa")

# Nur diese beiden Stufen sind Gate-relevant (Ticket: „Critical-/Serious-
# Verstoesse fuehren sofort zum Fehlschlag"). ``moderate``/``minor`` landen
# vollstaendig im optionalen Report-Artefakt fuer den Audit-Fan-out.
BLOCKING_IMPACTS = ("critical", "serious")

# HTML-Ausschnitte je Knoten kappen — ein ungekappter Snippet einer Tabellen-
# zeile sprengt sowohl die Assertion-Meldung als auch das Report-JSON.
_SNIPPET_MAX = 200

_AXE_READY = "() => typeof window.axe !== 'undefined'"

# ``axe.run`` bekommt ein async-Praedikat NICHT ueber ``wait_for_function``
# (siehe ``_helpers.wait_until``), sondern ueber ``page.evaluate`` — das
# ``await``et Promises korrekt.
_AXE_RUN = """
async ([ctx, tags]) => {
    const result = await window.axe.run(ctx || document, {
        runOnly: {type: 'tag', values: tags},
        resultTypes: ['violations'],
    });
    return result.violations;
}
"""


@dataclass(frozen=True)
class AxeResult:
    """Ergebnis eines ``axe.run()`` auf genau einer Seite."""

    url: str
    violations: list[dict[str, Any]] = field(default_factory=list)

    def blocking(self) -> list[dict[str, Any]]:
        """Nur ``critical``/``serious`` — die Gate-relevanten Verstoesse."""
        return serious_or_critical(self.violations)

    def blocking_counts(self) -> dict[str, int]:
        """``{rule_id: anzahl_knoten}`` ueber die Gate-relevanten Verstoesse."""
        counts: dict[str, int] = {}
        for violation in self.blocking():
            counts[violation["rule"]] = counts.get(violation["rule"], 0) + len(violation["nodes"])
        return counts

    def counts_by_impact(self) -> dict[str, int]:
        """``{impact: anzahl_knoten}`` ueber ALLE Impact-Stufen."""
        counts: dict[str, int] = {}
        for violation in self.violations:
            key = violation["impact"] or "unknown"
            counts[key] = counts.get(key, 0) + len(violation["nodes"])
        return counts


def _normalise(violation: dict[str, Any]) -> dict[str, Any]:
    """axe-Rohbefund auf das im Ticket vereinbarte, schlanke Format bringen."""
    return {
        "rule": violation.get("id", ""),
        "impact": violation.get("impact"),
        "help": violation.get("help", ""),
        "help_url": violation.get("helpUrl", ""),
        "nodes": [
            {
                "target": node.get("target", []),
                "html_snippet": (node.get("html") or "")[:_SNIPPET_MAX],
            }
            for node in violation.get("nodes", [])
        ],
    }


def run_axe(
    page: Page,
    base_url: str,
    *,
    tags: tuple[str, ...] | list[str] = AXE_TAGS,
    exclude: list[str] | None = None,
) -> AxeResult:
    """axe-core injizieren, gegen die aktuelle Seite laufen lassen, Befunde normalisieren.

    ``exclude`` nimmt CSS-Selektoren, die aus dem Scan fallen. Es wird derzeit
    nirgends gebraucht — ``/admin-mgmt/`` (Fremd-UI aus Django/Unfold) steht
    gar nicht erst in der Seiten-Matrix.

    Rueckgabe: ``AxeResult`` mit ALLEN Violations jeder Impact-Stufe. Die
    Filterung auf ``critical``/``serious`` passiert bewusst erst beim Gate,
    damit der Report-Export (``AXE_REPORT_PATH``) den vollen Befund traegt.
    """
    page.add_script_tag(url=f"{base_url}{AXE_SCRIPT_PATH}")
    # Synchrones Praedikat — hier ist ``wait_for_function`` korrekt (die Falle
    # aus ``_helpers.wait_until`` betrifft ausschliesslich async-Callbacks).
    page.wait_for_function(_AXE_READY, timeout=15000)

    context: Any = {"exclude": [[selector] for selector in exclude]} if exclude else None
    raw = page.evaluate(_AXE_RUN, [context, list(tags)])
    return AxeResult(url=page.url, violations=[_normalise(v) for v in raw])


def serious_or_critical(violations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter auf die Gate-relevanten Impact-Stufen."""
    return [v for v in violations if v.get("impact") in BLOCKING_IMPACTS]


def format_violations(violations: list[dict[str, Any]], *, max_nodes: int = 3) -> str:
    """Lesbarer Assertion-Text: eine Zeile je Regel, darunter Beispielknoten."""
    if not violations:
        return "(keine)"
    lines: list[str] = []
    for violation in violations:
        nodes = violation["nodes"]
        lines.append(
            f"- {violation['rule']} [{violation['impact']}] x{len(nodes)} — "
            f"{violation['help']} ({violation['help_url']})"
        )
        for node in nodes[:max_nodes]:
            target = ", ".join(str(t) for t in node["target"])
            lines.append(f"    * {target}\n      {node['html_snippet']}")
        if len(nodes) > max_nodes:
            lines.append(f"    * … und {len(nodes) - max_nodes} weitere Knoten")
    return "\n".join(lines)


def load_baseline(path: Path) -> dict[str, dict[str, int]]:
    """Bestandsschutz-Baseline lesen; eine fehlende Datei ist eine leere Baseline."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def record_report(report_path: Path | None, page_key: str, result: AxeResult) -> None:
    """Alle Violations einer Seite ins Report-Artefakt schreiben (Merge-on-write).

    Nur aktiv, wenn ``AXE_REPORT_PATH`` gesetzt ist. Das Artefakt ist die
    Grundlage fuer den Audit-Fan-out (Lane B1) UND die Quelle, aus der die
    Baseline in Commit 3 gepinnt wird — deshalb traegt es JEDE Impact-Stufe,
    nicht nur die Gate-relevanten.

    Merge statt Sammelschreiben am Sessionende: ein abgebrochener Lauf soll die
    bis dahin gescannten Seiten trotzdem im Artefakt haben. Der a11y-Lauf ist
    seriell (Marker-Dokumentation in ``pyproject.toml``), deshalb reicht
    read-modify-write ohne Lock.
    """
    if report_path is None:
        return
    data: dict[str, Any] = {}
    if report_path.exists():
        content = report_path.read_text(encoding="utf-8").strip()
        if content:
            data = json.loads(content)
    data[page_key] = {"url": result.url, "violations": result.violations}
    report_path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def assert_within_baseline(page_key: str, result: AxeResult, baseline: dict[str, dict[str, int]]) -> None:
    """Das eigentliche Gate (Refs #1059, Entscheid R-B0-4).

    Rot, wenn

    a) eine Regel mit ``critical``/``serious`` auftritt, die fuer diese Seite
       NICHT in der Baseline steht, oder oefter auftritt als dort erlaubt, ODER
    b) ein Baseline-Eintrag dieser Seite gar nicht mehr auftritt.

    (b) ist kein Schikane-Check: eine stehengebliebene Baseline-Zeile ist ein
    stiller Freibrief fuer eine Regel, die laengst wieder verletzt werden
    koennte. Die Baseline soll auf ``{}`` schrumpfen — sie wird abgebaut, nicht
    gepflegt. Die Pruefung laeuft bewusst PRO SEITE, damit ein gefilterter Lauf
    (``-k``) nicht die Eintraege der nicht gelaufenen Seiten als stale meldet.
    """
    allowed = baseline.get(page_key, {})
    found = result.blocking_counts()

    regressions = [
        f"{rule}: {count} Knoten, Baseline erlaubt {allowed.get(rule, 0)}"
        for rule, count in sorted(found.items())
        if count > allowed.get(rule, 0)
    ]
    stale = [f"{rule} (Baseline: {count})" for rule, count in sorted(allowed.items()) if rule not in found]

    problems: list[str] = []
    if regressions:
        problems.append(
            "NEUE oder GEWACHSENE critical/serious-Verstoesse:\n  " + "\n  ".join(regressions),
        )
    if stale:
        problems.append(
            "VERALTETE Baseline-Eintraege — der Verstoss tritt nicht mehr auf, der Eintrag in\n"
            f"src/tests/e2e/a11y_baseline.json unter '{page_key}' ist ersatzlos zu ENTFERNEN\n"
            "(die Baseline wird abgebaut, nicht fortgeschrieben):\n  " + "\n  ".join(stale),
        )

    assert not problems, (
        f"axe-core-Gate auf '{page_key}' ({result.url}) — Refs #1059\n\n"
        + "\n\n".join(problems)
        + "\n\nAlle gefundenen Verstoesse dieser Seite (jede Impact-Stufe):\n"
        + format_violations(result.violations)
    )
