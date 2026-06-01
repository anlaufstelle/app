#!/usr/bin/env python3
"""Generate ``docs/testing/test-matrix-index.md`` from the Manual-Test-Matrix.

Parses ``docs/testing/manual-test-matrix.md`` and renders a compact
overview table per section (A/B/C/D) plus a totals block. The output file
is regenerated between ``<!-- INDEX-AUTOGEN:START -->`` and
``<!-- INDEX-AUTOGEN:END -->`` markers; any manual content above the
start marker (e.g. introductions, notes) is preserved.

Refs #909: Patcht zusätzlich den **Anhang C** in
``manual-test-matrix.md`` zwischen ``<!-- ANHANG-C:START -->`` und
``<!-- ANHANG-C:END -->`` mit einer per-Bereich-Coverage-Tabelle.
Wenn die Marker fehlen, wird der Matrix-Patch übersprungen (Index
bleibt erhalten).

Refs #916: Erweitert den Index um Listen für Automatisierungs-
kandidaten (Manuell-only nach Sektion), LOKAL/SSH-Cases und
Security/DSGVO-Cases ohne E2E.

Run via::

    python scripts/build_test_matrix_index.py
    python scripts/build_test_matrix_index.py --check  # CI-Mode: Exit 1 wenn stale

Idempotent: a second invocation produces a byte-identical output.
Stdlib only, no external dependencies. Refs #891.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "testing" / "manual-test-matrix.md"
TARGET = ROOT / "docs" / "testing" / "test-matrix-index.md"

AUTOGEN_START = "<!-- INDEX-AUTOGEN:START -->"
AUTOGEN_END = "<!-- INDEX-AUTOGEN:END -->"

ANHANG_C_START = "<!-- ANHANG-C:START -->"
ANHANG_C_END = "<!-- ANHANG-C:END -->"

# Section A, C and D use ``#### <TC-ID> — <Title>``.
# Section B uses ``### TC-ID: <TC-ID> — <Title>``.
HEADING_A_C_D = re.compile(r"^####\s+(?P<tcid>(?:SMK-A|AUD|DEV)-[A-Za-z0-9_-]+)\s+[—-]\s+(?P<title>.+?)\s*$")
HEADING_B = re.compile(r"^###\s+TC-ID:\s+(?P<tcid>ENT-[A-Za-z0-9_-]+)\s+[—-]\s+(?P<title>.+?)\s*$")

META_HEADER = re.compile(r"^\|\s*Bereich\s*\|\s*Rolle\s*\|\s*Browser\s*\|\s*Mobile\s*\|\s*E2E\s*\|\s*$")
LOKAL_MARK = re.compile(r"^>\s*🔧\s*\*\*LOKAL/SSH")

SECTIONS = {
    "A": "Sektion A — Anwender-Smoke (SMK-A)",
    "B": "Sektion B — Anwender-Komplett (ENT, systematisch)",
    "C": "Sektion C — Auditor-DSGVO/Security (AUD)",
    "D": "Sektion D — Entwickler-Probes (DEV, LOKAL/SSH)",
}


@dataclass
class Test:
    tcid: str
    title: str
    section: str  # "A" | "B" | "C" | "D"
    bereich: str
    rolle: str
    browser: str
    mobile: str
    e2e: str
    setup: str
    line: int

    @property
    def has_e2e(self) -> bool:
        return self.e2e.strip() not in {"—", "-", ""}

    @property
    def is_local_ssh(self) -> bool:
        return "LOKAL/SSH" in self.setup


def section_for(tcid: str) -> str:
    if tcid.startswith("SMK-A-"):
        return "A"
    if tcid.startswith("ENT-"):
        return "B"
    if tcid.startswith("AUD-"):
        return "C"
    if tcid.startswith("DEV-"):
        return "D"
    raise ValueError(f"Cannot derive section from TC-ID {tcid!r}")


def split_meta_row(row: str) -> list[str]:
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    return cells


_GH_STRIP = re.compile(r"[\0-\x1f!-,\./:-@\[-\^`\{-\xa0\xa8\xad]+")


def github_slug(text: str) -> str:
    """Mimic GitHub's heading anchor generation (Flet/github-slugger regex).

    Keeps word chars, hyphens, underscores, and Unicode punctuation like
    em-dashes/en-dashes. Removes ASCII punctuation (except `-`/`_`) and
    control characters, then replaces whitespace runs with single hyphens.
    """
    s = text.strip().lower()
    s = _GH_STRIP.sub("", s)
    s = re.sub(r"\s+", "-", s)
    return s


def parse_matrix(src: Path) -> list[Test]:
    lines = src.read_text(encoding="utf-8").splitlines()
    tests: list[Test] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = HEADING_A_C_D.match(line) or HEADING_B.match(line)
        if not m:
            i += 1
            continue
        tcid = m.group("tcid")
        title = m.group("title").strip()
        heading_line = i + 1  # 1-indexed
        section = section_for(tcid)

        # Look ahead up to 30 lines for the meta-header. Capture LOKAL/SSH marker on the way.
        setup = ""
        meta_header_idx: int | None = None
        for j in range(i + 1, min(i + 30, len(lines))):
            if LOKAL_MARK.match(lines[j]):
                setup = "🔧 LOKAL/SSH"
            if META_HEADER.match(lines[j]):
                meta_header_idx = j
                break
        if meta_header_idx is None:
            print(
                f"WARN line {heading_line}: TC {tcid} has no metadata table within 30 lines — skipping",
                file=sys.stderr,
            )
            i += 1
            continue

        # Metadata row is two lines after header (header, separator, data).
        data_idx = meta_header_idx + 2
        if data_idx >= len(lines):
            print(f"ERROR line {heading_line}: metadata table truncated for {tcid}", file=sys.stderr)
            sys.exit(1)
        cells = split_meta_row(lines[data_idx])
        if len(cells) < 5:
            print(
                f"ERROR line {data_idx + 1}: malformed metadata row for {tcid} ({len(cells)} cells)",
                file=sys.stderr,
            )
            sys.exit(1)
        bereich, rolle, browser, mobile, e2e = (cells[0], cells[1], cells[2], cells[3], cells[4])

        tests.append(
            Test(
                tcid=tcid,
                title=title,
                section=section,
                bereich=bereich,
                rolle=rolle,
                browser=browser,
                mobile=mobile,
                e2e=e2e,
                setup=setup,
                line=heading_line,
            )
        )
        i = data_idx + 1

    seen: dict[str, int] = {}
    for t in tests:
        if t.tcid in seen:
            print(
                f"WARN duplicate TC-ID {t.tcid} (first at line {seen[t.tcid]}, again at line {t.line})",
                file=sys.stderr,
            )
        else:
            seen[t.tcid] = t.line
    return tests


def render_table(tests: list[Test]) -> list[str]:
    rows = [
        "| TC-ID | Bezeichnung | Bereich | Rolle | Browser | Mobile | E2E | Setup |",
        "|-------|-------------|---------|-------|---------|:------:|-----|-------|",
    ]
    for t in tests:
        anchor_text = f"{t.tcid} — {t.title}" if t.section in {"A", "C", "D"} else f"TC-ID: {t.tcid} — {t.title}"
        anchor = github_slug(anchor_text)
        link = f"[`{t.tcid}`](manual-test-matrix.md#{anchor})"
        # Escape pipe characters that would break the markdown table.
        title = t.title.replace("|", "\\|")
        e2e = t.e2e.replace("|", "\\|")
        rows.append(f"| {link} | {title} | {t.bereich} | {t.rolle} | {t.browser} | {t.mobile} | {e2e} | {t.setup} |")
    return rows


def render_index(tests: list[Test]) -> str:
    by_section: dict[str, list[Test]] = {"A": [], "B": [], "C": [], "D": []}
    for t in tests:
        by_section[t.section].append(t)

    def stats(group: list[Test]) -> tuple[int, int, int, str]:
        n = len(group)
        with_e2e = sum(1 for t in group if t.has_e2e)
        without_e2e = n - with_e2e
        quote = f"{(with_e2e / n * 100):.0f} %" if n else "—"
        return n, with_e2e, without_e2e, quote

    a_total, a_e2e, a_no, a_quote = stats(by_section["A"])
    b_total, b_e2e, b_no, b_quote = stats(by_section["B"])
    c_total, c_e2e, c_no, c_quote = stats(by_section["C"])
    d_total, d_e2e, d_no, d_quote = stats(by_section["D"])
    g_total, g_e2e, g_no, g_quote = stats(tests)

    lines: list[str] = []
    lines.append("## Gesamt-Statistik")
    lines.append("")
    lines.append(f"- **{g_total} Tests** in vier Sektionen")
    lines.append(f"- **mit E2E-Coverage:** {g_e2e} ({g_quote})")
    lines.append(f"- **ohne E2E (`—`):** {g_no}")
    lines.append("")
    lines.append("| Sektion | Tests | mit E2E | ohne E2E | E2E-Quote |")
    lines.append("|---------|------:|--------:|---------:|----------:|")
    lines.append(f"| A – Anwender-Smoke (SMK-A) | {a_total} | {a_e2e} | {a_no} | {a_quote} |")
    lines.append(f"| B – Anwender-Komplett (ENT) | {b_total} | {b_e2e} | {b_no} | {b_quote} |")
    lines.append(f"| C – Auditor-DSGVO/Security (AUD) | {c_total} | {c_e2e} | {c_no} | {c_quote} |")
    lines.append(f"| D – Entwickler-Probes (DEV, LOKAL/SSH) | {d_total} | {d_e2e} | {d_no} | {d_quote} |")
    lines.append(f"| **Gesamt** | **{g_total}** | **{g_e2e}** | **{g_no}** | **{g_quote}** |")
    lines.append("")

    for sec_key in ("A", "B", "C", "D"):
        lines.append(f"## {SECTIONS[sec_key]}")
        lines.append("")
        lines.extend(render_table(by_section[sec_key]))
        lines.append("")

    # Refs #916: Zusatzlisten für Automatisierungskandidaten, LOKAL/SSH,
    # Security/DSGVO ohne E2E.
    lines.extend(render_extras(tests))

    return "\n".join(lines).rstrip() + "\n"


def _row_link(t: Test) -> str:
    anchor_text = f"{t.tcid} — {t.title}" if t.section in {"A", "C", "D"} else f"TC-ID: {t.tcid} — {t.title}"
    anchor = github_slug(anchor_text)
    return f"[`{t.tcid}`](manual-test-matrix.md#{anchor})"


def render_extras(tests: list[Test]) -> list[str]:
    """Refs #916: ergänzende Listen unter dem Index — gleicher Autogen-Block."""
    lines: list[str] = []

    # 1) Automatisierungskandidaten: Manuell-only nach Sektion.
    manuell_only = sorted([t for t in tests if not t.has_e2e], key=lambda t: (t.section, t.tcid))
    lines.append("## Automatisierungskandidaten (Manuell-only)")
    lines.append("")
    lines.append(
        "> Refs #916: Cases ohne E2E-Spiegelung "
        "sind potenzielle Kandidaten zur Automatisierung. Sortiert nach Sektion + TC-ID."
    )
    lines.append("")
    if not manuell_only:
        lines.append("_Aktuell keine Manuell-only-Cases._")
        lines.append("")
    else:
        lines.append("| TC-ID | Bezeichnung | Sektion | Bereich | Rolle | Setup |")
        lines.append("|-------|-------------|---------|---------|-------|-------|")
        for t in manuell_only:
            title = t.title.replace("|", "\\|")
            lines.append(f"| {_row_link(t)} | {title} | {t.section} | {t.bereich} | {t.rolle} | {t.setup} |")
        lines.append("")

    # 2) 🔧 LOKAL/SSH-Cases.
    lokal = sorted([t for t in tests if t.is_local_ssh], key=lambda t: (t.section, t.tcid))
    lines.append("## 🔧 LOKAL/SSH-Cases")
    lines.append("")
    lines.append(
        "> Refs #916: Cases, die direkten "
        "Server-Zugriff brauchen (`docker compose exec web python manage.py …`, `psql`, …)."
    )
    lines.append("")
    if not lokal:
        lines.append("_Aktuell keine LOKAL/SSH-Cases._")
        lines.append("")
    else:
        lines.append("| TC-ID | Bezeichnung | Sektion | Bereich |")
        lines.append("|-------|-------------|---------|---------|")
        for t in lokal:
            title = t.title.replace("|", "\\|")
            lines.append(f"| {_row_link(t)} | {title} | {t.section} | {t.bereich} |")
        lines.append("")

    # 3) Security/DSGVO ohne E2E.
    sec_dsgvo = sorted(
        [t for t in tests if t.section == "C" and not t.has_e2e],
        key=lambda t: t.tcid,
    )
    lines.append("## Security/DSGVO-Cases ohne E2E-Coverage")
    lines.append("")
    lines.append(
        "> Refs #916: Sektion-C-Cases ohne "
        "automatisierte Spiegelung — höchste Priorität für nachfolgende E2E-/Architekturtests."
    )
    lines.append("")
    if not sec_dsgvo:
        lines.append("_Alle Sektion-C-Cases haben E2E-Coverage._")
        lines.append("")
    else:
        lines.append("| TC-ID | Bezeichnung | Bereich |")
        lines.append("|-------|-------------|---------|")
        for t in sec_dsgvo:
            title = t.title.replace("|", "\\|")
            lines.append(f"| {_row_link(t)} | {title} | {t.bereich} |")
        lines.append("")

    return lines


