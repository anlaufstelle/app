#!/usr/bin/env python3
"""Guard: kein ausgelieferter Test darf hart von Pfaden abhängen, die der
Public-/Stage-Release-Snapshot ENTFERNT.

Hintergrund (Refs #1137, #1051, #1047)
======================================
Tests unter ``src/tests/`` werden mit ausgeliefert (sie liegen im Public-
Snapshot). Der Release-Build (``dev-ops/release/build-release.sh``) strippt
dagegen alle dev-only Pfade (``dev-ops/``, ``scripts/dev/``, ``docs/dev/``,
``docs/ai/``, ``docs/archive/``, ``CLAUDE.md``, …). Ein Test, der eine solche
Datei *liest* (z.B. ``Path("dev-ops/deploy/run-as-admin.sh").read_text()``),
läuft im Dev-Tree grün — alle lokalen Gates auch — und fällt **erst auf der
public Stage-CI** mit ``FileNotFoundError`` um, weil dort kein ``dev-ops/``
mehr existiert. Dieselbe Fehlklasse wie die pip-audit-Lücke aus #1051: ein
Check greift erst am gestrippten Stage-Artefakt.

Dieser Guard fängt die Klasse **vor** dem Stage-Push im frühen ``make ci``-
Dev-Gate ab.

Single Source der Exclude-Liste
===============================
Die ausgeschlossenen Prefixe werden NICHT dupliziert, sondern zur Laufzeit aus
der ``FORBIDDEN``-Regex in ``dev-ops/release/verify-leak.sh`` geparst (genau die
Liste, gegen die der Release-Build seine Public-History prüft). Driftet die
Release-Exclude-Liste, driftet dieser Guard automatisch mit. Fehlt die Datei
(z.B. weil der Guard versehentlich im gestrippten Tree läuft, wo ``dev-ops/``
weg ist), endet der Guard mit Exit 0 + Hinweis — dort ist er per Definition
moot, und er soll Stage-CI nicht selbst rot machen.

Was zählt als „verbotene Referenz"
==================================
Gescannt wird ``src/tests/**/*.py``. Für jede Datei werden über das ``ast``-
Modul **Code-String-Literale** von **Docstrings** getrennt. Nur ein
ausgeschlossener Prefix, der in einem *Code*-String-Literal auftaucht (also
potenziell an einen echten Datei-/Pfadzugriff geht), ist relevant — Docstrings
und ``#``-Kommentare erscheinen gar nicht als AST-String-Konstanten und können
ohnehin kein ``FileNotFoundError`` auslösen; sie werden ignoriert.

Skip-Gate-Semantik (pragmatisch, Datei-Ebene)
=============================================
Eine Code-Referenz auf einen ausgeschlossenen Prefix ``P`` ist **erlaubt**
(gegated), wenn dieselbe Datei mindestens EIN Skip-Gate enthält, dessen
Begründungs-/Bedingungstext ``P`` nennt. Als Skip-Gate gilt:

  * ``pytest.skip("… P …")``
  * ``pytest.importorskip("… P …")``
  * ``@pytest.mark.skip(reason="… P …")`` / ``pytest.mark.skipif(…, reason="… P …")``
  * ein ``.exists()``-Guard in einer Zeile, die ``P`` nennt
    (Muster ``if not <…>.exists(): pytest.skip(…)`` aus #1047)

Die Datei-Ebene ist bewusst grob gewählt (KISS): der reale Fall aus #1047
referenziert ``dev-ops/`` und skippt mit Begründung ``dev-ops/`` — Block-genaues
Matching brächte keinen Mehrwert, nur Brüchigkeit. Der Prefix im Skip-Reason
ist die Selbst-Dokumentation, *warum* der Test im Public-Tree übersprungen wird.

Stdlib-only. Aufruf::

    python scripts/verify_release_test_guard.py
    python scripts/verify_release_test_guard.py --tests-dir src/tests \
        --leak-script dev-ops/release/verify-leak.sh

Exit-Codes::

    0  sauber (oder Exclude-Quelle nicht vorhanden → moot)
    1  ungegatete Referenz auf ausgeschlossenen Pfad gefunden
    2  Konfigurations-/Parse-Fehler (z.B. FORBIDDEN-Regex nicht lesbar)
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TESTS_DIR = ROOT / "src" / "tests"
DEFAULT_LEAK_SCRIPT = ROOT / "dev-ops" / "release" / "verify-leak.sh"

# Zeile der Form  FORBIDDEN='^( … )'  in verify-leak.sh.
_FORBIDDEN_LINE = re.compile(r"""^\s*FORBIDDEN=(['"])(?P<re>.*)\1\s*$""")

# Skip-Gate-Marker, deren Begründungstext einen Prefix nennen kann.
_SKIP_CALL = re.compile(r"pytest\.(?:skip|importorskip)\b")
_SKIP_MARK = re.compile(r"pytest\.mark\.skip(?:if)?\b")
_EXISTS_GUARD = re.compile(r"\.exists\s*\(\s*\)")


# ── Exclude-Liste (Single Source: verify-leak.sh) ──────────────────────────
def parse_excluded_prefixes(leak_script: Path) -> list[str]:
    """Ausgeschlossene Pfad-Prefixe aus der ``FORBIDDEN``-Regex extrahieren.

    Die Regex ist eine ``^(a|b|c|…)``-Alternation aus ``verify-leak.sh``. Wir
    zerlegen sie in ihre Top-Level-Alternativen und übersetzen jede in einen
    oder mehrere literale Pfad-/Prefix-Kerne (Regex-Anker/Escapes entfernt).

    Wichtig: Eine eingebettete Gruppe ``(a|b|c)`` wird **expandiert**, nicht
    abgeschnitten. Sonst würde z.B. ``deploy/(backup|…)\\.sh$`` zum viel zu
    breiten Prefix ``deploy/`` verallgemeinert — ``deploy/`` selbst bleibt aber
    public (nur einzelne ``deploy/*.sh`` werden gestrippt), und ein
    legitimer Pfad wie ``dev-ops/deploy/run-as-admin.sh`` würde fälschlich
    matchen. Beispiele:

        ``dev-ops/``                              -> ``dev-ops/``
        ``scripts/(run_mutmut|check_perf_budgets)\\.``
                                                  -> ``scripts/run_mutmut.``,
                                                     ``scripts/check_perf_budgets.``
        ``deploy/(backup|bootstrap)\\.sh$``       -> ``deploy/backup.sh``,
                                                     ``deploy/bootstrap.sh``
        ``CLAUDE\\.md$``                          -> ``CLAUDE.md``
        ``\\.github/workflows/dev-image\\.yml$``  -> ``.github/workflows/dev-image.yml``

    Single Source: ändert sich die Liste in verify-leak.sh, ändert sich diese
    Rückgabe automatisch mit.
    """
    raw_line = ""
    for line in leak_script.read_text(encoding="utf-8").splitlines():
        match = _FORBIDDEN_LINE.match(line)
        if match:
            raw_line = match.group("re")
            break
    if not raw_line:
        raise ValueError(f"Keine FORBIDDEN='…'-Zeile in {leak_script} gefunden — Format geändert?")

    body = raw_line
    if body.startswith("^"):
        body = body[1:]
    if body.startswith("(") and body.endswith(")"):
        body = body[1:-1]

    prefixes: list[str] = []
    for alt in _split_top_level_alternatives(body):
        for prefix in _alternative_to_prefixes(alt):
            if prefix and prefix not in prefixes:
                prefixes.append(prefix)
    if not prefixes:
        raise ValueError(f"FORBIDDEN-Regex in {leak_script} ergab keine Prefixe — Parser anpassen.")
    return prefixes


def _split_top_level_alternatives(body: str) -> list[str]:
    """``a|b(c|d)|e`` an Top-Level-``|`` splitten (Gruppen-Pipes ignorieren)."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    i = 0
    while i < len(body):
        char = body[i]
        if char == "\\" and i + 1 < len(body):
            current.append(char)
            current.append(body[i + 1])
            i += 2
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "|" and depth == 0:
            parts.append("".join(current))
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    parts.append("".join(current))
    return parts


def _alternative_to_prefixes(alt: str) -> list[str]:
    """Eine Regex-Alternative in ihre literalen Pfad-/Prefix-Kerne übersetzen.

    Eine *einzelne* eingebettete Gruppe ``pre(a|b|c)post`` wird zu
    ``pre+a+post``, ``pre+b+post``, ``pre+c+post`` expandiert; ohne Gruppe
    bleibt es ein einzelner Kern. Danach Endanker ``$`` und Backslash-Escapes
    entfernen, sodass ein literaler Substring übrig bleibt, gegen den sich
    Code-String-Literale prüfen lassen. Mehr als eine Gruppe pro Alternative
    kommt in der FORBIDDEN-Regex nicht vor; ein solcher (unerwarteter) Fall
    fällt konservativ auf „nur den Prefix bis zur Gruppe" zurück.
    """
    open_idx = alt.find("(")
    if open_idx == -1:
        return [_literalize(alt)]

    close_idx = alt.find(")", open_idx)
    if close_idx == -1 or "(" in alt[open_idx + 1 : close_idx]:
        # Unerwartete/verschachtelte Gruppe: konservativ bis zur Gruppe kappen.
        return [_literalize(alt[:open_idx])]

    pre = alt[:open_idx]
    post = alt[close_idx + 1 :]
    options = alt[open_idx + 1 : close_idx].split("|")
    return [_literalize(pre + opt + post) for opt in options]


def _literalize(fragment: str) -> str:
    """Regex-Fragment → literaler Pfad-Kern: ``$`` und Escapes entfernen."""
    return fragment.replace("\\", "").rstrip("$").strip()


# ── AST-/Token-Analyse einer Test-Datei ────────────────────────────────────
def _docstring_node_ids(tree: ast.AST) -> set[int]:
    """``id()`` aller String-Konstanten, die Docstrings sind (Modul/Klasse/Func).

    Docstrings beschreiben nur — sie lösen keinen Dateizugriff aus und sollen
    den Guard nicht triggern (z.B. „Schreibt den Report nach docs/archive/…"
    im Modul-Docstring von test_authz_audit.py)."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            ids.add(id(first.value))
    return ids


def _code_string_literals(source: str, tree: ast.AST) -> list[str]:
    """Alle String-Literale aus *echtem Code* — ohne Docstrings."""
    skip = _docstring_node_ids(tree)
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            out.append(node.value)
    return out


def _gated_prefixes(source: str, prefixes: list[str]) -> set[str]:
    """Prefixe, für die die Datei ein Skip-Gate mit passendem Reason-Text hat.

    Datei-Ebene: jede Zeile, die einen Skip-Marker ODER einen ``.exists()``-
    Guard enthält, wird auf erwähnte Prefixe geprüft. Ein dort genannter Prefix
    gilt für die gesamte Datei als gegated."""
    gated: set[str] = set()
    for line in source.splitlines():
        is_gate = bool(_SKIP_CALL.search(line) or _SKIP_MARK.search(line) or _EXISTS_GUARD.search(line))
        if not is_gate:
            continue
        for prefix in prefixes:
            if prefix in line:
                gated.add(prefix)
    return gated


def scan_file(path: Path, prefixes: list[str]) -> list[tuple[str, str]]:
    """Ungegatete Code-Referenzen auf ausgeschlossene Prefixe in ``path``.

    Rückgabe: Liste ``(prefix, fundstelle)``. Leere Liste = sauber."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:  # defensiv — soll im realen Repo nie passieren
        return [("<syntax>", f"{path}: nicht parsebar ({exc})")]

    code_strings = _code_string_literals(source, tree)
    if not code_strings:
        return []

    referenced: dict[str, str] = {}
    for literal in code_strings:
        for prefix in prefixes:
            if prefix in literal and prefix not in referenced:
                referenced[prefix] = literal
    if not referenced:
        return []

    gated = _gated_prefixes(source, prefixes)
    return [(prefix, literal) for prefix, literal in referenced.items() if prefix not in gated]


def iter_test_files(tests_dir: Path) -> list[Path]:
    return sorted(p for p in tests_dir.rglob("*.py") if p.is_file())


# ── CLI ────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--tests-dir",
        type=Path,
        default=DEFAULT_TESTS_DIR,
        help="Wurzel der ausgelieferten Tests (default: src/tests)",
    )
    parser.add_argument(
        "--leak-script",
        type=Path,
        default=DEFAULT_LEAK_SCRIPT,
        help="Quelle der Exclude-Liste (default: dev-ops/release/verify-leak.sh)",
    )
    args = parser.parse_args(argv)

    leak_script: Path = args.leak_script
    if not leak_script.is_file():
        # Im gestrippten Public-Tree gibt es dev-ops/ nicht mehr — dort ist der
        # Guard moot. Nicht failen (würde Stage-CI selbst rot machen).
        print(
            f"HINWEIS: Exclude-Quelle {leak_script} nicht vorhanden — "
            "Guard übersprungen (im Public-Snapshot per Definition moot).",
        )
        return 0

    try:
        prefixes = parse_excluded_prefixes(leak_script)
    except ValueError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 2

    tests_dir: Path = args.tests_dir
    if not tests_dir.is_dir():
        print(f"FEHLER: Test-Verzeichnis nicht gefunden: {tests_dir}", file=sys.stderr)
        return 2

    findings: list[tuple[Path, str, str]] = []
    for path in iter_test_files(tests_dir):
        for prefix, literal in scan_file(path, prefixes):
            findings.append((path, prefix, literal))

    if findings:
        print(
            f"FEHLER: {len(findings)} ungegatete Referenz(en) auf Public-ausgeschlossene "
            "Pfade in ausgelieferten Tests:",
            file=sys.stderr,
        )
        for path, prefix, literal in findings:
            rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
            snippet = literal if len(literal) <= 80 else literal[:77] + "…"
            print(f"  - {rel}: '{snippet}' verweist auf ausgeschlossenen Pfad '{prefix}'", file=sys.stderr)
        print(
            "\nSolche Tests fallen erst auf der public Stage-CI mit FileNotFoundError um "
            "(der Release-Build strippt diese Pfade). Behebung: den Test skip-gaten —\n"
            "  if not <pfad>.exists():\n"
            '      pytest.skip("<prefix> nicht im Public-Snapshot — Guard nur im Dev-Repo relevant")\n'
            "Der Skip-Reason MUSS den ausgeschlossenen Prefix nennen (Refs #1137, #1047).",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK: {len(prefixes)} ausgeschlossene Prefixe (aus {leak_script.name}), "
        f"keine ungegateten Referenzen in {tests_dir}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
