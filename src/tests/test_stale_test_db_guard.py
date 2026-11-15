"""A reused test database that still holds rows of an aborted run stops the next run with a clear message.

``--reuse-db`` keeps ``test_<name>`` between runs. A run that is killed while a ``transaction=True`` test
has committed its rows leaves them behind; the next run then fails on every fixture that creates the same
unique name (``duplicate key ... (name)=(Teststelle)``) until some later test happens to empty the tables.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.db import connection

from core.models import Facility, Organization
from tests._stale_test_db import find_stale_rows, guard_enabled, stop_session

ROOT = Path(__file__).resolve().parents[2]

INNER_TEST = textwrap.dedent(
    """
    import pytest


    @pytest.mark.django_db
    def test_uses_the_database():
        assert True
    """
)

INNER_CONFTEST = textwrap.dedent(
    """
    import os

    import pytest

    pytest_plugins = ["tests._stale_test_db"]


    @pytest.fixture(scope="session")
    def django_db_modify_db_settings_xdist_suffix():
        # Run on exactly the database the outer test is connected to. Under xdist that is the
        # worker database test_<name>_gw<N>, which a plain inner session would not use.
        from django.db import connections

        for settings in connections.databases.values():
            settings.setdefault("TEST", {})["NAME"] = os.environ["INNER_SESSION_TEST_DATABASE"]
    """
)


def _run_inner_session(tmp_path, extra_env=None) -> subprocess.CompletedProcess:
    """Start a second pytest session on the very database this test is connected to."""
    inner = tmp_path / "test_inner_session.py"
    inner.write_text(INNER_TEST)
    # Loaded like the suite loads it: through a conftest, i.e. after pytest-django's own fixtures.
    (tmp_path / "conftest.py").write_text(INNER_CONFTEST)
    env = {k: v for k, v in os.environ.items() if k != "MUTMUT_DB_SLOT"}
    env["INNER_SESSION_TEST_DATABASE"] = connection.settings_dict["NAME"]
    env.update(extra_env or {})
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(inner),
            "-c",
            str(ROOT / "pyproject.toml"),
            "--rootdir",
            str(ROOT),
            "-p",
            "no:cacheprovider",
            "-q",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


@pytest.mark.django_db
def test_find_stale_rows_reports_only_tables_that_hold_rows():
    assert find_stale_rows(connection) == {}
    Facility.objects.create(organization=Organization.objects.create(name="Testorg"), name="Teststelle")
    assert find_stale_rows(connection) == {"core_organization": 1, "core_facility": 1}


def test_serial_session_exits_with_the_message_and_exit_code_one():
    with pytest.raises(pytest.exit.Exception) as caught:
        stop_session(SimpleNamespace(), "rows left behind")

    assert caught.value.returncode == 1
    assert "rows left behind" in str(caught.value)


def test_xdist_worker_fails_its_tests_with_the_message_instead_of_exiting():
    # pytest.exit inside a worker ends in an INTERNALERROR of the controller and the message is lost.
    with pytest.raises(pytest.fail.Exception, match="rows left behind"):
        stop_session(SimpleNamespace(workerinput={}), "rows left behind")


@pytest.mark.architecture
@pytest.mark.django_db(transaction=True)
def test_next_session_refuses_to_run_on_rows_left_by_an_aborted_run(tmp_path):
    # Committed rows are what a killed transaction=True test leaves behind.
    Facility.objects.create(organization=Organization.objects.create(name="Testorg"), name="Teststelle")

    result = _run_inner_session(tmp_path)

    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "--create-db" in output
    assert "core_facility" in output
    assert "IntegrityError" not in output
    # The guard acted on the database the outer test wrote to, not on a neighbour.
    assert connection.settings_dict["NAME"] in output


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, True),
        ({"MUTMUT_DB_SLOT": ""}, True),
        ({"MUTMUT_DB_SLOT": "x"}, True),
        ({"MUTMUT_DB_SLOT": "-1"}, True),
        ({"MUTMUT_DB_SLOT": "0"}, False),
        ({"MUTMUT_DB_SLOT": "3"}, False),
    ],
)
def test_guard_is_inactive_only_under_the_mutation_runner(environ, expected):
    assert guard_enabled(environ) is expected


@pytest.mark.architecture
@pytest.mark.django_db(transaction=True)
def test_guard_stays_silent_under_the_mutation_runner_although_rows_are_left(tmp_path):
    Facility.objects.create(organization=Organization.objects.create(name="Testorg"), name="Teststelle")

    result = _run_inner_session(tmp_path, {"MUTMUT_DB_SLOT": "0"})

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    assert "still holds rows" not in result.stdout + result.stderr


@pytest.mark.architecture
@pytest.mark.django_db(transaction=True)
def test_next_session_runs_on_a_database_without_leftover_rows(tmp_path):
    result = _run_inner_session(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
