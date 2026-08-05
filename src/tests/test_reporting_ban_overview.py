"""Tests fuer das Beispiel-Backend ``ban-overview`` (Refs #1754).

Hochsensibler Sonderfall: Hausverbote gehoeren bewusst NICHT in den
Sachbericht, dieser interne Bericht existiert per ausdruecklichem
Produktentscheid. Die Faelle pruefen deshalb neben der Checkliste
(``docs/reporting-plugins.md`` § 10) besonders scharf, was **nicht** hereinkommt:
kein Slug-Fallback, keine Gruende, keine Personen — und die ``data_json``-Lesart
inklusive Feldverschluesselung.
"""

import dataclasses
from datetime import date, timedelta

import pytest
from django.utils import timezone
from django.utils.functional import Promise

from core.models import (
    Client,
    DocumentType,
    DocumentTypeField,
    Event,
    Facility,
    FieldTemplate,
    Organization,
    Settings,
)
from core.reporting import registry
from core.reporting.base import CompletenessIssue, DatasetResult
from core.reporting.data_source import BANS_IN_PERIOD
from core.services.file_vault import is_encrypted_value

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """Registry je Test isolieren, echte Backends bleiben verfuegbar (§ 10)."""
    registry._discover()
    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))
    monkeypatch.setattr(registry, "_discovered", True)
    yield


@pytest.fixture
def backend():
    return registry.get_backend("ban-overview")


@pytest.fixture
def zeitraum():
    """Zeitraum relativ zu heute — ``aktiv_aktuell`` ist eine Momentaufnahme
    auf den Stichtag heute."""
    heute = timezone.localdate()
    return heute - timedelta(days=30), heute + timedelta(days=30)


@pytest.fixture
def doc_type_hausverbot(facility):
    return DocumentType.objects.create(
        facility=facility,
        name="Hausverbot",
        system_type=DocumentType.SystemType.BAN,
    )


@pytest.fixture
def doc_type_hausverbot_verschluesselt(facility):
    """Ban-Typ mit feldverschluesseltem ``aktiv``/``bis`` — die Lesart muss
    ueber ``safe_decrypt`` laufen (Muster ``_collect_active_bans``)."""
    doc_type = DocumentType.objects.create(
        facility=facility,
        name="Hausverbot (verschlüsselt)",
        system_type=DocumentType.SystemType.BAN,
    )
    for name in ("Aktiv", "Bis"):
        template = FieldTemplate.objects.create(
            facility=facility,
            name=name,
            field_type=FieldTemplate.FieldType.TEXT,
            is_encrypted=True,
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=template, sort_order=0)
    return doc_type


def _ban(facility, doc_type, user, *, aktiv=True, bis="", when=None, client=None):
    return Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=when or timezone.now(),
        data_json={"aktiv": aktiv, "bis": bis, "grund": "Freitext bleibt draußen"},
        created_by=user,
    )


def _kennzahlen(result):
    return {row["kennzahl"]: row["wert"] for row in result.records}


class TestDiscovery:
    def test_backend_wird_automatisch_gefunden(self):
        assert "ban-overview" in [b.slug for b in registry.get_backends()]


class TestBackendMetadaten:
    def test_slug_und_snapshot_flag(self, backend):
        assert backend.slug == "ban-overview"
        assert backend.snapshot_enabled is False

    def test_population_ist_hausverbote_im_zeitraum(self, backend):
        """Fix-Runde 1 (Refs #1754): frueher ``ALL_EVENTS`` — sachlich falsch.
        Gezaehlt werden nur Hausverbots-Ereignisse, und zwei der drei Zahlen
        ignorieren ``occurred_at`` voellig (Stichtag bzw. Ablaufdatum)."""
        assert backend.population == BANS_IN_PERIOD

    def test_name_und_labels_sind_lazy(self, backend):
        assert isinstance(backend.name, Promise)
        for spec in backend.get_field_specs():
            assert isinstance(spec.label, Promise), f"FieldSpec {spec.key!r} traegt Klartext statt gettext_lazy"

    def test_field_spec_keys(self, backend):
        assert [spec.key for spec in backend.get_field_specs()] == ["kennzahl", "wert"]


