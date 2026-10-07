"""A psql meta-command in a documented code block never carries a trailing comment.

psql does not treat ``-- ...`` after ``\\set`` (or any other backslash command) as a comment:
the words become further arguments and are appended to the value. ``\\set dbname x  -- note``
therefore yields the database name ``x--note``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.architecture

META_COMMAND = re.compile(r"^\s*\\[A-Za-z]+\b")
QUOTED = re.compile(r"'(?:[^']|'')*'|\"[^\"]*\"")
FENCE = re.compile(r"^\s*(```|~~~)")


def find_meta_comments(root: Path) -> list[str]:
    """Return ``file:line: text`` for every backslash command with a trailing ``--`` comment."""
    findings: list[str] = []
    for path in sorted(root.rglob("*.md")):
        in_fence = False
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if FENCE.match(line):
                in_fence = not in_fence
            elif in_fence and META_COMMAND.match(line) and "--" in QUOTED.sub("", line):
                findings.append(f"{path.relative_to(root)}:{number}: {line.strip()}")
    return findings


def test_documented_psql_meta_commands_have_no_trailing_comment() -> None:
    assert find_meta_comments(REPO_ROOT / "docs") == []


def test_scanner_flags_a_comment_behind_set_and_ignores_quoted_dashes(tmp_path: Path) -> None:
    doc = tmp_path / "probe.md"
    doc.write_text(
        "```sql\n"
        "-- fine: a comment line of its own\n"
        "\\set dbname anlaufstelle  -- adjust\n"
        "\\echo 'dbname =[':dbname']'\n"
        "\\echo 'a -- b'\n"
        "```\n"
        "\\set outside_fence x -- not a code block\n",
        encoding="utf-8",
    )

    assert find_meta_comments(tmp_path) == ["probe.md:3: \\set dbname anlaufstelle  -- adjust"]
