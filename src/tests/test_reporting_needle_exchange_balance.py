"""Tests fuer das Beispiel-Backend ``needle-exchange-balance`` (Refs #1754).

Erstes Backend, das ``data_json``-FELDWERTE auswertet (Kernluecke aus #1754).
Die Pflichtfaelle decken deshalb genau die Fehlerklassen ab, die es dabei neu
gibt und die kein bisheriges Backend haben konnte:

* **Zahl-Koersion** ueber gemischte Rohwerte (``int``/``float``/Zahl-als-String/
  Muell) — Ausschuss darf niemals crashen, sondern zaehlt als Vollstaendigkeits-
  Befund.
* **Verschluesselte Feldwerte** (``is_encrypted``-Roundtrip): ``Event.save()``
  legt ``{"__encrypted__": …}`` ab, ``safe_decrypt`` liefert einen **String**
  zurueck — ohne Koersion waere die Summe still 0.
* **Typaufloesung** genau nach dem ``resolve_category``-Muster: seit E3
  (Refs #1849) entscheidet der ``report_key`` allein, der Paket-Slug ordnet
  nichts mehr zu.
* **K-Anon fuer Summen** (Maintainer-Entscheid): eine Summe ist keine
  Personenzaehlung — sie faellt, wenn die Zahl der **beitragenden** Events unter
  der Schwelle liegt, und der ``saldo`` faellt immer mit ihr.
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
from core.reporting.backends.needle_exchange_balance import NeedleExchangeBalanceBackend
from core.reporting.base import DatasetResult
from core.reporting.data_source import TYPED_EVENTS
from core.reporting.export import export_dataset
from tests._reporting_helpers import feld_report_key_fuer

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """Registry je Test isolieren, echte Backends bleiben verfuegbar (Doku § 10)."""
    registry._discover()
    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))
    monkeypatch.setattr(registry, "_discovered", True)
    yield


@pytest.fixture
def backend():
    return registry.get_backend("needle-exchange-balance")


@pytest.fixture
def date_range():
    today = timezone.localdate()
    return today - timedelta(days=1), today + timedelta(days=1)


def _feld(facility, name, *, encrypted=False):
    return FieldTemplate.objects.create(
        facility=facility,
        name=name,
        field_type=FieldTemplate.FieldType.NUMBER,
        is_encrypted=encrypted,
        report_key=feld_report_key_fuer(name),
    )


def _spritzentausch_typ(facility, *, name="Spritzentausch", report_key="needle_exchange"):
    """Dokumentationstyp mit den Paket-Feldern ``ausgabe``/``rueckgabe``.

    Der ``report_key`` ist die Achse, auf der dieses Backend bindet (E3, Refs
    #1849) — und seit E4 (Refs #1850) die einzige: ``system_type`` kennt den
    Wert ``needle_exchange`` nicht mehr. Die Aufloesungstests setzen ihn
    bewusst auf ``None``.
    """
    doc_type = DocumentType.objects.create(
        facility=facility,
        name=name,
        report_key=report_key,
    )
    for feld in (_feld(facility, "Ausgabe"), _feld(facility, "Rückgabe")):
        DocumentTypeField.objects.create(document_type=doc_type, field_template=feld)
    return doc_type


def _event(facility, doc_type, user, data):
    return Event.objects.create(
        facility=facility,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json=data,
        created_by=user,
    )


class TestDiscoveryUndMetadaten:
    def test_discovery_findet_das_backend(self):
        assert "needle-exchange-balance" in [b.slug for b in registry.get_backends()]

    def test_registriert_unter_slug(self, backend):
        assert isinstance(backend, NeedleExchangeBalanceBackend)
        assert backend.slug == "needle-exchange-balance"

    def test_snapshot_bleibt_aus(self, backend):
        assert backend.snapshot_enabled is False

    def test_grundgesamtheit_ist_enger_als_der_default(self, backend):
        """I3: das Backend zaehlt NICHT alle Ereignisse des Zeitraums, sondern
        nur die seiner Dokumentationstypen — ``base.py`` verlangt dann eine
        explizite, engere Deklaration.
        """
        assert backend.population == TYPED_EVENTS

    def test_field_specs(self, backend):
        assert [spec.key for spec in backend.get_field_specs()] == [
            "events",
            "summe_ausgabe",
            "summe_rueckgabe",
            "saldo",
        ]

    def test_labels_sind_lazy(self, backend):
        for spec in backend.get_field_specs():
            assert isinstance(spec.label, Promise), f"FieldSpec {spec.key!r} traegt Klartext statt gettext_lazy"

    def test_name_ist_lazy(self, backend):
        assert isinstance(NeedleExchangeBalanceBackend.name, Promise)


class TestBuildDataset:
    def test_mischwerte_werden_koerziert(self, backend, facility, admin_user, date_range):
        """int, float und Zahl-als-String zaehlen; Muell ist Ausschuss."""
        doc_type = _spritzentausch_typ(facility)
        _event(facility, doc_type, admin_user, {"ausgabe": 10, "rueckgabe": 4})
        _event(facility, doc_type, admin_user, {"ausgabe": 2.5, "rueckgabe": 0})
        _event(facility, doc_type, admin_user, {"ausgabe": "7", "rueckgabe": "3"})
        _event(facility, doc_type, admin_user, {"ausgabe": "viele", "rueckgabe": "keine"})

        result = backend.build_dataset(facility, *date_range)

        assert result.mode == "aggregate"
        assert result.summary["events"] == 4
        assert result.summary["summe_ausgabe"] == pytest.approx(19.5)
        assert result.summary["summe_rueckgabe"] == 7
        assert result.summary["saldo"] == pytest.approx(12.5)
        assert result.summary["events_ohne_werte"] == 1

    def test_records_projizieren_das_aggregat(self, backend, facility, admin_user, date_range):
        doc_type = _spritzentausch_typ(facility)
        _event(facility, doc_type, admin_user, {"ausgabe": 10, "rueckgabe": 4})

        result = backend.build_dataset(facility, *date_range)

        assert result.records == [{"events": 1, "summe_ausgabe": 10, "summe_rueckgabe": 4, "saldo": 6}]

    def test_bool_ist_kein_zahlwert(self, backend, facility, admin_user, date_range):
        """``True`` ist in Python ein ``int`` — als Stueckzahl waere es Unsinn."""
        doc_type = _spritzentausch_typ(facility)
        _event(facility, doc_type, admin_user, {"ausgabe": True, "rueckgabe": False})

        result = backend.build_dataset(facility, *date_range)

        assert result.summary["summe_ausgabe"] == 0
        assert result.summary["events_ohne_werte"] == 1

    def test_teilwerte_zaehlen_als_beitragend(self, backend, facility, admin_user, date_range):
        """Ein Event mit nur EINEM numerischen Wert traegt bei — ``events_ohne_werte``
        zaehlt nur Events, bei denen BEIDE Werte unbrauchbar sind.
        """
        doc_type = _spritzentausch_typ(facility)
        _event(facility, doc_type, admin_user, {"ausgabe": 5})

        result = backend.build_dataset(facility, *date_range)

        assert result.summary["summe_ausgabe"] == 5
        assert result.summary["summe_rueckgabe"] == 0
        assert result.summary["events_ohne_werte"] == 0

    def test_fehlende_felder_melden_completeness(self, backend, facility, admin_user, date_range):
        doc_type = _spritzentausch_typ(facility)
        _event(facility, doc_type, admin_user, {})
        _event(facility, doc_type, admin_user, {"ausgabe": 3, "rueckgabe": 1})

        result = backend.build_dataset(facility, *date_range)

        assert result.summary["events_ohne_werte"] == 1
        codes = [issue.code for issue in result.completeness]
        assert codes == ["missing_field_values"]
        assert result.completeness[0].count == 1

    def test_ohne_luecke_keine_meldung(self, backend, facility, admin_user, date_range):
        doc_type = _spritzentausch_typ(facility)
        _event(facility, doc_type, admin_user, {"ausgabe": 3, "rueckgabe": 1})

        result = backend.build_dataset(facility, *date_range)

        assert result.completeness == []

    def test_verschluesselte_feldwerte_roundtrip(self, backend, facility, admin_user, date_range):
        """``is_encrypted``: ``Event.save()`` legt einen Fernet-Marker ab,
        ``safe_decrypt`` liefert einen String zurueck — die Koersion muss ihn fassen.
        """
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Spritzentausch",
            report_key="needle_exchange",
        )
        for feld in (_feld(facility, "Ausgabe", encrypted=True), _feld(facility, "Rückgabe", encrypted=True)):
            DocumentTypeField.objects.create(document_type=doc_type, field_template=feld)
        event = _event(facility, doc_type, admin_user, {"ausgabe": 12, "rueckgabe": 5})
        event.refresh_from_db()
        assert event.data_json["ausgabe"]["__encrypted__"] is True

        result = backend.build_dataset(facility, *date_range)

        assert result.summary["summe_ausgabe"] == 12
        assert result.summary["summe_rueckgabe"] == 5
        assert result.summary["events_ohne_werte"] == 0

    def test_leerer_zeitraum(self, backend, facility, admin_user):
        doc_type = _spritzentausch_typ(facility)
        _event(facility, doc_type, admin_user, {"ausgabe": 3, "rueckgabe": 1})
        vorher = timezone.localdate() - timedelta(days=100)

        result = backend.build_dataset(facility, vorher, vorher + timedelta(days=1))

        assert result.summary["events"] == 0
        assert result.summary["saldo"] == 0
        # Die Aggregatzeile wird datenunabhaengig emittiert — ihre blosse
        # Existenz darf nichts ueber den Bestand aussagen.
        assert result.records == [{"events": 0, "summe_ausgabe": 0, "summe_rueckgabe": 0, "saldo": 0}]

    def test_soft_geloeschte_events_zaehlen_nicht(self, backend, facility, admin_user, date_range):
        doc_type = _spritzentausch_typ(facility)
        event = _event(facility, doc_type, admin_user, {"ausgabe": 3, "rueckgabe": 1})
        Event.objects.filter(pk=event.pk).update(is_deleted=True)

        result = backend.build_dataset(facility, *date_range)

        assert result.summary["events"] == 0

    def test_fremde_einrichtung_zaehlt_nicht(
        self, backend, facility, second_facility, second_facility_user, date_range
    ):
        fremd_typ = _spritzentausch_typ(second_facility)
        _event(second_facility, fremd_typ, second_facility_user, {"ausgabe": 3, "rueckgabe": 1})

        result = backend.build_dataset(facility, *date_range)

        assert result.summary["events"] == 0


class TestTypaufloesung:
    def test_typ_zaehlt_ueber_den_report_key(self, backend, facility, admin_user, date_range):
        """Ein einrichtungseigener Typ mit Schluessel zaehlt mit (E-D)."""
        doc_type = _spritzentausch_typ(facility, report_key="needle_exchange")
        _event(facility, doc_type, admin_user, {"ausgabe": 8, "rueckgabe": 2})

        result = backend.build_dataset(facility, *date_range)

        assert result.summary["events"] == 1
        assert result.summary["saldo"] == 6

    def test_der_paket_slug_allein_zaehlt_nicht_mehr(self, backend, facility, admin_user, date_range):
        """Entscheid E-E (Refs #1849): der Slug ``spritzentausch`` ordnet nichts mehr zu."""
        doc_type = _spritzentausch_typ(facility, report_key=None)
        assert doc_type.slug == "spritzentausch"
        _event(facility, doc_type, admin_user, {"ausgabe": 8, "rueckgabe": 2})

        result = backend.build_dataset(facility, *date_range)

        assert result.summary["events"] == 0

    def test_fremder_slug_ohne_schluessel_zaehlt_nicht(self, backend, facility, admin_user, date_range):
        doc_type = _spritzentausch_typ(facility, name="Materialausgabe", report_key=None)
        _event(facility, doc_type, admin_user, {"ausgabe": 8, "rueckgabe": 2})

        result = backend.build_dataset(facility, *date_range)

        assert result.summary["events"] == 0


class TestPrepareExport:
    def _result(self, events, ausgabe, rueckgabe, ohne_werte=0):
        return DatasetResult(
            mode="aggregate",
            records=[
                {
                    "events": events,
                    "summe_ausgabe": ausgabe,
                    "summe_rueckgabe": rueckgabe,
                    "saldo": ausgabe - rueckgabe,
                }
            ],
            summary={
                "events": events,
                "summe_ausgabe": ausgabe,
                "summe_rueckgabe": rueckgabe,
                "saldo": ausgabe - rueckgabe,
                "events_ohne_werte": ohne_werte,
            },
            completeness=[],
        )

    def test_unter_der_schwelle_faellt_alles_inklusive_saldo(self, backend, facility):
        prepared = backend.prepare_export(facility, self._result(4, 40, 10))

        assert prepared.summary["events"] is None
        assert prepared.summary["summe_ausgabe"] is None
        assert prepared.summary["summe_rueckgabe"] is None
        assert prepared.summary["saldo"] is None
        assert prepared.records == [{"events": None, "summe_ausgabe": None, "summe_rueckgabe": None, "saldo": None}]
        assert prepared.summary["k_anonymity_threshold"] == 5

    def test_ab_der_schwelle_alles_sichtbar(self, backend, facility):
        prepared = backend.prepare_export(facility, self._result(5, 40, 10))

        assert prepared.summary["events"] == 5
        assert prepared.summary["summe_ausgabe"] == 40
        assert prepared.summary["summe_rueckgabe"] == 10
        assert prepared.summary["saldo"] == 30
        assert prepared.records == [{"events": 5, "summe_ausgabe": 40, "summe_rueckgabe": 10, "saldo": 30}]

    def test_zu_wenige_beitragende_events_lassen_die_summen_fallen(self, backend, facility):
        """Kern-Semantik (globals.md): eine Summe ist keine Personenzaehlung —
        sie faellt, wenn die Zahl der BEITRAGENDEN Events unter *k* liegt, auch
        wenn die Event-Zahl selbst publiziert werden darf.
        """
        prepared = backend.prepare_export(facility, self._result(9, 40, 10, ohne_werte=6))

        assert prepared.summary["events"] == 9
        assert prepared.summary["summe_ausgabe"] is None
        assert prepared.summary["summe_rueckgabe"] is None
        assert prepared.summary["saldo"] is None

    def test_events_ohne_werte_faellt_als_kleinstzelle(self, backend, facility):
        """``events`` = beitragende + ``events_ohne_werte`` ist eine exakte
        Partition: eine publizierte Kleinstzelle waere direkt rueckrechenbar.
        """
        prepared = backend.prepare_export(facility, self._result(9, 40, 10, ohne_werte=2))

        assert prepared.summary["events"] == 9
        assert prepared.summary["summe_ausgabe"] == 40
        assert prepared.summary["events_ohne_werte"] is None

    def test_events_ohne_werte_ab_k_sichtbar(self, backend, facility):
        prepared = backend.prepare_export(facility, self._result(12, 40, 10, ohne_werte=5))

        assert prepared.summary["events_ohne_werte"] == 5

    def test_completeness_ist_datenunabhaengig_und_unbeziffert(self, backend, facility):
        """K6 (Refs #1769): die Export-Meldung steht immer und nennt nie eine Zahl."""
        ohne_luecke = backend.prepare_export(facility, self._result(12, 40, 10, ohne_werte=0))
        mit_luecke = backend.prepare_export(facility, self._result(12, 40, 10, ohne_werte=6))

        assert [issue.code for issue in ohne_luecke.completeness] == ["missing_field_values"]
        assert [issue.code for issue in mit_luecke.completeness] == ["missing_field_values"]
        assert str(ohne_luecke.completeness[0].message) == str(mit_luecke.completeness[0].message)
        assert ohne_luecke.completeness[0].count is None

    def test_schwelle_aus_den_facility_settings(self, backend, facility):
        Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 2})

        prepared = backend.prepare_export(facility, self._result(4, 40, 10))

        assert prepared.summary["events"] == 4
        assert prepared.summary["saldo"] == 30
        assert prepared.summary["k_anonymity_threshold"] == 2


