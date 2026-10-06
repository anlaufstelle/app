"""Guard: documented host-crontab entries must work when copied into a crontab.

Two rules, both about what a copied example does in a classic crontab:

1. Every ``%`` is escaped as ``\\%``. In crontab(5) (sixth field) an unescaped ``%``
   becomes a newline and everything after the first one is fed to the command as
   stdin. A copied line such as ``... LIKE 'pbkdf2_%' ...`` therefore reaches the
   shell with an open quote and fails on every run, without anyone noticing.
2. Every entry is one physical line. crontab(5) has no line continuation: the
   command ends at the newline, a trailing ``\\`` is not special, and the next
   line is read as a new schedule. ``docker compose ...`` is no schedule, so the
   table is rejected or the job never runs.

The scan looks at fenced code blocks of the Markdown files: every line of a
``cron``/``crontab`` block plus every five-field schedule line in any other
block. ``deploy/crontab`` is deliberately out of scope: the sidecar runs
supercronic, which does not treat ``%`` specially, so a backslash there would
end up in the command.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]

_FENCE = re.compile(r"^(?P<indent>\s*)(?P<ticks>`{3,})\s*(?P<lang>[\w+-]*)\s*$")
_SCHEDULE = re.compile(r"^\s*(?:@[a-z]+|(?:[0-9*/,\-]+\s+){4}[0-9*/,\-]+)\s+\S")
_UNESCAPED_PERCENT = re.compile(r"(?<!\\)%")
_ENV_ASSIGNMENT = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*=")
_CRON_LANGS = {"cron", "crontab"}


def _markdown_files(root: Path) -> list[Path]:
    """Tracked Markdown files; plain filesystem walk when ``root`` is no git checkout."""
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--", "*.md"],
            cwd=root,
            capture_output=True,
            check=True,
            timeout=30,
        ).stdout.decode("utf-8")
    except (OSError, subprocess.SubprocessError):
        return sorted(root.rglob("*.md"))
    return [root / name for name in listed.split("\0") if name]


def find_unescaped_percent(root: Path) -> list[str]:
    """Return ``path:line: text`` for every crontab example line with an unescaped ``%``."""
    findings: list[str] = []
    for path in _markdown_files(root):
        if not path.is_file():
            continue
        fence_ticks = ""
        fence_lang = ""
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            fence = _FENCE.match(line)
            if fence and (not fence_ticks or (fence["ticks"] == fence_ticks and not fence["lang"])):
                if fence_ticks:
                    fence_ticks = fence_lang = ""
                else:
                    fence_ticks, fence_lang = fence["ticks"], fence["lang"].lower()
                continue
            if not fence_ticks or line.lstrip().startswith("#") or not line.strip():
                continue
            in_cron_block = fence_lang in _CRON_LANGS and not _ENV_ASSIGNMENT.match(line)
            if (in_cron_block or _SCHEDULE.match(line)) and _UNESCAPED_PERCENT.search(line):
                findings.append(f"{path.relative_to(root)}:{number}: {line.strip()[:120]}")
    return findings


def find_multiline_entries(root: Path) -> list[str]:
    """Return ``path:line: reason`` for every crontab example entry spread over several lines.

    In ``cron``/``crontab`` blocks every line that is neither a comment, blank nor an
    environment assignment must be a complete entry (schedule plus command) without a
    trailing backslash. In other fences a schedule line ending in a backslash is flagged.
    """
    findings: list[str] = []
    for path in _markdown_files(root):
        if not path.is_file():
            continue
        fence_ticks = ""
        fence_lang = ""
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            fence = _FENCE.match(line)
            if fence and (not fence_ticks or (fence["ticks"] == fence_ticks and not fence["lang"])):
                if fence_ticks:
                    fence_ticks = fence_lang = ""
                else:
                    fence_ticks, fence_lang = fence["ticks"], fence["lang"].lower()
                continue
            if not fence_ticks or line.lstrip().startswith("#") or not line.strip():
                continue
            where = f"{path.relative_to(root)}:{number}"
            in_cron_block = fence_lang in _CRON_LANGS and not _ENV_ASSIGNMENT.match(line)
            is_schedule = bool(_SCHEDULE.match(line))
            if (in_cron_block or is_schedule) and line.rstrip().endswith("\\"):
                findings.append(f"{where}: entry continues on the next line: {line.strip()[:80]}")
            elif in_cron_block and not is_schedule:
                findings.append(f"{where}: line is no complete entry (schedule + command): {line.strip()[:80]}")
    return findings


def test_documented_crontab_entries_are_single_line() -> None:
    findings = find_multiline_entries(REPO_ROOT)
    assert not findings, "crontab entries must be one physical line (no backslash continuation):\n" + "\n".join(
        findings
    )


def test_documented_crontab_lines_escape_percent() -> None:
    findings = find_unescaped_percent(REPO_ROOT)
    assert not findings, "unescaped % in crontab examples (write \\% instead):\n" + "\n".join(findings)


def test_scan_flags_unescaped_percent_in_cron_block(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text(
        "```cron\n0 5 * * 0 psql -c \"SELECT 1 WHERE x LIKE 'pbkdf2_%'\"\n```\n",
        encoding="utf-8",
    )
    assert find_unescaped_percent(tmp_path) == ["a.md:2: 0 5 * * 0 psql -c \"SELECT 1 WHERE x LIKE 'pbkdf2_%'\""]


def test_scan_accepts_escaped_percent_and_comments(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text(
        "```cron\n# comment with 100% safe text\n0 5 * * 0 echo \"LIKE 'pbkdf2_\\%'\"\n```\n",
        encoding="utf-8",
    )
    assert find_unescaped_percent(tmp_path) == []


def test_scan_flags_schedule_line_in_other_fence_but_not_plain_commands(tmp_path: Path) -> None:
    (tmp_path / "b.md").write_text(
        "```bash\n"
        "date +%F\n"
        "*/5 * * * * echo $(date +%s) >> /tmp/x\n"
        "```\n"
        "\n"
        "`0 5 * * 0 echo %` outside any fence is prose\n",
        encoding="utf-8",
    )
    assert find_unescaped_percent(tmp_path) == ["b.md:3: */5 * * * * echo $(date +%s) >> /tmp/x"]


def test_scan_flags_every_line_of_a_cron_block_after_env_assignments(tmp_path: Path) -> None:
    (tmp_path / "c.md").write_text(
        "```crontab\nMAILTO=ops\n@daily /usr/bin/date +%F\n```\n",
        encoding="utf-8",
    )
    assert find_unescaped_percent(tmp_path) == ["c.md:3: @daily /usr/bin/date +%F"]


def test_scan_ignores_unterminated_state_after_the_closing_fence(tmp_path: Path) -> None:
    (tmp_path / "d.md").write_text(
        "```cron\n0 1 * * * true\n```\n\n0 5 * * 0 echo 100%\n",
        encoding="utf-8",
    )
    assert find_unescaped_percent(tmp_path) == []


def test_multiline_scan_flags_continuation_and_orphan_lines(tmp_path: Path) -> None:
    (tmp_path / "e.md").write_text(
        "```cron\n"
        "30 * * * * cd /opt/x && \\\n"
        "  docker compose run --rm -T maintenance \\\n"
        "  detect_breaches >> /var/log/x.log 2>&1\n"
        "```\n",
        encoding="utf-8",
    )
    assert find_multiline_entries(tmp_path) == [
        "e.md:2: entry continues on the next line: 30 * * * * cd /opt/x && \\",
        "e.md:3: entry continues on the next line: docker compose run --rm -T maintenance \\",
        "e.md:4: line is no complete entry (schedule + command): detect_breaches >> /var/log/x.log 2>&1",
    ]


def test_multiline_scan_accepts_single_line_entries_and_shell_continuations_elsewhere(tmp_path: Path) -> None:
    (tmp_path / "f.md").write_text(
        "```crontab\n"
        "MAILTO=ops\n"
        "# 0 1 * * * disabled\n"
        "0 3 * * * cd /opt/x && docker compose run --rm -T maintenance enforce_retention >> /var/log/x.log 2>&1\n"
        "@daily /usr/bin/true\n"
        "```\n"
        "\n"
        "```bash\n"
        "docker compose run --rm -T maintenance \\\n"
        "  enforce_retention\n"
        "```\n",
        encoding="utf-8",
    )
    assert find_multiline_entries(tmp_path) == []


def test_multiline_scan_flags_schedule_line_with_continuation_in_other_fence(tmp_path: Path) -> None:
    (tmp_path / "g.md").write_text(
        "```bash\n0 3 * * * cd /opt/x && \\\n  true\n```\n",
        encoding="utf-8",
    )
    assert find_multiline_entries(tmp_path) == ["g.md:2: entry continues on the next line: 0 3 * * * cd /opt/x && \\"]
