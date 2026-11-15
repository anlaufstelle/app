"""Stop a session that would reuse a test database still holding rows of an aborted run."""

import pytest
from django.db import connection as default_connection

PROBED_TABLES = ("core_organization", "core_facility", "core_user")


def find_stale_rows(connection=default_connection) -> dict[str, int]:
    """Return ``{table: row count}`` for the probed tables that hold rows."""
    found = {}
    with connection.cursor() as cursor:
        for table in PROBED_TABLES:
            cursor.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed table names
            count = cursor.fetchone()[0]
            if count:
                found[table] = count
    return found


def stop_session(config, message: str) -> None:
    """Stop the session with ``message``.

    A serial session ends at once. An xdist worker must not call ``pytest.exit``: the controller then
    stops with an INTERNALERROR and the message is lost, so the worker fails its tests with the message.
    """
    if hasattr(config, "workerinput"):
        pytest.fail(message, pytrace=False)
    # Exit code 1 is what such a session returned before (tests failed), so callers that map the exit
    # code, such as the mutation runner, see no new code.
    pytest.exit(message, returncode=1)


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker, request):
    """Check the reused database once per session, right after pytest-django has set it up.

    A killed run leaves committed rows behind, and every later fixture that creates the same unique
    name then fails (``duplicate key ... (name)=(Teststelle)``) until a test happens to empty the tables:
    thousands of errors that hide the cause.
    """
    with django_db_blocker.unblock():
        stale = find_stale_rows()
        database = default_connection.settings_dict["NAME"]
    if stale:
        rows = ", ".join(f"{table}: {count}" for table, count in stale.items())
        stop_session(
            request.config,
            f"Test database {database} still holds rows that no running test wrote ({rows}). "
            "A previous run was probably aborted (kill, crash, restart) while a test had committed data, "
            "or another pytest session is using the same database. "
            "Start once with --create-db to rebuild it, then run again.",
        )
