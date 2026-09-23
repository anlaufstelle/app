"""Tests fuer das Beispiel-Backend ``case-metrics`` (Refs #1754).

Referenz fuer ein Aggregat aus **mehreren unabhaengigen Skalaren**: hier gibt es
keine Aufschluesselung, die eine Randsumme partitioniert — jede Zahl steht fuer
sich an der k-Schwelle. Die Faelle folgen der Checkliste aus
``docs/reporting-plugins.md`` § 10 und pruefen zusaetzlich die Fall-Semantik
(``closed_at`` wird von ``close_case`` gesetzt und von ``reopen_case``
genullt).
"""

import dataclasses
from datetime import timedelta

import pytest
from django.utils import timezone
from django.utils.functional import Promise

from core.models import Case, Client, DocumentType, Facility, Organization, Settings
from core.reporting import registry
from core.reporting.base import CompletenessIssue, DatasetResult
from core.reporting.data_source import CASES_IN_PERIOD
from core.services.case.cases import close_case, reopen_case

pytestmark = pytest.mark.django_db


@pytest.fixture
def backend():
    return registry.get_backend("case-metrics")


@pytest.fixture
def zeitraum():
    """Zeitraum relativ zu heute — ``offene_faelle_aktuell`` ist eine
    Momentaufnahme auf den Stichtag heute, ein fixes Kalenderfenster liefe
    daran vorbei."""
    heute = timezone.localdate()
    return heute - timedelta(days=30), heute


def _person(facility, pseudonym="P1"):
    return Client.objects.create(facility=facility, pseudonym=pseudonym)


def _fall(facility, person, titel="Fall", *, angelegt=None):
    case = Case.objects.create(facility=facility, client=person, title=titel)
    if angelegt is not None:
        # ``created_at`` ist ``auto_now_add`` — nur ein Queryset-``update``
        # kommt daran vorbei.
        Case.objects.filter(pk=case.pk).update(created_at=angelegt)
        case.refresh_from_db()
    return case


def _kennzahlen(result):
    return {row["kennzahl"]: row["wert"] for row in result.records}


class TestDiscovery:
    def test_backend_wird_automatisch_gefunden(self):
        assert "case-metrics" in [b.slug for b in registry.get_backends()]


class TestBackendMetadaten:
    def test_slug_und_snapshot_flag(self, backend):
        assert backend.slug == "case-metrics"
        assert backend.snapshot_enabled is False

    def test_population_ist_faelle_im_zeitraum(self, backend):
        """Refs #1754: frueher ``PERSONS`` — sachlich falsch.
        ``PERSONS`` ist definiert als „Personen mit mindestens einem Ereignis im
        Zeitraum"; ein Fall setzt aber kein Ereignis voraus, und
        ``offene_faelle_aktuell`` hat gar keinen Zeitraumbezug. Der Ausweis muss
        die tatsaechliche Basismenge benennen, sonst ist er genau die Sorte
        Falschangabe, die K3-A3 beseitigen sollte."""
        assert backend.population == CASES_IN_PERIOD

    def test_name_und_labels_sind_lazy(self, backend):
        assert isinstance(backend.name, Promise)
        for spec in backend.get_field_specs():
            assert isinstance(spec.label, Promise), f"FieldSpec {spec.key!r} traegt Klartext statt gettext_lazy"

    def test_field_spec_keys(self, backend):
        assert [spec.key for spec in backend.get_field_specs()] == ["kennzahl", "wert"]


