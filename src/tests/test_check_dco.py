"""Regression tests for the local DCO commit-range check."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path("scripts/dev/check_dco.py")


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _git(repo: Path, *args: str) -> str:
    result = _run(["git", *args], cwd=repo)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _prepare_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "DCO Test")
    _git(repo, "config", "user.email", "dco-test@example.org")

    script_target = repo / SCRIPT
    script_target.parent.mkdir(parents=True)
    source_script = PROJECT_ROOT / SCRIPT
    if not source_script.exists():
        pytest.skip("scripts/dev/ nicht im Public-Snapshot — Test nur im Dev-Repo relevant")
    shutil.copy2(source_script, script_target)
    return repo


def _commit(
    repo: Path,
    subject: str,
    *,
    signed_off: bool,
    filename: str = "tracked.txt",
) -> str:
    tracked = repo / filename
    with tracked.open("a", encoding="utf-8") as handle:
        handle.write(f"{subject}\n")
    _git(repo, "add", filename)
    command = ["commit", "-m", subject]
    if signed_off:
        command.append("--signoff")
    _git(repo, *command)
    return _git(repo, "rev-parse", "HEAD")


def _check(repo: Path, revision_range: str) -> subprocess.CompletedProcess[str]:
    return _run(
        [sys.executable, "scripts/dev/check_dco.py", revision_range],
        cwd=repo,
    )


def test_reports_every_unsigned_non_merge_commit(tmp_path: Path) -> None:
    repo = _prepare_repo(tmp_path)
    base = _commit(repo, "signed base", signed_off=True)
    first_missing = _commit(repo, "first missing sign-off", signed_off=False)
    _commit(repo, "signed middle", signed_off=True)
    second_missing = _commit(repo, "second missing sign-off", signed_off=False)

    result = _check(repo, f"{base}..HEAD")

    assert result.returncode == 1
    assert first_missing in result.stdout
    assert "first missing sign-off" in result.stdout
    assert second_missing in result.stdout
    assert "second missing sign-off" in result.stdout


def test_accepts_signed_commits_and_ignores_merge_commit(tmp_path: Path) -> None:
    repo = _prepare_repo(tmp_path)
    base = _commit(repo, "signed base", signed_off=True)

    _git(repo, "switch", "-c", "topic")
    _commit(repo, "signed topic", signed_off=True, filename="topic.txt")
    _git(repo, "switch", "main")
    _commit(repo, "signed main", signed_off=True, filename="main.txt")
    _git(repo, "merge", "--no-ff", "topic", "-m", "unsigned merge")

    result = _check(repo, f"{base}..HEAD")

    assert result.returncode == 0
    assert "unsigned merge" not in result.stdout


def test_invalid_revision_range_exits_with_two(tmp_path: Path) -> None:
    repo = _prepare_repo(tmp_path)
    _commit(repo, "signed base", signed_off=True)

    result = _check(repo, "not-a-valid-range")

    assert result.returncode == 2
    assert "not-a-valid-range" in result.stderr
