"""Tests für das ``normalize_db_ownership`` Management-Command (Refs #1085, #1026).

Der Deploy-Migrate-Job (``docker-migrate.sh``) läuft als BYPASSRLS-**Admin**-
Rolle (``POSTGRES_USER``-Override, Refs #863) — migrierte Tabellen entstehen
dadurch admin-owned. ``normalize_db_ownership`` weist alle vom aktuellen User
(Admin) besessenen Objekte dem **Datenbank-Owner** (``pg_database.datdba``,
gesetzt per ``ALTER DATABASE OWNER`` in ``01-app-role.sh``) zu — das
0093-Muster (Refs #1030) generalisiert.

Seit der Runtime-/Owner-Trennung (A1.4, Refs #1026) ist der Datenbank-Owner die
**Admin-Rolle** selbst (nicht mehr die App-Rolle). Weil ``migrate`` als eben
diese Admin-Rolle läuft, gilt ``current_user == db_owner`` und der Reassign ist
im Prod-Modell ein **No-op**; die App-Runtime bekommt ihre Tabellen-Rechte per
DML-GRANT (``ALTER DEFAULT PRIVILEGES``), nicht mehr über Ownership. Der Command
bleibt als idempotentes Sicherheitsnetz für Bestands-/Edge-Fälle erhalten und
wird hier weiterhin auf beiden Pfaden (No-op + Reassign) getestet.

``transaction=True``: CREATE/DROP ROLE + SET ROLE brauchen echte Sessions
(Muster aus ``test_rls.py::TestAppendOnlyTriggerOwnership``). Der DB-Test-User
ist Superuser und DB-Owner; ``current_user == db_owner`` ist daher der Default-
No-op-Pfad. Für den Reassign-Pfad simuliert ``TMP_ADMIN`` eine Rolle, die
Objekte besitzt, aber selbst nicht der Datenbank-Owner ist: NOSUPERUSER und
Mitglied des DB-Owners (``GRANT <db_owner> TO tmp_admin``), damit der Reassign
die Objekte an den Datenbank-Owner zurückgeben kann.
"""

from __future__ import annotations

import pytest
from django.db import connection

from core.management.commands.normalize_db_ownership import normalize_db_ownership
from tests.conftest import xdist_role_suffix

# Refs #1479 (TST-03): Worker-Suffix am cluster-globalen Rollennamen, sonst
# kollidieren zwei xdist-Worker beim CREATE/DROP ROLE. Der Tabellenname ist
# DB-lokal (jeder Worker hat eine eigene Test-DB) und braucht keinen Suffix.
TMP_ADMIN = f"norm_owner_test_admin{xdist_role_suffix()}"
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
