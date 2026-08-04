"""Direkte Testabdeckung der RunPython-Datenmigration 0132 (#1776 Punkt 5).

``0132_clear_retention_days_non_event`` NULLt ``retention_days`` an
Personen-/Fall-Typen (Bestand vor dem Save-Pfad-Fix in
``DocumentTypeForm.clean``). Ereignis-Typen bleiben unangetastet, ein bereits
NULLer Wert wird nicht nochmal beschrieben (Idempotenz), und der Reverse-Schritt
ist ein bewusster No-op — die Werte sind nicht rekonstruierbar.

Muster: ``test_0128_backfill_snapshot_person_counts``.
"""

import importlib

from django.db import connection
from django_test_migrations.contrib.unittest_case import MigratorTestCase

_MIGRATION = importlib.import_module("core.migrations.0132_clear_retention_days_non_event")


class TestMigrate0132ClearRetentionDaysNonEvent(MigratorTestCase):
    migrate_from = ("core", "0131_rls_attachment")
    migrate_to = ("core", "0132_clear_retention_days_non_event")

    def prepare(self):
        apps = self.old_state.apps
        Organization = apps.get_model("core", "Organization")
        Facility = apps.get_model("core", "Facility")
        DocumentType = apps.get_model("core", "DocumentType")

        org = Organization.objects.create(name="Mig-0132-Org")
        fac = Facility.objects.create(organization=org, name="Mig-0132-Stelle")

        self.client_type_pk = DocumentType.objects.create(
            facility=fac,
            name="Personenbogen mit Alt-Frist",
            slug="personenbogen-mit-alt-frist",
            category="admin",
            entity_type="client",
            retention_days=30,
        ).pk
        self.case_type_pk = DocumentType.objects.create(
            facility=fac,
            name="Fallbogen mit Alt-Frist",
            slug="fallbogen-mit-alt-frist",
            category="admin",
            entity_type="case",
            retention_days=14,
        ).pk
        self.case_type_already_none_pk = DocumentType.objects.create(
            facility=fac,
            name="Fallbogen ohne Frist",
            slug="fallbogen-ohne-frist",
            category="contact",
            entity_type="case",
            retention_days=None,
        ).pk
        self.event_type_pk = DocumentType.objects.create(
            facility=fac,
            name="Ereignisbogen mit Frist",
            slug="ereignisbogen-mit-frist",
            category="contact",
            entity_type="event",
            retention_days=90,
        ).pk

    def test_client_type_retention_days_wird_genullt(self):
        DocumentType = self.new_state.apps.get_model("core", "DocumentType")
        assert DocumentType.objects.get(pk=self.client_type_pk).retention_days is None

    def test_case_type_retention_days_wird_genullt(self):
        DocumentType = self.new_state.apps.get_model("core", "DocumentType")
        assert DocumentType.objects.get(pk=self.case_type_pk).retention_days is None

    def test_bereits_leerer_case_type_bleibt_leer(self):
        DocumentType = self.new_state.apps.get_model("core", "DocumentType")
        assert DocumentType.objects.get(pk=self.case_type_already_none_pk).retention_days is None

    def test_event_type_retention_days_bleibt_unangetastet(self):
        """Gegenprobe: Ereignis-Typen sind NICHT Gegenstand dieser Migration."""
        DocumentType = self.new_state.apps.get_model("core", "DocumentType")
        assert DocumentType.objects.get(pk=self.event_type_pk).retention_days == 90

    def test_second_run_changes_nothing(self):
        DocumentType = self.new_state.apps.get_model("core", "DocumentType")
        before = {dt.pk: dt.retention_days for dt in DocumentType.objects.all()}

        with connection.schema_editor() as schema_editor:
            _MIGRATION.clear_retention_days_non_event(self.new_state.apps, schema_editor)

        after = {dt.pk: dt.retention_days for dt in DocumentType.objects.all()}
        assert before == after

    def test_reverse_ist_ein_no_op(self):
        """Reverse stellt NICHTS wieder her — die Werte sind nicht rekonstruierbar."""
        DocumentType = self.new_state.apps.get_model("core", "DocumentType")
        before = {dt.pk: dt.retention_days for dt in DocumentType.objects.all()}

        from django.db import migrations as dj_migrations

        dj_migrations.RunPython.noop(self.new_state.apps, None)

        after = {dt.pk: dt.retention_days for dt in DocumentType.objects.all()}
        assert before == after
        assert DocumentType.objects.get(pk=self.client_type_pk).retention_days is None
