"""RLS-Swap 0131: direkte Policy auf ``core_attachment`` + Reverse-Pins (Refs #1738).

ADR-039 §11: 0131 ersetzt die Join-Policy (0047/0085) durch den direkten
Spaltenvergleich (Vorlage 0127). Der Reverse muss die **0085-Variante inkl.
``is_super_admin``-OR-Branch** wiederherstellen (0047 hatte den Branch nicht —
sonst faellt ``test_super_admin_bypass_branch_on_all_policies``) und darf RLS
**nicht** deaktivieren (anders als Vorlage 0111/0127, die RLS neu einfuehrten).
"""

import importlib

import pytest
from django.db import connection
from django_test_migrations.contrib.unittest_case import MigratorTestCase

MIGRATION = importlib.import_module("core.migrations.0131_rls_attachment")


@pytest.mark.django_db
class TestPolicy0131:
    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("pg_policies braucht PostgreSQL")

    def _qual(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT qual FROM pg_policies WHERE tablename = 'core_attachment' AND policyname = 'facility_isolation'"
            )
            row = cursor.fetchone()
        assert row is not None, "facility_isolation-Policy fehlt auf core_attachment"
        return row[0] or ""

    def test_policy_ist_direkt_nicht_join(self):
        qual = self._qual()
        assert "facility_id" in qual, f"direkte facility-Klausel fehlt: {qual}"
        assert "core_event" not in qual, f"Join-Klausel noch aktiv: {qual}"

    def test_policy_hat_superadmin_bypass(self):
        assert "is_super_admin" in self._qual()

    def test_force_rls_aktiv(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'core_attachment'")
            row_security, force_security = cursor.fetchone()
        assert row_security and force_security


class TestReversePins0131:
    """String-Pins auf den Migrations-Konstanten — der Reverse laesst sich im
    Test nicht fahren, seine Eigenschaften sind aber sicherheitskritisch."""

    def test_reverse_stellt_0085_join_variante_wieder_her(self):
        assert "SELECT id FROM core_event" in MIGRATION.REVERSE_SQL
        assert "is_super_admin" in MIGRATION.REVERSE_SQL, "Reverse ohne Bypass-Branch = 0047-Variante (falsch)"

    def test_reverse_deaktiviert_rls_nicht(self):
        assert "DISABLE ROW LEVEL SECURITY" not in MIGRATION.REVERSE_SQL

    def test_forward_ist_idempotent_droppt_altpolicy(self):
        assert "DROP POLICY IF EXISTS" in MIGRATION.ENABLE_SQL


class TestMigrate0131ReverseExecution(MigratorTestCase):
    """Fuehrt den 0131-Reverse ECHT aus (G2-Befund: Substring-Pins koennen eine
    kaputte Reverse-Policy nicht sehen). Rueckwaerts 0131→0130: die Join-Policy
    muss in der 0085-Variante (inkl. Bypass-Branch) wiederhergestellt sein und
    RLS aktiv bleiben."""

    migrate_from = ("core", "0131_rls_attachment")
    migrate_to = ("core", "0130_attachment_owner_facility")

    def prepare(self):
        # Leerer Bestand: der Rollback-Guard (nur event-owned zulaessig) laesst
        # den Reverse damit durch.
        pass

    def test_reverse_stellt_join_policy_wieder_her(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT qual FROM pg_policies WHERE tablename = 'core_attachment' AND policyname = 'facility_isolation'"
            )
            row = cursor.fetchone()
        assert row is not None, "Nach dem Reverse existiert keine Policy — Tabelle waere default-deny"
        qual = row[0] or ""
        assert "core_event" in qual, f"erwartet Join-Klausel nach Reverse, gefunden: {qual}"
        assert "is_super_admin" in qual, "Reverse ohne Bypass-Branch = 0047-Variante (falsch)"

    def test_reverse_laesst_rls_aktiv(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'core_attachment'")
            row_security, force_security = cursor.fetchone()
        assert row_security and force_security, "Reverse hat RLS deaktiviert — verboten (ADR-039 §11)"


class TestOperationOrder0131:
    def test_rollback_guard_ist_letzte_operation(self):
        from django.db import migrations as dj_migrations

        last = MIGRATION.Migration.operations[-1]
        assert isinstance(last, dj_migrations.RunPython)
        assert last.reverse_code is MIGRATION.guard_no_owner_rows
        assert last.code is dj_migrations.RunPython.noop
