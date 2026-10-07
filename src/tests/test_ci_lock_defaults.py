"""Make and direct mutation invocations must share one portable lock convention."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("directory_variable", [None, "TMPDIR", "XDG_RUNTIME_DIR"])
def test_make_and_runner_lock_defaults_match_without_ci_lock(tmp_path, directory_variable):
    runner = ROOT / "scripts/dev/run_mutmut.py"
    if not runner.exists():
        pytest.skip("scripts/dev/run_mutmut.py is excluded from public snapshots")
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"CI_LOCK", "CI_LOCK_TIMEOUT", "XDG_RUNTIME_DIR", "TMPDIR"}
    }
    if directory_variable:
        env[directory_variable] = str(tmp_path)
    if directory_variable == "XDG_RUNTIME_DIR":
        env["TMPDIR"] = str(tmp_path / "unused")
    shutil.copyfile(ROOT / "Makefile", tmp_path / "Makefile")
    (tmp_path / "probe.mk").write_text(
        'include Makefile\nprint-lock:\n\t@echo "$(CI_LOCK)" "$$CI_LOCK" "$$CI_LOCK_TIMEOUT"\n'
    )
    make = subprocess.run(
        ["make", "-s", "-f", "probe.mk", "print-lock"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    python = subprocess.run(
        [sys.executable, "-c", "import runpy, sys; print(runpy.run_path(sys.argv[1])['CI_LOCK_DEFAULT'])", str(runner)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert make.returncode == python.returncode == 0, make.stderr + python.stderr
    values = make.stdout.strip().split()
    assert values[0] == python.stdout.strip()
    assert values == [python.stdout.strip(), python.stdout.strip(), "14400"]


def test_command_line_lock_settings_are_exported(tmp_path):
    shutil.copyfile(ROOT / "Makefile", tmp_path / "Makefile")
    (tmp_path / "probe.mk").write_text('include Makefile\nprint-lock:\n\t@echo "$$CI_LOCK" "$$CI_LOCK_TIMEOUT"\n')
    lock = tmp_path / "shared.lock"
    result = subprocess.run(
        ["make", "-s", "-f", "probe.mk", "print-lock", f"CI_LOCK={lock}", "CI_LOCK_TIMEOUT=23"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"{lock} 23"


def test_mutation_docs_do_not_claim_obsolete_lock_directory():
    doc = ROOT / "docs/testing/mutation-testing.md"
    if not doc.exists():
        pytest.skip("docs/testing/mutation-testing.md is excluded from public snapshots")
    text = doc.read_text()
    assert "Default `/work`" not in text
    assert "Default `/work/anlaufstelle-ci.lock`" not in text
