"""Text helpers for guards that read the manual test matrices."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MATRIX_B = REPO_ROOT / "docs" / "testing" / "manual-test-matrix-b.md"
MATRIX_D = REPO_ROOT / "docs" / "testing" / "manual-test-matrix-d.md"


def matrix_case(path: Path, heading: str) -> str:
    """Return one test case, from its heading up to the next ``---`` rule."""
    text = path.read_text(encoding="utf-8")
    start = text.index(heading)
    end = text.find("\n---\n", start)
    return text[start : end if end != -1 else len(text)]


def case_section(block: str, label: str) -> str:
    """Return the body of a bold ``**Label:**`` section up to the next one."""
    start = block.index(f"**{label}:**")
    match = re.search(r"\n\*\*[^*\n]+:\*\*", block[start + 1 :])
    end = start + 1 + match.start() if match else len(block)
    return block[start:end]


def non_empty_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]