class TestExportPipeline:
    """Doku § 10: der Datensatz muss durch alle drei Renderer laufen.

    Nicht der Format-Dispatch ist hier der Prueflauf (den pinnt
    ``test_reporting_export.py`` generisch), sondern DIESER Datensatz: eine
    einzelne Aggregatzeile mit ``float``-Summen und ``None``-Zellen aus der
    Suppression ist genau die Form, an der ein Serialisierer stolpert.
    """

    @pytest.mark.parametrize("fmt", ["csv", "json", "xml"])
    def test_export_laeuft_und_ist_wohlgeformt(self, backend, facility, admin_user, date_range, fmt):
        doc_type = _spritzentausch_typ(facility)
        for wert in (10, 12.5, "7", 4, 9, 3):
            _event(facility, doc_type, admin_user, {"ausgabe": wert, "rueckgabe": 1})

        response = export_dataset(backend, facility, *date_range, format=fmt)
        body = b"".join(response.streaming_content) if fmt == "csv" else response.content

        assert response.status_code == 200
        assert f"needle-exchange-balance_{date_range[0]}_{date_range[1]}.{fmt}" in response["Content-Disposition"]
        if fmt == "json":
            payload = json.loads(body)
            assert payload["records"] == [{"events": 6, "summe_ausgabe": 45.5, "summe_rueckgabe": 6, "saldo": 39.5}]
        if fmt == "xml":
            # Begruendung fuers noqa (Muster ``test_reporting_export.py``):
            # eigener, gerade erst generierter Output, keine fremde Eingabe.
            ET.fromstring(body)  # noqa: S314
        if fmt == "csv":
            zeilen = body.decode().splitlines()
            assert next(csv.reader(zeilen[1:])) == ["Ereignisse", "Ausgabe gesamt", "Rückgabe gesamt", "Saldo"]

    @pytest.mark.parametrize("fmt", ["csv", "json", "xml"])
    def test_unterdrueckter_datensatz_laeuft_ebenfalls(self, backend, facility, admin_user, date_range, fmt):
        """Der Suppressionsfall ist der haeufigere — und der mit ``None`` in
        jeder Zelle.
        """
        doc_type = _spritzentausch_typ(facility)
        _event(facility, doc_type, admin_user, {"ausgabe": 10, "rueckgabe": 4})

        response = export_dataset(backend, facility, *date_range, format=fmt)
        body = b"".join(response.streaming_content) if fmt == "csv" else response.content

        assert response.status_code == 200
        if fmt == "json":
            assert json.loads(body)["records"] == [
                {"events": None, "summe_ausgabe": None, "summe_rueckgabe": None, "saldo": None}
            ]
        if fmt == "xml":
            ET.fromstring(body)  # noqa: S314


