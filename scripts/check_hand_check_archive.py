#!/usr/bin/env python3
"""Reject historical release checks in the active hand-check delta."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STEP = "python scripts/check_hand_check_archive.py"


def issue_ids(text: str) -> set[str]:
    return set(re.findall(r"\[#(\d+)\]\(", text))


def unreleased(text: str) -> str:
    match = re.search(r"^## \[Unreleased\][^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if match is None:
        raise ValueError("Missing [Unreleased] section")
    return match[1]


def findings(root: Path) -> list[str]:
    dev = root / "docs/dev"
    active = unreleased((dev / "hand-checks.md").read_text())
    archived: set[str] = set()
    archives = sorted(dev.glob("hand-checks-archive-*.md"))
    if not archives:
        return ["Missing hand-check archive"]
    for archive in archives:
        archived.update(issue_ids(archive.read_text()))
    changelog = (root / "CHANGELOG.md").read_text()
    released = re.split(r"^## \[Unreleased\][^\n]*\n", changelog, maxsplit=1, flags=re.M)[-1]
    released = re.split(r"^## ", released, maxsplit=1, flags=re.M)
    if len(released) > 1:
        archived.update(issue_ids(released[1]))
    stale = issue_ids(active) & archived
    errors = [f"Historical hand-check in [Unreleased]: #{number}" for number in sorted(stale, key=int)]
    if STEP not in (dev / "release-checklist.md").read_text():
        errors.append("Missing hand-check archive guard in release checklist")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    if not (args.root / "docs" / "dev").exists():
        print("Hand-check archive: not available in the public source tree")
        return 0
    try:
        errors = findings(args.root)
    except (OSError, ValueError) as error:
        print(f"Hand-check archive check unavailable: {error}")
        return 1
    for error in errors:
        print(error)
    if not errors:
        print("Hand-check archive: active delta contains no historical checks")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
