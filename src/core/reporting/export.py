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

from django.core.exceptions import ImproperlyConfigured
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone
from django.utils.translation import gettext

from core.reporting.base import DatasetBackend, DatasetResult, FieldSpec
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


class SuppressionContractError(ImproperlyConfigured):
    """Ein Backend liefert einen Datensatz ohne ausgewiesene k-Anon-Schwelle.

    Erbt von ``ImproperlyConfigured``, weil genau das vorliegt: eine
    Fehlkonfiguration des Systems (ein Backend, das seinen Vertrag nicht
    erfuellt), kein Benutzerfehler. Der eigene Name macht den Fall in
    Log/Fehler-Tracking unterscheidbar und spaeter gezielt fangbar, ohne das
    bestehende Verhalten gegenueber ``ImproperlyConfigured`` zu aendern.
    """


def enforce_suppression_contract(backend: DatasetBackend, result: DatasetResult) -> DatasetResult:
    """Fail-Closed-Gate fuer die ausgewiesene k-Anon-Schwelle (Refs #2069).

    Bis #2069 war die
    Suppressionspflicht eine Konvention: ``DatasetBackend.prepare_export`` ist
    ein Identity-Passthrough, und der AST-Guard kann ein Backend **ohne**
    Override prinzipiell nicht sehen — es erzeugt keinen ``FunctionDef``-Knoten
    und rutscht lautlos durch. Ein vergessener Override exportierte damit
    Kleinstfallzahlen, ohne dass ein Test rot wird.

    **Was dieser Check leistet — und was nicht.** Er erzwingt die *Erklaerung*
    des Backends, es habe suppressiert: dass ``summary["k_anonymity_threshold"]``
    vorhanden, eine echte ganze Zahl und ``>= 1`` ist. Er erzwingt **nicht** die
    fachliche Suppressionswirkung — ein Backend, das die Schwelle stempelt und
    trotzdem eine Zelle unterhalb davon ausliefert, passiert dieses Gate. Das
    ist keine Nachlaessigkeit, sondern eine Grenze der Bauform: ``records`` und
    ``summary`` sind je Backend anders geschnitten, ein generischer Kleinzahl-
    Scan haette hier keinen belastbaren Ansatzpunkt und wuerde je nach Schema
    falsch-positiv oder falsch-negativ. **Die Wirkung selbst bleibt Sache der
    backend-spezifischen Tests** (``tests/test_reporting_<backend>.py``,
    ``tests/test_reporting_disclosure_regression.py``) und der zentralen
    Suppressionsmaschinerie in
    :mod:`core.services.dashboard.external_report`, die die
    Architektur-Guards erzwingen. Dieses Gate schliesst genau eine Luecke: das
    **stille Fehlen** jeder Suppression.

    Auch der **Wert** wird hier nicht geprueft: ein Vergleich gegen
    ``_get_threshold(facility)`` braeuchte eine Datenbankabfrage und machte die
    gesamte DB-freie Renderer-Pipeline DB-pflichtig. Der Wertvergleich laeuft
    deshalb im DB-gestuetzten Vertragstest
    ``tests/test_reporting_suppression_contract.py``.

    ``bool`` wird ausdruecklich ausgeschlossen: es ist ein Subtyp von ``int``,
    ein versehentlich gestempeltes ``True`` ginge sonst als Schwelle 1 durch —
    und Schwelle 1 unterdrueckt nichts. ``0`` ebenso: das waere ein Fail-Open
    mit gesetztem Schluessel.

    ``raise``, nie ``assert``: eine Sicherheitsinvariante, die sich mit
    ``python -O`` abschalten laesst, ist keine.
    """
    threshold = result.summary.get("k_anonymity_threshold")
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 1:
        raise SuppressionContractError(
            f"Reporting-Backend {backend.slug!r} ({type(backend).__name__}) liefert einen Datensatz ohne "
            f"ausgewiesene k-Anonymitäts-Schwelle (summary['k_anonymity_threshold'] = {threshold!r}). "
            "Jedes extern zirkulierende Berichts-Artefakt unterdrückt Kleinstfallzahlen (ADR-038 § 5, "
            "docs/reporting-plugins.md § 6, Refs #1278/T1, Security R4+R14) — Download und Panel weisen "
            "einen Datensatz ohne diese Angabe deshalb fail-closed ab. Überschreibe `prepare_export`, "
            "suppressiere über `suppress_single_margin(...)` bzw. die zentrale Maschinerie in "
            "core.services.dashboard.external_report und weise die Schwelle mit `stamp_threshold(...)` "
            "aus. Der Stempel allein genügt nicht: er ist die Erklärung, nicht die Wirkung — die "
            "prüfen die backend-eigenen Tests."
        )
    return result


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
    #
    # Refs #2069: die Invariante ist seitdem **erzwungen**, nicht mehr nur
    # beschrieben — ein Backend ohne ausgewiesene Schwelle scheitert hier
    # fail-closed, statt Rohzahlen an einen Renderer weiterzureichen.
    result = backend.prepare_export(facility, result)
    result = enforce_suppression_contract(backend, result)
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
    """CSV-Renderer: Metadaten-Kommentar, Header aus FieldSpec-Labels, dann Records.

    Jede Zelle (auch Metadaten und Header) laeuft durch ``_sanitize_csv_cell``.
    Completeness-Issues erscheinen bewusst nicht im CSV — es bleibt
    maschinenlesbar; sie stehen in JSON/XML/Panel.

    **Der letzte backend-eigene Aufruf laeuft eager, nicht im Generator**
    (Refs #2070, gleiche Begruendung wie
    ``test_reporting_suppression_contract.py::test_streaming_bricht_nicht_erst_beim_konsumieren``):
    ``get_field_specs()`` ist Backend-Code und kann werfen. Im Generator faellt
    der Fehler erst beim Auslesen von ``streaming_content`` — die View hat dann
    bereits eine 200-Response mit Download-Headern gebaut und der Empfaenger
    bekaeme eine abgebrochene Datei statt eines klaren Fehlers. Eager gerufen
    landet er im Fehlerfang der Export-View (HTTP 502). Ab hier ist nichts mehr
    backend-eigen: die Zeilenschleife arbeitet nur noch auf dem bereits
    suppressierten ``result``.
    """
    field_specs = backend.get_field_specs()
    return _csv_rows(backend, facility, date_from, date_to, result, generated_at, field_specs)


