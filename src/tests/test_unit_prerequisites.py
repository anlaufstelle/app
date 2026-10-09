"""Unit prerequisites must be checked instead of silently skipped."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture
ROOT = Path(__file__).resolve().parents[2]


def test_empty_parameter_sets_fail_collection(tmp_path):
    test_file = tmp_path / "test_empty.py"
    test_file.write_text('import pytest\n@pytest.mark.parametrize("value", [])\ndef test_value(value): pass\n')
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(ROOT / "pyproject.toml"),
            str(test_file),
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "Empty parameter set" in result.stdout + result.stderr


def test_compose_without_example_checks_its_prerequisite():
    from tests.test_architecture_guards_ops import TestServerErrorSupportContactAcrossAllStacks

    try:
        TestServerErrorSupportContactAcrossAllStacks().test_error_page_settings_are_documented_in_the_example_env(
            Path("docker-compose.yml"), None
        )
    except pytest.skip.Exception as error:
        pytest.fail(f"Unchecked prerequisite: {error}")


def test_activity_length_boundary_checks_its_prerequisite():
    from core.models.activity import Activity
    from tests.test_boundary_maxlength import test_charfields_respect_max_length

    try:
        test_charfields_respect_max_length(Activity, {}, {}, None)
    except pytest.skip.Exception as error:
        pytest.fail(f"Unchecked prerequisite: {error}")


def test_offline_layout_prerequisite_is_executable():
    from tests.test_i18n_offline_db_update import TestNoLoadPathStaysSilent

    TestNoLoadPathStaysSilent().test_these_layouts_really_have_no_label()
