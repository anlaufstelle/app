import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

CHANGELOG_PATH = Path("CHANGELOG.md")
OPERATOR_SECTION = "Erforderliche Betreiber-Aktionen"
RELEASE_HEADING = re.compile(r"^## \[([^]]+)](?: - .+)?$")


def _blocks(text: str) -> dict[str, list[str]]:
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        match = RELEASE_HEADING.match(line)
        if match:
            current = match.group(1)
            blocks[current] = []
        elif current is not None:
            blocks[current].append(line)
    return blocks


def _version(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def _section_entries(lines: list[str], section_name: str) -> tuple[int, list[str]]:
    heading = f"### {section_name}"
    heading_indexes = [index for index, line in enumerate(lines) if line == heading]
    entries: list[str] = []
    if len(heading_indexes) == 1:
        for line in lines[heading_indexes[0] + 1 :]:
            if line.startswith("### "):
                break
            if line.startswith("- "):
                entries.append(line)
    return len(heading_indexes), entries


def _assert_operator_sections(text: str) -> None:
    blocks = _blocks(text)
    for name, lines in blocks.items():
        if name != "Unreleased" and _version(name) <= (0, 99, 1):
            continue
        count, entries = _section_entries(lines, OPERATOR_SECTION)
        assert count == 1, f"{name} must contain exactly one operator-actions section"
        assert entries, f"{name} operator-actions section must contain an entry"


def test_open_and_future_release_blocks_have_operator_actions() -> None:
    text = CHANGELOG_PATH.read_text(encoding="utf-8")
    blocks = _blocks(text)

    _assert_operator_sections(text)
    first_heading = next(line for line in blocks["Unreleased"] if line.startswith("### "))
    assert first_heading == f"### {OPERATOR_SECTION}"


def test_historical_jump_and_operator_notes_are_explained() -> None:
    blocks = _blocks(CHANGELOG_PATH.read_text(encoding="utf-8"))

    assert any("0.23.0" in line for line in blocks["0.80.0"])
    count, entries = _section_entries(blocks["0.23.0"], OPERATOR_SECTION)
    assert count == 1
    assert len(entries) == 3


def test_release_stamp_keeps_operator_action_rule_valid(tmp_path: Path) -> None:
    stamped_changelog = tmp_path / "CHANGELOG.md"
    stamped_text = CHANGELOG_PATH.read_text(encoding="utf-8").replace(
        "## [Unreleased]",
        "## [9.9.9] - 2099-01-01",
        1,
    )
    stamped_changelog.write_text(stamped_text, encoding="utf-8")

    _assert_operator_sections(stamped_changelog.read_text(encoding="utf-8"))
