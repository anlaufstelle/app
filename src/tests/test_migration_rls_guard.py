"""Fail-Loud-Guard fuer Datenmigrationen auf RLS-Tabellen (Refs #1746).

Backfill-Migrationen auf RLS-Tabellen setzen stillschweigend voraus, dass sie
als BYPASSRLS-Migrationsrolle laufen ([ADR-020](../../docs/adr/020-three-role-postgres-model.md)).
Liefe eine solche Migration versehentlich unter der NOBYPASSRLS-App-Rolle,
traefe sie ohne gesetztes ``app.current_facility_id`` **still 0 Zeilen** — die
Migration gilt als erfolgreich, der Backfill fehlt.

:func:`core.utils.migration_guards.assert_migration_role_bypasses_rls` macht
genau diesen Fall laut: harter Abbruch statt stillem Null-Lauf.

Rollenwechsel-Muster (``rls_test_role`` / ``as_rls_role``) aus
``tests/test_rls_functional.py`` — dieselbe Infrastruktur, die auch
``tests/test_connection_bypassrls.py`` fuer die Deploy-Gate-Assertion nutzt.
"""

from __future__ import annotations

import pytest
from django.db import connection, transaction

from core.utils.migration_guards import MigrationRoleError, assert_migration_role_bypasses_rls
from tests.test_rls_functional import (  # noqa: F401
    as_rls_role,
    rls_test_role,
)


class _FakeConnection:
    """Minimaler Connection-Stub fuer den Nicht-PostgreSQL-Zweig.

    Bewusst OHNE ``cursor()``: greift der Guard hier trotzdem auf die DB zu,
    schlaegt der Test mit ``AttributeError`` fehl statt still gruen zu sein.
    """

    def __init__(self, vendor: str) -> None:
        self.vendor = vendor


class _FakeSchemaEditor:
    """``RunPython``-``schema_editor``-Stub: der Guard braucht nur ``.connection``."""

    def __init__(self, db_connection) -> None:
        self.connection = db_connection


@pytest.mark.django_db
def test_guard_passes_under_migration_role():
    """Die Test-/Migrations-Connection ist Superuser bzw. BYPASSRLS -> kein Abbruch."""
    with connection.schema_editor() as schema_editor:
        assert assert_migration_role_bypasses_rls(schema_editor) is None


@pytest.mark.django_db(transaction=True)
def test_guard_aborts_under_non_bypass_role(rls_test_role):  # noqa: F811
    """Unter einer NOSUPERUSER/NOBYPASSRLS-Rolle bricht der Guard hart ab.

    Genau der Fehlkonfigurationsfall aus #1746: ohne Guard liefe die
    Datenmigration hier still auf 0 Zeilen durch.
    """
    with transaction.atomic(), as_rls_role(rls_test_role, facility_id=""):
        with connection.schema_editor() as schema_editor:
            with pytest.raises(MigrationRoleError) as excinfo:
                assert_migration_role_bypasses_rls(schema_editor)

    message = str(excinfo.value)
    assert rls_test_role in message, "Fehlermeldung muss die tatsaechliche Rolle nennen"
    assert "BYPASSRLS" in message
    assert "POSTGRES_ADMIN_USER" in message, "Fehlermeldung muss den Fix nennen (ADR-020)"


@pytest.mark.django_db(transaction=True)
def test_guard_does_not_accept_super_admin_guc_as_substitute(rls_test_role):  # noqa: F811
    """``app.is_super_admin`` ersetzt die BYPASSRLS-Rolle NICHT.

    Die RLS-Policies tragen einen Super-Admin-Bypass-Zweig ueber die GUC
    ``app.is_super_admin`` (Migration ``0085``), und
    ``core.services.system._db_admin.has_rls_bypass_context`` akzeptiert ihn
    bewusst fuer Wartungs-/Cron-Pfade. Fuer Migrationen gilt das **nicht**:
    ADR-020 legt die Migrationsrolle auf ``POSTGRES_ADMIN_USER`` (BYPASSRLS)
    fest, und eine request-lose Migration darf nicht von einer Session-GUC
    abhaengen, die kein Migrationspfad setzt. Der Guard prueft daher das
    Rollen-Attribut — dieser Test friert die Abgrenzung ein.
    """
    with transaction.atomic(), as_rls_role(rls_test_role, facility_id="") as cur:
        # is_local=true: die GUC verschwindet am Transaktionsende und leakt nicht
        # ueber die (pool-)geteilte Connection in Folgetests.
        cur.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        with connection.schema_editor() as schema_editor:
            with pytest.raises(MigrationRoleError):
                assert_migration_role_bypasses_rls(schema_editor)


def test_guard_is_noop_on_non_postgresql():
    """Auf Nicht-PostgreSQL ist der Guard ein No-op (kein ``pg_roles``-Zugriff)."""
    schema_editor = _FakeSchemaEditor(_FakeConnection("sqlite"))

    assert assert_migration_role_bypasses_rls(schema_editor) is None
