"""Under mutmut, the ``rls_test_role`` skip must stay scoped to the fixture (Refs #1388).

Two dozen test modules import ``rls_test_role`` from ``tests.test_rls_functional``
(some indirectly, e.g. via ``tests.test_rls`` -> ``tests._residue_expectations``).
A module-level ``pytest.skip`` there under ``MUTANT_UNDER_TEST`` propagated through
every such import and silently dropped all of those modules from mutmut's test
pool, including the tests that never touch the fixture.

The probe below runs in a subprocess (mutmut sets the variable process-wide; an
in-process ``pytest.main()`` would itself trigger the DatabaseBlocker leak) with
the database port pointed at a closed port: the fixture test carries the
``django_db`` mark like the real users, so any test-DB setup before the skip
would turn it into an error instead of a skip.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

PROBE_MODULE = """
import pytest

from tests._residue_expectations import SCOPED_TABLES  # noqa: F401
from tests.test_rls_functional import rls_test_role  # noqa: F401


def test_without_fixture():
    assert True


@pytest.mark.django_db
def test_with_fixture(rls_test_role):
    raise AssertionError("must be skipped under mutmut")
"""


def _run_probe(tmp_path: Path, mutant_under_test: str) -> dict[str, str]:
    probe = tmp_path / "test_rls_skip_scope_probe.py"
    probe.write_text(PROBE_MODULE)
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    env.update(MUTANT_UNDER_TEST=mutant_under_test, POSTGRES_HOST="127.0.0.1", POSTGRES_PORT="1")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(probe),
            "-c",
            str(REPO_ROOT / "pyproject.toml"),
            "--rootdir",
            str(REPO_ROOT),
            "-v",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:randomly",
            "-p",
            "no:xdist",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    outcomes = dict(re.findall(r"::(test_\w+) (PASSED|SKIPPED|FAILED|ERROR)", result.stdout))
    assert outcomes, result.stdout + result.stderr
    return outcomes


@pytest.mark.parametrize("mutant_under_test", ["", "stats"], ids=["clean-phase", "stats-phase"])
def test_importing_module_keeps_tests_without_fixture_under_mutmut(tmp_path, mutant_under_test):
    assert _run_probe(tmp_path, mutant_under_test) == {
        "test_without_fixture": "PASSED",
        "test_with_fixture": "SKIPPED",
    }