class TestBuildDataset:
    def test_leere_facility_liefert_drei_nullzeilen(self, backend, facility, zeitraum):
        result = backend.build_dataset(facility, *zeitraum)

        assert result.mode == "aggregate"
        assert _kennzahlen(result) == {"ausgesprochen": 0, "aktiv_aktuell": 0, "abgelaufen_im_zeitraum": 0}
        assert result.summary == {"ausgesprochen": 0, "aktiv_aktuell": 0, "abgelaufen_im_zeitraum": 0}
        assert [issue.code for issue in result.completeness] == ["point_in_time_active_bans"]

    def test_records_spiegeln_das_summary(self, backend, facility, zeitraum, staff_user, doc_type_hausverbot):
        _ban(facility, doc_type_hausverbot, staff_user)

        result = backend.build_dataset(facility, *zeitraum)

        assert _kennzahlen(result) == result.summary

    def test_ausgesprochen_nur_im_zeitraum(self, backend, facility, zeitraum, staff_user, doc_type_hausverbot):
        _ban(facility, doc_type_hausverbot, staff_user)
        _ban(facility, doc_type_hausverbot, staff_user, when=timezone.now() - timedelta(days=90))

        assert backend.build_dataset(facility, *zeitraum).summary["ausgesprochen"] == 1

    def test_nicht_ban_typen_zaehlen_nicht(self, backend, facility, zeitraum, staff_user):
        kontakt = DocumentType.objects.create(
            facility=facility, name="Kontakt", system_type=DocumentType.SystemType.CONTACT
        )
        _ban(facility, kontakt, staff_user)

        assert backend.build_dataset(facility, *zeitraum).summary == {
            "ausgesprochen": 0,
            "aktiv_aktuell": 0,
            "abgelaufen_im_zeitraum": 0,
        }

    def test_kein_slug_fallback(self, backend, facility, zeitraum, staff_user):
        """Ein Typ mit dem Slug ``hausverbot``, aber ohne ``system_type``, ist
        KEIN Hausverbot im Sinne dieses Berichts (Disclosure-Lehre #1785): ein
        bewusst nicht gesetzter ``system_type`` darf sich nicht ueber die
        Namenskonvention Zugang verschaffen."""
        getarnt = DocumentType.objects.create(facility=facility, name="Hausverbot", slug="hausverbot")
        _ban(facility, getarnt, staff_user)

        assert backend.build_dataset(facility, *zeitraum).summary["ausgesprochen"] == 0

    def test_soft_geloeschtes_ban_ereignis_zaehlt_nicht(
        self, backend, facility, zeitraum, staff_user, doc_type_hausverbot
    ):
        event = _ban(facility, doc_type_hausverbot, staff_user)
        event.is_deleted = True
        event.save(update_fields=["is_deleted"])

        assert backend.build_dataset(facility, *zeitraum).summary["ausgesprochen"] == 0
        assert backend.build_dataset(facility, *zeitraum).summary["aktiv_aktuell"] == 0

    def test_fremde_facility_ist_unsichtbar(self, backend, facility, zeitraum, staff_user):
        fremd = Facility.objects.create(organization=Organization.objects.create(name="Fremd"), name="Fremdstelle")
        fremd_typ = DocumentType.objects.create(
            facility=fremd, name="Hausverbot", system_type=DocumentType.SystemType.BAN
        )
        _ban(fremd, fremd_typ, staff_user)

        assert backend.build_dataset(facility, *zeitraum).summary["ausgesprochen"] == 0


