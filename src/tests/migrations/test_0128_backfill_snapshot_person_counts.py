"""Direkte Testabdeckung der RunPython-Datenmigration 0128 (K0-b, Refs #1770).

``0128_backfill_snapshot_person_counts`` traegt die DISTINCT-Personenzahlen in
Bestands-Snapshots nach. Der Kern ist die **Fail-safe-Regel**: eine Zahl wird
nur uebernommen, wenn die live gezaehlte Ereigniszahl des Buckets exakt mit dem
Snapshot uebereinstimmt — sonst ist der Monat nicht mehr vollstaendig in der DB
(Loeschfristen, Soft-Delete) und die Personenzahl bleibt ``None`` = unbekannt.
Ein Logikfehler hier liefe ueber ``makemigrations --check`` still gruen durch
und wuerde die k-Suppression fail-open aushebeln.

Muster: ``test_0122_backfill_backend_data``.
"""

import importlib
from datetime import UTC, datetime

from django.db import connection
from django_test_migrations.contrib.unittest_case import MigratorTestCase

_MIGRATION = importlib.import_module("core.migrations.0128_backfill_snapshot_person_counts")


def _snapshot_payload(cluster_count, stage_counts):
    return {
        "total_contacts": sum(stage_counts.values()),
        "by_contact_stage": dict(stage_counts),
        "by_document_type": [],
        "by_age_cluster": [{"cluster": "18_26", "label": "18–26", "count": cluster_count}],
        "unique_clients": 2,
    }