class TestBuildDataset:
    def test_leere_facility_liefert_vier_nullzeilen(self, backend, facility, zeitraum):
        """Die Zeilen werden **datenunabhaengig** emittiert: waere ihre blosse
        Existenz an Daten geknuepft, verriete die Gestalt der Tabelle bereits,
        welche Kennzahl > 0 ist."""
        result = backend.build_dataset(facility, *zeitraum)

        assert result.mode == "aggregate"
        assert _kennzahlen(result) == {
            "neue_faelle": 0,
            "geschlossene_faelle": 0,
            "offene_faelle_aktuell": 0,
            "betroffene_personen": 0,
        }
        assert result.summary == {
            "neue_faelle": 0,
            "geschlossene_faelle": 0,
            "offene_faelle_aktuell": 0,
            "betroffene_personen": 0,
        }
        assert [issue.code for issue in result.completeness] == ["point_in_time_open_cases"]

    def test_records_spiegeln_das_summary(self, backend, facility, zeitraum):
        person = _person(facility)
        _fall(facility, person)

        result = backend.build_dataset(facility, *zeitraum)

        assert _kennzahlen(result) == result.summary

    def test_neue_faelle_nur_im_zeitraum(self, backend, facility, zeitraum):
        person = _person(facility)
        _fall(facility, person, "Neu")
        _fall(facility, person, "Alt", angelegt=timezone.now() - timedelta(days=90))

        assert backend.build_dataset(facility, *zeitraum).summary["neue_faelle"] == 1

    def test_geschlossene_faelle_ueber_closed_at(self, backend, facility, zeitraum, staff_user):
        person = _person(facility)
        close_case(_fall(facility, person, "Zu"), staff_user)
        _fall(facility, person, "Offen")

        summary = backend.build_dataset(facility, *zeitraum).summary

        assert summary["geschlossene_faelle"] == 1
        assert summary["offene_faelle_aktuell"] == 1

    def test_ausserhalb_geschlossener_fall_zaehlt_nicht(self, backend, facility, zeitraum, staff_user):
        person = _person(facility)
        case = close_case(_fall(facility, person, "Lange zu"), staff_user)
        Case.objects.filter(pk=case.pk).update(closed_at=timezone.now() - timedelta(days=90))

        summary = backend.build_dataset(facility, *zeitraum).summary

        assert summary["geschlossene_faelle"] == 0
        assert summary["offene_faelle_aktuell"] == 0

    def test_wiedereroeffneter_fall_zaehlt_nicht_als_geschlossen(self, backend, facility, zeitraum, staff_user):
        """``reopen_case`` nullt ``closed_at`` — der Fall verschwindet damit aus
        der Zaehlung und taucht wieder unter den offenen auf."""
        person = _person(facility)
        case = close_case(_fall(facility, person, "Hin und her"), staff_user)
        reopen_case(case, staff_user)

        summary = backend.build_dataset(facility, *zeitraum).summary

        assert summary["geschlossene_faelle"] == 0
        assert summary["offene_faelle_aktuell"] == 1

    def test_offene_faelle_sind_eine_momentaufnahme_ohne_zeitraumbezug(self, backend, facility, zeitraum):
        """Ein vor dem Zeitraum angelegter, heute noch offener Fall zaehlt mit —
        die Zahl beschreibt den Stichtag heute, nicht das Zeitfenster."""
        person = _person(facility)
        _fall(facility, person, "Alt und offen", angelegt=timezone.now() - timedelta(days=90))

        summary = backend.build_dataset(facility, *zeitraum).summary

        assert summary["neue_faelle"] == 0
        assert summary["offene_faelle_aktuell"] == 1

    def test_betroffene_personen_zaehlt_distinct(self, backend, facility, zeitraum, staff_user):
        eins = _person(facility, "P1")
        zwei = _person(facility, "P2")
        _fall(facility, eins, "A")
        close_case(_fall(facility, eins, "B"), staff_user)
        _fall(facility, zwei, "C")

        summary = backend.build_dataset(facility, *zeitraum).summary

        assert summary["neue_faelle"] == 3
        assert summary["betroffene_personen"] == 2

    def test_betroffene_personen_ignoriert_faelle_ausserhalb(self, backend, facility, zeitraum):
        person = _person(facility)
        _fall(facility, person, "Alt", angelegt=timezone.now() - timedelta(days=90))

        assert backend.build_dataset(facility, *zeitraum).summary["betroffene_personen"] == 0

    def test_soft_geloeschter_fall_zaehlt_nicht(self, backend, facility, zeitraum):
        person = _person(facility)
        case = _fall(facility, person)
        case.is_deleted = True
        case.save(update_fields=["is_deleted"])

        summary = backend.build_dataset(facility, *zeitraum).summary

        assert summary["neue_faelle"] == 0
        assert summary["offene_faelle_aktuell"] == 0
        assert summary["betroffene_personen"] == 0

    def test_getilgter_fall_bleibt_draussen(self, backend, facility, zeitraum):
        """Refs #1818: die Tilgung holt einen Fall NICHT in die Statistik zurueck.

        ``anonymize_case`` redigiert den Inhalt und laesst ``is_deleted=True``
        stehen — der Fall bleibt also draussen. Der Docstring von
        ``_visible_cases`` behauptete zwischenzeitlich das Gegenteil („zaehlt
        dagegen weiter"); dieser Pin haelt Prosa und Verhalten zusammen.
        """
        from core.services.case import anonymize_case

        person = _person(facility)
        case = _fall(facility, person)
        case.soft_delete(user=None)

        anonymize_case(case, user=None, trigger="trash_days_expired")

        summary = backend.build_dataset(facility, *zeitraum).summary
        assert summary["neue_faelle"] == 0
        assert summary["offene_faelle_aktuell"] == 0

    def test_fall_einer_anonymisierten_person_zaehlt_weiter(self, backend, facility, zeitraum):
        """Der Personen-Weg redigiert Faelle, ohne sie zu loeschen — sie zaehlen weiter.

        Das ist die Redaktion, die die Zeitreihe stetig haelt: ``is_deleted``
        bleibt am Fall ``False``, nur der Inhalt faellt. Genau hier verlaeuft
        die Grenze zum Fall-Loeschweg (Refs #1818).
        """
        from core.services.case import redact_case_content

        person = _person(facility)
        case = _fall(facility, person)

        redact_case_content(case)

        summary = backend.build_dataset(facility, *zeitraum).summary
        assert summary["neue_faelle"] == 1
        assert summary["offene_faelle_aktuell"] == 1

    def test_fremde_facility_ist_unsichtbar(self, backend, facility, zeitraum):
        fremd = Facility.objects.create(organization=Organization.objects.create(name="Fremd"), name="Fremdstelle")
        _fall(fremd, _person(fremd, "F1"), "Fremdfall")

        assert backend.build_dataset(facility, *zeitraum).summary["neue_faelle"] == 0


