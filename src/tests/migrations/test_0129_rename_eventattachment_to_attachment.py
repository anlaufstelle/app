"""Rename-Migration 0129: Tabelle, Self-FK und RLS-Flags (M24, Refs #1738).

``RenameModel`` benennt nur die Tabelle um — Policies/RLS-Flags/GRANTs haengen
an der Tabellen-OID und muessen das Rename ueberleben (ADR-039 §11). Der
Self-FK ``superseded_by`` (Versionskette, Refs #587) muss nach dem Rename auf
``core_attachment`` selbst zeigen — ein haengengebliebener Verweis auf die
Alt-Tabelle waere ein stiller Bruch der Replace-History.
"""

import pytest
from django.db import connection


@pytest.mark.django_db
class TestRename0129:
    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("Schema-Introspektion braucht PostgreSQL")

    def test_tabelle_umbenannt_alt_name_weg(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('core_attachment'), to_regclass('core_eventattachment')")
            new_table, old_table = cursor.fetchone()
        assert new_table is not None, "core_attachment fehlt"
        assert old_table is None, "core_eventattachment existiert noch"

    def test_self_fk_zeigt_auf_neue_tabelle(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT confrelid::regclass::text
                FROM pg_constraint
                WHERE conrelid = 'core_attachment'::regclass
                  AND contype = 'f'
                  AND conname LIKE '%superseded_by%'
                """
            )
            targets = [row[0] for row in cursor.fetchall()]
        assert targets == ["core_attachment"], f"Self-FK zeigt auf: {targets}"

    def test_rls_flags_ueberleben_das_rename(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'core_attachment'")
            row_security, force_security = cursor.fetchone()
        assert row_security and force_security, "ENABLE/FORCE ROW LEVEL SECURITY nach Rename verloren"
