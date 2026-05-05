#!/usr/bin/env python3
"""CI check: every translated file must declare a translation-version
header, and it must be no more than two minor versions behind the
current project version (`pyproject.toml`). Refs #832.
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_MINOR_BEHIND = 2

VERSION_RE = re.compile(r"<!--\s*translation-version:\s*v(\d+)\.(\d+)\.(\d+)\s*-->")
SOURCE_RE = re.compile(r"<!--\s*translation-source:\s*[^\n]+?-->")

TRANSLATED_FILES = [
    "README.en.md",
    "CONTRIBUTING.en.md",
    "docs/en/README.md",
    "docs/en/admin-guide.md",
    "docs/en/user-guide.md",
    "docs/en/domain-concept-summary.md",
    "docs/en/glossary.md",
]


def current_version() -> tuple[int, int, int]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    raw = data["project"]["version"]
    parts = raw.split(".")
    return int(parts[0]), int(parts[1]), int(parts[2])


def parse_marker(text: str) -> tuple[int, int, int] | None:
    match = VERSION_RE.search(text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def main() -> int:
    cur_major, cur_minor, _cur_patch = current_version()
    errors: list[str] = []
    for rel in TRANSLATED_FILES:
        path = ROOT / rel
        if not path.exists():
            errors.append(f"{rel}: file missing")
            continue
        text = path.read_text()
        if not SOURCE_RE.search(text):
            errors.append(f"{rel}: missing translation-source header")
        version = parse_marker(text)
        if version is None:
            errors.append(f"{rel}: missing translation-version header")
            continue
        major, minor, _patch = version
        if (major, minor) > (cur_major, cur_minor):
            errors.append(
                f"{rel}: translation-version v{major}.{minor} ahead of "
                f"pyproject.toml v{cur_major}.{cur_minor}"
            )
            continue
        if major != cur_major:
            errors.append(
                f"{rel}: translation-version major v{major} != "
                f"current v{cur_major}"
            )
            continue
        behind = cur_minor - minor
        if behind > MAX_MINOR_BEHIND:
            errors.append(
                f"{rel}: translation-version v{major}.{minor} is "
                f"{behind} minor releases behind v{cur_major}.{cur_minor} "
                f"(max {MAX_MINOR_BEHIND})"
            )

    if errors:
        print("Translation-version drift detected:", file=sys.stderr)
        for line in errors:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print(
        f"All {len(TRANSLATED_FILES)} translations within "
        f"{MAX_MINOR_BEHIND} minor releases of v{cur_major}.{cur_minor}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
