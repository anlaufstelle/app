"""Formatunabhaengige Export-Pipeline fuer Reporting-Backends (M5 P4, Refs #1061).

:func:`export_dataset` ist der einzige oeffentliche Einstiegspunkt: sie ruft
``backend.build_dataset(facility, date_from, date_to)`` auf und reicht das
Ergebnis an genau einen der drei Format-Renderer (CSV/JSON/XML) weiter. Alle
drei liefern ueber :func:`core.utils.downloads.safe_download_response` eine
fertige Download-Response mit RFC-5987-Dateiname, ``nosniff`` und
``Cache-Control: no-store``.

CSV wird gestreamt (Muster ``export_events_csv`` in
``core.services.system.export``:200-231) — Aggregat-Berichte koennen viele
Records enthalten. JSON und XML sind dagegen kleine, geschlossene
Aggregat-Payloads und werden als einzelne Bytes-Antwort gebaut.
"""

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from typing import Any

from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone

from core.reporting.base import DatasetBackend, DatasetResult

# Bewusste Wiederverwendung des Formula-Injection-Schutzes (OWASP CSV-
# Injection, Refs #719) statt einer Neuimplementierung — dieselbe Zelle
# (``=SUM(...)`` etc.) ist in jedem CSV-Export gleich gefaehrlich.
from core.services.system.export import _sanitize_csv_cell
from core.utils.downloads import safe_download_response

_CONTENT_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "json": "application/json",
    "xml": "application/xml; charset=utf-8",
}

# Gueltiger XML-Elementname (vereinfachtes NCName-Subset: Python-Identifier-
# artig). Summary-Keys, die nicht darauf passen (z. B. Monatsschluessel wie
# "2026-01"), werden als ``<entry key="…">`` statt als eigenes Element
# gerendert.
_XML_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def export_dataset(
    backend: DatasetBackend,
    facility,
    date_from,
    date_to,
    format: str = "csv",
) -> HttpResponse | StreamingHttpResponse:
    """Baue+rendere den Datensatz von ``backend`` als Download-Response.

    ``format`` bestimmt den Renderer (``"csv"``, ``"json"`` oder ``"xml"``);
    ein unbekannter Wert wirft ``ValueError`` (die View in P5 antwortet
    darauf mit HTTP 400). Der Dateiname folgt immer dem Muster
    ``f"{backend.slug}_{date_from}_{date_to}.{format}"``.
    """
    if format not in _CONTENT_TYPES:
        raise ValueError(f"Unbekanntes Export-Format {format!r} — erwartet 'csv', 'json' oder 'xml'.")

    result = backend.build_dataset(facility, date_from, date_to)
    # Repo-Invariante (Refs #1278/T1, Security R4+R14): jedes extern
    # zirkulierende Berichts-Artefakt unterdrueckt Kleinstfallzahlen. Ein
    # Formatwechsel (CSV/JSON/XML statt PDF) darf diese Haertung nie
    # aushebeln — der Hook laeuft deshalb vor *jedem* Renderer. Snapshots
    # umgehen ihn bewusst: sie rufen ``build_dataset`` direkt (Roh-Payload).
    result = backend.prepare_export(facility, result)
    generated_at = timezone.now()
    filename = f"{backend.slug}_{date_from}_{date_to}.{format}"

    content: Iterator[str] | bytes
    if format == "csv":
        content = _render_csv(backend, facility, date_from, date_to, result, generated_at)
    elif format == "json":
        content = _render_json(backend, date_from, date_to, result, generated_at)
    else:
        content = _render_xml(backend, date_from, date_to, result, generated_at)

    return safe_download_response(filename, _CONTENT_TYPES[format], content)


def _render_csv(
    backend: DatasetBackend,
    facility,
    date_from,
    date_to,
    result: DatasetResult,
    generated_at,
) -> Iterator[str]:
    """CSV-Generator: Metadaten-Kommentar, Header aus FieldSpec-Labels, dann Records.

    Jede Zelle (auch Metadaten und Header) laeuft durch ``_sanitize_csv_cell``.
    Completeness-Issues erscheinen bewusst nicht im CSV — es bleibt
    maschinenlesbar; sie stehen in JSON/XML/Panel.
    """
    field_specs = backend.get_field_specs()
    output = io.StringIO()
    writer = csv.writer(output, dialect="excel")

    def _flush(row: list[str]) -> str:
        writer.writerow(row)
        chunk = output.getvalue()
        output.seek(0)
        output.truncate(0)
        return chunk

    metadata = f"# Bericht: {backend.slug} | {facility} | {date_from}–{date_to} | {generated_at.isoformat()}"
    # Ohne diesen Zusatz saehe der Empfaenger nur leere Zellen ohne Erklaerung —
    # das CSV traegt (anders als JSON/XML/Panel) keine Completeness-Hinweise.
    # Nur wenn das Backend ueberhaupt suppressiert, also eine Schwelle ausweist.
    threshold = result.summary.get("k_anonymity_threshold")
    if threshold is not None:
        metadata += f" | k-Anonymität: Zellen < {threshold} unterdrückt"
    yield _flush([_sanitize_csv_cell(metadata)])
    yield _flush([_sanitize_csv_cell(spec.label) for spec in field_specs])
    for record in result.records:
        yield _flush([_sanitize_csv_cell(record.get(spec.key)) for spec in field_specs])


