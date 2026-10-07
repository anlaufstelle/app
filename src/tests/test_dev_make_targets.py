"""Portable contributor targets must survive a stripped source tree."""

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("target", ["dco-check", "mutation", "mutation-report", "mutation-delta", "mutation-skipped"])
def test_stripped_dev_targets_report_unavailable(tmp_path, target):
    shutil.copyfile(ROOT / "Makefile", tmp_path / "Makefile")
    result = subprocess.run(
        ["make", target, "PYTHON=false", "BASE=HEAD"],
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
    import os

    env = dict(os.environ, XDG_RUNTIME_DIR=str(tmp_path))
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
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
    assert result.returncode != 0
    assert "PREPARATION" not in result.stdout