class TestAktivAktuell:
    """Lesart exakt nach ``_collect_active_bans`` (``services/system/bans.py``)."""

    def test_leeres_bis_gilt_als_aktiv(self, backend, facility, zeitraum, staff_user, doc_type_hausverbot):
        _ban(facility, doc_type_hausverbot, staff_user, aktiv=True, bis="")

        assert backend.build_dataset(facility, *zeitraum).summary["aktiv_aktuell"] == 1

    def test_bis_heute_gilt_noch_als_aktiv(self, backend, facility, zeitraum, staff_user, doc_type_hausverbot):
        _ban(facility, doc_type_hausverbot, staff_user, bis=timezone.localdate().isoformat())

        assert backend.build_dataset(facility, *zeitraum).summary["aktiv_aktuell"] == 1

    def test_bis_gestern_ist_nicht_mehr_aktiv(self, backend, facility, zeitraum, staff_user, doc_type_hausverbot):
        gestern = (timezone.localdate() - timedelta(days=1)).isoformat()
        _ban(facility, doc_type_hausverbot, staff_user, bis=gestern)

        assert backend.build_dataset(facility, *zeitraum).summary["aktiv_aktuell"] == 0

    def test_aufgehobenes_hausverbot_ist_nicht_aktiv(
        self, backend, facility, zeitraum, staff_user, doc_type_hausverbot
    ):
        _ban(facility, doc_type_hausverbot, staff_user, aktiv=False, bis="")

        assert backend.build_dataset(facility, *zeitraum).summary["aktiv_aktuell"] == 0

    def test_ist_momentaufnahme_ohne_zeitraumbezug(self, backend, facility, zeitraum, staff_user, doc_type_hausverbot):
        """Ein lange vor dem Zeitraum ausgesprochenes, heute noch geltendes
        Hausverbot zaehlt mit — die Zahl beschreibt den Stichtag heute."""
        _ban(facility, doc_type_hausverbot, staff_user, when=timezone.now() - timedelta(days=365))

        summary = backend.build_dataset(facility, *zeitraum).summary

        assert summary["ausgesprochen"] == 0
        assert summary["aktiv_aktuell"] == 1

    def test_defektes_bis_datum_gilt_als_kein_ablauf(
        self, backend, facility, zeitraum, staff_user, doc_type_hausverbot
    ):
        _ban(facility, doc_type_hausverbot, staff_user, bis="kein-datum")

        summary = backend.build_dataset(facility, *zeitraum).summary

        assert summary["aktiv_aktuell"] == 1
        assert summary["abgelaufen_im_zeitraum"] == 0

    def test_verschluesselte_felder_werden_entschluesselt(
        self, backend, facility, zeitraum, staff_user, doc_type_hausverbot_verschluesselt
    ):
        """``aktiv``/``bis`` liegen als Fernet-Token in der Datenbank; ohne
        ``safe_decrypt`` liefe die Zaehlung gegen Chiffrat."""
        bis = (timezone.localdate() + timedelta(days=10)).isoformat()
        event = _ban(facility, doc_type_hausverbot_verschluesselt, staff_user, aktiv=True, bis=bis)
        event.refresh_from_db()

        assert is_encrypted_value(event.data_json["aktiv"]), "Testaufbau: ``aktiv`` muss verschlüsselt vorliegen"
        assert is_encrypted_value(event.data_json["bis"]), "Testaufbau: ``bis`` muss verschlüsselt vorliegen"

        summary = backend.build_dataset(facility, *zeitraum).summary

        assert summary["aktiv_aktuell"] == 1
        assert summary["abgelaufen_im_zeitraum"] == 1


