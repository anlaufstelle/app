"""Tests für das ``normalize_db_ownership`` Management-Command (Refs #1085).

Der Deploy-Migrate-Job (``docker-migrate.sh``) läuft als BYPASSRLS-**Admin**-
Rolle (``POSTGRES_USER``-Override, Refs #863) — dadurch entstehen alle
migrierten Tabellen admin-owned, und die NOSUPERUSER/NOBYPASSRLS-App-Runtime-
Rolle bekommt ``permission denied`` (Fresh-Install-Crash, Refs #1085).
``normalize_db_ownership`` weist alle vom aktuellen User (Admin) besessenen
Objekte dem Datenbank-Owner (= App-Rolle, gesetzt per ``ALTER DATABASE OWNER``
in ``01-app-role.sh``) zu — das 0093-Muster (Refs #1030) generalisiert.

``transaction=True``: CREATE/DROP ROLE + SET ROLE brauchen echte Sessions
(Muster aus ``test_rls.py::TestReplicationRoleGrant``). Der DB-Test-User ist
Superuser und DB-Owner; ``current_user == db_owner`` ist daher der Default-
No-op-Pfad. Für den Reassign-Pfad simuliert ``TMP_ADMIN`` die Deploy-Admin-
Rolle: NOSUPERUSER, aber Mitglied des DB-Owners (= ``GRANT app TO admin``).
"""

from __future__ import annotations

import pytest
from django.db import connection

from core.management.commands.normalize_db_ownership import normalize_db_ownership

TMP_ADMIN = "norm_owner_test_admin"
TMP_TABLE = "norm_owner_test_tbl"


def _db_owner() -> str:
    with connection.cursor() as cur:
        cur.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()")
        return cur.fetchone()[0]


def _table_owner(name: str) -> str | None:
    with connection.cursor() as cur:
        cur.execute("SELECT tableowner FROM pg_tables WHERE tablename = %s", [name])
        row = cur.fetchone()
    return row[0] if row else None


@pytest.mark.django_db(transaction=True)
class TestNormalizeDbOwnership:
    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("Ownership-Normalisierung benötigt PostgreSQL")
        self._cleanup()

    def teardown_method(self):
        if connection.vendor == "postgresql":
            self._cleanup()

    def _cleanup(self):
        with connection.cursor() as cur:
            cur.execute("RESET ROLE")
            cur.execute(f"DROP TABLE IF EXISTS {TMP_TABLE}")
            cur.execute(
                "DO $$ BEGIN "
                f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{TMP_ADMIN}') THEN "
                f"  DROP OWNED BY {TMP_ADMIN}; "
                f"  DROP ROLE {TMP_ADMIN}; "
                "END IF; END $$;"
            )

    def _make_admin_owned_table(self, owner: str):
        """Erzeuge eine Tabelle, die der (deploy-analogen) Admin-Rolle gehört."""
        with connection.cursor() as cur:
            cur.execute(f"CREATE ROLE {TMP_ADMIN} NOSUPERUSER NOBYPASSRLS")
            cur.execute(f"GRANT CREATE ON SCHEMA public TO {TMP_ADMIN}")
            cur.execute(f'GRANT "{owner}" TO {TMP_ADMIN}')  # admin ∈ db_owner (= app)
            cur.execute(f"SET ROLE {TMP_ADMIN}")
            cur.execute(f"CREATE TABLE {TMP_TABLE} (id int)")

    def test_noop_when_current_user_is_db_owner(self):
        # dev/test/e2e: migrate läuft als DB-Owner -> reine No-op, kein Fehler.
        result = normalize_db_ownership()
        assert result.current_user == _db_owner()
        assert result.reassigned is False

    def test_reassigns_admin_owned_table_to_db_owner(self):
        owner = _db_owner()
        self._make_admin_owned_table(owner)
        # Reproduziert den Fresh-Install-Bug: Tabelle gehört der Admin-Rolle.
        assert _table_owner(TMP_TABLE) == TMP_ADMIN

        # Command unter der Admin-Rolle ausführen (wie im Deploy).
        result = normalize_db_ownership()
        with connection.cursor() as cur:
            cur.execute("RESET ROLE")

        assert result.reassigned is True
        assert result.db_owner == owner
        assert result.current_user == TMP_ADMIN
        assert _table_owner(TMP_TABLE) == owner

    def test_idempotent_second_run_keeps_db_owner(self):
        owner = _db_owner()
        self._make_admin_owned_table(owner)
        normalize_db_ownership()
        normalize_db_ownership()  # zweiter Lauf darf nicht fehlschlagen
        with connection.cursor() as cur:
            cur.execute("RESET ROLE")
        assert _table_owner(TMP_TABLE) == owner