class TestIsAvailable:
    def test_beide_felder_angehaengt(self, backend, facility):
        _spritzentausch_typ(facility)

        assert backend.is_available(facility) is True

    def test_nur_ein_feld_reicht_nicht(self, backend, facility):
        doc_type = DocumentType.objects.create(facility=facility, name="Spritzentausch", report_key="needle_exchange")
        DocumentTypeField.objects.create(document_type=doc_type, field_template=_feld(facility, "Ausgabe"))

        assert backend.is_available(facility) is False

    def test_ohne_passenden_typ_nicht_verfuegbar(self, backend, facility, doc_type_contact):
        assert backend.is_available(facility) is False

    def test_inaktiver_typ_zaehlt_nicht(self, backend, facility):
        doc_type = _spritzentausch_typ(facility)
        DocumentType.objects.filter(pk=doc_type.pk).update(is_active=False)

        assert backend.is_available(facility) is False

    def test_fremde_einrichtung_macht_nicht_verfuegbar(self, backend, facility, second_facility):
        _spritzentausch_typ(second_facility)

        assert backend.is_available(facility) is False

    def test_report_key_genuegt(self, backend, facility):
        _spritzentausch_typ(facility, report_key="needle_exchange")

        assert backend.is_available(facility) is True

    def test_paket_slug_allein_macht_nicht_verfuegbar(self, backend, facility):
        """Kehrseite von E-E: ohne Schluessel faellt das Backend aus dem Dropdown."""
        _spritzentausch_typ(facility, report_key=None)

        assert backend.is_available(facility) is False
