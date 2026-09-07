"""Tests fuer das Beispiel-Backend ``counseling-hours`` (Refs #1754).

Zweites Feldwert-Backend, aber die erste **Zeilen**-Tabelle ueber Feldwerten —
und damit der Fall, an dem die Suppressions-Kaskade sichtbar wird: eine
unterdrueckte Zeile zieht ihre Minuten mit, und die Minuten-Randsumme faellt,
sobald ueberhaupt eine Zeilen-Minutenzahl fehlt (sonst waere sie die Differenz).
"""

import csv
import json
import xml.etree.ElementTree as ET
from datetime import timedelta

import pytest
from django.utils import timezone
from django.utils.functional import Promise

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate, Settings
from core.reporting import registry
from core.reporting.backends.counseling_hours import BEITRAGENDE_KEY, CounselingHoursBackend
from core.reporting.base import DatasetResult
from core.reporting.data_source import TYPED_EVENTS
from core.reporting.export import export_dataset
from tests._reporting_helpers import feld_report_key_fuer

pytestmark = pytest.mark.django_db


@pytest.fixture
def backend():
    return registry.get_backend("counseling-hours")


def _typ_mit_dauer(facility, name, *, report_key, encrypted=False, mit_dauer=True):
    """Dokumentationstyp mit der geteilten Paket-Feldvorlage ``dauer``.

    Die Vorlage ist je Einrichtung eindeutig (``unique_facility_fieldtemplate_name``)
    und haengt im Standardpaket an allen drei Gespraechstypen — sie wird deshalb
    einmal je Einrichtung angelegt und wiederverwendet.

    Der ``report_key`` ist die Achse, auf der dieses Backend bindet (E3, Refs
    #1849) — und seit E4 (Refs #1850) die einzige. Die Aufloesungstests setzen
    ihn bewusst auf ``None``.
    """
    doc_type = DocumentType.objects.create(
        facility=facility,
        name=name,
        report_key=report_key,
    )
    if mit_dauer:
        feld, _created = FieldTemplate.objects.get_or_create(
            facility=facility,
            name="Dauer",
            defaults={
                "field_type": FieldTemplate.FieldType.NUMBER,
                "is_encrypted": encrypted,
                "report_key": feld_report_key_fuer("Dauer"),
            },
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=feld)
    return doc_type


def _events(facility, doc_type, user, anzahl, dauer):
    for _index in range(anzahl):
        Event.objects.create(
            facility=facility,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={} if dauer is None else {"dauer": dauer},
            created_by=user,
        )


class TestDiscoveryUndMetadaten:
    def test_discovery_findet_das_backend(self):
        assert "counseling-hours" in [b.slug for b in registry.get_backends()]

    def test_registriert_unter_slug(self, backend):
        assert isinstance(backend, CounselingHoursBackend)
        assert backend.slug == "counseling-hours"

    def test_snapshot_bleibt_aus(self, backend):
        assert backend.snapshot_enabled is False

    def test_grundgesamtheit_ist_enger_als_der_default(self, backend):
        """I3: das Backend zaehlt NICHT alle Ereignisse des Zeitraums, sondern
        nur die seiner Dokumentationstypen — ``base.py`` verlangt dann eine
        explizite, engere Deklaration.
        """
        assert backend.population == TYPED_EVENTS

    def test_field_specs(self, backend):
        assert [spec.key for spec in backend.get_field_specs()] == ["kategorie", "events", "minuten"]

    def test_labels_sind_lazy(self, backend):
        for spec in backend.get_field_specs():
            assert isinstance(spec.label, Promise), f"FieldSpec {spec.key!r} traegt Klartext statt gettext_lazy"

    def test_name_ist_lazy(self, backend):
        assert isinstance(CounselingHoursBackend.name, Promise)