class TestAbgelaufenImZeitraum:
    def test_bis_im_zeitraum_zaehlt(self, backend, facility, zeitraum, staff_user, doc_type_hausverbot):
        _ban(facility, doc_type_hausverbot, staff_user, bis=(timezone.localdate() - timedelta(days=5)).isoformat())

        assert backend.build_dataset(facility, *zeitraum).summary["abgelaufen_im_zeitraum"] == 1

    def test_bis_ausserhalb_zaehlt_nicht(self, backend, facility, zeitraum, staff_user, doc_type_hausverbot):
        _ban(facility, doc_type_hausverbot, staff_user, bis=(timezone.localdate() - timedelta(days=90)).isoformat())

        assert backend.build_dataset(facility, *zeitraum).summary["abgelaufen_im_zeitraum"] == 0

    def test_leeres_bis_laeuft_nie_ab(self, backend, facility, zeitraum, staff_user, doc_type_hausverbot):
        _ban(facility, doc_type_hausverbot, staff_user, bis="")

        assert backend.build_dataset(facility, *zeitraum).summary["abgelaufen_im_zeitraum"] == 0

    def test_aufgehobenes_hausverbot_zaehlt_trotzdem_als_abgelaufen(
        self, backend, facility, zeitraum, staff_user, doc_type_hausverbot
    ):
        """Bewusster Entscheid: ``aktiv`` ist der **heutige** Zustand und keine
        historische Aussage. Wuerde er hier ausgewertet, aenderte ein
        Aufheben von heute die Zahl eines laengst vergangenen Zeitraums."""
        _ban(
            facility,
            doc_type_hausverbot,
            staff_user,
            aktiv=False,
            bis=(timezone.localdate() - timedelta(days=5)).isoformat(),
        )

        summary = backend.build_dataset(facility, *zeitraum).summary

        assert summary["aktiv_aktuell"] == 0
        assert summary["abgelaufen_im_zeitraum"] == 1


class TestKeineSensiblenDetails:
    def test_weder_gruende_noch_personen_im_ergebnis(
        self, backend, facility, zeitraum, staff_user, doc_type_hausverbot
    ):
        """Der Bericht kennt drei Zahlen und sonst nichts — kein Freitext, kein
        Pseudonym, keine Zeile je Hausverbot."""
        person = Client.objects.create(facility=facility, pseudonym="Betroffen-01")
        _ban(facility, doc_type_hausverbot, staff_user, client=person)

        result = backend.build_dataset(facility, *zeitraum)
        text = repr(result)

        assert "Freitext" not in text
        assert "Betroffen-01" not in text
        assert len(result.records) == 3


class TestIsAvailable:
    def test_positiv_mit_aktivem_ban_typ(self, backend, facility, doc_type_hausverbot):
        assert backend.is_available(facility) is True

    def test_negativ_bei_leerer_facility(self, backend, facility):
        assert backend.is_available(facility) is False

    def test_negativ_ohne_ban_typ(self, backend, facility):
        DocumentType.objects.create(facility=facility, name="Kontakt", system_type=DocumentType.SystemType.CONTACT)

        assert backend.is_available(facility) is False

    def test_negativ_wenn_ban_typ_deaktiviert(self, backend, facility, doc_type_hausverbot):
        doc_type_hausverbot.is_active = False
        doc_type_hausverbot.save()

        assert backend.is_available(facility) is False

    def test_kein_slug_fallback(self, backend, facility):
        DocumentType.objects.create(facility=facility, name="Hausverbot", slug="hausverbot")

        assert backend.is_available(facility) is False


def _rohergebnis(**werte):
    summary = {"ausgesprochen": 0, "aktiv_aktuell": 0, "abgelaufen_im_zeitraum": 0, **werte}
    return DatasetResult(
        mode="aggregate",
        records=[{"kennzahl": key, "wert": wert} for key, wert in summary.items()],
        summary=dict(summary),
        completeness=[],
    )