def render_anhang_c(tests: list[Test]) -> str:
    """Refs #909: Per-Bereich-Coverage-Tabelle für Matrix-Anhang C."""
    by_section_bereich: dict[tuple[str, str], list[Test]] = {}
    for t in tests:
        by_section_bereich.setdefault((t.section, t.bereich), []).append(t)

    lines: list[str] = []
    lines.append("**Per-Bereich-Statistik (auto-generiert):**")
    lines.append("")
    lines.append("| Sektion | Bereich | Cases | mit E2E | Manuell-only | E2E-Quote |")
    lines.append("|---------|---------|------:|--------:|-------------:|----------:|")
    for (section, bereich), group in sorted(by_section_bereich.items()):
        n = len(group)
        with_e2e = sum(1 for t in group if t.has_e2e)
        manuell = n - with_e2e
        quote = f"{(with_e2e / n * 100):.0f} %" if n else "—"
        lines.append(f"| {section} | {bereich} | {n} | {with_e2e} | {manuell} | {quote} |")

    # Sektion-Totals + Gesamt.
    lines.append("")
    lines.append("**Sektion-Totals:**")
    lines.append("")
    lines.append("| Sektion | Cases | mit E2E | Manuell-only | E2E-Quote |")
    lines.append("|---------|------:|--------:|-------------:|----------:|")
    for sec_key in ("A", "B", "C", "D"):
        group = [t for t in tests if t.section == sec_key]
        n = len(group)
        with_e2e = sum(1 for t in group if t.has_e2e)
        manuell = n - with_e2e
        quote = f"{(with_e2e / n * 100):.0f} %" if n else "—"
        lines.append(f"| {sec_key} | {n} | {with_e2e} | {manuell} | {quote} |")
    total = len(tests)
    total_e2e = sum(1 for t in tests if t.has_e2e)
    total_manuell = total - total_e2e
    total_quote = f"{(total_e2e / total * 100):.0f} %" if total else "—"
    lines.append(f"| **Gesamt** | **{total}** | **{total_e2e}** | **{total_manuell}** | **{total_quote}** |")
    lines.append("")
    lines.append("> Auto-generiert per `python scripts/build_test_matrix_index.py` (#909).")

    return "\n".join(lines) + "\n"


