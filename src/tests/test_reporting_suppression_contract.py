"""Fail-Closed-Vertrag der k-Anon-Suppression (Refs https://github.com/anlaufstelle/app/issues/2069).

Bis #2069 war die
Suppressionspflicht jedes Backends eine **Konvention mit statischer
Absicherung**: ``DatasetBackend.prepare_export`` ist ein Identity-Passthrough,
und der AST-Guard in ``test_architecture_guards_services.py`` kann ein Backend
**ohne** Override prinzipiell nicht sehen — es erzeugt keinen
``FunctionDef``-Knoten. Ein neues Backend, dessen Autor den Override vergisst,
exportierte damit Kleinstfallzahlen, ohne dass ein Test rot wird.

``core.reporting.export.enforce_suppression_contract`` erzwingt die Invariante
jetzt zur Laufzeit — an **beiden** Grenzen, die
[ADR-038 § 5](../../docs/adr/038-reporting-plugin-backends.md) ausdruecklich
gleichstellt: dem Download (``export_dataset``) **und** der Panel-Vorschau
(``build_report_panel_context``).

Der Modulkopf traegt bewusst **keinen** ``django_db``-Marker: der Laufzeit-Check
darf keine Datenbank anfassen (sonst waere jedes Plain-Python-Facility-Double in
``test_reporting_export.py`` DB-pflichtig). Nur die beiden Vertragstests ueber
die **registrierten** Backends am Dateiende brauchen die DB.
"""

import dataclasses
from datetime import date

import pytest

from core.reporting import registry
from core.reporting.base import DatasetBackend, DatasetResult, FieldSpec, stamp_threshold
from core.reporting.export import SuppressionContractError, export_dataset
from core.services.dashboard.external_report import _get_threshold
from tests.conftest import isolate_reporting_registry


class _Facility:
    """Minimales Facility-Double mit ``__str__`` — kein Django-Model noetig.

    Muster aus ``test_reporting_export.py``: der Check laeuft DB-frei, deshalb
    reicht hier ein Plain-Python-Objekt.
    """

    def __init__(self, name: str = "Beispiel-Einrichtung") -> None:
        self.name = name

    def __str__(self) -> str:
        return self.name


class _OhneSuppression(DatasetBackend):
    """Das Fehlerbild aus #2069:
    ein Backend, dessen Autor ``prepare_export`` schlicht vergessen hat.
    """

    slug = "ohne_suppression"
    name = "Ohne Suppression"

    def get_field_specs(self) -> list[FieldSpec]:
        return [FieldSpec(key="anzahl", label="Anzahl")]

    def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
        return DatasetResult(mode="rows", records=[{"anzahl": 7}], summary={"total": 7}, completeness=[])


class _MitSuppression(_OhneSuppression):
    """Gegenprobe: derselbe Datensatz, aber mit ausgewiesener Schwelle.

    Benutzt bewusst den echten Produktionshelfer ``stamp_threshold`` aus
    ``core.reporting.base`` — die Positiv-Kontrolle soll denselben Vertragspfad
    ueben wie die neun ausgelieferten Backends.
    """

    slug = "mit_suppression"
    name = "Mit Suppression"

    def prepare_export(self, facility, result: DatasetResult) -> DatasetResult:
        return dataclasses.replace(result, summary=stamp_threshold(result.summary, 5))


class _SchwellenBackend(_OhneSuppression):
    """Stempelt einen **frei waehlbaren** Wert — fuer die Typ-/Wertebereichsfaelle."""

    slug = "schwellen_backend"
    name = "Schwellen-Backend"
    schwelle: object = 5

    def prepare_export(self, facility, result: DatasetResult) -> DatasetResult:
        return dataclasses.replace(result, summary={**result.summary, "k_anonymity_threshold": self.schwelle})


@pytest.fixture
def facility_double():
    return _Facility()


@pytest.fixture
def date_range():
    return date(2026, 1, 1), date(2026, 1, 31)


def _mit_schwelle(wert) -> _SchwellenBackend:
    backend = _SchwellenBackend()
    backend.schwelle = wert
    return backend