def _render_json(
    backend: DatasetBackend,
    date_from,
    date_to,
    result: DatasetResult,
    generated_at,
) -> bytes:
    """JSON-Aggregat-Payload — kein Streaming, kein CSV-Sanitizing (kein Excel-Kontext).

    ``name`` wird explizit ``str()``-gecastet: Backends tragen typischerweise
    ``gettext_lazy``-Proxys, die ``json.dumps`` (auch mit ``DjangoJSONEncoder``)
    nicht direkt serialisieren kann.
    """
    payload = {
        "backend": backend.slug,
        "name": str(backend.name),
        "date_from": date_from,
        "date_to": date_to,
        "generated_at": generated_at,
        "mode": result.mode,
        "field_specs": [{"key": spec.key, "label": spec.label} for spec in backend.get_field_specs()],
        "summary": result.summary,
        "records": result.records,
        "completeness": [
            {"code": issue.code, "message": issue.message, "severity": issue.severity} for issue in result.completeness
        ],
    }
    return json.dumps(payload, cls=DjangoJSONEncoder, ensure_ascii=False, indent=2).encode("utf-8")


def _render_xml(
    backend: DatasetBackend,
    date_from,
    date_to,
    result: DatasetResult,
    generated_at,
) -> bytes:
    """XML-Aggregat-Payload via stdlib ``ElementTree``.

    Nur generierend, kein Parsen von Fremddaten — die XXE-/Billion-Laughs-
    Angriffsflaeche von ``xml.etree.ElementTree`` betrifft ausschliesslich
    ``.parse()``/``.fromstring()`` auf nicht vertrauenswuerdigem Input; hier
    wird ausschliesslich serialisiert. ``defusedxml`` (das nur Parsing
    haertet) ist daher bewusst keine zusaetzliche Abhaengigkeit.
    """
    root = ET.Element(
        "dataset",
        {
            "backend": backend.slug,
            "date_from": str(date_from),
            "date_to": str(date_to),
            "generated_at": generated_at.isoformat(),
            "mode": result.mode,
        },
    )

    completeness_el = ET.SubElement(root, "completeness")
    for issue in result.completeness:
        issue_el = ET.SubElement(completeness_el, "issue", {"code": issue.code, "severity": issue.severity})
        issue_el.text = issue.message

    summary_el = ET.SubElement(root, "summary")
    _fill_xml_value(summary_el, result.summary)

    records_el = ET.SubElement(root, "records")
    for record in result.records:
        record_el = ET.SubElement(records_el, "record")
        for spec in backend.get_field_specs():
            field_el = ET.SubElement(record_el, spec.key)
            value = record.get(spec.key)
            field_el.text = "" if value is None else str(value)

    return ET.tostring(root, xml_declaration=True, encoding="utf-8")


def _fill_xml_value(element: ET.Element, value: Any) -> None:
    """Fuellt ``element`` rekursiv aus einem JSON-artigen Wert (dict/list/Skalar).

    dict → je Key ein Kindelement (gueltige Elementnamen) bzw. ein
    ``<entry key="…">`` (alles andere, z. B. Monatsschluessel "2026-01").
    list → ``<item>``-Kinder. Alles uebrige wird als Text eingesetzt.
    """
    if isinstance(value, dict):
        for key, val in value.items():
            if _XML_IDENTIFIER_RE.match(str(key)):
                child = ET.SubElement(element, str(key))
                _fill_xml_value(child, val)
            else:
                entry = ET.SubElement(element, "entry", {"key": str(key)})
                _fill_xml_value(entry, val)
    elif isinstance(value, list):
        for item in value:
            child = ET.SubElement(element, "item")
            _fill_xml_value(child, item)
    else:
        element.text = "" if value is None else str(value)