class TestMigrate0128BackfillSnapshotPersonCounts(MigratorTestCase):
    migrate_from = ("core", "0127_rls_offline_client_revocation")
    migrate_to = ("core", "0128_backfill_snapshot_person_counts")

    def prepare(self):
        apps = self.old_state.apps
        Organization = apps.get_model("core", "Organization")
        Facility = apps.get_model("core", "Facility")
        User = apps.get_model("core", "User")
        Client = apps.get_model("core", "Client")
        DocumentType = apps.get_model("core", "DocumentType")
        Event = apps.get_model("core", "Event")
        StatisticsSnapshot = apps.get_model("core", "StatisticsSnapshot")

        org = Organization.objects.create(name="Org")
        fac = Facility.objects.create(organization=org, name="Stelle")
        user = User.objects.create(username="mig-0128", facility=fac, role="staff")
        doc_type = DocumentType.objects.create(facility=fac, name="Kontakt", category="contact")

        # Zwei Personen, drei Ereignisse im Januar 2025 — die Personenzahl des
        # Buckets ist damit 2, die Ereigniszahl 3.
        personen = [
            Client.objects.create(
                facility=fac,
                pseudonym=f"Mig-{index}",
                contact_stage="identified",
                age_cluster="18_26",
                created_by=user,
            )
            for index in range(2)
        ]
        for occurred, person in (
            (datetime(2025, 1, 5, 10, 0, tzinfo=UTC), personen[0]),
            (datetime(2025, 1, 6, 10, 0, tzinfo=UTC), personen[0]),
            (datetime(2025, 1, 7, 10, 0, tzinfo=UTC), personen[1]),
        ):
            Event.objects.create(
                facility=fac,
                client=person,
                document_type=doc_type,
                occurred_at=occurred,
                data_json={},
                created_by=user,
            )

        # (a) Rekonstruierbar: Snapshot-Zahlen decken sich mit dem Bestand.
        self.exakt_pk = StatisticsSnapshot.objects.create(
            facility=fac,
            year=2025,
            month=1,
            data=_snapshot_payload(3, {"anonym": 0, "identifiziert": 3, "qualifiziert": 0}),
            backend_data={
                "sample-report-1": {
                    "total": 3,
                    "by_category": [["Kontakte", 3]],
                    "by_age_cluster": [{"cluster": "18_26", "label": "18–26", "count": 3}],
                    "unique_clients": 2,
                }
            },
        ).pk

        # (b) Nicht rekonstruierbar: der Snapshot zaehlt mehr Ereignisse, als
        # die DB noch hergibt (retention-bereinigter Februar ohne Ereignisse).
        self.retention_pk = StatisticsSnapshot.objects.create(
            facility=fac,
            year=2025,
            month=2,
            data=_snapshot_payload(9, {"anonym": 0, "identifiziert": 9, "qualifiziert": 0}),
            backend_data={
                "sample-report-1": {
                    "total": 9,
                    "by_category": [["Kontakte", 9]],
                    "by_age_cluster": [{"cluster": "18_26", "label": "18–26", "count": 9}],
                    "unique_clients": 4,
                }
            },
        ).pk

        # (c) Bereits im neuen Format: darf nicht angefasst werden.
        self.schon_neu_pk = StatisticsSnapshot.objects.create(
            facility=fac,
            year=2025,
            month=3,
            data={
                "total_contacts": 3,
                "by_contact_stage": {"anonym": 0, "identifiziert": 3, "qualifiziert": 0},
                "persons_by_contact_stage": {"anonym": 0, "identifiziert": 99, "qualifiziert": 0},
                "by_document_type": [],
                "by_age_cluster": [{"cluster": "18_26", "label": "18–26", "count": 3, "persons": 99}],
                "unique_clients": 2,
            },
        ).pk

    # --- Rekonstruierbarer Monat --------------------------------------------

    def test_altersgruppe_bekommt_die_echte_personenzahl(self):
        StatisticsSnapshot = self.new_state.apps.get_model("core", "StatisticsSnapshot")
        snap = StatisticsSnapshot.objects.get(pk=self.exakt_pk)

        assert snap.data["by_age_cluster"] == [{"cluster": "18_26", "label": "18–26", "count": 3, "persons": 2}], (
            "Drei Ereignisse von zwei Personen ⇒ persons = 2"
        )

    def test_kontaktstufen_bekommen_die_echte_personenzahl(self):
        StatisticsSnapshot = self.new_state.apps.get_model("core", "StatisticsSnapshot")
        snap = StatisticsSnapshot.objects.get(pk=self.exakt_pk)

        assert snap.data["persons_by_contact_stage"] == {"anonym": 0, "identifiziert": 2, "qualifiziert": 0}

    def test_sachbericht_aggregat_wird_mitgezogen(self):
        StatisticsSnapshot = self.new_state.apps.get_model("core", "StatisticsSnapshot")
        snap = StatisticsSnapshot.objects.get(pk=self.exakt_pk)

        assert snap.backend_data["sample-report-1"]["by_age_cluster"] == [
            {"cluster": "18_26", "label": "18–26", "count": 3, "persons": 2}
        ]
        # Fremde Schluessel des Aggregats bleiben unangetastet.
        assert snap.backend_data["sample-report-1"]["total"] == 3

    # --- Nicht rekonstruierbarer Monat ---------------------------------------

    def test_nicht_rekonstruierbare_monate_werden_unbekannt_nicht_geraten(self):
        StatisticsSnapshot = self.new_state.apps.get_model("core", "StatisticsSnapshot")
        snap = StatisticsSnapshot.objects.get(pk=self.retention_pk)

        assert snap.data["by_age_cluster"][0]["persons"] is None
        assert snap.data["persons_by_contact_stage"] == {"anonym": 0, "identifiziert": None, "qualifiziert": 0}
        assert snap.backend_data["sample-report-1"]["by_age_cluster"][0]["persons"] is None

    # --- Bestand im neuen Format ---------------------------------------------

    def test_vorhandene_personenzahlen_werden_nicht_ueberschrieben(self):
        StatisticsSnapshot = self.new_state.apps.get_model("core", "StatisticsSnapshot")
        snap = StatisticsSnapshot.objects.get(pk=self.schon_neu_pk)

        assert snap.data["by_age_cluster"][0]["persons"] == 99
        assert snap.data["persons_by_contact_stage"]["identifiziert"] == 99

    # --- Idempotenz + Reverse -------------------------------------------------

    def test_second_run_changes_nothing(self):
        StatisticsSnapshot = self.new_state.apps.get_model("core", "StatisticsSnapshot")
        before = {snap.pk: (snap.data, snap.backend_data) for snap in StatisticsSnapshot.objects.all()}

        with connection.schema_editor() as schema_editor:
            _MIGRATION.backfill_person_counts(self.new_state.apps, schema_editor)

        after = {snap.pk: (snap.data, snap.backend_data) for snap in StatisticsSnapshot.objects.all()}
        assert before == after

    def test_reverse_entfernt_die_nachgetragenen_schluessel(self):
        StatisticsSnapshot = self.new_state.apps.get_model("core", "StatisticsSnapshot")

        with connection.schema_editor() as schema_editor:
            _MIGRATION.strip_person_counts(self.new_state.apps, schema_editor)

        snap = StatisticsSnapshot.objects.get(pk=self.exakt_pk)
        assert "persons_by_contact_stage" not in snap.data
        assert snap.data["by_age_cluster"] == [{"cluster": "18_26", "label": "18–26", "count": 3}]
        assert snap.backend_data["sample-report-1"]["by_age_cluster"] == [
            {"cluster": "18_26", "label": "18–26", "count": 3}
        ]
