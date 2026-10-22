#!/usr/bin/env python3
"""Deterministischer Scheiben-Schnitt der E2E-Suite (Refs #1976).

Die E2E-Suite laeuft bewusst **seriell** — parallele Playwright-Laeufe auf
ressourcenknappen Runnern sind instabil (Refs #1606). Damit waechst die
Laufzeit eines einzelnen Jobs mit jedem Meilenstein; beim v0.82.0-Lauf kam der
CI-Job bis 92 % und wurde vom Job-Timeout gekappt, ohne einen einzigen
Testfehler. Die Antwort ist nicht ``-n``, sondern **mehrere Jobs mit je einer
Scheibe**, jeder fuer sich seriell. Dieses Script bestimmt, welche Testdateien
in welche Scheibe gehoeren — dieselbe Zerlegung fuer die CI-Matrix
(``.github/workflows/e2e.yml``) und fuer ``make test-e2e-halves`` lokal, damit
ein rot gewordener CI-Job lokal exakt nachstellbar ist.

Aufteilungsschluessel: **Testzahl je Datei**, nicht Dateiname
=============================================================
Der naheliegende Schnitt "erste Haelfte der sortierten Dateiliste / zweite
Haelfte" ist zwar deterministisch, aber unausgewogen: bei 102 Dateien und 827
Testfunktionen ergab er 335 gegen 492 Tests (+47 %). Gemessen wird deshalb
statisch die Zahl der Testfunktionen je Datei (``ast``, keine Imports, keine
Datenbank, keine Playwright-Installation noetig) und daraus per
Greedy-Verfahren (schwerste Datei zuerst in die aktuell leichteste Scheibe)
verteilt. Das haelt die Scheiben in der Praxis auf unter einem Prozent
Streuung.

Die Testzahl ist ein **Proxy** fuer Laufzeit, keine Messung: ein Test mit
PDF-Export dauert laenger als ein Klick auf einen Filter. Fuer den Zweck —
kein Job laeuft in sein Timeout — reicht der Proxy und hat den Vorteil, ohne
Laufzeit-Artefakt aus einem Vorlauf auszukommen (die waeren pro Runner anders
und damit nicht mehr deterministisch).

Dateiweise geschnitten wird, weil die E2E-Fixtures dateiweise haengen: der
Chromium wird pro Testdatei recycelt (modul-scoped ``browser``-Fixture,
Refs #1787). Ein Schnitt mitten durch eine Datei wuerde denselben Browser in
zwei Sessions aufbauen.

Aufruf::

    python scripts/e2e_slice.py --slices 8 --index 2   # Dateien der Scheibe 2
    python scripts/e2e_slice.py --slices 8 --summary   # Uebersicht aller Scheiben

Bei jedem Fehler (unbekanntes Verzeichnis, leerer Schnitt, Index ausserhalb
des Bereichs) bleibt **stdout leer** und der Exit-Code ist != 0. Das ist
Absicht: die Ausgabe wird direkt als Dateiliste an ``pytest`` gehaengt, und
``pytest`` ohne Dateiargumente sammelt die **gesamte** Suite ein — ein still
leerer Schnitt waere also ein unbemerkter Voll-Lauf je Scheibe.

Stdlib-only.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE_DIR = ROOT / "src" / "tests" / "e2e"

# Spiegel der pytest-Defaults ``python_files``/``python_classes``/
# ``python_functions``: gezaehlt wird nur, was pytest auch einsammeln wuerde.
TEST_FILE_GLOBS = ("test_*.py", "*_test.py")
TEST_CLASS_PREFIX = "Test"
TEST_FUNCTION_PREFIX = "test"


def count_tests(path: Path) -> int:
    """Zaehlt die Testfunktionen in ``path`` statisch (ohne Import).

    Gezaehlt werden Funktionen auf Modulebene und Methoden in ``Test``-Klassen.
    Verschachtelte ``def``s innerhalb einer Testfunktion und Methoden von
    Nicht-``Test``-Klassen (Helfer) zaehlen nicht — pytest sammelt sie auch
    nicht ein, sie wuerden das Gewicht der Datei nur verzerren.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return _count_in_body(tree.body)


def _count_in_body(body: list[ast.stmt]) -> int:
    total = 0
    for node in body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name.startswith(TEST_FUNCTION_PREFIX):
                total += 1
        elif isinstance(node, ast.ClassDef) and node.name.startswith(TEST_CLASS_PREFIX):
            total += _count_in_body(node.body)
    return total


