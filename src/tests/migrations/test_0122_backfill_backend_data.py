"""Direkte Testabdeckung der RunPython-Datenmigration 0122 (M5 P3, Refs #1061).

``0122_backfill_backend_data`` hebt Bestands-Snapshots auf das neue
``backend_data``-Schema: das Aggregat aus ``jugendamt_data`` zieht unter den
Schluessel ``"jugendamt"`` um. Der Lauf ist additiv — ``jugendamt_data`` bleibt
unangetastet (Dual-Write-Uebergangsphase), bereits gefuellte ``"jugendamt"``-
Eintraege werden nicht ueberschrieben, fremde Backend-Schluessel bleiben stehen.
Muster: ``test_0120_normalize_underscore_fieldtemplate_slugs``.
"""

import importlib

from django.db import connection
from django_test_migrations.contrib.unittest_case import MigratorTestCase

_MIGRATION = importlib.import_module("core.migrations.0122_backfill_backend_data")

_JUGENDAMT_AGGREGAT = {
    "total": 7,
    "by_category": [["Kontakte", 5], ["Beratung", 2]],
    "by_age_cluster": [{"cluster": "18_26", "label": "18–26", "count": 7}],
    "unique_clients": 4,
}


class TestMigrate0122BackfillBackendData(MigratorTestCase):
    migrate_from = ("core", "0121_statisticssnapshot_backend_data")
    migrate_to = ("core", "0122_backfill_backend_data")

    def prepare(self):
        Organization = self.old_state.apps.get_model("core", "Organization")
        Facility = self.old_state.apps.get_model("core", "Facility")
        StatisticsSnapshot = self.old_state.apps.get_model("core", "StatisticsSnapshot")

        org = Organization.objects.create(name="Org")
        fac = Facility.objects.create(organization=org, name="Stelle")

        # (a) Normalfall: Alt-Snapshot mit jugendamt_data, leeres backend_data.
        self.legacy_pk = StatisticsSnapshot.objects.create(
            facility=fac, year=2025, month=1, data={"total_contacts": 9}, jugendamt_data=_JUGENDAMT_AGGREGAT
        ).pk
        # (b) Leeres jugendamt_data -> kein Schluessel.
        self.empty_pk = StatisticsSnapshot.objects.create(
            facility=fac, year=2025, month=2, data={"total_contacts": 0}, jugendamt_data={}
        ).pk
        # (c) Bereits migriert: vorhandener "jugendamt"-Eintrag bleibt stehen.
        self.already_pk = StatisticsSnapshot.objects.create(
            facility=fac,
            year=2025,
            month=3,
            data={},
            jugendamt_data={"total": 1},
            backend_data={"jugendamt": {"total": 999}},
        ).pk
        # (d) Fremdes Backend im backend_data -> wird ergaenzt, nicht ersetzt.
        self.other_backend_pk = StatisticsSnapshot.objects.create(
            facility=fac,
            year=2025,
            month=4,
            data={},
            jugendamt_data={"total": 3},
            backend_data={"traeger": {"total": 42}},
        ).pk

    # --- Normalfall -----------------------------------------------------------

    def test_legacy_snapshot_gets_jugendamt_key(self):
        StatisticsSnapshot = self.new_state.apps.get_model("core", "StatisticsSnapshot")
        snap = StatisticsSnapshot.objects.get(pk=self.legacy_pk)
        assert snap.backend_data["jugendamt"] == _JUGENDAMT_AGGREGAT

    def test_jugendamt_data_is_left_untouched(self):
        """Forward zerstoert nichts — deshalb ist der Rueckweg ein noop."""
        StatisticsSnapshot = self.new_state.apps.get_model("core", "StatisticsSnapshot")
        assert StatisticsSnapshot.objects.get(pk=self.legacy_pk).jugendamt_data == _JUGENDAMT_AGGREGAT

    # --- Grenzfaelle ----------------------------------------------------------

    def test_empty_jugendamt_data_creates_no_key(self):
        StatisticsSnapshot = self.new_state.apps.get_model("core", "StatisticsSnapshot")
        assert StatisticsSnapshot.objects.get(pk=self.empty_pk).backend_data == {}

    def test_existing_jugendamt_key_is_not_overwritten(self):
        StatisticsSnapshot = self.new_state.apps.get_model("core", "StatisticsSnapshot")
        assert StatisticsSnapshot.objects.get(pk=self.already_pk).backend_data == {"jugendamt": {"total": 999}}

    def test_foreign_backend_keys_are_preserved(self):
        StatisticsSnapshot = self.new_state.apps.get_model("core", "StatisticsSnapshot")
        snap = StatisticsSnapshot.objects.get(pk=self.other_backend_pk)
        assert snap.backend_data == {"traeger": {"total": 42}, "jugendamt": {"total": 3}}

    def tearDown(self):
        """Snapshots vor dem Vorwaerts-Reset der Migrationskette raeumen (Refs #1739).

        ``MigratorTestCase.tearDown`` migriert bis zum Kopf durch. Migration
        ``0123`` prueft dabei fail-loud auf Snapshots, deren Altfeld vom
        ``backend_data``-Eintrag abweicht — Fall (c) konstruiert genau so eine
        Divergenz absichtlich (in Produktion unerreichbar, weil der Dual-Write
        beide Felder aus derselben Quelle schreibt). Ohne dieses Aufraeumen
        schluege der Reset fehl.
        """
        StatisticsSnapshot = self.new_state.apps.get_model("core", "StatisticsSnapshot")
        StatisticsSnapshot.objects.all().delete()
        super().tearDown()

    # --- Idempotenz -----------------------------------------------------------

    def test_second_run_changes_nothing(self):
        StatisticsSnapshot = self.new_state.apps.get_model("core", "StatisticsSnapshot")
        before = {snap.pk: snap.backend_data for snap in StatisticsSnapshot.objects.all()}

        with connection.schema_editor() as schema_editor:
            _MIGRATION.backfill_backend_data(self.new_state.apps, schema_editor)

        after = {snap.pk: snap.backend_data for snap in StatisticsSnapshot.objects.all()}
        assert before == after
