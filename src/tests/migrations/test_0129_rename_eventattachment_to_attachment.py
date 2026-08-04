"""Rename-Migration 0129: Tabelle, Self-FK und RLS-Flags (M24, Refs #1738).

``RenameModel`` benennt nur die Tabelle um — Policies/RLS-Flags/GRANTs haengen
an der Tabellen-OID und muessen das Rename ueberleben (ADR-039 §11). Der
Self-FK ``superseded_by`` (Versionskette, Refs #587) muss nach dem Rename auf
``core_attachment`` selbst zeigen — ein haengengebliebener Verweis auf die
Alt-Tabelle waere ein stiller Bruch der Replace-History.
"""

import pytest
from django.db import connection
from django_test_migrations.contrib.unittest_case import MigratorTestCase


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


class TestMigrate0129Zwischenstand(MigratorTestCase):
    """Pin auf den ECHTEN Zwischenstand nach 0129 (G2-Befund: der Endzustands-
    Test oben kann eine Rename-Regression nicht sehen, weil 0131 ENABLE/FORCE
    idempotent neu setzt). Hier steht die DB exakt auf 0129: die Join-Policy
    aus 0047/0085 muss die umbenannte Tabelle weiter fail-closed halten —
    das ist die Lueckenlosigkeits-Zusage aus ADR-039 §11."""

    migrate_from = ("core", "0128_backfill_snapshot_person_counts")
    migrate_to = ("core", "0129_rename_eventattachment_to_attachment")

    def prepare(self):
        pass

    def test_join_policy_aktiv_auf_umbenannter_tabelle(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT qual FROM pg_policies WHERE tablename = 'core_attachment' AND policyname = 'facility_isolation'"
            )
            row = cursor.fetchone()
        assert row is not None, "Join-Policy hat das Rename nicht ueberlebt"
        qual = row[0] or ""
        assert "core_event" in qual, f"erwartet Join-Klausel, gefunden: {qual}"
        assert "is_super_admin" in qual, "0085-Bypass-Branch fehlt am Zwischenstand"

    def test_rls_flags_aktiv_am_zwischenstand(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'core_attachment'")
            row_security, force_security = cursor.fetchone()
        assert row_security and force_security