def _csv_rows(
    backend: DatasetBackend,
    facility,
    date_from,
    date_to,
    result: DatasetResult,
    generated_at,
    field_specs: list[FieldSpec],
) -> Iterator[str]:
    """Die eigentliche Zeilenausgabe — bewusst lazy (Streaming, siehe :func:`_render_csv`)."""
    output = io.StringIO()
    writer = csv.writer(output, dialect="excel")

    def _flush(row: list[str]) -> str:
        writer.writerow(row)
        chunk = output.getvalue()
        output.seek(0)
        output.truncate(0)
        return chunk

    # Refs #1933: die drei Labels werden bewusst VOR den f-Strings aufgeloest.
    # In einem f-String-Ausdruck extrahieren nur neuere xgettext-Versionen den
    # Aufruf (gemessen: 0.23.1 ja, 0.21 nein); die CI-Toolchain
    # (ubuntu-24.04, gettext 0.21) sieht ihn nicht und
    # ``make i18n-update`` wuerde die msgid dann still aus dem Katalog werfen.
    # Guard: ``tests/test_i18n_fstring_extraction_guard.py``.
    report_label = gettext("Bericht")
    data_source_label = gettext("Datenquelle")
    population_label = gettext("Grundgesamtheit")
    # Refs #1766: auch das Zeilenpraefix ist Fliesstext — bliebe es deutsch,
    # waere die Zeile nach dem Uebersetzen der Segmente immer noch gemischt.
    # Der Wert dahinter (``backend.slug``) ist der stabile Code und bleibt.
    metadata = f"# {report_label}: {backend.slug} | {facility} | {date_from}–{date_to} | {generated_at.isoformat()}"
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
    metadata += f" | data_source: {LIVE} | {data_source_label}: {DATA_SOURCE_NOTES[LIVE]}"
    # K3-A3 (Refs #1770): dasselbe Muster fuer die Grundgesamtheit. Panel und
    # Download zeigen denselben Datensatz — benennt nur das Panel seine
    # Basismenge, fehlt die Angabe genau in der Datei, die das Haus verlaesst.
    metadata += f" | population: {backend.population} | {population_label}: {POPULATION_NOTES[backend.population]}"
    # Ohne diesen Zusatz saehe der Empfaenger nur leere Zellen ohne Erklaerung —
    # das CSV traegt (anders als JSON/XML/Panel) keine Completeness-Hinweise.
    # Refs #2069: die Praesenz des Schluessels garantiert
    # ``enforce_suppression_contract`` — ein defensiver ``is not None``-Zweig
    # stuende hier nur noch tot und verschleierte die Zusage, statt sie zu
    # tragen.
    #
    # Refs #1766: als EIN msgid mit Platzhalter statt „Label + Wert" wie bei den
    # Nachbarsegmenten — hier gibt es keinen sprachunabhaengigen Code, an dem
    # sich das Segment trennen liesse, und ein halb uebersetztes Segment waere
    # genau der DE/EN-Mischtext, den #1765 fuer die Nachbarn beseitigt hat.
    # Die Schwelle selbst ist bewusst publiziert (Transparenz), nicht redigiert.
    threshold = result.summary["k_anonymity_threshold"]
    metadata += " | " + gettext("k-Anonymität: Zellen < %(threshold)s unterdrückt") % {"threshold": threshold}
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