class TestBuildDataset:
    def test_kategorien_aufteilung_in_fester_ordnung(self, backend, facility, admin_user, date_range):
        """Drei Paket-Typen, eine Zeile je Kategorie — die Ordnung ist fest und
        damit wertunabhaengig (K5), nicht nach Anzahl sortiert.
        """
        krise = _typ_mit_dauer(facility, "Krisengespräch", report_key="crisis")
        kontakt = _typ_mit_dauer(facility, "Kontakt", report_key="contact")
        beratung = _typ_mit_dauer(facility, "Beratungsgespräch", report_key="counseling")
        _events(facility, krise, admin_user, 1, 90)
        _events(facility, kontakt, admin_user, 2, 15)
        _events(facility, beratung, admin_user, 3, 60)

        result = backend.build_dataset(facility, *date_range)

        assert result.mode == "rows"
        assert [(row["kategorie"], row["events"], row["minuten"]) for row in result.records] == [
            ("contact", 2, 30),
            ("counseling", 3, 180),
            ("crisis", 1, 90),
        ]
        assert result.summary["events_gesamt"] == 6
        assert result.summary["minuten_gesamt"] == 300

    def test_leere_kategorien_erzeugen_keine_zeile(self, backend, facility, admin_user, date_range):
        """Nur nichtleere Buckets — sonst waere die Zeilen-Existenz-Untergrenze
        des Inferenzmodells (``lo = 1``) falsch und der Guard liefe fail-open.
        """
        kontakt = _typ_mit_dauer(facility, "Kontakt", report_key="contact")
        _events(facility, kontakt, admin_user, 2, 15)

        result = backend.build_dataset(facility, *date_range)

        assert [row["kategorie"] for row in result.records] == ["contact"]

    def test_events_ohne_dauer_zaehlen_als_event(self, backend, facility, admin_user, date_range):
        """Entscheid (Brief D2): ein Ereignis ohne ``dauer`` ist trotzdem ein
        Ereignis — es zaehlt in ``events``, traegt 0 Minuten bei und erscheint
        als Vollstaendigkeits-Befund.
        """
        kontakt = _typ_mit_dauer(facility, "Kontakt", report_key="contact")
        _events(facility, kontakt, admin_user, 2, 15)
        _events(facility, kontakt, admin_user, 3, None)

        result = backend.build_dataset(facility, *date_range)

        assert result.records[0]["events"] == 5
        assert result.records[0]["minuten"] == 30
        assert result.records[0][BEITRAGENDE_KEY] == 2
        assert [issue.code for issue in result.completeness] == ["missing_field_values"]
        assert result.completeness[0].count == 3

    def test_muell_in_dauer_ist_ausschuss(self, backend, facility, admin_user, date_range):
        kontakt = _typ_mit_dauer(facility, "Kontakt", report_key="contact")
        _events(facility, kontakt, admin_user, 1, "eine Stunde")
        _events(facility, kontakt, admin_user, 1, "45")

        result = backend.build_dataset(facility, *date_range)

        assert result.records[0]["events"] == 2
        assert result.records[0]["minuten"] == 45
        assert result.records[0][BEITRAGENDE_KEY] == 1

    def test_verschluesselte_dauer_roundtrip(self, backend, facility, admin_user, date_range):
        kontakt = _typ_mit_dauer(facility, "Kontakt", report_key="contact", encrypted=True)
        _events(facility, kontakt, admin_user, 1, 45)
        event = Event.objects.get(document_type=kontakt)
        assert event.data_json["dauer"]["__encrypted__"] is True

        result = backend.build_dataset(facility, *date_range)

        assert result.records[0]["minuten"] == 45
        assert result.completeness == []

    def test_ohne_luecke_keine_meldung(self, backend, facility, admin_user, date_range):
        kontakt = _typ_mit_dauer(facility, "Kontakt", report_key="contact")
        _events(facility, kontakt, admin_user, 2, 15)

        result = backend.build_dataset(facility, *date_range)

        assert result.completeness == []

    def test_leerer_zeitraum(self, backend, facility, admin_user):
        kontakt = _typ_mit_dauer(facility, "Kontakt", report_key="contact")
        _events(facility, kontakt, admin_user, 2, 15)
        vorher = timezone.localdate() - timedelta(days=100)

        result = backend.build_dataset(facility, vorher, vorher + timedelta(days=1))

        assert result.records == []
        assert result.summary == {"events_gesamt": 0, "minuten_gesamt": 0}

    def test_soft_geloeschte_events_zaehlen_nicht(self, backend, facility, admin_user, date_range):
        kontakt = _typ_mit_dauer(facility, "Kontakt", report_key="contact")
        _events(facility, kontakt, admin_user, 1, 15)
        Event.objects.update(is_deleted=True)

        result = backend.build_dataset(facility, *date_range)

        assert result.records == []

    def test_fremde_einrichtung_zaehlt_nicht(
        self, backend, facility, second_facility, second_facility_user, date_range
    ):
        fremd = _typ_mit_dauer(second_facility, "Kontakt", report_key="contact")
        _events(second_facility, fremd, second_facility_user, 3, 15)

        result = backend.build_dataset(facility, *date_range)

        assert result.records == []


