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
