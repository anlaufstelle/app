"""Direkte Testabdeckung der RunPython-Datenmigration 0123 (Refs #1739).

``0123_rename_backend_data_key_sample_report_1`` zieht das Aggregat des
Beispiel-Sachberichts von ``backend_data["jugendamt"]`` auf
``backend_data["sample-report-1"]`` um: fremde Backend-Schluessel bleiben
stehen, das Altfeld ``jugendamt_data`` bleibt unangetastet (``0124`` entfernt
es), der Rueckweg benennt zurueck. Vor dem Umzug bricht die Migration
fail-loud ab, wenn ein Snapshot ein befuelltes ``jugendamt_data`` traegt, das
von ``backend_data["jugendamt"]`` abweicht (Refs #1740).
Muster: ``test_0122_backfill_backend_data``.
"""

import importlib

import pytest
from django.db import connection
from django_test_migrations.contrib.unittest_case import MigratorTestCase

_MIGRATION = importlib.import_module("core.migrations.0123_rename_backend_data_key_sample_report_1")

_AGGREGAT = {
    "total": 7,
    "by_category": [["Kontakte", 5], ["Beratung", 2]],
    "by_age_cluster": [{"cluster": "18_26", "label": "18–26", "count": 7}],
    "unique_clients": 4,
}


class TestMigrate0123RenameBackendDataKey(MigratorTestCase):
    migrate_from = ("core", "0122_backfill_backend_data")
    migrate_to = ("core", "0123_rename_backend_data_key_sample_report_1")

    def prepare(self):
        Organization = self.old_state.apps.get_model("core", "Organization")
        Facility = self.old_state.apps.get_model("core", "Facility")
        StatisticsSnapshot = self.old_state.apps.get_model("core", "StatisticsSnapshot")

        org = Organization.objects.create(name="Org")
        fac = Facility.objects.create(organization=org, name="Stelle")
        self.facility_pk = fac.pk

        # (a) Normalfall: Alt-Schluessel, Dual-Write konsistent.
        self.legacy_pk = StatisticsSnapshot.objects.create(
            facility=fac,
            year=2025,
            month=1,
            data={"total_contacts": 9},
            jugendamt_data=_AGGREGAT,
            backend_data={"jugendamt": _AGGREGAT},
        ).pk
        # (b) Fremdes Backend bleibt erhalten.
        self.other_backend_pk = StatisticsSnapshot.objects.create(
            facility=fac,
            year=2025,
            month=2,
            data={},
            jugendamt_data={"total": 3},
            backend_data={"traeger": {"total": 42}, "jugendamt": {"total": 3}},
        ).pk
        # (c) Kein Alt-Schluessel (leeres Altfeld, 0122 hat nichts nachgetragen).
        self.untouched_pk = StatisticsSnapshot.objects.create(
            facility=fac, year=2025, month=3, data={}, jugendamt_data={}, backend_data={}
        ).pk

    # --- Normalfall -----------------------------------------------------------

    def test_alt_schluessel_zieht_auf_neuen_slug_um(self):
        StatisticsSnapshot = self.new_state.apps.get_model("core", "StatisticsSnapshot")
        snap = StatisticsSnapshot.objects.get(pk=self.legacy_pk)
        assert snap.backend_data == {"sample-report-1": _AGGREGAT}

    def test_altfeld_bleibt_unangetastet(self):
        """``0123`` ist reiner Schluesselwechsel — die Spalte entfernt erst ``0124``."""
        StatisticsSnapshot = self.new_state.apps.get_model("core", "StatisticsSnapshot")
        assert StatisticsSnapshot.objects.get(pk=self.legacy_pk).jugendamt_data == _AGGREGAT

    # --- Grenzfaelle ----------------------------------------------------------

    def test_fremde_backend_schluessel_bleiben_erhalten(self):
        StatisticsSnapshot = self.new_state.apps.get_model("core", "StatisticsSnapshot")
        snap = StatisticsSnapshot.objects.get(pk=self.other_backend_pk)
        assert snap.backend_data == {"traeger": {"total": 42}, "sample-report-1": {"total": 3}}

    def test_snapshot_ohne_alt_schluessel_bleibt_leer(self):
        StatisticsSnapshot = self.new_state.apps.get_model("core", "StatisticsSnapshot")
        assert StatisticsSnapshot.objects.get(pk=self.untouched_pk).backend_data == {}

    # --- Idempotenz -----------------------------------------------------------

    def test_zweiter_lauf_aendert_nichts(self):
        StatisticsSnapshot = self.new_state.apps.get_model("core", "StatisticsSnapshot")
        before = {snap.pk: snap.backend_data for snap in StatisticsSnapshot.objects.all()}

        with connection.schema_editor() as schema_editor:
            _MIGRATION.rename_key_forward(self.new_state.apps, schema_editor)

        after = {snap.pk: snap.backend_data for snap in StatisticsSnapshot.objects.all()}
        assert before == after

    # --- Rueckweg -------------------------------------------------------------

    def test_reverse_benennt_zurueck(self):
        StatisticsSnapshot = self.new_state.apps.get_model("core", "StatisticsSnapshot")

        with connection.schema_editor() as schema_editor:
            _MIGRATION.rename_key_backward(self.new_state.apps, schema_editor)

        snap = StatisticsSnapshot.objects.get(pk=self.other_backend_pk)
        assert snap.backend_data == {"traeger": {"total": 42}, "jugendamt": {"total": 3}}

    # --- Fail-Loud-Vorpruefung (Refs #1740, Punkt 2) --------------------------

    def test_stale_altfeld_bricht_ab(self):
        """Abweichendes, befuelltes ``jugendamt_data`` -> harter Abbruch statt stillem Umzug.

        Ohne den Guard wuerde der veraltete ``backend_data``-Stand unter dem
        neuen Schluessel festgeschrieben und der frischere Altfeld-Stand mit
        ``0124`` verworfen. Der Zeilenaufbau spiegelt den Roll-Forward-Fall:
        Alt-Code hat nur ``jugendamt_data`` fortgeschrieben.

        Aufgeraeumt wird im ``finally``: das ``tearDown`` von
        ``MigratorTestCase`` migriert vorwaerts und liefe sonst selbst in den
        Guard.
        """
        StatisticsSnapshot = self.new_state.apps.get_model("core", "StatisticsSnapshot")
        stale = StatisticsSnapshot.objects.create(
            facility_id=self.facility_pk,
            year=2025,
            month=9,
            data={},
            jugendamt_data={"total": 12},
            backend_data={"jugendamt": {"total": 3}},
        )
        try:
            with (
                pytest.raises(_MIGRATION.StaleSnapshotError) as excinfo,
                connection.schema_editor() as schema_editor,
            ):
                _MIGRATION.rename_key_forward(self.new_state.apps, schema_editor)

            assert "jugendamt_data" in str(excinfo.value)
            assert "create_statistics_snapshots" in str(excinfo.value)
        finally:
            stale.delete()

    def test_stale_pruefung_ignoriert_leeres_altfeld(self):
        """Leeres ``jugendamt_data`` ist kein Stand, der verloren gehen koennte."""
        StatisticsSnapshot = self.new_state.apps.get_model("core", "StatisticsSnapshot")
        harmless = StatisticsSnapshot.objects.create(
            facility_id=self.facility_pk,
            year=2025,
            month=10,
            data={},
            jugendamt_data={},
            backend_data={"sample-report-1": {"total": 3}},
        )
        try:
            with connection.schema_editor() as schema_editor:
                _MIGRATION.rename_key_forward(self.new_state.apps, schema_editor)
        finally:
            harmless.delete()
