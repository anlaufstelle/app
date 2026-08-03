"""Tests fuer ``core.reporting.export`` — formatunabhaengige Export-Pipeline (M5 P4, Refs #1061).

Deckt den Format-Dispatch (``export_dataset``) sowie die CSV-/JSON-/XML-
Renderer ab. Ausschliesslich selbstgebaute Dummy-Backends — die Registry
und echte Backends (z. B. JugendamtBackend aus P2) sind nicht Teil dieses
Moduls und werden hier bewusst nicht beruehrt.
"""

import csv
import json
import xml.etree.ElementTree as ET
from datetime import date

import pytest
from django.http import HttpResponse, StreamingHttpResponse
from django.utils.translation import gettext_lazy as _

from core.reporting.base import CompletenessIssue, DatasetBackend, DatasetResult, FieldSpec
from core.reporting.export import export_dataset


class _Facility:
    """Minimales Facility-Double mit ``__str__`` — kein Django-Model noetig."""

    def __init__(self, name: str = "Beispiel-Einrichtung") -> None:
        self.name = name

    def __str__(self) -> str:
        return self.name


class _RowsBackend(DatasetBackend):
    """Dummy-Backend im Modus ``rows`` — Records inkl. CSV-Injection-Payload.

    ``name`` ist bewusst ``gettext_lazy`` (wie echte Backends es taeten), um
    den ``str()``-Cast im JSON-Renderer tatsaechlich zu pruefen.
    """

    slug = "dummy_rows"
    name = _("Dummy Zeilenbericht")

    def get_field_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec(key="datum", label="Datum"),
            FieldSpec(key="formel", label="Formel-Feld"),
        ]

    def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
        return DatasetResult(
            mode="rows",
            records=[
                # Dict-Reihenfolge bewusst umgekehrt zu get_field_specs() —
                # der Renderer muss trotzdem in FieldSpec-Reihenfolge ausgeben.
                {"formel": "=SUM(A1:A9)", "datum": "2026-01-05"},
                {"formel": "normal", "datum": "2026-01-06"},
            ],
            summary={"total": 2},
            completeness=[CompletenessIssue(code="unmapped", message="Nicht zugeordnet.", severity="info")],
        )


class _UnicodeSlugBackend(_RowsBackend):
    """Slug bewusst mit Unicode — erzwingt den RFC-5987-Zweig in
    ``safe_download_response`` (die Encoding-Logik selbst ist dort bereits
    getestet; hier pruefen wir nur, dass der Header ueberhaupt gesetzt wird).
    """

    slug = "café"


class _AggregateBackend(DatasetBackend):
    """Dummy-Backend im Modus ``aggregate`` — verschachteltes summary (dict + Liste)."""

    slug = "dummy_aggregate"
    name = "Dummy Aggregatbericht"

    def get_field_specs(self) -> list[FieldSpec]:
        return [FieldSpec(key="monat", label="Monat"), FieldSpec(key="anzahl", label="Anzahl")]

    def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
        return DatasetResult(
            mode="aggregate",
            records=[{"monat": "2026-01", "anzahl": 3}],
            summary={
                "total": 3,
                # "2026-01" ist kein gueltiger XML-Elementname → muss als
                # <entry key="2026-01"> gerendert werden.
                "by_month": {"2026-01": 3, "2026-02": 5},
                "top_types": ["kontakt", "beratung"],
            },
            completeness=[],
        )


@pytest.fixture
def facility():
    return _Facility()


@pytest.fixture
def date_range():
    return date(2026, 1, 1), date(2026, 1, 31)


class TestFormatDispatch:
    def test_unbekanntes_format_wirft_value_error(self, facility, date_range):
        date_from, date_to = date_range
        with pytest.raises(ValueError):
            export_dataset(_RowsBackend(), facility, date_from, date_to, format="pdf")


class TestCsvExport:
    def test_ist_streaming_response(self, facility, date_range):
        date_from, date_to = date_range
        response = export_dataset(_RowsBackend(), facility, date_from, date_to, format="csv")
        assert isinstance(response, StreamingHttpResponse)

    def test_struktur_reihenfolge_und_injection_schutz(self, facility, date_range):
        date_from, date_to = date_range
        response = export_dataset(_RowsBackend(), facility, date_from, date_to, format="csv")
        content = b"".join(response.streaming_content).decode("utf-8")
        lines = content.splitlines()

        assert lines[0].startswith("# Bericht: dummy_rows | Beispiel-Einrichtung | 2026-01-01–2026-01-31 | ")

        rows = list(csv.reader(lines[1:]))
        # Header aus FieldSpec-Labels, in FieldSpec-Reihenfolge (datum, formel).
        assert rows[0] == ["Datum", "Formel-Feld"]
        # Zeilen ebenfalls in FieldSpec-Reihenfolge trotz umgekehrter Dict-Keys;
        # die Formel-Zelle traegt den OWASP-Praefix.
        assert rows[1] == ["2026-01-05", "'=SUM(A1:A9)"]
        assert rows[2] == ["2026-01-06", "normal"]

    def test_completeness_erscheint_nicht_im_csv(self, facility, date_range):
        date_from, date_to = date_range
        response = export_dataset(_RowsBackend(), facility, date_from, date_to, format="csv")
        content = b"".join(response.streaming_content).decode("utf-8")
        assert "unmapped" not in content
        assert "Nicht zugeordnet" not in content

    def test_content_type(self, facility, date_range):
        date_from, date_to = date_range
        response = export_dataset(_RowsBackend(), facility, date_from, date_to, format="csv")
        assert response["Content-Type"] == "text/csv; charset=utf-8"


