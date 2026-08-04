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
from django.utils.translation import gettext

from core.reporting.base import DatasetBackend, DatasetResult
from core.reporting.data_source import DATA_SOURCE_NOTES, LIVE, POPULATION_NOTES

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

# Oeffentliche Formatmenge (Refs #1061, M5 P5 Review M1): Single Source of
# Truth fuer Aufrufer, die VOR ``export_dataset()`` selbst pruefen wollen (z. B.
# Views, die ein unbekanntes Format als eigenes HTTP 400 beantworten statt den
# internen ``ValueError`` blind abzufangen — ein zu breiter ``except ValueError``
# um den ganzen Aufruf wuerde sonst auch Renderer-Fehler als 400 maskieren).
SUPPORTED_FORMATS = frozenset(_CONTENT_TYPES)

# Gueltiger XML-Elementname (vereinfachtes NCName-Subset: Python-Identifier-
# artig). Summary-Keys, die nicht darauf passen (z. B. Monatsschluessel wie
# "2026-01"), werden als ``<entry key="…">`` statt als eigenes Element
# gerendert. Geprueft wird immer mit ``fullmatch`` — ``$`` matcht in Python VOR
# einem finalen Zeilenumbruch, ``"total\n"`` passierte einen ``match``-Guard
# also und wuerde zum Elementnamen ``<total\n>`` (den der Parser still als
# ``<total>`` zurueckliest: deklarierter Key != Elementname).
_XML_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# In XML 1.0 unzulaessige Codepoints (alles ausserhalb von #x9 | #xA | #xD |
# [#x20-#xD7FF] | [#xE000-#xFFFD] | [#x10000-#x10FFFF], inkl. einzelner
# Surrogate). Siehe :func:`_xml_safe`.
_XML_ILLEGAL_RE = re.compile(r"[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


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
    # Quellenausweis (Refs #1743): die Pipeline liest immer live. Steht VOR dem
    # k-Anon-Hinweis, damit dieser das Zeilenende bleibt.
    #
    # Refs #1765: bis hierhin stand nur das uebersetzte Label mit hart
    # deutschem Praefix ("Datenquelle: <uebersetzter Wert>") — unter
    # EN-Locale ein DE/EN-Mischtext, und anders als JSON/XML ohne stabilen
    # Code zum Parsen. Jetzt traegt die Zeile BEIDES: den stabilen,
    # sprachunabhaengigen Code (Paritaet zu JSON/XML, ``data_source``-Feld
    # aus core.reporting.data_source) UND das mituebersetzte Label —
    # derselbe ``msgid`` "Datenquelle" wie im Berichts-Panel/PDF
    # ({% trans "Datenquelle" %}), damit die EN-Katalogpflege nicht
    # dupliziert.
    metadata += f" | data_source: {LIVE} | {gettext('Datenquelle')}: {DATA_SOURCE_NOTES[LIVE]}"
    # K3-A3 (Refs #1770): dasselbe Muster fuer die Grundgesamtheit. Panel und
    # Download zeigen denselben Datensatz — benennt nur das Panel seine
    # Basismenge, fehlt die Angabe genau in der Datei, die das Haus verlaesst.
    metadata += (
        f" | population: {backend.population} | {gettext('Grundgesamtheit')}: {POPULATION_NOTES[backend.population]}"
    )
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

    ``name`` und die FieldSpec-Labels werden trotzdem explizit ``str()``-gecastet:
    ``DjangoJSONEncoder`` KANN ``gettext_lazy``-Proxys selbst serialisieren (sein
    ``default()`` behandelt ``Promise`` wie ``Decimal``/``UUID`` per ``str(o)``)
    — der Cast hier ist reine Explizitheit an der Payload-Grenze, keine
    technische Notwendigkeit. Fuer die (seit #1744 ebenfalls lazyen)
    Completeness-Meldungen greift genau dieser Encoder-Pfad.
    """
    payload = {
        "backend": backend.slug,
        "name": str(backend.name),
        "date_from": date_from,
        "date_to": date_to,
        "generated_at": generated_at,
        "mode": result.mode,
        # Quellenausweis (Refs #1743): ``data_source`` ist der stabile,
        # sprachunabhaengige Code fuer Maschinen, ``data_source_note`` die
        # uebersetzte Kurzform fuer Menschen, die den Payload lesen.
        "data_source": LIVE,
        "data_source_note": str(DATA_SOURCE_NOTES[LIVE]),
        # K3-A3 (Refs #1770): Grundgesamtheit im selben Muster — stabiler Code
        # fuer Maschinen, uebersetzte Kurzform fuer Menschen. Sie kommt aus
        # ``DatasetBackend.population``, nicht aus einem Literal hier: ein
        # Backend, das enger zaehlt, muss den Ausweis mitbringen koennen.
        "population": backend.population,
        "population_note": str(POPULATION_NOTES[backend.population]),
        # ``label`` ist seit #1744 ``gettext_lazy``; der Cast ist — wie bei
        # ``name`` — Explizitheit an der Payload-Grenze, nicht Notwendigkeit.
        "field_specs": [{"key": spec.key, "label": str(spec.label)} for spec in backend.get_field_specs()],
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
            # Quellenausweis (Refs #1743) — Code + uebersetzte Kurzform.
            "data_source": LIVE,
            "data_source_note": _xml_safe(str(DATA_SOURCE_NOTES[LIVE])),
            # K3-A3 (Refs #1770) — dasselbe Muster fuer die Grundgesamtheit.
            "population": backend.population,
            "population_note": _xml_safe(str(POPULATION_NOTES[backend.population])),
        },
    )

    completeness_el = ET.SubElement(root, "completeness")
    for issue in result.completeness:
        issue_el = ET.SubElement(
            completeness_el,
            "issue",
            {"code": _xml_safe(issue.code), "severity": _xml_safe(issue.severity)},
        )
        issue_el.text = _xml_safe(issue.message)

    summary_el = ET.SubElement(root, "summary")
    _fill_xml_value(summary_el, result.summary)

    records_el = ET.SubElement(root, "records")
    # Vor die Schleife gehoisted statt pro Record neu berechnet — die
    # FieldSpec-Liste ist fuer den gesamten Export identisch.
    field_specs = backend.get_field_specs()
    for record in result.records:
        record_el = ET.SubElement(records_el, "record")
        for spec in field_specs:
            value = record.get(spec.key)
            text = "" if value is None else _xml_safe(str(value))
            # Gleicher Fallback wie im summary-Renderer (``_fill_xml_value``):
            # ein FieldSpec-Key, der kein gueltiger XML-Elementname ist (z. B.
            # Ziffern-initial), wuerde sonst still unparsbares XML erzeugen.
            if _XML_IDENTIFIER_RE.fullmatch(spec.key):
                field_el = ET.SubElement(record_el, spec.key)
            else:
                field_el = ET.SubElement(record_el, "entry", {"key": _xml_safe(spec.key)})
            field_el.text = text

    return ET.tostring(root, xml_declaration=True, encoding="utf-8")


def _fill_xml_value(element: ET.Element, value: Any) -> None:
    """Fuellt ``element`` rekursiv aus einem JSON-artigen Wert (dict/list/Skalar).

    dict → je Key ein Kindelement (gueltige Elementnamen) bzw. ein
    ``<entry key="…">`` (alles andere, z. B. Monatsschluessel "2026-01").
    list → ``<item>``-Kinder. Alles uebrige wird als Text eingesetzt.
    """
    if isinstance(value, dict):
        for key, val in value.items():
            if _XML_IDENTIFIER_RE.fullmatch(str(key)):
                child = ET.SubElement(element, str(key))
                _fill_xml_value(child, val)
            else:
                entry = ET.SubElement(element, "entry", {"key": _xml_safe(str(key))})
                _fill_xml_value(entry, val)
    elif isinstance(value, list):
        for item in value:
            child = ET.SubElement(element, "item")
            _fill_xml_value(child, item)
    else:
        element.text = "" if value is None else _xml_safe(str(value))


def _xml_safe(text: Any) -> str:
    """Entfernt in XML 1.0 unzulaessige Codepoints aus ``text``.

    Nimmt bewusst ``Any`` und castet selbst: Backend-Strings sind seit
    [#1744] ``gettext_lazy``-Proxys, und ``re.sub`` weist die mit
    ``TypeError: expected string or bytes-like object`` ab. Der Fehler traefe
    ausgerechnet **nur** den XML-Pfad — CSV (``_sanitize_csv_cell`` castet)
    und JSON (``DjangoJSONEncoder`` kann ``Promise``) blieben unauffaellig,
    derselbe Bericht waere in einem Format ein 500er.

    ``ElementTree`` escaped beim Serialisieren nur ``&``/``<``/``>`` (bzw.
    zusaetzlich ``"``/``\\t``/``\\n``/``\\r`` in Attributwerten) und schreibt
    Steuerzeichen wie ``\\x0b`` roh heraus — die XML-1.0-Grammatik verbietet sie
    aber. Ein einziges Steuerzeichen in einem Freitext (z. B. einem
    ``DocumentType.name``, den ein Backend in seine Records projiziert) machte
    damit das GESAMTE Dokument fuer jeden konformen Parser unlesbar, waehrend
    CSV/JSON desselben Berichts unauffaellig blieben.

    Entfernt statt ersetzt: Steuerzeichen tragen in einem Bericht keine
    Bedeutung, ein Platzhalter waere reines Rauschen. Analog zu
    ``_sanitize_csv_cell`` im CSV-Pfad greift der Schutz zentral im Renderer,
    nicht in den einzelnen Backends.
    """
    return _XML_ILLEGAL_RE.sub("", str(text))
