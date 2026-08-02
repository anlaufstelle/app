"""Direkte Testabdeckung der RunPython-Datenmigration 0115 (Refs #1058, #1713).

``0115_global_block_sequence`` hebt Gruppen und ungruppierte Felder in EINE
globale Block-Sequenz je DocumentType: Gruppen bekommen die Positionen
``0..k-1`` (in ``(sort_order, name)``-Reihenfolge), die ungruppierten Felder
schließen mit ``k..k+m-1`` an (in ``sort_order``-Reihenfolge). Gruppierte Felder
behalten ihren Innen-``sort_order``. Ein Logikfehler hier verschöbe das Layout
aller Bestands-DocumentTypes, liefe aber über ``makemigrations --check`` still
durch — daher fährt dieser ``MigratorTestCase`` die Migration real gegen
Bestandsdaten.
"""

from django_test_migrations.contrib.unittest_case import MigratorTestCase


class TestMigrate0115GlobalBlockSequence(MigratorTestCase):
    migrate_from = ("core", "0114_rls_fieldcondition")
    migrate_to = ("core", "0115_global_block_sequence")

    def prepare(self):
        Organization = self.old_state.apps.get_model("core", "Organization")
        Facility = self.old_state.apps.get_model("core", "Facility")
        DocumentType = self.old_state.apps.get_model("core", "DocumentType")
        FieldTemplate = self.old_state.apps.get_model("core", "FieldTemplate")
        FieldGroup = self.old_state.apps.get_model("core", "FieldGroup")
        DocumentTypeField = self.old_state.apps.get_model("core", "DocumentTypeField")

        org = Organization.objects.create(name="Org")
        fac = Facility.objects.create(organization=org, name="Stelle")
        dt = DocumentType.objects.create(facility=fac, name="Kontakt", category="contact")

        # Zwei Gruppen mit alter, getrennter Sequenz — bewusst „krumm" (10/20),
        # damit die Kompaktierung auf 0/1 sichtbar wird. B vor A per sort_order.
        self.g_a = FieldGroup.objects.create(document_type=dt, name="A-Gruppe", slug="a-gruppe", sort_order=20)
        self.g_b = FieldGroup.objects.create(document_type=dt, name="B-Gruppe", slug="b-gruppe", sort_order=10)

        def _ft(name, slug):
            return FieldTemplate.objects.create(facility=fac, name=name, slug=slug, field_type="text")

        # Gruppiertes Feld — Innen-sort_order muss unangetastet bleiben.
        self.grouped = DocumentTypeField.objects.create(
            document_type=dt, field_template=_ft("In Gruppe", "in-gruppe"), field_group=self.g_b, sort_order=3
        )
        # Zwei ungruppierte Felder mit alter eigener Sequenz 0/1.
        self.free_0 = DocumentTypeField.objects.create(
            document_type=dt, field_template=_ft("Frei 0", "frei-0"), field_group=None, sort_order=0
        )
        self.free_1 = DocumentTypeField.objects.create(
            document_type=dt, field_template=_ft("Frei 1", "frei-1"), field_group=None, sort_order=1
        )

    def test_groups_compact_then_free_fields_appended(self):
        FieldGroup = self.new_state.apps.get_model("core", "FieldGroup")
        DocumentTypeField = self.new_state.apps.get_model("core", "DocumentTypeField")

        # Gruppen kompakt 0..k-1 in (sort_order, name) — B(10) vor A(20).
        assert FieldGroup.objects.get(pk=self.g_b.pk).sort_order == 0
        assert FieldGroup.objects.get(pk=self.g_a.pk).sort_order == 1

        # Ungruppierte Felder schließen bei k=2 an (globale Sequenz).
        assert DocumentTypeField.objects.get(pk=self.free_0.pk).sort_order == 2
        assert DocumentTypeField.objects.get(pk=self.free_1.pk).sort_order == 3

        # Gruppiertes Feld: Innen-sort_order unverändert.
        assert DocumentTypeField.objects.get(pk=self.grouped.pk).sort_order == 3