class TestExportPfad:
    """Zusage Z1-Z3: der Download weist einen unsuppressierten Datensatz ab."""

    @pytest.mark.parametrize("format", ["csv", "json", "xml"])
    def test_export_ohne_schwelle_scheitert_in_allen_formaten(self, facility_double, date_range, format):
        """Der Check sitzt VOR der Renderer-Verzweigung — deshalb alle drei.

        Ein Check je Renderer waere die naechste Drift-Quelle: ein viertes
        Format erbte ihn nicht.
        """
        date_from, date_to = date_range
        with pytest.raises(SuppressionContractError):
            export_dataset(_OhneSuppression(), facility_double, date_from, date_to, format=format)

    def test_fehlermeldung_nennt_slug_und_klasse(self, facility_double, date_range):
        """Die Meldung muss dem Entwickler den Weg zeigen, nicht nur „nein" sagen."""
        date_from, date_to = date_range
        with pytest.raises(SuppressionContractError) as excinfo:
            export_dataset(_OhneSuppression(), facility_double, date_from, date_to, format="json")

        text = str(excinfo.value)
        assert "ohne_suppression" in text
        assert "_OhneSuppression" in text
        assert "prepare_export" in text

    @pytest.mark.parametrize("format", ["csv", "json", "xml"])
    def test_gueltige_schwelle_passiert(self, facility_double, date_range, format):
        """Ohne diese Kontrolle waere ein Check, der IMMER wirft, gruen."""
        date_from, date_to = date_range
        response = export_dataset(_MitSuppression(), facility_double, date_from, date_to, format=format)

        assert response.status_code == 200

    def test_bool_gilt_nicht_als_schwelle(self, facility_double, date_range):
        """``bool`` ist Subtyp von ``int``: ``True`` ginge sonst als Schwelle 1
        durch — und Schwelle 1 unterdrueckt nichts."""
        date_from, date_to = date_range
        with pytest.raises(SuppressionContractError):
            export_dataset(_mit_schwelle(True), facility_double, date_from, date_to, format="json")

    def test_null_schwelle_scheitert(self, facility_double, date_range):
        """``0`` hiesse „keine Suppression" — ein Fail-Open mit gesetztem Schluessel."""
        date_from, date_to = date_range
        with pytest.raises(SuppressionContractError):
            export_dataset(_mit_schwelle(0), facility_double, date_from, date_to, format="json")

    def test_string_schwelle_scheitert(self, facility_double, date_range):
        """Ein String ``"5"`` ist keine Schwelle — er landete ununterscheidbar im Payload."""
        date_from, date_to = date_range
        with pytest.raises(SuppressionContractError):
            export_dataset(_mit_schwelle("5"), facility_double, date_from, date_to, format="json")

    def test_none_schwelle_scheitert(self, facility_double, date_range):
        """Ein explizit auf ``None`` gesetzter Schluessel ist keine Schwelle."""
        date_from, date_to = date_range
        with pytest.raises(SuppressionContractError):
            export_dataset(_mit_schwelle(None), facility_double, date_from, date_to, format="json")

    def test_streaming_bricht_nicht_erst_beim_konsumieren(self, facility_double, date_range):
        """Der CSV-Renderer ist ein Generator.

        Faellt der Fehler erst beim Auslesen von ``streaming_content``, hat die
        View bereits eine 200-Response mit Download-Headern gebaut — der
        Empfaenger bekaeme eine abgebrochene Datei statt eines klaren Fehlers.
        """
        date_from, date_to = date_range
        with pytest.raises(SuppressionContractError):
            export_dataset(_OhneSuppression(), facility_double, date_from, date_to, format="csv")

    def test_format_gate_bleibt_vor_dem_check(self, facility_double, date_range):
        """Ein unbekanntes Format bleibt ``ValueError`` (HTTP 400 in der View) —
        der Suppressions-Check darf es nicht zu einem 500 hochstufen."""
        date_from, date_to = date_range
        with pytest.raises(ValueError) as excinfo:
            export_dataset(_OhneSuppression(), facility_double, date_from, date_to, format="pdf")

        assert not isinstance(excinfo.value, SuppressionContractError)


class TestPanelPfad:
    """Zusage Z4 (ADR-038 § 5): Panel und Download sind gleichgestellt.

    Ein Check nur im Download haette zur Folge: ein neues Backend ohne Override
    scheitert beim Herunterladen hart — und zeigt in der HTMX-Vorschau
    Rohzahlen. Genau die halbe Haertung, gegen die
    #1278 angetreten ist.
    """

    def _period_state(self, date_range):
        from core.services.dashboard import PeriodState

        date_from, date_to = date_range
        return PeriodState(period="frei", date_from=date_from, date_to=date_to)

    def test_panel_ohne_schwelle_scheitert(self, facility_double, date_range):
        from core.views.statistics import build_report_panel_context

        with pytest.raises(SuppressionContractError):
            build_report_panel_context(facility_double, _OhneSuppression(), self._period_state(date_range))

    def test_panel_mit_schwelle_liefert_kontext(self, facility_double, date_range):
        from core.views.statistics import build_report_panel_context

        context = build_report_panel_context(facility_double, _MitSuppression(), self._period_state(date_range))

        assert context["k_anonymity_threshold"] == 5
        assert context["preview_total"] == 1


@pytest.mark.django_db
class TestRegistrierteBackendsStempelnDenKonfiguriertenWert:
    """Zusage Z7: der Laufzeit-Check prueft Praesenz und Typ — dass der Stempel
    auch **den konfigurierten** Wert traegt, gehoert bewusst hierher.

    Begruendung: ein Vergleich gegen ``_get_threshold(facility)`` im
    Laufzeitpfad braeuchte eine DB-Abfrage in ``export_dataset`` und machte die
    gesamte DB-freie Renderer-Suite in ``test_reporting_export.py``
    DB-pflichtig.
    """

    @pytest.fixture(autouse=True)
    def _isolated_registry(self, monkeypatch):
        isolate_reporting_registry(monkeypatch)

    def test_jedes_registrierte_backend_stempelt_den_konfigurierten_wert(self, facility):
        erwartet = _get_threshold(facility)
        date_from, date_to = date(2026, 1, 1), date(2026, 1, 31)

        for backend in registry.get_backends():
            result = backend.prepare_export(facility, backend.build_dataset(facility, date_from, date_to))
            assert result.summary.get("k_anonymity_threshold") == erwartet, backend.slug

    def test_abweichend_konfigurierte_schwelle_wird_uebernommen(self, facility):
        """Nicht bloss „irgendein int": ein hart kodiertes ``5`` faellt hier auf."""
        from core.models import Settings

        Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 8})
        date_from, date_to = date(2026, 1, 1), date(2026, 1, 31)

        for backend in registry.get_backends():
            result = backend.prepare_export(facility, backend.build_dataset(facility, date_from, date_to))
            assert result.summary.get("k_anonymity_threshold") == 8, backend.slug

    def test_anti_null_anker(self):
        """Zweiter Anker: die Schleifen oben duerfen nicht still leer laufen."""
        assert len(registry.get_backends()) >= 9
