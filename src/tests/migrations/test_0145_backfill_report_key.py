"""Direkte Testabdeckung der RunPython-Datenmigration 0145 (E3, Refs #1849).

``0145_backfill_report_key`` uebersetzt den Bestand auf den neuen
Erstschluessel. Die Migration ist die einzige Stelle, an der der heutige
Zweitschluessel-Pfad (Slug) in etwas Dauerhaftes ueberfuehrt wird — faellt sie
aus oder greift sie unvollstaendig, fallen nach der Abschaltung des
Zweitschluessels ganze Typen still aus dem Bericht.

Muster: ``test_0132_clear_retention_days_non_event``.
"""

import importlib

from django.db import connection
from django_test_migrations.contrib.unittest_case import MigratorTestCase

_MIGRATION = importlib.import_module("core.migrations.0145_backfill_report_key")


class TestMigrate0145BackfillReportKey(MigratorTestCase):
    migrate_from = ("core", "0144_report_key")
    migrate_to = ("core", "0145_backfill_report_key")

    def prepare(self):
        apps = self.old_state.apps
        Organization = apps.get_model("core", "Organization")
        Facility = apps.get_model("core", "Facility")
        DocumentType = apps.get_model("core", "DocumentType")
        FieldTemplate = apps.get_model("core", "FieldTemplate")

        org = Organization.objects.create(name="Mig-0145-Org")
        fac = Facility.objects.create(organization=org, name="Mig-0145-Stelle")
        self.facility_pk = fac.pk

        def typ(name, slug, **kwargs):
            return DocumentType.objects.create(facility=fac, name=name, slug=slug, **kwargs).pk

        # (1) Die sieben Berichts-Systemtypen — gleichnamig zu uebernehmen.
        self.gemappt_pks = {
            key: typ(f"Typ {key}", f"typ-{key.replace('_', '-')}", system_type=key)
            for key in ("contact", "crisis", "medical", "needle_exchange", "accompaniment", "counseling", "referral")
        }
        # (2) Bewusst ausgeschlossen.
        self.notiz_pk = typ("Notiz", "notiz", system_type="note")
        self.hausverbot_pk = typ("Hausverbot", "hausverbot", system_type="ban")
        # (3) Zweitschluessel-Pfad: NULL bzw. Leerstring als Erststufe.
        self.slug_null_pk = typ("Erstkontakt", "kontakt", system_type=None)
        self.slug_leer_pk = typ("Gespräch", "beratungsgespraech", system_type="")
        # (4) Echte Luecke: kein Systemtyp, Slug ausserhalb der Konvention.
        self.luecke_pk = typ("Freitext", "freitext", system_type=None)
        # Ein Slug der Konvention MIT gesetztem, ausgeschlossenem Systemtyp:
        # der Erstschluessel gewinnt inklusive seiner Ausschluesse (Refs #1785)
        # — hier darf NICHT ``contact`` entstehen.
        self.getarnt_pk = typ("Getarnte Notiz", "kontakt-2", system_type="note")
        # Bereits gesetzter Wert (Fremdbestand/erneuter Lauf) bleibt stehen.
        self.vorbelegt_pk = typ("Vorbelegt", "vorbelegt", system_type="contact", report_key="fantasie")
        # Personen-Typ mit Systemtyp: bekommt bewusst ebenfalls einen Schluessel
        # (kein ``entity_type``-Filter, Konzept #1847).
        self.personen_typ_pk = typ("Personenbogen", "personenbogen", system_type="contact", entity_type="client")

        def vorlage(name, slug, **kwargs):
            return FieldTemplate.objects.create(facility=fac, name=name, slug=slug, **kwargs).pk

        self.feld_pks = {
            slug: vorlage(f"Feld {slug}", slug)
            for slug in ("dauer", "ausgabe", "rueckgabe", "leistungen", "art-der-krise")
        }
        self.feld_fremd_pk = vorlage("Gesprächsdauer", "gespraechsdauer")

    def _doc_types(self):
        return self.new_state.apps.get_model("core", "DocumentType")

    def _templates(self):
        return self.new_state.apps.get_model("core", "FieldTemplate")

    def test_berichts_systemtypen_werden_gleichnamig_uebernommen(self):
        DocumentType = self._doc_types()
        for key, pk in self.gemappt_pks.items():
            assert DocumentType.objects.get(pk=pk).report_key == key

    def test_notiz_und_hausverbot_werden_zum_sentinel(self):
        DocumentType = self._doc_types()
        assert DocumentType.objects.get(pk=self.notiz_pk).report_key == "excluded"
        assert DocumentType.objects.get(pk=self.hausverbot_pk).report_key == "excluded"

    def test_slug_zweitschluessel_bei_leerem_system_type(self):
        DocumentType = self._doc_types()
        assert DocumentType.objects.get(pk=self.slug_null_pk).report_key == "contact"

    def test_leerstring_zaehlt_wie_null(self):
        """``system_type=""`` ist exportseitig identisch mit NULL — sonst fiele
        genau dieser Bestand nach der Umstellung aus dem Bericht.
        """
        DocumentType = self._doc_types()
        assert DocumentType.objects.get(pk=self.slug_leer_pk).report_key == "counseling"

    def test_echte_luecke_bleibt_leer(self):
        DocumentType = self._doc_types()
        assert DocumentType.objects.get(pk=self.luecke_pk).report_key is None

    def test_gesetzter_system_type_sperrt_den_slug_pfad(self):
        """Refs #1785: ein Notiz-Typ mit dem Slug ``kontakt`` darf sich die
        Berichtszuordnung nicht ueber die Namenskonvention holen.
        """
        DocumentType = self._doc_types()
        assert DocumentType.objects.get(pk=self.getarnt_pk).report_key == "excluded"

    def test_bestehender_wert_wird_nicht_ueberschrieben(self):
        DocumentType = self._doc_types()
        assert DocumentType.objects.get(pk=self.vorbelegt_pk).report_key == "fantasie"

    def test_personen_typ_bekommt_ebenfalls_einen_schluessel(self):
        """Kein ``entity_type``-Filter (konzepttreu): die Wirkung ist reine
        Anzeige — alle Backends zaehlen Ereignisse.
        """
        DocumentType = self._doc_types()
        assert DocumentType.objects.get(pk=self.personen_typ_pk).report_key == "contact"

    def test_feldvorlagen_der_fuenf_paket_slugs(self):
        FieldTemplate = self._templates()
        erwartet = {
            "dauer": "dauer_minuten",
            "ausgabe": "ausgabe_stueck",
            "rueckgabe": "rueckgabe_stueck",
            "leistungen": "leistungen",
            "art-der-krise": "art_der_krise",
        }
        for slug, key in erwartet.items():
            assert FieldTemplate.objects.get(pk=self.feld_pks[slug]).report_key == key

    def test_abweichend_benannte_feldvorlage_bleibt_leer(self):
        """Genau der Fall, den E3 sichtbar machen soll: „Gesprächsdauer" statt
        „Dauer" traegt heute still nichts zur Minutensumme bei.
        """
        FieldTemplate = self._templates()
        assert FieldTemplate.objects.get(pk=self.feld_fremd_pk).report_key is None

    def test_zweiter_lauf_aendert_nichts(self):
        DocumentType = self._doc_types()
        FieldTemplate = self._templates()
        vorher = (
            {dt.pk: dt.report_key for dt in DocumentType.objects.all()},
            {ft.pk: ft.report_key for ft in FieldTemplate.objects.all()},
        )

        with connection.schema_editor() as schema_editor:
            _MIGRATION.backfill_report_key(self.new_state.apps, schema_editor)

        nachher = (
            {dt.pk: dt.report_key for dt in DocumentType.objects.all()},
            {ft.pk: ft.report_key for ft in FieldTemplate.objects.all()},
        )
        assert vorher == nachher

    def test_reverse_ist_ein_no_op(self):
        """Reverse NULLt bewusst nichts: welcher Wert aus dem Backfill und
        welcher aus einer spaeteren Bearbeitung stammt, ist nicht mehr
        unterscheidbar.
        """
        from django.db import migrations as dj_migrations

        DocumentType = self._doc_types()
        vorher = {dt.pk: dt.report_key for dt in DocumentType.objects.all()}

        dj_migrations.RunPython.noop(self.new_state.apps, None)

        assert {dt.pk: dt.report_key for dt in DocumentType.objects.all()} == vorher
