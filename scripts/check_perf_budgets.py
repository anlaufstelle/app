#!/usr/bin/env python3
"""Vergleicht den ``locust --csv``-Stats-Output mit den Budgets aus
``docs/performance-budgets.json``. Refs #825.

Aufruf:

    python scripts/check_perf_budgets.py perf_stats.csv

Faellt bei Verletzung mit Exit-Code 1 und schreibt eine Markdown-Tabelle
nach stdout, die der Nightly-Workflow als Issue-Body verwenden kann.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_perf_budgets.py <locust-stats.csv>", file=sys.stderr)
        return 2
    stats_path = Path(sys.argv[1])
    budgets_path = ROOT / "docs" / "performance-budgets.json"
    budgets: dict[str, float] = json.loads(budgets_path.read_text())

    overruns: list[tuple[str, float, float]] = []
    seen: set[str] = set()
    with stats_path.open() as fh:
        for row in csv.DictReader(fh):
            name = row.get("Name", "").strip()
            if name not in budgets:
                continue
            seen.add(name)
            try:
                p95 = float(row["95%"])
            except (KeyError, ValueError):
                continue
            budget = budgets[name]
            if p95 > budget:
                overruns.append((name, p95, budget))

    missing = sorted(set(budgets) - seen)
    if missing:
        print(f"WARN: kein Locust-Stat fuer {missing}", file=sys.stderr)

    if overruns:
        print("# Performance-Budget Overrun")
        print()
        print("| Endpoint | p95 | Budget |")
        print("|---|---|---|")
        for name, p95, budget in overruns:
            print(f"| `{name}` | {p95:.0f} ms | {budget:.0f} ms |")
        return 1
    print("All endpoints within budget.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