class TestTypaufloesung:
    def test_typ_zaehlt_ueber_den_report_key(self, backend, facility, admin_user, sample_event, date_range):
        """``doc_type_contact`` aus der conftest: die Zuordnung kommt allein aus
        ``report_key=contact`` (E-D; seit E4 gibt es keine zweite Achse mehr).
        """
        result = backend.build_dataset(facility, *date_range)

        assert [(row["kategorie"], row["events"], row["minuten"]) for row in result.records] == [("contact", 1, 15)]

    def test_der_slug_allein_zaehlt_nicht_mehr(self, backend, facility, admin_user, date_range):
        """Entscheid E-E (Refs #1849): der Paket-Slug ordnet nichts mehr zu.

        Bis E3 zog dieser Typ seine Ereignisse ueber den Slug ``kontakt`` in die
        Stundenbilanz — genau das war der Weg, ueber den in #1785 auch ein
        Notiz-Typ hereinkam.
        """
        typ = _typ_mit_dauer(facility, "Kontakt", report_key=None)
        assert typ.slug == "kontakt"
        _events(facility, typ, admin_user, 3, 15)

        result = backend.build_dataset(facility, *date_range)

        assert result.records == []

    def test_fremder_report_key_zaehlt_nicht(self, backend, facility, admin_user, date_range):
        typ = _typ_mit_dauer(facility, "Spritzentausch", report_key="needle_exchange")
        _events(facility, typ, admin_user, 3, 15)

        result = backend.build_dataset(facility, *date_range)

        assert result.records == []


def _zeile(kategorie, events, minuten, beitragende=None):
    return {
        "kategorie": kategorie,
        "events": events,
        "minuten": minuten,
        BEITRAGENDE_KEY: events if beitragende is None else beitragende,
    }


def _result(zeilen):
    return DatasetResult(
        mode="rows",
        records=zeilen,
        summary={
            "events_gesamt": sum(row["events"] for row in zeilen),
            "minuten_gesamt": sum(row["minuten"] for row in zeilen),
        },
        completeness=[],
    )


class TestPrepareExport:
    def test_alles_ueber_der_schwelle_bleibt_stehen(self, backend, facility):
        prepared = backend.prepare_export(facility, _result([_zeile("contact", 9, 135), _zeile("crisis", 7, 420)]))

        assert prepared.records == [
            {"kategorie": "contact", "events": 9, "minuten": 135},
            {"kategorie": "crisis", "events": 7, "minuten": 420},
        ]
        assert prepared.summary["events_gesamt"] == 16
        assert prepared.summary["minuten_gesamt"] == 555
        assert prepared.summary["k_anonymity_threshold"] == 5

    def test_kleine_zeile_zieht_ihre_minuten_und_die_minuten_randsumme_mit(self, backend, facility):
        """Suppressions-Kaskade: ``events`` < k -> auch ``minuten`` der Zeile weg,
        und ``minuten_gesamt`` faellt, weil es sonst die Differenz preisgaebe.
        """
        prepared = backend.prepare_export(
            facility, _result([_zeile("contact", 20, 300), _zeile("counseling", 9, 540), _zeile("crisis", 2, 120)])
        )

        zeilen = {row["kategorie"]: row for row in prepared.records}
        assert zeilen["crisis"] == {"kategorie": "crisis", "events": None, "minuten": None}
        assert prepared.summary["minuten_gesamt"] is None
        # Die Ereignis-Randsumme bleibt: mit zwei unterdrueckten Zellen ist
        # keine davon exakt rueckrechenbar (der zentrale Guard entscheidet das,
        # nicht das Backend).
        assert prepared.summary["events_gesamt"] == 31

    def test_zu_wenige_beitragende_events_lassen_nur_die_minuten_fallen(self, backend, facility):
        """Summen-Regel: die Bezugsgroesse der Minuten ist die Zahl der Events
        MIT ``dauer``, nicht die Zeilen-Ereigniszahl.
        """
        prepared = backend.prepare_export(
            facility, _result([_zeile("contact", 20, 300, beitragende=3), _zeile("crisis", 11, 660)])
        )

        zeilen = {row["kategorie"]: row for row in prepared.records}
        assert zeilen["contact"]["events"] == 20
        assert zeilen["contact"]["minuten"] is None
        assert zeilen["crisis"]["minuten"] == 660
        assert prepared.summary["minuten_gesamt"] is None

    def test_randsumme_unter_schwelle_faellt(self, backend, facility):
        prepared = backend.prepare_export(facility, _result([_zeile("contact", 2, 30)]))

        assert prepared.records == [{"kategorie": "contact", "events": None, "minuten": None}]
        assert prepared.summary["events_gesamt"] is None
        assert prepared.summary["minuten_gesamt"] is None

    def test_leerer_zeitraum_faellt_vollstaendig(self, backend, facility):
        prepared = backend.prepare_export(facility, _result([]))

        assert prepared.records == []
        assert prepared.summary["events_gesamt"] is None
        assert prepared.summary["minuten_gesamt"] is None

    def test_records_tragen_nur_die_field_spec_keys(self, backend, facility):
        """Weder das interne ``suppressed``-Flag noch der Suppressions-Eingabewert
        ``beitragende`` duerfen in CSV/JSON/XML landen.
        """
        prepared = backend.prepare_export(facility, _result([_zeile("contact", 9, 135)]))

        assert set(prepared.records[0]) == {"kategorie", "events", "minuten"}

    def test_completeness_ist_datenunabhaengig_und_unbeziffert(self, backend, facility):
        ohne = backend.prepare_export(facility, _result([_zeile("contact", 9, 135)]))
        mit = backend.prepare_export(facility, _result([_zeile("contact", 9, 135, beitragende=6)]))

        assert [issue.code for issue in ohne.completeness] == ["missing_field_values"]
        assert str(ohne.completeness[0].message) == str(mit.completeness[0].message)
        assert ohne.completeness[0].count is None

    def test_schwelle_aus_den_facility_settings(self, backend, facility):
        Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 2})

        prepared = backend.prepare_export(facility, _result([_zeile("contact", 2, 30), _zeile("crisis", 3, 90)]))

        assert prepared.summary["events_gesamt"] == 5
        assert prepared.summary["minuten_gesamt"] == 120


