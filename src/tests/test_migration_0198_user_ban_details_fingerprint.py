"""Forward/backward safety of migration 0198 (ban details fingerprint on the user row)."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

APP = "core"
TABLE = "core_user"
COLUMN = "ban_details_fingerprint"
TARGET_BACKWARD = [(APP, "0197_drop_statistics_event_flat_view")]
TARGET_FORWARD = [(APP, "0198_user_ban_details_fingerprint")]


def _column_default():
    """``(exists, max length)`` of the fingerprint column."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT character_maximum_length, is_nullable FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = %s",
            [TABLE, COLUMN],
        )
        return cursor.fetchone()


@pytest.mark.django_db(transaction=False)
def test_migration_0198_runs_backward_and_forward_again():
    leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
    assert _column_default() == (64, "NO")
    try:
        MigrationExecutor(connection).migrate(TARGET_BACKWARD)
        assert _column_default() is None, "backward must drop the column"

        MigrationExecutor(connection).migrate(TARGET_FORWARD)
        assert _column_default() == (64, "NO")
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT count(*) FROM {TABLE} WHERE {COLUMN} <> ''")
            assert cursor.fetchone()[0] == 0, "existing accounts start with the details shown"
    finally:
        MigrationExecutor(connection).migrate(leaves)