def write_index(content: str, target: Path) -> str:
    autogen_block = f"{AUTOGEN_START}\n{content}{AUTOGEN_END}\n"

    if target.exists():
        existing = target.read_text(encoding="utf-8")
        if AUTOGEN_START in existing and AUTOGEN_END in existing:
            prefix, _, rest = existing.partition(AUTOGEN_START)
            _, _, suffix = rest.partition(AUTOGEN_END)
            suffix = suffix.lstrip("\n")
            new = f"{prefix}{autogen_block}"
            if suffix.strip():
                new += suffix if suffix.startswith("\n") else "\n" + suffix
            target.write_text(new, encoding="utf-8")
            return new

    header = (
        "# Manual-Test-Matrix — Index\n"
        "\n"
        "> Generiert aus [`docs/testing/manual-test-matrix.md`](manual-test-matrix.md) per\n"
        "> `python scripts/build_test_matrix_index.py`.\n"
        "> Bei Änderungen an der Matrix neu generieren.\n"
        "\n"
    )
    out = f"{header}{autogen_block}"
    target.write_text(out, encoding="utf-8")
    return out


def patch_anhang_c(content: str, source: Path) -> tuple[bool, str | None]:
    """Refs #909: Anhang-C-Block in der Matrix mit Per-Bereich-Stats füllen.

    Returns ``(changed, error_or_None)``. Wenn die Marker fehlen,
    wird die Matrix nicht angefasst und ``(False, None)`` zurückgegeben.
    """
    existing = source.read_text(encoding="utf-8")
    if ANHANG_C_START not in existing or ANHANG_C_END not in existing:
        return False, None
    block = f"{ANHANG_C_START}\n{content}{ANHANG_C_END}"
    prefix, _, rest = existing.partition(ANHANG_C_START)
    _, _, suffix = rest.partition(ANHANG_C_END)
    new = f"{prefix}{block}{suffix}"
    if new == existing:
        return False, None
    source.write_text(new, encoding="utf-8")
    return True, None