class TestExportPipeline:
    """Doku § 10: der Datensatz muss durch alle drei Renderer laufen.

    Geprueft wird auf dem Weg zugleich die Rueckprojektion: weder der interne
    Suppressions-Marker noch der Suppressions-Eingabewert ``beitragende`` darf
    im Artefakt auftauchen.
    """

    @pytest.mark.parametrize("fmt", ["csv", "json", "xml"])
    def test_export_laeuft_und_ist_wohlgeformt(self, backend, facility, admin_user, date_range, fmt):
        kontakt = _typ_mit_dauer(facility, "Kontakt", report_key="contact")
        krise = _typ_mit_dauer(facility, "Krisengespräch", report_key="crisis")
        _events(facility, kontakt, admin_user, 7, 20)
        _events(facility, krise, admin_user, 6, 90)

        response = export_dataset(backend, facility, *date_range, format=fmt)
        body = b"".join(response.streaming_content) if fmt == "csv" else response.content

        assert response.status_code == 200
        assert f"counseling-hours_{date_range[0]}_{date_range[1]}.{fmt}" in response["Content-Disposition"]
        if fmt == "json":
            payload = json.loads(body)
            assert payload["records"] == [
                {"kategorie": "contact", "events": 7, "minuten": 140},
                {"kategorie": "crisis", "events": 6, "minuten": 540},
            ]
        if fmt == "xml":
            # Begruendung fuers noqa (Muster ``test_reporting_export.py``):
            # eigener, gerade erst generierter Output, keine fremde Eingabe.
            ET.fromstring(body)  # noqa: S314
        if fmt == "csv":
            zeilen = body.decode().splitlines()
            assert next(csv.reader(zeilen[1:])) == ["Kategorie", "Ereignisse", "Minuten"]

    @pytest.mark.parametrize("fmt", ["csv", "json", "xml"])
    def test_kaskade_am_artefakt(self, backend, facility, admin_user, date_range, fmt):
        """Kleine Zeile im Artefakt: ``None`` in ZWEI Spalten. Die zweite Zeile
        faellt ueber die zentrale Sekundaer-Suppression mit (Residuum sonst
        exakt) — der Guard entscheidet das, nicht dieses Backend.
        """
        kontakt = _typ_mit_dauer(facility, "Kontakt", report_key="contact")
        krise = _typ_mit_dauer(facility, "Krisengespräch", report_key="crisis")
        _events(facility, kontakt, admin_user, 7, 20)
        _events(facility, krise, admin_user, 2, 90)

        response = export_dataset(backend, facility, *date_range, format=fmt)
        body = b"".join(response.streaming_content) if fmt == "csv" else response.content

        assert response.status_code == 200
        if fmt == "json":
            payload = json.loads(body)
            assert payload["records"] == [
                {"kategorie": "contact", "events": None, "minuten": None},
                {"kategorie": "crisis", "events": None, "minuten": None},
            ]
            assert payload["summary"]["minuten_gesamt"] is None
        if fmt == "xml":
            ET.fromstring(body)  # noqa: S314


class TestIsAvailable:
    def test_passender_typ_mit_dauer(self, backend, facility, doc_type_contact):
        assert backend.is_available(facility) is True

    def test_passender_typ_ohne_dauer(self, backend, facility):
        _typ_mit_dauer(facility, "Kontakt", report_key="contact", mit_dauer=False)

        assert backend.is_available(facility) is False

    def test_dauer_an_unpassendem_typ(self, backend, facility):
        _typ_mit_dauer(facility, "Spritzentausch", report_key="needle_exchange")

        assert backend.is_available(facility) is False

    def test_inaktiver_typ_zaehlt_nicht(self, backend, facility, doc_type_contact):
        DocumentType.objects.filter(pk=doc_type_contact.pk).update(is_active=False)

        assert backend.is_available(facility) is False

    def test_fremde_einrichtung_macht_nicht_verfuegbar(self, backend, facility, second_facility):
        _typ_mit_dauer(second_facility, "Kontakt", report_key="contact")

        assert backend.is_available(facility) is False
