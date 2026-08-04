"""Migration 0134: Kategorie-Feld entfernen, Natural Key einengen (Refs #1749, ADR-040).

Drei Zusagen der Migration werden hier gepinnt:

1. **Vorwärts** — die Spalte ``category`` verschwindet, die Tripel-Constraint
   ``unique_facility_documenttype_name_category`` weicht der Paar-Constraint
   ``unique_facility_documenttype_name``, und die neue Regel greift auch
   wirklich auf DB-Ebene.
2. **Rückwärts** — der Rollback läuft durch (Spalte wieder da, Default
   ``contact``, alte Constraint wieder da). Er stellt die **ursprünglichen**
   Kategorien bewusst NICHT wieder her; genau das hält der Test fest, damit
   niemand den Rückweg für eine Datenrettung hält.
3. **Abbruch bei Duplikaten** — gibt es gleichnamige Typen einer Einrichtung
   (bis 0134 zulässig, wenn die Kategorie abwich), bricht der Guard HART ab,
   statt still einen der beiden zu verlieren. Kein Auto-Dedupe.

Muster: ``test_0130_attachment_owner_facility`` (MigratorTestCase + direkter
Guard-Aufruf).
"""

import importlib

import pytest
from django.db import IntegrityError, connection, transaction
from django_test_migrations.contrib.unittest_case import MigratorTestCase
from django_test_migrations.migrator import Migrator

MIGRATION = importlib.import_module("core.migrations.0134_remove_documenttype_category")

_VORHER = ("core", "0131_rls_attachment")
_NACHHER = ("core", "0134_remove_documenttype_category")


def _spalten(tabelle="core_documenttype"):
    with connection.cursor() as cursor:
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", [tabelle])
        return {row[0] for row in cursor.fetchall()}


def _constraints(tabelle="core_documenttype"):
    with connection.cursor() as cursor:
        cursor.execute("SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass", [tabelle])
        return {row[0] for row in cursor.fetchall()}


def _basisdaten(apps, *, kategorien=("contact",), name="Notiz"):
    """Eine Einrichtung mit je einem Typ ``name`` pro übergebener Kategorie."""
    Organization = apps.get_model("core", "Organization")
    Facility = apps.get_model("core", "Facility")
    DocumentType = apps.get_model("core", "DocumentType")

    org = Organization.objects.create(name="Mig-0134-Org")
    fac = Facility.objects.create(organization=org, name="Mig-0134-Stelle")
    for index, kategorie in enumerate(kategorien):
        DocumentType.objects.create(
            facility=fac,
            name=name,
            slug=f"mig-0134-{index}",
            category=kategorie,
        )
    return fac


class TestMigrate0134Vorwaerts(MigratorTestCase):
    migrate_from = _VORHER
    migrate_to = _NACHHER

    def prepare(self):
        # Kollisionsfrei: EIN Typ je Name — der Regelfall, den die Migration
        # unverändert durchlassen muss.
        self.facility_pk = _basisdaten(self.old_state.apps, kategorien=("service",)).pk

    def test_spalte_category_ist_weg(self):
        assert "category" not in _spalten()

    def test_constraint_wurde_auf_das_paar_eingeengt(self):
        namen = _constraints()
        assert "unique_facility_documenttype_name" in namen
        assert "unique_facility_documenttype_name_category" not in namen

    def test_bestandszeile_ueberlebt(self):
        DocumentType = self.new_state.apps.get_model("core", "DocumentType")
        assert DocumentType.objects.filter(facility_id=self.facility_pk, name="Notiz").count() == 1

    def test_neue_constraint_greift_auf_db_ebene(self):
        DocumentType = self.new_state.apps.get_model("core", "DocumentType")
        with pytest.raises(IntegrityError), transaction.atomic():
            DocumentType.objects.create(facility_id=self.facility_pk, name="Notiz", slug="mig-0134-neu")


@pytest.mark.django_db(transaction=True)
class TestMigrate0134Rueckwaerts:
    """Rollback 0134 → 0131 — strukturell vollständig, inhaltlich nicht."""

    def test_reverse_stellt_spalte_und_alte_constraint_her(self, request):
        migrator = Migrator()
        request.addfinalizer(migrator.reset)

        alt_state = migrator.apply_initial_migration(_VORHER)
        facility_pk = _basisdaten(alt_state.apps, kategorien=("admin",)).pk
        migrator.apply_tested_migration(_NACHHER)
        assert "category" not in _spalten()

        zurueck = migrator.apply_tested_migration(_VORHER)

        assert "category" in _spalten()
        namen = _constraints()
        assert "unique_facility_documenttype_name_category" in namen
        assert "unique_facility_documenttype_name" not in namen

        # Ehrlichkeit des Rückwegs: die ursprüngliche Kategorie ``admin`` ist
        # verloren, jede Zeile steht auf dem Modell-Default ``contact``.
        DocumentType = zurueck.apps.get_model("core", "DocumentType")
        wieder = DocumentType.objects.get(facility_id=facility_pk, name="Notiz")
        assert wieder.category == "contact"


@pytest.mark.django_db(transaction=True)
class TestMigrate0134GuardBrichtAb:
    """Gleichnamige Typen einer Einrichtung ⇒ harter Abbruch, kein Auto-Dedupe."""

    def test_duplikate_brechen_die_migration_ab(self, request):
        migrator = Migrator()
        request.addfinalizer(migrator.reset)

        alt_state = migrator.apply_initial_migration(_VORHER)
        # Bis 0134 zulässig: derselbe Name in zwei Kategorien.
        _basisdaten(alt_state.apps, kategorien=("note", "admin"))
        DocumentType = alt_state.apps.get_model("core", "DocumentType")

        try:
            with pytest.raises(RuntimeError) as excinfo:
                migrator.apply_tested_migration(_NACHHER)

            meldung = str(excinfo.value)
            assert "Notiz" in meldung, meldung
            assert "umbenennen" in meldung, meldung
            # Nichts angefasst: die Spalte steht noch, es wurde nichts gemergt.
            assert "category" in _spalten()
            assert DocumentType.objects.filter(name="Notiz").count() == 2
        finally:
            # Der Konflikt muss weg, bevor ``migrator.reset()`` im Finalizer die
            # Migrationskette bis zum Kopf nachzieht — sonst bricht der Guard
            # dort erneut ab und der (bestandene) Test stirbt im Teardown.
            DocumentType.objects.filter(slug="mig-0134-1").delete()

    def test_guard_ist_die_erste_operation(self):
        """Tragend: läuft der Guard nach dem RemoveField, prüfte er zu spät."""
        from django.db import migrations as dj_migrations

        erste = MIGRATION.Migration.operations[0]
        assert isinstance(erste, dj_migrations.RunPython)
        assert erste.code is MIGRATION.guard_no_duplicate_names
        assert erste.reverse_code is dj_migrations.RunPython.noop