def discover(base_dir: Path) -> list[Path]:
    """Alle Testdateien unter ``base_dir``, nach Pfad sortiert."""
    found: set[Path] = set()
    for glob in TEST_FILE_GLOBS:
        found.update(p for p in base_dir.rglob(glob) if p.is_file() and "__pycache__" not in p.parts)
    return sorted(found)


def distribute(weights: dict[Path, int], slices: int) -> list[list[Path]]:
    """Verteilt die gewichteten Dateien auf ``slices`` Scheiben.

    Greedy (Longest Processing Time first): schwerste Datei zuerst, jeweils in
    die aktuell leichteste Scheibe. Beide Sortierungen sind vollstaendig
    durchdefiniert — bei gleichem Gewicht entscheidet der Pfad, bei gleicher
    Last die kleiner besetzte und dann die kleinere Scheibe. Damit haengt das
    Ergebnis nur an der Menge der Dateien, nicht an der Reihenfolge, in der sie
    hereinkommen.

    Die Dateizahl im Zielschluessel ist kein Schoenheitskriterium, sondern
    haelt die Zusicherung "keine Scheibe bleibt leer" auch bei **Gewicht 0**
    aufrecht: eine ``test_*.py`` ohne eigene Testfunktion (nur Fixtures) wiegt
    0, und ohne diesen Schluessel fielen alle Nuller in dieselbe Scheibe,
    waehrend die uebrigen leer blieben — fehlerfrei und still, obwohl eine
    leere Scheibe je Scheibe einen Voll-Lauf ausloest. Mit ihm gewinnt eine
    noch leere Scheibe (Last 0, 0 Dateien) jeden Vergleich gegen eine bereits
    belegte gleicher Last; da ``len(weights) >= slices`` erzwungen ist,
    bekommt jede Scheibe mindestens eine Datei.
    """
    if slices < 1:
        raise ValueError(f"--slices muss >= 1 sein, war {slices}")
    if not weights:
        raise ValueError("Keine Testdateien gefunden — ein leerer Schnitt liefe als Voll-Lauf durch")
    if len(weights) < slices:
        raise ValueError(f"{len(weights)} Testdatei(en) lassen sich nicht auf {slices} Scheiben verteilen")

    buckets: list[list[Path]] = [[] for _ in range(slices)]
    loads = [0] * slices
    for path, weight in sorted(weights.items(), key=lambda item: (-item[1], item[0])):
        target = min(range(slices), key=lambda i: (loads[i], len(buckets[i]), i))
        buckets[target].append(path)
        loads[target] += weight
    return [sorted(bucket) for bucket in buckets]


def build_slices(base_dir: Path, slices: int) -> list[list[Path]]:
    """Schneidet die Testdateien unter ``base_dir`` in ``slices`` Scheiben."""
    if not base_dir.is_dir():
        raise ValueError(f"Kein Verzeichnis: {base_dir}")
    files = discover(base_dir)
    return distribute({path: count_tests(path) for path in files}, slices)


def _display(path: Path) -> str:
    """Repo-relativer Pfad, sonst absolut (z.B. bei Tests auf tmp-Baeumen)."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministischer Scheiben-Schnitt der E2E-Suite (Refs #1976).")
    parser.add_argument("--slices", type=int, required=True, help="Anzahl der Scheiben (= Jobs der CI-Matrix)")
    parser.add_argument("--index", type=int, help="Welche Scheibe ausgegeben wird, 1-basiert")
    parser.add_argument("--summary", action="store_true", help="Uebersicht aller Scheiben statt einer Dateiliste")
    parser.add_argument("--base-dir", type=Path, default=DEFAULT_BASE_DIR, help="Testverzeichnis (Default: E2E-Suite)")
    args = parser.parse_args(argv)

    if (args.index is None) == (not args.summary):
        parser.error("genau eines von --index und --summary angeben")

    try:
        buckets = build_slices(args.base_dir, args.slices)
    except ValueError as exc:
        print(f"e2e_slice: {exc}", file=sys.stderr)
        return 2

    if args.summary:
        for number, bucket in enumerate(buckets, start=1):
            load = sum(count_tests(path) for path in bucket)
            print(f"Scheibe {number}/{args.slices}: {len(bucket)} Dateien, {load} Tests")
        return 0

    if not 1 <= args.index <= args.slices:
        print(f"e2e_slice: --index {args.index} liegt ausserhalb von 1..{args.slices}", file=sys.stderr)
        return 2

    for path in buckets[args.index - 1]:
        print(_display(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