class TestJsonExport:
    def test_roundtrip_pflichtfelder(self, facility, date_range):
        date_from, date_to = date_range
        backend = _RowsBackend()
        response = export_dataset(backend, facility, date_from, date_to, format="json")
        assert isinstance(response, HttpResponse)
        assert not isinstance(response, StreamingHttpResponse)

        payload = json.loads(response.content)
        assert payload["backend"] == "dummy_rows"
        assert payload["name"] == "Dummy Zeilenbericht"
        assert isinstance(payload["name"], str)
        assert payload["date_from"] == "2026-01-01"
        assert payload["date_to"] == "2026-01-31"
        assert "generated_at" in payload
        assert payload["mode"] == "rows"
        assert payload["field_specs"] == [
            {"key": "datum", "label": "Datum"},
            {"key": "formel", "label": "Formel-Feld"},
        ]
        assert payload["summary"] == {"total": 2}
        # Kein CSV-Sanitizing im JSON — Rohwert bleibt erhalten.
        assert payload["records"] == [
            {"formel": "=SUM(A1:A9)", "datum": "2026-01-05"},
            {"formel": "normal", "datum": "2026-01-06"},
        ]
        assert payload["completeness"] == [{"code": "unmapped", "message": "Nicht zugeordnet.", "severity": "info"}]

    def test_content_type(self, facility, date_range):
        date_from, date_to = date_range
        response = export_dataset(_RowsBackend(), facility, date_from, date_to, format="json")
        assert response["Content-Type"] == "application/json"


class TestXmlExport:
    def test_wohlgeformt_und_wurzel_attribute(self, facility, date_range):
        date_from, date_to = date_range
        response = export_dataset(_AggregateBackend(), facility, date_from, date_to, format="xml")
        # Begruendung fuers folgende noqa: eigener, gerade erst generierter
        # Output (kein Fremddaten-Parsing) — wirft ParseError bei Missbildung.
        root = ET.fromstring(response.content)  # noqa: S314
        assert root.tag == "dataset"
        assert root.attrib["backend"] == "dummy_aggregate"
        assert root.attrib["date_from"] == "2026-01-01"
        assert root.attrib["date_to"] == "2026-01-31"
        assert root.attrib["mode"] == "aggregate"
        assert "generated_at" in root.attrib

    def test_summary_verschachteltes_dict_und_liste(self, facility, date_range):
        date_from, date_to = date_range
        response = export_dataset(_AggregateBackend(), facility, date_from, date_to, format="xml")
        # Begruendung fuers folgende noqa: eigener, gerade erst generierter
        # Output (kein Fremddaten-Parsing).
        root = ET.fromstring(response.content)  # noqa: S314
        summary = root.find("summary")
        assert summary.find("total").text == "3"

        by_month = summary.find("by_month")
        entries = {entry.get("key"): entry.text for entry in by_month.findall("entry")}
        assert entries == {"2026-01": "3", "2026-02": "5"}

        top_types = summary.find("top_types")
        items = [item.text for item in top_types.findall("item")]
        assert items == ["kontakt", "beratung"]

    def test_records_und_completeness(self, facility, date_range):
        date_from, date_to = date_range
        response = export_dataset(_RowsBackend(), facility, date_from, date_to, format="xml")
        # Begruendung fuers folgende noqa: eigener, gerade erst generierter
        # Output (kein Fremddaten-Parsing).
        root = ET.fromstring(response.content)  # noqa: S314

        records = root.find("records").findall("record")
        assert len(records) == 2
        assert records[0].find("datum").text == "2026-01-05"
        assert records[0].find("formel").text == "=SUM(A1:A9)"

        issues = root.find("completeness").findall("issue")
        assert len(issues) == 1
        assert issues[0].get("code") == "unmapped"
        assert issues[0].get("severity") == "info"
        assert issues[0].text == "Nicht zugeordnet."

    def test_content_type(self, facility, date_range):
        date_from, date_to = date_range
        response = export_dataset(_AggregateBackend(), facility, date_from, date_to, format="xml")
        assert response["Content-Type"] == "application/xml; charset=utf-8"


class TestAlleBackendsAlleFormate:
    """Pflichtfall 6: beide Dummy-Backends × alle drei Formate → 200-fähige Response."""

    @pytest.mark.parametrize("backend_cls", [_RowsBackend, _AggregateBackend])
    @pytest.mark.parametrize(
        ("format_", "content_type", "ext"),
        [
            ("csv", "text/csv; charset=utf-8", "csv"),
            ("json", "application/json", "json"),
            ("xml", "application/xml; charset=utf-8", "xml"),
        ],
    )
    def test_response_ist_downloadfaehig(self, facility, date_range, backend_cls, format_, content_type, ext):
        date_from, date_to = date_range
        backend = backend_cls()
        response = export_dataset(backend, facility, date_from, date_to, format=format_)

        assert response.status_code == 200
        assert response["Content-Type"] == content_type
        expected_filename = f"{backend.slug}_{date_from}_{date_to}.{ext}"
        assert expected_filename in response["Content-Disposition"]


class TestRfc5987Dateiname:
    def test_unicode_dateiname_setzt_rfc5987_header(self, date_range):
        date_from, date_to = date_range
        facility = _Facility(name="Fördeinrichtung Süd")
        response = export_dataset(_UnicodeSlugBackend(), facility, date_from, date_to, format="csv")
        assert "filename*=" in response["Content-Disposition"]