class TestIsAvailable:
    def test_immer_verfuegbar(self, backend, facility):
        """Das Backend konsumiert keine Konfiguration — die Seed-Bindung der
        uebrigen Beispiele greift hier nicht (siehe Docstring)."""
        assert backend.is_available(facility) is True

    def test_auch_ohne_dokumenttypen(self, backend, facility):
        assert DocumentType.objects.filter(facility=facility).count() == 0
        assert backend.is_available(facility) is True


def _rohergebnis(**werte):
    summary = {
        "neue_faelle": 0,
        "geschlossene_faelle": 0,
        "offene_faelle_aktuell": 0,
        "betroffene_personen": 0,
        **werte,
    }
    return DatasetResult(
        mode="aggregate",
        records=[{"kennzahl": key, "wert": wert} for key, wert in summary.items()],
        summary=dict(summary),
        completeness=[],
    )


class TestPrepareExport:
    def test_zahlen_unter_der_schwelle_fallen(self, backend, facility):
        prepared = backend.prepare_export(facility, _rohergebnis(neue_faelle=4, offene_faelle_aktuell=9))

        assert prepared.summary["neue_faelle"] is None
        assert prepared.summary["geschlossene_faelle"] is None
        assert prepared.summary["betroffene_personen"] is None
        assert prepared.summary["offene_faelle_aktuell"] == 9
        assert prepared.summary["k_anonymity_threshold"] == 5

    def test_records_und_summary_bleiben_deckungsgleich(self, backend, facility):
        prepared = backend.prepare_export(facility, _rohergebnis(neue_faelle=4, offene_faelle_aktuell=9))

        assert {row["kennzahl"]: row["wert"] for row in prepared.records} == {
            key: wert for key, wert in prepared.summary.items() if key != "k_anonymity_threshold"
        }

    def test_zahlen_ab_der_schwelle_bleiben_sichtbar(self, backend, facility):
        prepared = backend.prepare_export(
            facility,
            _rohergebnis(neue_faelle=7, geschlossene_faelle=5, offene_faelle_aktuell=12, betroffene_personen=9),
        )

        assert prepared.summary["neue_faelle"] == 7
        assert prepared.summary["geschlossene_faelle"] == 5
        assert prepared.summary["offene_faelle_aktuell"] == 12
        assert prepared.summary["betroffene_personen"] == 9

    def test_records_tragen_exakt_die_field_spec_keys(self, backend, facility):
        prepared = backend.prepare_export(facility, _rohergebnis(neue_faelle=2))

        field_keys = {spec.key for spec in backend.get_field_specs()}
        for row in prepared.records:
            assert set(row) == field_keys

    def test_summary_traegt_keine_zahl_ohne_zugehoerige_zeile(self, backend, facility):
        """Fail-safe: ``summary`` wird aus den suppressierten Zeilen gebaut, nicht
        aus dem Rohergebnis ergaenzt. Faellt eine Zeile weg (die
        Eskalationsleiter des Residual-Guards kann eine Aufschluesselung ganz
        entfernen), darf der Rohwert nicht im Aggregat ueberleben."""
        roh = _rohergebnis(neue_faelle=42, offene_faelle_aktuell=17)
        ohne_zeilen = dataclasses.replace(roh, records=[])

        prepared = backend.prepare_export(facility, ohne_zeilen)

        assert prepared.records == []
        assert prepared.summary["neue_faelle"] is None
        assert prepared.summary["offene_faelle_aktuell"] is None

    def test_momentaufnahme_hinweis_erreicht_das_artefakt(self, backend, facility):
        """I1: Die Kennzeichnung von ``offene_faelle_aktuell`` als
        Stichtagszahl darf nicht nur im Docstring stehen — die Empfaengerin
        liest den Docstring nie. Der Hinweis ist datenunabhaengig (immer da,
        egal welche Zahlen) und ziffernfrei (sonst publizierte er selbst eine
        Groesse), Severity ``info``: es ist kein Datenqualitaets-Mangel, sondern
        eine Lesehilfe."""
        leer = backend.prepare_export(facility, _rohergebnis())
        voll = backend.prepare_export(
            facility, _rohergebnis(neue_faelle=7, geschlossene_faelle=6, offene_faelle_aktuell=12)
        )

        for prepared in (leer, voll):
            assert [issue.code for issue in prepared.completeness] == ["point_in_time_open_cases"]
            issue = prepared.completeness[0]
            assert issue.severity == "info"
            assert issue.count is None
            assert not any(zeichen.isdigit() for zeichen in str(issue.message))

    def test_momentaufnahme_hinweis_wird_nicht_aus_dem_ergebnis_abgeleitet(self, backend, facility):
        """Fail-safe wie K6 (Refs #1769): der Block wird aus dem festen Code
        gebaut, nicht aus ``result.completeness``. Ein kuenftiger, bezifferter
        Hinweis erreicht den Export damit nicht ungeprueft."""
        roh = _rohergebnis(neue_faelle=7)
        mit_fremdem_hinweis = dataclasses.replace(
            roh, completeness=[CompletenessIssue("leaky", "17 Fälle betroffen", "warning", count=17)]
        )

        prepared = backend.prepare_export(facility, mit_fremdem_hinweis)

        assert [issue.code for issue in prepared.completeness] == ["point_in_time_open_cases"]

    def test_schwelle_kommt_aus_den_facility_settings(self, backend, facility):
        Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 1})

        prepared = backend.prepare_export(facility, _rohergebnis(neue_faelle=2))

        assert prepared.summary["neue_faelle"] == 2
        assert prepared.summary["k_anonymity_threshold"] == 1


class TestEndeZuEndeUeberBuildDataset:
    def test_panel_sicht_zeigt_suppressierte_zahlen(self, backend, facility, zeitraum):
        person = _person(facility)
        _fall(facility, person, "Einziger")

        prepared = backend.prepare_export(facility, backend.build_dataset(facility, *zeitraum))

        assert all(row["wert"] is None for row in prepared.records)
        assert prepared.summary["neue_faelle"] is None
