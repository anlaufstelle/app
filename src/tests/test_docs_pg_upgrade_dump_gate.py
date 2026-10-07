"""The documented PostgreSQL upgrade only removes the volume after a confirmed dump (runbook § 13.1).

The prepare and replace blocks are extracted from the ops runbook (DE and EN) and run as printed
in one bash session against a ``docker`` stub on ``PATH``; no Docker daemon is involved. The stub
records every volume removal, so the test sees whether the destructive step ran.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = REPO_ROOT / "docs"

pytestmark = pytest.mark.architecture

DOC_FILES = ["ops-runbook.md", "en/ops-runbook.md"]
SECTION = re.compile(r"^### 13\.1 .*?(?=^### 13\.2 )", re.S | re.M)
BASH_BLOCK = re.compile(r"```bash\n(.*?)```", re.S)

DOCKER_STUB = """#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
case "$*" in
  "volume ls"*) echo "anlaufstelle-prod_pgdata" ;;
  *pg_dump*) printf '%s' "$STUB_DUMP" ;;
  *"pg_restore --list"*) cat > /dev/null; exit "$STUB_RESTORE_RC" ;;
esac
exit 0
"""


def _blocks(doc: str) -> tuple[str, str]:
    section = SECTION.search((DOCS / doc).read_text(encoding="utf-8"))
    assert section, doc
    blocks = BASH_BLOCK.findall(section.group(0))
    return blocks[0], blocks[1]


def _run(doc: str, tmp_path: Path, dump: str, restore_rc: int) -> tuple[subprocess.CompletedProcess, list[str], Path]:
    work = tmp_path / "work"
    (work / "scripts" / "ops").mkdir(parents=True)
    (work / "scripts" / "ops" / "backup.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (work / "scripts" / "ops" / "backup.sh").chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(DOCKER_STUB, encoding="utf-8")
    (bin_dir / "docker").chmod(0o755)
    log = tmp_path / "docker.log"
    prepare, replace = _blocks(doc)
    script = (prepare + "\n" + replace).replace("cd /opt/anlaufstelle", f"cd {work}")
    result = subprocess.run(
        ["bash", "-c", script],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "STUB_LOG": str(log),
            "STUB_DUMP": dump,
            "STUB_RESTORE_RC": str(restore_rc),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result, log.read_text(encoding="utf-8").splitlines(), work


@pytest.mark.parametrize("doc", DOC_FILES)
@pytest.mark.parametrize(
    ("dump", "restore_rc"),
    [("", 0), ("PGDMP-content", 1)],
    ids=["empty-dump", "unreadable-dump"],
)
def test_volume_is_kept_without_a_confirmed_dump(doc: str, tmp_path: Path, dump: str, restore_rc: int) -> None:
    result, calls, _ = _run(doc, tmp_path, dump, restore_rc)

    assert "dump_ok=nein" in result.stdout, result.stdout
    assert "ABBRUCH" in result.stderr, result.stderr
    assert [call for call in calls if call.startswith("volume rm")] == []
    assert not [call for call in calls if " rm -f db" in call]


@pytest.mark.parametrize("doc", DOC_FILES)
def test_volume_is_removed_once_after_a_readable_dump_and_the_work_dir_is_private(doc: str, tmp_path: Path) -> None:
    result, calls, work = _run(doc, tmp_path, "PGDMP-content", 0)

    assert "dump_ok=yes" in result.stdout, result.stdout
    assert "ABBRUCH" not in result.stderr
    assert [call for call in calls if call.startswith("volume rm")] == ["volume rm anlaufstelle-prod_pgdata"]
    assert stat.S_IMODE((work / "upgrade-work").stat().st_mode) == 0o700
