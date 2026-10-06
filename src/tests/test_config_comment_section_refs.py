"""Section references (``docs/x.md § 2.1``) in config and script comments must resolve to a heading."""

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACKED_PATTERNS = [
    "docker-compose*.yml",
    ".env*.example",
    "Caddyfile*",
    "scripts/*.sh",
    "scripts/ops/*.sh",
    "scripts/ops/lib/*.sh",
]
if (REPO_ROOT / "dev-ops").exists():  # dev-ops/ is stripped from the public snapshot
    TRACKED_PATTERNS.append("dev-ops/deploy/*.sh")
REFERENCE = re.compile(r"(?P<doc>docs/[\w./-]+\.md)\s+§\s*(?P<section>\d+(?:\.\d+)?[a-z]?)(?![\w.]*\d)")
HEADING_NUMBER = re.compile(r"^#{2,4}\s+(?P<number>\d+(?:\.\d+)?[a-z]?)\.?\s")


def heading_numbers(text: str) -> set[str]:
    return {m.group("number") for line in text.splitlines() if (m := HEADING_NUMBER.match(line))}


def unresolved_references(files: list[Path], root: Path) -> list[str]:
    """Return ``file:line: doc § section`` for every reference without a matching numbered heading."""
    problems: list[str] = []
    for path in files:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for match in REFERENCE.finditer(line):
                target = root / match.group("doc")
                if not target.exists():
                    continue
                if match.group("section") not in heading_numbers(target.read_text(encoding="utf-8")):
                    problems.append(
                        f"{path.relative_to(root)}:{number}: {match.group('doc')} § {match.group('section')}"
                    )
    return problems


def test_section_reference_scanner_flags_missing_headings(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("## 1. Install\n\n### 2.1 Bootstrap\n", encoding="utf-8")
    probe = tmp_path / "probe.yml"
    probe.write_text(
        "# see docs/guide.md § 1\n# see docs/guide.md § 2.1\n# see docs/guide.md § 3.4\n", encoding="utf-8"
    )
    assert unresolved_references([probe], tmp_path) == ["probe.yml:3: docs/guide.md § 3.4"]


def test_config_comment_section_references_resolve() -> None:
    try:
        listed = subprocess.run(
            ["git", "ls-files", *TRACKED_PATTERNS],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("not a git checkout (release snapshot)")
    assert listed, "git ls-files returned no config files"
    assert unresolved_references([REPO_ROOT / name for name in listed], REPO_ROOT) == []
