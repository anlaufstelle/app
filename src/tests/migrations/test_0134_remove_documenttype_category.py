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

Muster durchgehend ``MigratorTestCase`` (wie ``test_0130_attachment_owner_
facility``) — auch für den Rückweg, der schlicht ein ``MigratorTestCase`` mit
umgekehrtem ``migrate_from``/``migrate_to`` ist. Ein direkt instanziierter
``Migrator`` wäre hier die falsche Wahl: er lässt die ``post_migrate``-Receiver
laufen bzw. stellt sie nicht wieder her und hinterlässt die Test-DB ohne
``django_content_type``-Zeilen — nachfolgende Tests derselben Session fielen
dann mit fremd aussehenden FK-Fehlern um.
"""

import importlib

import pytest
from django.db import IntegrityError, connection, transaction
from django_test_migrations.contrib.unittest_case import MigratorTestCase

MIGRATION = importlib.import_module("core.migrations.0134_remove_documenttype_category")

_VORHER = ("core", "0131_rls_attachment")
_NACHHER = ("core", "0134_remove_documenttype_category")


class _SchemaEditorStub:
    """Reicht nur ``connection`` durch — mehr nutzt der Guard nicht."""

    def __init__(self, conn):
        self.connection = conn


def _spalten(tabelle="core_documenttype"):
    with connection.cursor() as cursor:
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", [tabelle])
        return {row[0] for row in cursor.fetchall()}


def _constraints(tabelle="core_documenttype"):
    with connection.cursor() as cursor:
        cursor.execute("SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass", [tabelle])
        return {row[0] for row in cursor.fetchall()}


def _facility(apps, suffix=""):
    Organization = apps.get_model("core", "Organization")
    Facility = apps.get_model("core", "Facility")
    org = Organization.objects.create(name=f"Mig-0134-Org{suffix}")
    return Facility.objects.create(organization=org, name=f"Mig-0134-Stelle{suffix}")


class TestMigrate0134Vorwaerts(MigratorTestCase):
    migrate_from = _VORHER
    migrate_to = _NACHHER

    def prepare(self):
        # Kollisionsfrei: EIN Typ je Name — der Regelfall, den die Migration
        # unverändert durchlassen muss.
        apps = self.old_state.apps
        facility = _facility(apps)
        apps.get_model("core", "DocumentType").objects.create(
            facility=facility, name="Notiz", slug="mig-0134-0", category="service"
        )
        self.facility_pk = facility.pk

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


class TestMigrate0134Rueckwaerts(MigratorTestCase):
    """Rollback 0134 → 0131 — strukturell vollständig, inhaltlich nicht."""

    migrate_from = _NACHHER
    migrate_to = _VORHER

    def prepare(self):
        apps = self.old_state.apps
        facility = _facility(apps, suffix="-rev")
        apps.get_model("core", "DocumentType").objects.create(facility=facility, name="Notiz", slug="mig-0134-rev")
        self.facility_pk = facility.pk

    def test_spalte_und_alte_constraint_sind_zurueck(self):
        assert "category" in _spalten()
        namen = _constraints()
        assert "unique_facility_documenttype_name_category" in namen
        assert "unique_facility_documenttype_name" not in namen

    def test_reverse_setzt_alles_auf_den_modell_default(self):
        """Ehrlichkeit des Rückwegs: historische Kategorien sind verloren."""
        DocumentType = self.new_state.apps.get_model("core", "DocumentType")
        assert DocumentType.objects.get(facility_id=self.facility_pk, name="Notiz").category == "contact"


class TestGuard0134(MigratorTestCase):
    """Der Duplikat-Guard, direkt gegen den Stand VOR 0134 aufgerufen.

    Der Guard kann nicht am Endzustand geprüft werden: dort verbietet die neue
    Constraint die Konstellation, die er finden soll. Deshalb bleibt die DB auf
    ``0131`` stehen (Muster ``TestReverseGuard0130``).
    """

    migrate_from = ("core", "0130_attachment_owner_facility")
    migrate_to = _VORHER

    def prepare(self):
        pass

    def _doc_types(self, *kategorien, name="Notiz"):
        apps = self.new_state.apps
        facility = _facility(apps, suffix=f"-guard-{len(kategorien)}-{name}")
        DocumentType = apps.get_model("core", "DocumentType")
        for index, kategorie in enumerate(kategorien):
            DocumentType.objects.create(
                facility=facility, name=name, slug=f"mig-0134-guard-{name}-{index}", category=kategorie
            )
        return apps

    def test_guard_bricht_bei_gleichnamigen_typen_ab(self):
        # Bis 0134 zulässig: derselbe Name in zwei Kategorien.
        apps = self._doc_types("note", "admin")
        DocumentType = apps.get_model("core", "DocumentType")
        try:
            with pytest.raises(RuntimeError) as excinfo:
                MIGRATION.guard_no_duplicate_names(apps, _SchemaEditorStub(connection))
            meldung = str(excinfo.value)
            assert "Notiz" in meldung, meldung
            assert "umbenennen" in meldung, meldung
            assert DocumentType.objects.filter(name="Notiz").count() == 2, "Guard hat Zeilen angefasst"
        finally:
            # ``TransactionTestCase`` rollt nicht zurück: der Konflikt muss weg,
            # bevor ``tearDown`` die Migrationskette bis zum Kopf nachzieht —
            # sonst bricht der Guard dort erneut ab und der (bestandene) Test
            # stirbt im Teardown.
            DocumentType.objects.filter(name="Notiz").delete()

    def test_guard_laesst_eindeutige_namen_durch(self):
        apps = self._doc_types("note", name="Eindeutig")
        MIGRATION.guard_no_duplicate_names(apps, _SchemaEditorStub(connection))

    def test_guard_ist_die_erste_operation(self):
        """Tragend: läuft der Guard nach dem RemoveField, prüfte er zu spät."""
        from django.db import migrations as dj_migrations

        erste = MIGRATION.Migration.operations[0]
        assert isinstance(erste, dj_migrations.RunPython)
        assert erste.code is MIGRATION.guard_no_duplicate_names
        assert erste.reverse_code is dj_migrations.RunPython.noop
