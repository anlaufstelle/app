"""Direkte Testabdeckung der RunPython-Datenmigration 0074 (Refs #1479, TST-02).

``0074_redact_legacy_eventhistory_delete`` redaktiert Bestands-EventHistory-
DELETE-Eintraege, deren ``data_before`` noch den vollen Klartext trug
(DSGVO Art. 17 / § 67 SGB X, Refs #714). Ein Logikfehler haette hier
personenbezogenen Klartext in der append-only Historie belassen — ein stiller
DSGVO-Verstoss, den ``makemigrations --check`` nicht erkennt. Dieser
``MigratorTestCase`` prueft die Redaktions-Datenlogik real gegen Bestandsdaten:
Klartext verschwindet, die Feldnamen (Slugs) bleiben sortiert erhalten, bereits
redaktierte und Nicht-DELETE-Eintraege bleiben unangetastet.
"""

from django.utils import timezone
from django_test_migrations.contrib.unittest_case import MigratorTestCase


class TestMigrate0074RedactLegacyEventHistoryDelete(MigratorTestCase):
    migrate_from = ("core", "0073_settings_auditlog_retention_months")
    migrate_to = ("core", "0074_redact_legacy_eventhistory_delete")

    def prepare(self):
        Organization = self.old_state.apps.get_model("core", "Organization")
        Facility = self.old_state.apps.get_model("core", "Facility")
        DocumentType = self.old_state.apps.get_model("core", "DocumentType")
        Event = self.old_state.apps.get_model("core", "Event")
        EventHistory = self.old_state.apps.get_model("core", "EventHistory")

        org = Organization.objects.create(name="Org")
        fac = Facility.objects.create(organization=org, name="Stelle")
        dt = DocumentType.objects.create(facility=fac, name="Kontakt")
        event = Event.objects.create(
            facility=fac,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={},
        )

        # Legacy-DELETE mit vollem Klartext -> muss redaktiert werden.
        self.legacy_pk = EventHistory.objects.create(
            event=event,
            action="delete",
            data_before={"notiz": "geheimer Klartext", "dauer": 15},
        ).pk
        # Bereits redaktiert -> bleibt exakt gleich (Idempotenz).
        self.redacted_pk = EventHistory.objects.create(
            event=event,
            action="delete",
            data_before={"_redacted": True, "fields": ["notiz"]},
        ).pk
        # Nicht-DELETE (update) -> ausserhalb des Scopes, Klartext bleibt.
        self.update_pk = EventHistory.objects.create(
            event=event,
            action="update",
            data_before={"notiz": "weiterhin sichtbar"},
        ).pk

    def test_legacy_delete_history_is_redacted(self):
        EventHistory = self.new_state.apps.get_model("core", "EventHistory")

        legacy = EventHistory.objects.get(pk=self.legacy_pk)
        assert legacy.data_before == {"_redacted": True, "fields": ["dauer", "notiz"]}, (
            "Legacy-DELETE-Klartext wurde nicht redaktiert oder die Slug-Liste ist nicht sortiert: "
            f"{legacy.data_before!r}"
        )

        redacted = EventHistory.objects.get(pk=self.redacted_pk)
        assert redacted.data_before == {"_redacted": True, "fields": ["notiz"]}, (
            "Bereits redaktierter Eintrag darf nicht erneut angefasst werden."
        )

        update = EventHistory.objects.get(pk=self.update_pk)
        assert update.data_before == {"notiz": "weiterhin sichtbar"}, (
            "Nicht-DELETE-Eintrag (action='update') darf nicht redaktiert werden."
        )
