"""Direkte Testabdeckung der RunPython-Datenmigration 0037 (Refs #1479, TST-02).

``0037_migrate_encrypted_to_sensitivity`` setzt fuer alle bestehenden
``FieldTemplate`` mit ``is_encrypted=True`` und noch leerer ``sensitivity``
den Wert ``sensitivity='high'``. Ein Logikfehler hier haette
Sicherheits-/Zugriffsauswirkungen (die Sensibilitaet steuert
Verschluesselungs-/Anzeigepfad), liefe aber ueber ``makemigrations --check``
still gruen durch. Dieser ``MigratorTestCase`` faehrt die Migration real gegen
Bestandsdaten und prueft die Datenlogik.
"""

from django_test_migrations.contrib.unittest_case import MigratorTestCase


class TestMigrate0037EncryptedToSensitivity(MigratorTestCase):
    migrate_from = ("core", "0036_fieldtemplate_sensitivity")
    migrate_to = ("core", "0037_migrate_encrypted_to_sensitivity")

    def prepare(self):
        Organization = self.old_state.apps.get_model("core", "Organization")
        Facility = self.old_state.apps.get_model("core", "Facility")
        FieldTemplate = self.old_state.apps.get_model("core", "FieldTemplate")

        org = Organization.objects.create(name="Org")
        fac = Facility.objects.create(organization=org, name="Stelle")

        # Verschluesselt + leere Sensibilitaet -> muss auf 'high' heilen.
        self.encrypted_pk = FieldTemplate.objects.create(
            facility=fac,
            name="Krisennotiz",
            slug="krisennotiz",
            field_type="textarea",
            is_encrypted=True,
            sensitivity="",
        ).pk
        # Nicht verschluesselt -> bleibt leer (Filter greift nicht).
        self.plain_pk = FieldTemplate.objects.create(
            facility=fac,
            name="Dauer",
            slug="dauer",
            field_type="number",
            is_encrypted=False,
            sensitivity="",
        ).pk
        # Verschluesselt, aber bereits 'high' -> darf nicht angefasst werden.
        self.prehigh_pk = FieldTemplate.objects.create(
            facility=fac,
            name="Vorab-Hoch",
            slug="vorab-hoch",
            field_type="textarea",
            is_encrypted=True,
            sensitivity="high",
        ).pk

    def test_encrypted_templates_get_high_sensitivity(self):
        FieldTemplate = self.new_state.apps.get_model("core", "FieldTemplate")

        assert FieldTemplate.objects.get(pk=self.encrypted_pk).sensitivity == "high", (
            "Verschluesseltes FieldTemplate mit leerer Sensibilitaet wurde nicht auf 'high' geheilt."
        )
        assert FieldTemplate.objects.get(pk=self.plain_pk).sensitivity == "", (
            "Unverschluesseltes FieldTemplate darf keine Sensibilitaet erhalten."
        )
        assert FieldTemplate.objects.get(pk=self.prehigh_pk).sensitivity == "high", (
            "Bereits gesetzte Sensibilitaet ('high') darf die Migration nicht ueberschreiben."
        )
