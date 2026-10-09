#!/usr/bin/env python3
"""Reject historical release checks in the active hand-check delta."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STEP = "python scripts/check_hand_check_archive.py"


def entries(text: str) -> set[tuple[str, str]]:
    """Identify checks by issue and whitespace-normalized text, ignoring link URLs."""
    return {
        (number, " ".join(body.split()))
        for number, body in re.findall(r"^\s*- \[#(\d+)\]\([^\n]+?\)\s*[—–-]\s*(.*)$", text, re.M)
    }


def unreleased(text: str) -> str:
    match = re.search(r"^## \[Unreleased\][^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if match is None:
        raise ValueError("Missing [Unreleased] section")
    return match[1]


def findings(root: Path) -> list[str]:
    dev = root / "docs/dev"
    active = unreleased((dev / "hand-checks.md").read_text())
    archived: set[tuple[str, str]] = set()
    archives = sorted(dev.glob("hand-checks-archive-*.md"))
    if not archives:
        return ["Missing hand-check archive"]
    for archive in archives:
        archived.update(entries(archive.read_text()))
    stale = entries(active) & archived
    errors = [
        f"Historical hand-check in [Unreleased]: #{number} — {body}"
        for number, body in sorted(stale, key=lambda entry: (int(entry[0]), entry[1]))
    ]
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
