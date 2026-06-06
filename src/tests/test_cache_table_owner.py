"""Refs #1030: ``anlaufstelle_cache`` muss dem DB-Owner (App-Rolle) gehören.

Hintergrund: Migration 0092 legt die DatabaseCache-Tabelle per ``RunSQL`` an.
Im Deploy laufen Migrationen als BYPASSRLS-**Admin**-Rolle (Refs #863), wodurch
die Tabelle admin-owned wird und die Runtime-App-Rolle ``permission denied``
bekommt. Migration 0093 setzt den Owner env-agnostisch auf den Datenbank-Owner.

Zwei Tests:

* ``test_cache_table_owned_by_database_owner`` — Invariante nach allen
  Migrationen (läuft immer; im Single-Rollen-Test-Setup trivial erfüllt, dient
  als Regressions-Anker gegen versehentliche Owner-Drift).
* ``test_migration_0093_reassigns_foreign_owned_cache_table`` — reproduziert
  den Deploy-Fall direkt: Tabelle einer Fremdrolle übereignen, das Migrations-
  SQL anwenden, Owner muss zurück beim DB-Owner sein. Braucht Superuser-Rechte
  (Rollen anlegen + Owner wechseln) und überspringt sonst.
"""

import importlib

import pytest
from django.db import connection

_MIGRATION = "core.migrations.0093_cache_table_owner"
_TABLE = "anlaufstelle_cache"
_THROWAWAY_ROLE = "test_cache_owner_probe"


def _scalar(cur, sql, params=None):
    cur.execute(sql, params or [])
    row = cur.fetchone()
    return row[0] if row else None


def _table_owner(cur):
    return _scalar(
        cur,
        "SELECT pg_catalog.pg_get_userbyid(relowner) FROM pg_catalog.pg_class WHERE relname = %s",
        [_TABLE],
    )


def _db_owner(cur):
    return _scalar(
        cur,
        "SELECT pg_catalog.pg_get_userbyid(datdba) FROM pg_catalog.pg_database WHERE datname = current_database()",
    )


@pytest.mark.django_db
def test_cache_table_owned_by_database_owner():
    with connection.cursor() as cur:
        owner = _table_owner(cur)
        assert owner is not None, f"{_TABLE} fehlt — Migration 0092 nicht angewandt?"
        db_owner = _db_owner(cur)
    assert owner == db_owner, (
        f"{_TABLE} ist {owner}-owned, erwartet DB-Owner {db_owner}. "
        "Die Runtime-App-Rolle braucht Zugriff (Refs #1030, Migration 0093)."
    )


@pytest.mark.django_db(transaction=True)
def test_migration_0093_reassigns_foreign_owned_cache_table():
    fix_sql = importlib.import_module(_MIGRATION)._FIX_OWNER
    with connection.cursor() as cur:
        if not _scalar(cur, "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"):
            pytest.skip("braucht Superuser zum Rollen-/Owner-Wechsel")
        db_owner = _db_owner(cur)
        cur.execute(f"DROP ROLE IF EXISTS {_THROWAWAY_ROLE}")
        cur.execute(f"CREATE ROLE {_THROWAWAY_ROLE}")
        try:
            # Deploy-Fall simulieren: Tabelle gehört einer Fremdrolle (≙ Admin).
            cur.execute(f"ALTER TABLE {_TABLE} OWNER TO {_THROWAWAY_ROLE}")
            assert _table_owner(cur) == _THROWAWAY_ROLE  # Vorbedingung

            # Migrations-SQL anwenden → Owner muss zurück beim DB-Owner sein.
            cur.execute(fix_sql)
            assert _table_owner(cur) == db_owner, "Migration 0093 hat den Owner nicht auf den DB-Owner zurückgesetzt."
        finally:
            cur.execute(fix_sql)  # idempotenter Reset auf DB-Owner
            cur.execute(f"DROP ROLE IF EXISTS {_THROWAWAY_ROLE}")
