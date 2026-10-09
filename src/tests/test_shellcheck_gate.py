"""ShellCheck is a required prerequisite, never a successful no-op."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture
ROOT = Path(__file__).resolve().parents[2]


def test_missing_shellcheck_fails_with_install_hint():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_shell_scripts.py")],
        env={**os.environ, "PATH": ""},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "ShellCheck is required" in result.stdout


def test_shellcheck_target_calls_required_guard():
    result = subprocess.run(["make", "-n", "shellcheck"], cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "scripts/check_shell_scripts.py" in result.stdout


def test_shellcheck_reports_script_errors(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/broken.sh").write_text("#!/bin/bash\necho ${value\n")
    executable = tmp_path / "shellcheck"
    executable.write_text("#!/bin/sh\necho SC1073\nexit 1\n")
    executable.chmod(0o755)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_shell_scripts.py"), "--root", str(tmp_path)],
        env={**os.environ, "PATH": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "SC1073" in result.stdout


def test_existing_guard_requires_shellcheck(monkeypatch):
    from tests.test_architecture_guards_ops import TestShippedShellScriptsSyntax

    monkeypatch.setattr("shutil.which", lambda name, **kwargs: None)
    try:
        with pytest.raises(AssertionError, match="ShellCheck is required"):
            TestShippedShellScriptsSyntax().test_scripts_are_shellcheck_clean()
    except pytest.skip.Exception as error:
        pytest.fail(f"Required lint was skipped: {error}")


def test_shellcheck_is_available_to_the_python_environment():
    import shutil

    executable = shutil.which(
        "shellcheck", path=os.pathsep.join((str(Path(sys.executable).parent), os.environ["PATH"]))
    )
    assert executable is not None, "Install the locked development requirements"
    result = subprocess.run([str(executable), "--version"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "version: 0.11.0" in result.stdout
