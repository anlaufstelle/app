#!/usr/bin/env python3
"""CI drift-guard: AGENTS.md ist die tool-neutrale SSOT, CLAUDE.md verweist darauf.

Die Agent-Konventionen sollen tool-neutral in ``AGENTS.md`` stehen (Single
Source of Truth); ``CLAUDE.md`` ist nur ein duenner Claude-Code-Wrapper, der auf
``AGENTS.md`` verweist und die neutralen Regeln **nicht** dupliziert. Zwei
prosaische Kopien wuerden garantiert auseinanderdriften — dieser Guard erzwingt
die Rollenteilung (Entscheidung 12.11 / CONTRACT Anhang B).

Bewusst **reiner String-/Strukturvergleich** — stdlib-only, kein Parser. Geprueft
wird:

  1. ``AGENTS.md`` existiert.
  2. ``AGENTS.md`` enthaelt die Pflicht-Abschnitte (Architektur, Coding, Tests,
     Git/Commits, Issues-Sperre, Verboten) plus die Truth-Source-Regel.
  3. ``CLAUDE.md`` existiert und **verweist** auf ``AGENTS.md``.
  4. ``CLAUDE.md`` **re-definiert** die neutralen SSOT-Abschnitte nicht
     (Copy-Drift): bestimmte neutrale Ueberschriften duerfen nur in AGENTS.md
     stehen.

Exit-Code != 0 bei Verstoss. Aufruf::

    python scripts/verify_agent_docs_sync.py

Refs #1403.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "AGENTS.md"
CLAUDE = ROOT / "CLAUDE.md"

# Pflicht-Abschnitte/Marker, die in AGENTS.md vorhanden sein MUESSEN.
REQUIRED_IN_AGENTS: tuple[str, ...] = (
    "## Projekt & Architektur",
    "## Coding-Regeln",
    "## Tests & Verifikation",
    "## Git & Commits",
    "## Issues & Plan",
    "###-Sperre",
    "### Verboten",
    "Code ist einzige Truth Source",
)

# Neutrale SSOT-Ueberschriften, die NUR in AGENTS.md stehen duerfen. Tauchen sie
# in CLAUDE.md (wieder) auf, ist die Regel dorthin kopiert worden -> Copy-Drift.
FORBIDDEN_IN_CLAUDE: tuple[str, ...] = (
    "## Coding-Regeln",
    "###-Sperre",
    "### Verboten",
)


def check() -> list[str]:
    """Fuehrt alle Checks aus und gibt die Liste der Fehlermeldungen zurueck."""
    errors: list[str] = []

    if not AGENTS.is_file():
        return ["AGENTS.md fehlt — die tool-neutrale SSOT der Agent-Konventionen muss existieren (Entscheidung 12.11)."]
    if not CLAUDE.is_file():
        return ["CLAUDE.md fehlt."]

    agents_text = AGENTS.read_text(encoding="utf-8")
    claude_text = CLAUDE.read_text(encoding="utf-8")

    for marker in REQUIRED_IN_AGENTS:
        if marker not in agents_text:
            errors.append(f"AGENTS.md: Pflicht-Abschnitt/Marker fehlt: {marker!r}")

    if "AGENTS.md" not in claude_text:
        errors.append(
            "CLAUDE.md verweist nicht auf AGENTS.md — der duenne Wrapper muss "
            "die SSOT referenzieren (z.B. 'siehe AGENTS.md')."
        )

    for marker in FORBIDDEN_IN_CLAUDE:
        if marker in claude_text:
            errors.append(
                f"CLAUDE.md re-definiert neutrale SSOT-Ueberschrift {marker!r} — "
                f"gehoert ausschliesslich nach AGENTS.md (Copy-Drift)."
            )

    return errors


def main(argv: list[str] | None = None) -> int:
    errors = check()
    if errors:
        print("FEHLER: AGENTS.md/CLAUDE.md-Drift erkannt:", file=sys.stderr)
        for msg in errors:
            print(f"  - {msg}", file=sys.stderr)
        print(
            "\nRegel: tool-neutrale Konventionen stehen in AGENTS.md (SSOT); "
            "CLAUDE.md ist ein duenner Wrapper, der darauf verweist und die "
            "neutralen Regeln nicht dupliziert.",
            file=sys.stderr,
        )
        return 1

    print("OK: AGENTS.md ist SSOT, CLAUDE.md verweist darauf (kein Drift).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
