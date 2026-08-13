"""Direkte Testabdeckung der RunPython-Datenmigration 0148 (Refs #1868).

``0148_session_absolute_hours`` fuegt ``Settings.session_absolute_hours``
(Default 12 h) hinzu und haengt danach den CheckConstraint
``settings_session_absolute_cap`` an, der ``session_timeout_minutes <
session_absolute_hours * 60`` erzwingt.

Dazwischen liegt der Schritt, den dieser Test abdeckt: eine Bestandszeile mit
einem hochgesetzten Inaktivitaets-Fenster (>= 720 Minuten) verletzt den
frischen Default und liesse ``AddConstraint`` scheitern. Zwei Zusagen haengen
daran:

1. **Angehoben wird das Maximum, nie gesenkt wird das Inaktivitaets-Fenster.**
   Ein Deploy darf keine vorhandene Einstellung verschaerfen — wer 20 Stunden
   Inaktivitaet konfiguriert hat, behaelt sie und bekommt ein passendes
   Maximum darueber.
2. **Nur der Rand jenseits von 168 h** (das Feld-Maximum) senkt das
   Inaktivitaets-Fenster; dort ist es unvermeidlich, sonst liesse sich der
   Constraint nicht anlegen. Die Migration protokolliert das laut.

``v0.80.0`` ist oeffentlich released — die Migration muss fuer beliebige
Fremdbestaende halten, nicht nur fuer die eigenen Instanzen mit ihren
30 Seed-Minuten. Muster: ``test_0147_reduce_documenttype_system_type``.
"""

from django_test_migrations.contrib.unittest_case import MigratorTestCase

_MAX_HOURS = 168


class TestMigrate0148SessionAbsoluteHours(MigratorTestCase):
    migrate_from = ("core", "0147_reduce_documenttype_system_type")
    migrate_to = ("core", "0148_session_absolute_hours")

    def prepare(self):
        apps = self.old_state.apps
        Organization = apps.get_model("core", "Organization")
        Facility = apps.get_model("core", "Facility")
        Settings = apps.get_model("core", "Settings")

        org = Organization.objects.create(name="Mig-0148-Org")

        def settings_row(name, timeout_minutes):
            fac = Facility.objects.create(organization=org, name=name)
            Settings.objects.create(facility=fac, session_timeout_minutes=timeout_minutes)
            return fac.pk

        # Realistischer Bestand: Seed-Default, deutlich unter 12 h.
        self.normal_pk = settings_row("Mig-0148-Normal", 30)
        # Genau am Rand: 720 Minuten sind NICHT kleiner als 12 h * 60.
        self.grenze_pk = settings_row("Mig-0148-Grenze", 720)
        # Darueber: 20 Stunden Inaktivitaet, vom Betreiber bewusst gesetzt.
        self.hoch_pk = settings_row("Mig-0148-Hoch", 20 * 60)
        # Krumm: muss aufgerundet werden (1000 min -> 17 h, nicht 16).
        self.krumm_pk = settings_row("Mig-0148-Krumm", 1000)
        # Absurd: jenseits des Feld-Maximums von 168 h.
        self.absurd_pk = settings_row("Mig-0148-Absurd", 200 * 60)

    def _row(self, facility_pk):
        Settings = self.new_state.apps.get_model("core", "Settings")
        return Settings.objects.get(facility_id=facility_pk)

    def test_unauffaelliger_bestand_behaelt_den_default(self):
        row = self._row(self.normal_pk)
        assert row.session_absolute_hours == 12
        assert row.session_timeout_minutes == 30

    def test_maximum_wird_angehoben_statt_das_fenster_zu_senken(self):
        for facility_pk, erwartete_stunden, erwartete_minuten in (
            # 720 min: die Regel ist ECHT kleiner, 12 h reichen also nicht.
            (self.grenze_pk, 13, 720),
            (self.hoch_pk, 21, 20 * 60),
            # 1000 min -> ceil(1001/60) = 17 h.
            (self.krumm_pk, 17, 1000),
        ):
            row = self._row(facility_pk)
            assert row.session_absolute_hours == erwartete_stunden
            assert row.session_timeout_minutes == erwartete_minuten, (
                "Das Inaktivitaets-Fenster darf die Migration nicht senken"
            )

    def test_jenseits_des_feld_maximums_wird_das_fenster_geklemmt(self):
        """Einziger Fall, in dem gesenkt wird — sonst liesse sich der
        Constraint nicht anlegen."""
        row = self._row(self.absurd_pk)
        assert row.session_absolute_hours == _MAX_HOURS
        assert row.session_timeout_minutes == _MAX_HOURS * 60 - 1

    def test_alle_zeilen_erfuellen_den_constraint(self):
        """Die eigentliche Zusage: nach der Migration haelt jede Zeile die
        Cross-Field-Regel — sonst waere ``AddConstraint`` gar nicht erst
        durchgelaufen."""
        Settings = self.new_state.apps.get_model("core", "Settings")
        for row in Settings.objects.all():
            assert 1 <= row.session_absolute_hours <= _MAX_HOURS
            assert row.session_timeout_minutes < row.session_absolute_hours * 60
