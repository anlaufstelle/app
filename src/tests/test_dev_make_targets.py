"""Portable contributor targets must survive a stripped source tree."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture
ROOT = Path(__file__).resolve().parents[2]
# Set when the suite itself runs under make (e.g. ``make ci``); a nested make would then
# act as a sub-make, print "Entering directory" lines and inherit the outer variables.
OUTER_MAKE_VARIABLES = {"MAKELEVEL", "MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES"}


def _make_env(**overrides):
    """Environment for a make subprocess that behaves like a top-level call."""
    env = {key: value for key, value in os.environ.items() if key not in OUTER_MAKE_VARIABLES}
    env.update(overrides)
    return env


@pytest.mark.parametrize("target", ["dco-check", "mutation", "mutation-report", "mutation-delta", "mutation-skipped"])
def test_stripped_dev_targets_report_unavailable(tmp_path, target):
    shutil.copyfile(ROOT / "Makefile", tmp_path / "Makefile")
    result = subprocess.run(
        ["make", target, "PYTHON=false", "BASE=HEAD"],
        env=_make_env(),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "not available" in result.stdout


def test_lock_default_is_shared_and_portable(tmp_path):
    shutil.copyfile(ROOT / "Makefile", tmp_path / "Makefile")
    (tmp_path / "probe.mk").write_text("include Makefile\nprint-lock:\n\t@echo $(CI_LOCK)\n")
    env = _make_env(XDG_RUNTIME_DIR=str(tmp_path))
    env.pop("CI_LOCK", None)
    result = subprocess.run(
        ["make", "-f", "probe.mk", "print-lock"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == str(tmp_path / "anlaufstelle-ci.lock")


@pytest.mark.parametrize("target", ["docs-screens", "e2e-server"])
def test_e2e_resource_targets_acquire_shared_lock(tmp_path, target):
    shutil.copyfile(ROOT / "Makefile", tmp_path / "Makefile")
    result = subprocess.run(
        ["make", "-n", target, "CI_LOCK=/shared/ci.lock"],
        env=_make_env(),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'exec 9>"/shared/ci.lock"' in result.stdout
    assert "flock -w 14400 9" in result.stdout
    assert "pkill" not in result.stdout
    assert 'kill "$server_pid"' in result.stdout


@pytest.mark.parametrize("target", ["docs-screens", "e2e-server"])
def test_busy_lock_prevents_e2e_preparation(tmp_path, target):
    import fcntl

    shutil.copyfile(ROOT / "Makefile", tmp_path / "Makefile")
    lock = tmp_path / "ci.lock"
    with lock.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(
            ["make", target, f"CI_LOCK={lock}", "CI_LOCK_TIMEOUT=0", "PYTHON=echo PREPARATION"],
            env=_make_env(),
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
    assert result.returncode != 0
    assert "PREPARATION" not in result.stdout


def test_delta_target_guard_is_recognized_in_public_docs(tmp_path):
    checker = ROOT / "dev-ops/release/sanitize_release_tree.py"
    if not checker.exists():
        pytest.skip("dev-ops/release/ and scripts/dev/ are excluded from public snapshots")
    import sys

    shutil.copyfile(ROOT / "Makefile", tmp_path / "Makefile")
    (tmp_path / "README.md").write_text("`make mutation-delta BASE=HEAD`\n")
    result = subprocess.run(
        [sys.executable, str(checker), "--check-make-targets", str(tmp_path), "^scripts/dev/"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_delta_requires_base_before_calling_available_runner(tmp_path):
    shutil.copyfile(ROOT / "Makefile", tmp_path / "Makefile")
    runner = tmp_path / "scripts" / "dev" / "run_mutmut.py"
    runner.parent.mkdir(parents=True)
    runner.touch()
    result = subprocess.run(
        ["make", "mutation-delta", "PYTHON=echo RUNNER_CALLED"],
        env=_make_env(),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "BASE=<tag/ref> required" in result.stdout
    assert "RUNNER_CALLED" not in result.stdout


def test_public_delta_without_base_reports_unavailable(tmp_path):
    shutil.copyfile(ROOT / "Makefile", tmp_path / "Makefile")
    result = subprocess.run(
        ["make", "mutation-delta", "PYTHON=false"],
        env=_make_env(),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "not available" in result.stdout