class TestPrepareExport:
    def test_zahlen_unter_der_schwelle_fallen(self, backend, facility):
        prepared = backend.prepare_export(facility, _rohergebnis(ausgesprochen=4, aktiv_aktuell=9))

        assert prepared.summary["ausgesprochen"] is None
        assert prepared.summary["abgelaufen_im_zeitraum"] is None
        assert prepared.summary["aktiv_aktuell"] == 9
        assert prepared.summary["k_anonymity_threshold"] == 5

    def test_records_und_summary_bleiben_deckungsgleich(self, backend, facility):
        prepared = backend.prepare_export(facility, _rohergebnis(ausgesprochen=4, aktiv_aktuell=9))

        assert {row["kennzahl"]: row["wert"] for row in prepared.records} == {
            key: wert for key, wert in prepared.summary.items() if key != "k_anonymity_threshold"
        }

    def test_zahlen_ab_der_schwelle_bleiben_sichtbar(self, backend, facility):
        prepared = backend.prepare_export(
            facility, _rohergebnis(ausgesprochen=6, aktiv_aktuell=12, abgelaufen_im_zeitraum=5)
        )

        assert prepared.summary["ausgesprochen"] == 6
        assert prepared.summary["aktiv_aktuell"] == 12
        assert prepared.summary["abgelaufen_im_zeitraum"] == 5

    def test_records_tragen_exakt_die_field_spec_keys(self, backend, facility):
        prepared = backend.prepare_export(facility, _rohergebnis(ausgesprochen=2))

        field_keys = {spec.key for spec in backend.get_field_specs()}
        for row in prepared.records:
            assert set(row) == field_keys

    def test_summary_traegt_keine_zahl_ohne_zugehoerige_zeile(self, backend, facility):
        """Fail-safe: ``summary`` entsteht aus den suppressierten Zeilen. Faellt
        eine Zeile weg, darf der Rohwert nicht im Aggregat ueberleben."""
        roh = _rohergebnis(ausgesprochen=42, aktiv_aktuell=17)
        ohne_zeilen = dataclasses.replace(roh, records=[])

        prepared = backend.prepare_export(facility, ohne_zeilen)

        assert prepared.records == []
        assert prepared.summary["ausgesprochen"] is None
        assert prepared.summary["aktiv_aktuell"] is None

    def test_momentaufnahme_hinweis_erreicht_das_artefakt(self, backend, facility):
        """I1 (Fix-Runde 1): ``aktiv_aktuell`` ist eine Stichtagszahl — die
        Kennzeichnung muss im Artefakt stehen, nicht nur im Docstring.
        Datenunabhaengig, ziffernfrei, Severity ``info``."""
        leer = backend.prepare_export(facility, _rohergebnis())
        voll = backend.prepare_export(facility, _rohergebnis(ausgesprochen=7, aktiv_aktuell=12))

        for prepared in (leer, voll):
            assert [issue.code for issue in prepared.completeness] == ["point_in_time_active_bans"]
            issue = prepared.completeness[0]
            assert issue.severity == "info"
            assert issue.count is None
            assert not any(zeichen.isdigit() for zeichen in str(issue.message))

    def test_momentaufnahme_hinweis_wird_nicht_aus_dem_ergebnis_abgeleitet(self, backend, facility):
        """Fail-safe wie K6 (Refs #1769): fester Code statt Ableitung aus
        ``result.completeness`` — bei diesem Thema besonders wichtig."""
        roh = _rohergebnis(ausgesprochen=7)
        mit_fremdem_hinweis = dataclasses.replace(
            roh, completeness=[CompletenessIssue("leaky", "3 Hausverbote betroffen", "warning", count=3)]
        )

        prepared = backend.prepare_export(facility, mit_fremdem_hinweis)

        assert [issue.code for issue in prepared.completeness] == ["point_in_time_active_bans"]

    def test_schwelle_kommt_aus_den_facility_settings(self, backend, facility):
        Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 1})

        prepared = backend.prepare_export(facility, _rohergebnis(ausgesprochen=2))

        assert prepared.summary["ausgesprochen"] == 2
        assert prepared.summary["k_anonymity_threshold"] == 1


class TestEndeZuEndeUeberBuildDataset:
    def test_panel_sicht_zeigt_suppressierte_zahlen(self, backend, facility, zeitraum, staff_user, doc_type_hausverbot):
        _ban(facility, doc_type_hausverbot, staff_user, bis=(date.today() + timedelta(days=5)).isoformat())

        prepared = backend.prepare_export(facility, backend.build_dataset(facility, *zeitraum))

        assert all(row["wert"] is None for row in prepared.records)
        assert prepared.summary["aktiv_aktuell"] is None