def check_stale(tests: list[Test]) -> bool:
    """Refs #916: True wenn Index bzw. Anhang C nicht zur Matrix passen."""
    expected_index = render_index(tests)
    if not TARGET.exists():
        return True
    existing_index = TARGET.read_text(encoding="utf-8")
    if AUTOGEN_START not in existing_index or AUTOGEN_END not in existing_index:
        return True
    _, _, rest = existing_index.partition(AUTOGEN_START)
    block, _, _ = rest.partition(AUTOGEN_END)
    if block.strip() != expected_index.strip():
        return True

    expected_anhang = render_anhang_c(tests)
    matrix_text = SOURCE.read_text(encoding="utf-8")
    if ANHANG_C_START in matrix_text and ANHANG_C_END in matrix_text:
        _, _, rest = matrix_text.partition(ANHANG_C_START)
        block, _, _ = rest.partition(ANHANG_C_END)
        # block ist normalisiert ohne Marker; expected_anhang endet auf "\n".
        if block.strip("\n") != expected_anhang.strip("\n"):
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Manual-Test-Matrix index.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if generated output differs from on-disk files (CI-Mode).",
    )
    args = parser.parse_args(argv)

    if not SOURCE.exists():
        print(f"ERROR: source matrix not found: {SOURCE}", file=sys.stderr)
        return 1
    tests = parse_matrix(SOURCE)
    if not tests:
        print("ERROR: no tests parsed — check matrix format", file=sys.stderr)
        return 1

    if args.check:
        if check_stale(tests):
            print(
                "STALE: test-matrix-index.md oder Anhang C in manual-test-matrix.md sind nicht aktuell. "
                "Bitte `python scripts/build_test_matrix_index.py` ausführen und committen.",
                file=sys.stderr,
            )
            return 1
        print("OK: Index und Anhang C sind aktuell.")
        return 0

    content = render_index(tests)
    write_index(content, TARGET)
    anhang_content = render_anhang_c(tests)
    changed, err = patch_anhang_c(anhang_content, SOURCE)
    if err:
        print(f"WARN: patch_anhang_c: {err}", file=sys.stderr)
    suffix = f"; Anhang C {'aktualisiert' if changed else 'unverändert / keine Marker'}"
    print(f"Wrote {TARGET.relative_to(ROOT)} ({len(tests)} tests){suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
