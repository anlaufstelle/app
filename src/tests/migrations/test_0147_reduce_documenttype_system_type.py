"""Direkte Testabdeckung der RunPython-Datenmigration 0147 (E4, Refs #1850).

``0147_reduce_documenttype_system_type`` nullt die Systemtypen, die mit der
Enum-Reduktion entfallen. Der Schritt ist die Vorbedingung dafuer, dass der
Paket-Loader gegen eine Bestandseinrichtung weiterlaeuft: bliebe ein Altwert
stehen, waere jede ``default.yaml`` ohne ``system_type``-Zeile ein
E13-Konflikt („gesetzt → weg").

Drei Zusagen haengen hier:

1. Die sieben entfallenen Werte werden ``NULL``.
2. ``ban``/``crisis`` bleiben unangetastet — sie tragen App-Verhalten.
3. **Geisterwerte** (ein Wert, den das Enum nie kannte) werden ebenfalls
   genullt. ``v0.80.0`` ist oeffentlich; ein Fremdbestand kann durch einen
   frueheren Direktschreibzugriff alles Moegliche in der Spalte haben, und ein
   ``NOT IN (…)``-Filter auf die sieben liesse ihn stehen.

Muster: ``test_0132_clear_retention_days_non_event``.
"""

import importlib

from django.db import connection
from django_test_migrations.contrib.unittest_case import MigratorTestCase

_MIGRATION = importlib.import_module("core.migrations.0147_reduce_documenttype_system_type")


class TestMigrate0147ReduceDocumentTypeSystemType(MigratorTestCase):
    migrate_from = ("core", "0146_remove_fieldtemplate_statistics_category")
    migrate_to = ("core", "0147_reduce_documenttype_system_type")

    def prepare(self):
        apps = self.old_state.apps
        Organization = apps.get_model("core", "Organization")
        Facility = apps.get_model("core", "Facility")
        DocumentType = apps.get_model("core", "DocumentType")

        org = Organization.objects.create(name="Mig-0147-Org")
        fac = Facility.objects.create(organization=org, name="Mig-0147-Stelle")

        def typ(name, slug, **kwargs):
            return DocumentType.objects.create(facility=fac, name=name, slug=slug, **kwargs).pk

        # Die sieben entfallenen Werte — inklusive des Ausschluss-Markers.
        self.entfallen_pks = {
            wert: typ(f"Typ {wert}", f"typ-{wert.replace('_', '-')}", system_type=wert, report_key=wert)
            for wert in ("contact", "medical", "needle_exchange", "accompaniment", "counseling", "referral")
        }
        self.notiz_pk = typ("Notiz", "notiz", system_type="note", report_key="excluded")
        # Die beiden Verhaltens-Schluessel bleiben.
        self.ban_pk = typ("Hausverbot", "hausverbot", system_type="ban", report_key="excluded")
        self.crisis_pk = typ("Krisengespräch", "krisengespraech", system_type="crisis", report_key="crisis")
        # Raender: nie gesetzt bzw. Leerstring (``blank=True``).
        self.null_pk = typ("Freitext", "freitext", system_type=None)
        self.leer_pk = typ("Leerer Systemtyp", "leerer-systemtyp", system_type="")
        # Geisterwert aus einem Fremdbestand.
        self.geist_pk = typ("Fremdwert", "fremdwert", system_type="fantasie")

    def _system_type(self, pk):
        DocumentType = self.new_state.apps.get_model("core", "DocumentType")
        return DocumentType.objects.get(pk=pk).system_type

    def test_die_sieben_entfallenen_werte_werden_genullt(self):
        for wert, pk in self.entfallen_pks.items():
            assert self._system_type(pk) is None, f"{wert} steht noch in der Spalte"
        assert self._system_type(self.notiz_pk) is None

    def test_verhaltens_schluessel_bleiben_stehen(self):
        assert self._system_type(self.ban_pk) == "ban"
        assert self._system_type(self.crisis_pk) == "crisis"

    def test_unbekannter_fremdwert_wird_ebenfalls_genullt(self):
        """Ein Wert, den das Enum nie kannte, waere sonst ein Geisterwert."""
        assert self._system_type(self.geist_pk) is None

    def test_report_key_bleibt_unangetastet(self):
        """Die Zuordnung wandert nicht — sie steht seit ``0145`` schon dort."""
        DocumentType = self.new_state.apps.get_model("core", "DocumentType")
        assert DocumentType.objects.get(pk=self.entfallen_pks["contact"]).report_key == "contact"
        assert DocumentType.objects.get(pk=self.notiz_pk).report_key == "excluded"
        assert DocumentType.objects.get(pk=self.ban_pk).report_key == "excluded"

    def test_leerstring_und_null_bleiben_wie_sie_sind(self):
        """``""`` ist bereits „keine Angabe" — ein Update darauf waere wirkungslos."""
        assert self._system_type(self.null_pk) is None
        assert self._system_type(self.leer_pk) == ""

    def test_second_run_changes_nothing(self):
        DocumentType = self.new_state.apps.get_model("core", "DocumentType")
        before = {dt.pk: dt.system_type for dt in DocumentType.objects.all()}

        with connection.schema_editor() as schema_editor:
            _MIGRATION.clear_removed_system_types(self.new_state.apps, schema_editor)

        after = {dt.pk: dt.system_type for dt in DocumentType.objects.all()}
        assert before == after

    def test_reverse_ist_ein_no_op(self):
        """Reverse stellt NICHTS wieder her — ``excluded`` stand fuer ``note`` UND ``ban``.

        Struktureller Pin statt eines Selbstaufrufs von ``RunPython.noop``:
        geprueft wird, dass die MIGRATION ihren Rueckweg tatsaechlich als
        ``noop`` deklariert — erst das macht die Zusage falsifizierbar.
        """
        from importlib import import_module

        from django.db import migrations as dj_migrations

        modul = import_module("core.migrations.0147_reduce_documenttype_system_type")
        run_python_ops = [op for op in modul.Migration.operations if isinstance(op, dj_migrations.RunPython)]
        assert run_python_ops, "Migration 0147 traegt keine RunPython-Operation mehr"
        for op in run_python_ops:
            assert op.reverse_code is dj_migrations.RunPython.noop
        assert self._system_type(self.notiz_pk) is None
