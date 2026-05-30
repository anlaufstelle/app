#!/usr/bin/env python3
"""Generate ``docs/testing/test-matrix-index.md`` from the Manual-Test-Matrix.

Parses ``docs/testing/manual-test-matrix.md`` and renders a compact
overview table per section (A/B/C) plus a totals block. The output file
is regenerated between ``<!-- INDEX-AUTOGEN:START -->`` and
``<!-- INDEX-AUTOGEN:END -->`` markers; any manual content above the
start marker (e.g. introductions, notes) is preserved.

Run via::

    python scripts/build_test_matrix_index.py

Idempotent: a second invocation produces a byte-identical file.
Stdlib only, no external dependencies. Refs #891.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "testing" / "manual-test-matrix.md"
TARGET = ROOT / "docs" / "testing" / "test-matrix-index.md"

AUTOGEN_START = "<!-- INDEX-AUTOGEN:START -->"
AUTOGEN_END = "<!-- INDEX-AUTOGEN:END -->"

# Section A and C use ``#### <TC-ID> — <Title>``.
# Section B uses ``### TC-ID: <TC-ID> — <Title>``.
HEADING_A_C = re.compile(r"^####\s+(?P<tcid>(?:SMK-A|AUD)-[A-Za-z0-9_-]+)\s+[—-]\s+(?P<title>.+?)\s*$")
HEADING_B = re.compile(r"^###\s+TC-ID:\s+(?P<tcid>ENT-[A-Za-z0-9_-]+)\s+[—-]\s+(?P<title>.+?)\s*$")

META_HEADER = re.compile(r"^\|\s*Bereich\s*\|\s*Rolle\s*\|\s*Browser\s*\|\s*Mobile\s*\|\s*E2E\s*\|\s*$")
LOKAL_MARK = re.compile(r"^>\s*🔧\s*\*\*LOKAL/SSH")

SECTIONS = {
    "A": "Sektion A — Anwender-Smoke (SMK-A)",
    "B": "Sektion B — Entwickler-Komplett (ENT)",
    "C": "Sektion C — Auditor-DSGVO/Security (AUD)",
}


@dataclass
class Test:
    tcid: str
    title: str
    section: str  # "A" | "B" | "C"
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


def section_for(tcid: str) -> str:
    if tcid.startswith("SMK-A-"):
        return "A"
    if tcid.startswith("ENT-"):
        return "B"
    if tcid.startswith("AUD-"):
        return "C"
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
        m = HEADING_A_C.match(line) or HEADING_B.match(line)
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
        anchor_text = f"{t.tcid} — {t.title}" if t.section in {"A", "C"} else f"TC-ID: {t.tcid} — {t.title}"
        anchor = github_slug(anchor_text)
        link = f"[`{t.tcid}`](manual-test-matrix.md#{anchor})"
        # Escape pipe characters that would break the markdown table.
        title = t.title.replace("|", "\\|")
        e2e = t.e2e.replace("|", "\\|")
        rows.append(f"| {link} | {title} | {t.bereich} | {t.rolle} | {t.browser} | {t.mobile} | {e2e} | {t.setup} |")
    return rows


def render_index(tests: list[Test]) -> str:
    by_section: dict[str, list[Test]] = {"A": [], "B": [], "C": []}
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
    g_total, g_e2e, g_no, g_quote = stats(tests)

    lines: list[str] = []
    lines.append("## Gesamt-Statistik")
    lines.append("")
    lines.append(f"- **{g_total} Tests** in drei Sektionen")
    lines.append(f"- **mit E2E-Coverage:** {g_e2e} ({g_quote})")
    lines.append(f"- **ohne E2E (`—`):** {g_no}")
    lines.append("")
    lines.append("| Sektion | Tests | mit E2E | ohne E2E | E2E-Quote |")
    lines.append("|---------|------:|--------:|---------:|----------:|")
    lines.append(f"| A – Anwender-Smoke (SMK-A) | {a_total} | {a_e2e} | {a_no} | {a_quote} |")
    lines.append(f"| B – Entwickler-Komplett (ENT) | {b_total} | {b_e2e} | {b_no} | {b_quote} |")
    lines.append(f"| C – Auditor-DSGVO/Security (AUD) | {c_total} | {c_e2e} | {c_no} | {c_quote} |")
    lines.append(f"| **Gesamt** | **{g_total}** | **{g_e2e}** | **{g_no}** | **{g_quote}** |")
    lines.append("")

    for sec_key in ("A", "B", "C"):
        lines.append(f"## {SECTIONS[sec_key]}")
        lines.append("")
        lines.extend(render_table(by_section[sec_key]))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_index(content: str, target: Path) -> None:
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
            return

    header = (
        "# Manual-Test-Matrix — Index\n"
        "\n"
        "> Generiert aus [`docs/testing/manual-test-matrix.md`](manual-test-matrix.md) per\n"
        "> `python scripts/build_test_matrix_index.py`.\n"
        "> Bei Änderungen an der Matrix neu generieren.\n"
        "\n"
    )
    target.write_text(f"{header}{autogen_block}", encoding="utf-8")


def main() -> int:
    if not SOURCE.exists():
        print(f"ERROR: source matrix not found: {SOURCE}", file=sys.stderr)
        return 1
    tests = parse_matrix(SOURCE)
    if not tests:
        print("ERROR: no tests parsed — check matrix format", file=sys.stderr)
        return 1
    content = render_index(tests)
    write_index(content, TARGET)
    print(f"Wrote {TARGET.relative_to(ROOT)} ({len(tests)} tests)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
