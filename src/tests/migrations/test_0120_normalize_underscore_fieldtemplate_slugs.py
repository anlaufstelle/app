"""Direkte Testabdeckung der RunPython-Datenmigration 0120 (#1730-5, Refs #1058).

``0120_normalize_underscore_fieldtemplate_slugs`` normalisiert Unterstrich-Slugs
von ``FieldTemplate`` (``contact_stage`` → ``contact-stage``) — sie sind im
M2-Paketformat (``_SLUG_RE``) nicht referenzierbar. Der Lauf schreibt die
betroffenen ``data_json``/``prefilled_data``-Keys von Event/Client/Case/
QuickTemplate mit; Kollisionen werden nur inventarisiert, nicht umbenannt; der
``ENABLE ALWAYS``-Immutabilitäts-Trigger wird sauber geklammert. Muster:
``test_0037_migrate_encrypted_to_sensitivity``.
"""

import importlib

from django.db import connection
from django_test_migrations.contrib.unittest_case import MigratorTestCase

_MIGRATION = importlib.import_module("core.migrations.0120_normalize_underscore_fieldtemplate_slugs")


class TestMigrate0120NormalizeUnderscoreSlugs(MigratorTestCase):
    migrate_from = ("core", "0119_case_dynamic_data")
    migrate_to = ("core", "0120_normalize_underscore_fieldtemplate_slugs")

    def prepare(self):
        Organization = self.old_state.apps.get_model("core", "Organization")
        Facility = self.old_state.apps.get_model("core", "Facility")
        DocumentType = self.old_state.apps.get_model("core", "DocumentType")
        FieldTemplate = self.old_state.apps.get_model("core", "FieldTemplate")
        Client = self.old_state.apps.get_model("core", "Client")
        Case = self.old_state.apps.get_model("core", "Case")
        Event = self.old_state.apps.get_model("core", "Event")
        QuickTemplate = self.old_state.apps.get_model("core", "QuickTemplate")

        org = Organization.objects.create(name="Org")
        fac = Facility.objects.create(organization=org, name="Stelle")
        # Zweite Facility: eigener Unterstrich-Slug, unabhängige Normalisierung.
        fac2 = Facility.objects.create(organization=org, name="Stelle 2")

        doc_type = DocumentType.objects.create(facility=fac, name="Kontakt", slug="kontakt")

        # Normalfall: Unterstrich-Slug → wird normalisiert.
        self.underscore_pk = FieldTemplate.objects.create(
            facility=fac, name="Kontaktstufe", slug="contact_stage", field_type="text"
        ).pk
        # Kollision: Wunsch-Slug "foo-bar" ist bereits (ohne Unterstrich) belegt.
        FieldTemplate.objects.create(facility=fac, name="Foo Bar", slug="foo-bar", field_type="text")
        self.collision_pk = FieldTemplate.objects.create(
            facility=fac, name="Foo Bar Unterstrich", slug="foo_bar", field_type="text"
        ).pk
        # Reservierter Bestands-Slug (#1726): nur Report, kein Rename.
        self.reserved_pk = FieldTemplate.objects.create(
            facility=fac, name="Titelchen", slug="title", field_type="text"
        ).pk
        # Zweite Facility, gleicher Unterstrich-Slug → wird ebenfalls normalisiert.
        self.fac2_pk = FieldTemplate.objects.create(
            facility=fac2, name="Kontaktstufe", slug="contact_stage", field_type="text"
        ).pk

        # Datensätze, deren data_json/prefilled_data den alten Slug als Key tragen.
        client = Client.objects.create(
            facility=fac, pseudonym="P1", data_json={"contact_stage": "qualified", "andere": "x"}
        )
        self.client_pk = client.pk
        self.case_pk = Case.objects.create(
            facility=fac, client=client, title="Fall", data_json={"contact_stage": "geheim-token"}
        ).pk
        self.event_pk = Event.objects.create(
            facility=fac,
            document_type=doc_type,
            occurred_at="2026-01-01T10:00:00+00:00",
            data_json={"contact_stage": "wert", "foo_bar": "bleibt"},
        ).pk
        self.qt_pk = QuickTemplate.objects.create(
            facility=fac, document_type=doc_type, name="QT", prefilled_data={"contact_stage": "vorbelegt"}
        ).pk

    # --- Normalfall: Rename + data_json-Rewrite ------------------------------

    def test_underscore_slug_is_normalized(self):
        FieldTemplate = self.new_state.apps.get_model("core", "FieldTemplate")
        assert FieldTemplate.objects.get(pk=self.underscore_pk).slug == "contact-stage"
        assert FieldTemplate.objects.get(pk=self.fac2_pk).slug == "contact-stage"

    def test_data_json_keys_rewritten_on_all_entities(self):
        Client = self.new_state.apps.get_model("core", "Client")
        Case = self.new_state.apps.get_model("core", "Case")
        Event = self.new_state.apps.get_model("core", "Event")
        QuickTemplate = self.new_state.apps.get_model("core", "QuickTemplate")

        client = Client.objects.get(pk=self.client_pk)
        assert client.data_json == {"contact-stage": "qualified", "andere": "x"}

        case = Case.objects.get(pk=self.case_pk)
        # Wert unverändert übernommen (verschlüsselte Tokens sind key-unabhängig).
        assert case.data_json == {"contact-stage": "geheim-token"}

        event = Event.objects.get(pk=self.event_pk)
        # foo_bar bleibt (die Kollisions-Vorlage wurde nicht umbenannt).
        assert event.data_json == {"contact-stage": "wert", "foo_bar": "bleibt"}

        qt = QuickTemplate.objects.get(pk=self.qt_pk)
        assert qt.prefilled_data == {"contact-stage": "vorbelegt"}

    # --- Kollision: kein Rename ----------------------------------------------

    def test_collision_slug_is_not_renamed(self):
        FieldTemplate = self.new_state.apps.get_model("core", "FieldTemplate")
        assert FieldTemplate.objects.get(pk=self.collision_pk).slug == "foo_bar"

    def test_reserved_slug_is_not_renamed(self):
        FieldTemplate = self.new_state.apps.get_model("core", "FieldTemplate")
        assert FieldTemplate.objects.get(pk=self.reserved_pk).slug == "title"

    # --- Trigger wieder ENABLE ALWAYS ----------------------------------------

    def test_slug_immutable_trigger_is_enabled_always_afterwards(self):
        with connection.cursor() as cur:
            cur.execute("SELECT tgenabled FROM pg_trigger WHERE tgname = 'fieldtemplate_slug_immutable'")
            row = cur.fetchone()
        assert row is not None, "Trigger fieldtemplate_slug_immutable fehlt nach der Migration."
        assert row[0] == "A", f"Trigger muss ENABLE ALWAYS ('A') sein, ist aber {row[0]!r}."

    # --- Idempotenz -----------------------------------------------------------

    def test_second_run_changes_nothing(self):
        FieldTemplate = self.new_state.apps.get_model("core", "FieldTemplate")
        Client = self.new_state.apps.get_model("core", "Client")

        before_slugs = {ft.pk: ft.slug for ft in FieldTemplate.objects.all()}
        before_client = Client.objects.get(pk=self.client_pk).data_json

        with connection.schema_editor() as schema_editor:
            _MIGRATION.normalize_underscore_fieldtemplate_slugs(self.new_state.apps, schema_editor)

        after_slugs = {ft.pk: ft.slug for ft in FieldTemplate.objects.all()}
        assert before_slugs == after_slugs
        assert Client.objects.get(pk=self.client_pk).data_json == before_client
