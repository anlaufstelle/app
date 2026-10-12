"""Views for statistics dashboard and exports."""

import logging
from urllib.parse import urlencode

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View

from core.constants import RATELIMIT_FREQUENT
from core.models import DocumentType
from core.reporting.data_source import (
    ALL_EVENTS,
    DATA_SOURCE_EXPLANATIONS,
    DATA_SOURCE_NOTES,
    EVENTS_WITH_PERSON,
    LIVE,
    SNAPSHOT_HYBRID,
    table_note,
)
from core.reporting.db_guard import reporting_statement_timeout
from core.reporting.export import SUPPORTED_FORMATS, enforce_suppression_contract, export_dataset
from core.reporting.registry import get_backend_or_404, get_backends
from core.services.dashboard import (
    InvalidStatisticsPeriodError,
    aggregate_trend_segments,
    build_release_overview,
    get_statistics,
    get_statistics_hybrid,
    get_statistics_trend,
    k_threshold_warning,
)
from core.services.dashboard.release_log import RELEASE_PAGE_SIZE, release_queryset
from core.services.system import export_events_csv
from core.utils.downloads import safe_download_response
from core.utils.ratelimit import ratelimit
from core.views.mixins import ExportRangeMixin, HTMXPartialMixin, LeadOrAdminRequiredMixin, htmx_absage, log_export

logger = logging.getLogger(__name__)


def event_document_types(facility):
    """Die im Statistik-Filter anwaehlbaren Dokumentationstypen der Einrichtung.

    M2 (Refs #1058, Entscheid E21): nur ``entity_type=EVENT`` — Statistiken
    werten Ereignisse aus, personen-/fallbezogene Typen gehoeren nicht in den
    Filter.
    """
    return (
        DocumentType.objects.for_facility(facility).filter(entity_type=DocumentType.EntityType.EVENT).order_by("name")
    )


def resolve_document_type_filter(facility, query_params):
    """``?doktyp=<id>`` → ``DocumentType`` dieser Einrichtung oder ``None``.

    Refs #1804 (Entscheid F3). Drei Eigenschaften, die hier zusammengehoeren
    und deshalb nicht auf Dashboard- und Chart-View verteilt werden duerfen:

    * **Facility-Scope.** Der Wert kommt aus der Query; ohne die
      ``facility``-Bedingung waere ``?doktyp=<fremde-uuid>`` ein Lesepfad in die
      Ereigniszahlen einer anderen Einrichtung.
    * **Fail-closed auf die ungefilterte Sicht.** Unbekannt, fremd oder gar
      keine UUID → ``None`` → alles wie ohne Filter. Nie ein 500er, nie fremde
      Daten.
    * **Ein Alias.** ``?document_type=`` war der Name, den das alte (wirkungs-
      lose) Alpine-Select an die Chart-API haengte; er bleibt lesbar, damit ein
      offener Tab beim Deploy nicht ins Leere greift.
    """
    raw = (query_params.get("doktyp") or query_params.get("document_type") or "").strip()
    if not raw:
        return None
    try:
        return event_document_types(facility).filter(pk=raw).first()
    except (ValidationError, ValueError, TypeError):
        # Keine gueltige UUID — Django wirft beim Bauen der Query, nicht beim
        # Auswerten. Derselbe Ausgang wie „nicht gefunden".
        return None


def backend_is_available(backend, facility) -> bool:
    """``backend.is_available(facility)`` mit Fehlerisolation (Refs #2070).

    ``is_available`` ist kein reiner Praedikat-Getter: die Default-Ableitung aus
    :attr:`DatasetBackend.verfuegbarkeit` setzt eine echte DB-Query ab
    (``Anforderung.erfuellt``). Sie laeuft ueber ``get_available_backends`` auf
    **jeder** Statistik-Seite und fuer **jedes** registrierte Backend — ein
    einziges werfendes Backend riss damit die gesamte Uebersicht ab, nicht nur
    seine eigene Karte.

    Ein Wurf bedeutet hier „nicht verfuegbar": dasselbe Ergebnis wie ein falsy
    Rueckgabewert, damit Sichtbarkeit (Liste) und Erreichbarkeit (Panel/Export
    → 404) nicht auseinanderlaufen. Fail-closed ist auch die sichere Richtung —
    ein Bericht, dessen Verfuegbarkeitspruefung nicht laeuft, ist nicht
    nachweislich freigegeben.
    """
    try:
        return bool(backend.is_available(facility))
    except Exception:
        logger.exception("statistics: is_available des Berichts-Backends %r ist gescheitert", backend.slug)
        return False


def available_report_backends(facility):
    """``get_available_backends`` mit Fehlerisolation je Backend (Refs #2070).

    Bewusst hier statt in der Registry: die Registry bleibt die schmale,
    webframework-freie Auskunft, die Fehlerbehandlung gehoert an den Rand
    (View-Schicht). Praedikat und Reihenfolge sind identisch zu
    :func:`core.reporting.registry.get_available_backends`.
    """
    return [backend for backend in get_backends() if backend_is_available(backend, facility)]


def get_backend_or_404_isolated(facility, slug):
    """``get_backend_or_404`` mit derselben Isolation wie die Liste (Refs #2070).

    ``get_backend_or_404`` ruft intern ``is_available`` — ein werfendes Backend
    ergaebe hier einen 500er, waehrend dasselbe Backend in der Liste still
    verschwindet. ``Http404`` muss dabei **vor** dem breiten Fang stehen: sie
    ist selbst eine ``Exception`` und die regulaere Antwort dieser Funktion.
    """
    try:
        return get_backend_or_404(facility, slug)
    except Http404:
        raise
    except Exception:
        logger.exception("statistics: Aufloesung des Berichts-Backends %r ist gescheitert", slug)
        raise Http404(_("Bericht für diese Einrichtung nicht verfügbar.")) from None


def resolve_report_backend(facility, query_params):
    """``?bericht=<slug>`` → verfuegbares Backend dieser Einrichtung oder ``None``.

    Refs #1804 (Konzept §3.3): Die Berichtsauswahl ist URL-Zustand, kein
    ``<select>``-Client-State — sie ueberlebt Reload und laesst sich teilen.
    ``?backend=`` bleibt als Alias lesbar: das ist der Name, den das
    GET-Formular der Export-Schaltflaechen ohnehin traegt.

    Aufgeloest wird gegen ``available_report_backends`` (nicht ``get_backend``):
    ein fuer diese Einrichtung nicht verfuegbares Backend darf ueber einen
    Deep-Link so wenig erreichbar sein wie ueber die Liste.
    """
    slug = (query_params.get("bericht") or query_params.get("backend") or "").strip()
    if not slug:
        return None
    return next((backend for backend in available_report_backends(facility) if backend.slug == slug), None)


def build_report_panel_context(facility, backend, period_state) -> dict:
    """Kontext der Berichts-Vorschau — geteilt von Reiter und HTMX-Partial.

    Refs #1804: Seit der Reiter die Auswahl serverseitig rendert, brauchen
    ``StatisticsView`` und ``DatasetReportPanelView`` denselben Kontext. Zwei
    Aufbauorte waeren die naechste Drift-Quelle — und diese Funktion traegt die
    Sicherheitszusage, die dabei nicht verloren gehen darf.

    Sicherheitskritisch (K-Anon, Refs #1278/T1): niemals ``build_dataset()``
    allein rendern — ``prepare_export()`` ist die export-paritaetische Sicht.

    Refs #2069: die Paritaet ist seitdem erzwungen. ADR-038 § 5 stellt Panel und
    Download ausdruecklich gleich — ein Fail-Closed-Check nur im Download hiesse:
    ein Backend ohne Suppression scheitert beim Herunterladen hart und zeigt in
    der Vorschau Rohzahlen.
    """
    result = backend.prepare_export(
        facility, backend.build_dataset(facility, period_state.date_from, period_state.date_to)
    )
    result = enforce_suppression_contract(backend, result)

    field_specs = backend.get_field_specs()
    preview_records = result.records if result.mode == "aggregate" else result.records[:10]
    table_labels = backend.get_table_labels()

    return {
        "backend": backend,
        "date_from": period_state.date_from,
        "date_to": period_state.date_to,
        "field_specs": field_specs,
        "result": result,
        "preview_rows": [
            [
                table_labels.get(record.get(spec.key), record.get(spec.key))
                if spec.key == "table"
                else record.get(spec.key)
                for spec in field_specs
            ]
            for record in preview_records
        ],
        "preview_total": len(result.records),
        "k_anonymity_threshold": result.summary.get("k_anonymity_threshold"),
        "k_threshold_warning": k_threshold_warning(result.summary.get("k_anonymity_threshold")),
        # Quellenausweis (Refs #1743): die Vorschau ruft ``build_dataset``,
        # liest also live — anders als das hybrid gespeiste Sachbericht-PDF.
        "data_source_note": DATA_SOURCE_NOTES[LIVE],
        "format_source_notes": [
            (format_name, DATA_SOURCE_NOTES[source]) for format_name, source in backend.get_format_sources().items()
        ],
        "data_source_explanation": DATA_SOURCE_EXPLANATIONS[LIVE],
        # K3-A3 (Refs #1770): Die Vorschau zeigt die tabellarische Projektion des
        # Backends — ihre Grundgesamtheit gehoert dazu. ``backend.population``
        # ist der vom Backend deklarierte Code (Default: alle Ereignisse).
        "population_note": table_note(backend.population, LIVE),
        "table_population_notes": [
            (table_labels.get(table, table), table_note(population, LIVE))
            for table, population in backend.get_table_populations(result).items()
        ],
        # K4a (Refs #1770): KEIN ``release_query``. Empfaenger/Zweck zur
        # Renderzeit in die Export-Ziele zu backen war ein Race: der Klick auf
        # einen Link blurrte zuerst das Freitextfeld (change → Panel-Neubau),
        # folgte dann aber synchron dem alten ``href`` — die Angabe fehlte still
        # im append-only EXPORT-Audit. Das Panel liegt im GET-Formular der
        # Berichte-Sektion, dessen Submit die Felder selbst serialisiert.
    }


REPORT_PANEL_ERROR_TEMPLATE = "core/statistics/partials/report_panel_error.html"


def backend_export_failed_response(beschreibung: str) -> HttpResponse:
    """Einheitliche 502-Antwort fuer einen gescheiterten Berichts-Download (Refs #2070).

    **Nur aus einem ``except``-Block aufrufen:** ``logger.exception`` liest die
    laufende Exception aus dem Kontext und legt den Traceback ins Log (und ueber
    die Sentry-LoggingIntegration ins Fehler-Tracking).

    HTTP 502, nicht 4xx (der Client hat nichts falsch gemacht) und nicht 500
    (der Fehler liegt im nachgelagerten Berichts-Backend). Bewusst eine nackte
    Textantwort: kein ``safe_download_response``, also **kein**
    ``Content-Disposition`` und kein Byte des Datensatzes. Der Aufrufer setzt
    seinen ``log_export``-Aufruf hinter den Fang — ein gescheiterter Export darf
    im append-only Audit nicht als Herausgabe stehen.
    """
    logger.exception("statistics: %s ist gescheitert", beschreibung)
    return HttpResponse(
        _("Der Bericht konnte nicht erstellt werden. Bitte versuchen Sie es später erneut."),
        status=502,
        content_type="text/plain; charset=utf-8",
    )


def build_report_panel_context_isolated(facility, backend, period_state) -> dict:
    """Panel-Kontext — oder ein **datenfreier** Fehlerkontext (Refs #2070).

    Die Isolation sitzt bewusst hier und nicht in
    :func:`build_report_panel_context`: diese traegt die Sicherheitszusage der
    atomaren Kette und **muss** weiter werfen (gepinnt in
    ``tests/test_reporting_suppression_contract.py::TestPanelPfad``). Ein Fang
    dort hiesse, dass ein Backend ohne ausgewiesene k-Anon-Schwelle nicht mehr
    fail-closed scheitert.

    **Kein Zwischenzustand.** Der Fehlerkontext enthaelt weder ``result`` noch
    ``preview_rows``, ``field_specs`` oder ``k_anonymity_threshold`` — es gibt
    also strukturell nichts, woraus ein Template eine Datenzeile bauen koennte,
    auch nicht versehentlich. Das ist die Umsetzung der Zusage „entweder die
    komplette, suppressierte Sicht oder gar keine".

    **``SuppressionContractError`` ist kein Sonderfall.** Sie faellt in
    denselben ``except Exception`` wie jeder Backend-Fehler: in JEDEM Fangfall
    folgt dieselbe Handlung — nichts rendern, Fehlerkarte zeigen, loggen. Eine
    Sonderbehandlung („bei diesem Fehler doch etwas anzeigen") waere die
    Sicherheitsregression, die ADR-038 § 5 ausschliesst.
    """
    try:
        with reporting_statement_timeout():
            return build_report_panel_context(facility, backend, period_state)
    except Exception:
        # ``logger.exception`` (nicht ``warning``): der Traceback gehoert ins
        # Log und — ueber die Sentry/GlitchTip-LoggingIntegration — ins
        # Fehler-Tracking. Ein defektes Backend darf nicht lautlos degradieren.
        logger.exception("statistics: Berichts-Panel des Backends %r ist gescheitert", backend.slug)
        return {"backend": backend, "report_error": True}


def statistics_state_params(period_state, document_type=None) -> dict[str, str]:
    """Der **aufgeloeste** Zustand des Statistik-Bereichs als Parameter-Dict.

    Drei Verwender teilen sich diese eine Aufloesung: die HTMX-Nachladeziele
    der Zeitraum-Leiste, die Chart-API und die Reiterzeile (``subnav_carry``,
    Refs #1804 §3.5). Der Zustand steht damit **serverseitig gerendert** in der
    Seite (R3) — die Diagramme lesen ihn aus dem Markup statt aus
    ``window.location.search``, das nach einem HTMX-Swap noch den vorherigen
    Stand tragen kann (die History-Aktualisierung folgt dem Swap).

    Aufgeloest heisst: kanonische Schluessel, nie der eingehende Alias. Ein
    Bookmark mit ``?period=quarter`` zeigt den richtigen Stand, breitet seine
    Schreibweise aber nicht ueber die Oberflaeche weiter aus.
    """
    from core.services.dashboard import PERIOD_FREI, PERIOD_JAHR

    params = {"zeitraum": period_state.period}
    if period_state.period == PERIOD_FREI:
        params["date_from"] = period_state.date_from.isoformat()
        params["date_to"] = period_state.date_to.isoformat()
    elif period_state.period == PERIOD_JAHR and period_state.selected_year:
        params["year"] = str(period_state.selected_year)
    if document_type is not None:
        params["doktyp"] = str(document_type.pk)
    return params


def statistics_state_query(period_state, document_type=None) -> str:
    """:func:`statistics_state_params` als Query-String (ohne fuehrendes ``?``)."""
    return urlencode(statistics_state_params(period_state, document_type))


class StatisticsView(LeadOrAdminRequiredMixin, HTMXPartialMixin, View):
    """Statistics dashboard with period selection."""

    template_name = "core/statistics/index.html"
    partial_template_name = "core/statistics/partials/full_content.html"

    # Refs #1589: interaktives Dashboard (Filterwechsel loesen je einen GET aus),
    # daher hoehere Frequenz als die 10/h der Export-Views — RATELIMIT_FREQUENT
    # (120/h). Der eigentliche DoS-Vektor (unbegrenzter custom-Range) ist bereits
    # durch das Clamping in parse_statistics_period entschaerft; das Ratelimit ist
    # Defense-in-Depth gegen wiederholtes Haemmern.
    @method_decorator(ratelimit(key="user", rate=RATELIMIT_FREQUENT, method="GET", block=True))
    def get(self, request):
        from core.services.dashboard import parse_statistics_period, parse_statistics_view

        facility = request.current_facility
        today = timezone.localdate()
        # Refs #816 (C-49): Period-Parsing zentral in der Service-Schicht.
        try:
            period_state = parse_statistics_period(request.GET, today)
        except InvalidStatisticsPeriodError as exc:
            # Refs #2385: Die Zeitraum-Pills/Report-Karten wechseln per HTMX in
            # #statistics-content — ein blosses 400 (vorher) ersetzte das ganze
            # Dashboard durch den Klartext (Fehlerklasse #2358).
            return htmx_absage(str(exc), status=400)

        # Refs #1804 (Entscheid F3): Bei gesetztem Doktyp-Filter faellt der
        # Snapshot-Pfad weg — Monats-Aggregate kennen keine Zerlegung nach
        # Dokumentationstyp. Der Preis (langsamer) ist bewusst gewaehlt; die
        # Alternative waere ein Filter, der Diagramme trifft, KPI und Tabellen
        # aber nicht — genau die Divergenz, die #1804 schliesst.
        selected_doc_type = resolve_document_type_filter(facility, request.GET)
        if selected_doc_type is not None:
            stats = get_statistics(facility, period_state.date_from, period_state.date_to, selected_doc_type)
            data_source = LIVE
        else:
            stats = get_statistics_hybrid(facility, period_state.date_from, period_state.date_to)
            data_source = SNAPSHOT_HYBRID

        # Refs #1813 (Konzept #1801 §7.1): der Reiter „Datensatz-Berichte" ist ein
        # serverseitiger Modus derselben Route — Hausmuster der Bereichs-
        # Konsolidierung (Zeitstrom ``?view=``, Refs #1124). Eine eigene Route
        # scheidet aus: ``core:statistics_report_panel`` ist ein HTMX-Partial ohne
        # ``base.html`` und antwortet ohne ``?backend=`` mit 400.
        #
        # Nur ``datensaetze`` ist gültig, alles andere fällt auf das Dashboard
        # zurück: ein Tippfehler in einem Deep-Link soll eine bekannte Seite
        # zeigen, keine leere. ``view_query`` hängt den Modus an die
        # HTMX-Nachladeziele der Zeitraum-Auswahl — ohne ihn spränge der erste
        # Filterklick zurück ins Dashboard.
        view_mode = parse_statistics_view(request.GET)

        # Refs #1804 (Konzept §3.3): Die Berichtsauswahl ist URL-Zustand und
        # wird serverseitig gerendert — der frueher noetige JS-Workaround gegen
        # den 400-Toast beim Zuruecksetzen des Selects auf den Platzhalter
        # entfaellt damit ersatzlos. Das Panel wird nur im Berichte-Modus
        # gebaut: ``build_dataset`` ist teuer und hat auf der Uebersicht nichts
        # zu suchen.
        selected_backend = resolve_report_backend(facility, request.GET) if view_mode else None
        # Refs #2070: fehlerisoliert — ein defektes Backend kostet seine eigene
        # Vorschau, nicht die ganze Seite (Kennzahlen, Diagramme, Kartenliste).
        report_panel = (
            build_report_panel_context_isolated(facility, selected_backend, period_state)
            if selected_backend is not None
            else None
        )

        context = {
            "view_mode": view_mode,
            "view_query": f"&ansicht={view_mode}" if view_mode else "",
            # Refs #1804 (§3.5): Kontrakt der gemeinsamen Zeitraum-Leiste. Hier
            # mit HTMX-Ziel — diese Seite hat eine Inhaltszone, die sich
            # unabhaengig von Reiterzeile und Ortsanzeige tauschen laesst.
            "zeitraum_url": reverse("core:statistics"),
            "zeitraum_target": "#statistics-content",
            # Refs #1804: der Doktyp-Filter haengt wie der Modus an den
            # HTMX-Nachladezielen der Zeitraum-Leiste — ohne ihn faellt er beim
            # ersten Zeitraum-Klick still weg.
            "doktyp_query": f"&doktyp={selected_doc_type.pk}" if selected_doc_type is not None else "",
            # Refs #1804 (§3.3 + §3.5): dasselbe fuer die Berichtsauswahl. Ohne
            # sie verschwanden Vorschau, Export-Schaltflaechen und die
            # Empfaenger/Zweck-Felder beim ersten Zeitraum-Klick — wortlos, denn
            # die Seite sieht danach aus wie ein frisch geoeffneter Reiter.
            # Genau beim Zeitraum-Wechsel braucht man die Auswahl aber:
            # denselben Bericht ueber ein anderes Fenster rechnen.
            "bericht_query": f"&bericht={selected_backend.slug}" if selected_backend is not None else "",
            "selected_doc_type": selected_doc_type,
            # Serverseitig gerenderter Zustand fuer die Chart-API (R3, kein
            # Client-State): siehe ``statistics_state_query``.
            "chart_query": statistics_state_query(period_state, selected_doc_type),
            # Refs #1804 (§3.5): Der Zeitraum reist an der Reiterzeile mit —
            # OHNE ``doktyp``: der Filter existiert nur auf der Uebersicht.
            "subnav_carry": statistics_state_params(period_state),
            "stats": stats,
            "period": period_state.period,
            "date_from": period_state.date_from,
            "date_to": period_state.date_to,
            "can_export": True,
            "selected_year": period_state.selected_year,
            "current_year": today.year,
            # M2 (Refs #1058): nur event-Typen — Statistiken werten Events aus,
            # client-/case-Typen gehoeren nicht in den Filter (Entscheid E21).
            "document_types": event_document_types(facility),
            # M5 P5 (Refs #1061): Berichts-Plugins (z. B. "Beispiel-Sachbericht")
            # fuer die Berichtsliste — nur fuer diese Facility verfuegbare.
            # Refs #2070: fehlerisoliert je Backend, weil dieser Aufruf auf
            # JEDER Statistik-Seite laeuft (auch der reinen KPI-Uebersicht).
            "report_backends": available_report_backends(facility),
            "selected_backend": selected_backend,
            # Refs #1804: Der Zeitraum reist an den Karten-Links mit — sonst
            # faellt er beim ersten Berichtswechsel auf die Vorgabe zurueck.
            "zeitraum_query": f"&{statistics_state_query(period_state, None)}",
            # K3-A3 (Refs #1770): Auch das interne Dashboard benennt seine
            # Grundgesamtheiten. Es liest hybrid (Monats-Aggregate mit
            # Live-Fallback) und zeigt die Alterstabelle **ohne** die
            # K3-A1-Restzeile — sie zaehlt hier also nur Ereignisse mit
            # Personenbezug, anders als im Artefakt. Genau diese Abweichung
            # muss dastehen, sonst liest jemand dieselbe Tabelle in zwei
            # Bedeutungen. Unterdrueckt wird hier bewusst nichts (interner
            # Rohpfad, RLS-Scope, #1311).
            # Refs #1804: die Herkunft folgt dem tatsaechlich gelaufenen Pfad.
            # Ein fester SNAPSHOT_HYBRID-Ausweis waere bei gesetztem Filter eine
            # falsche Angabe — und der Quellenausweis ist der Ort, an dem der
            # Preis des Filters sichtbar wird.
            "population_notes": {
                "by_document_type": table_note(ALL_EVENTS, data_source),
                "by_age_cluster": table_note(EVENTS_WITH_PERSON, data_source),
            },
        }

        # Der Panel-Kontext wird flach gemergt: ``report_panel.html`` ist auch
        # das Ziel des HTMX-Partials und erwartet dort dieselben Namen auf der
        # obersten Ebene — ein zweiter Namensraum hier hiesse, dasselbe Template
        # in zwei Kontraktversionen zu pflegen.
        if report_panel is not None:
            context.update(report_panel)

        return self.render_htmx_or_full(context)


GERMAN_MONTHS = [
    "",
    "Jan",
    "Feb",
    "Mär",
    "Apr",
    "Mai",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Okt",
    "Nov",
    "Dez",
]


class ChartDataView(LeadOrAdminRequiredMixin, View):
    """JSON API endpoint returning chart-ready statistics data."""

    # Refs #1589: siehe StatisticsView — hoehere Frequenz (RATELIMIT_FREQUENT,
    # 120/h) als die Export-Views, da die Chart-API bei jedem Filterwechsel per
    # JS nachgeladen wird.
    @method_decorator(ratelimit(key="user", rate=RATELIMIT_FREQUENT, method="GET", block=True))
    def get(self, request):
        from core.services.dashboard import parse_statistics_period

        facility = request.current_facility
        today = timezone.localdate()
        # Refs #816 (C-49): Period-Parsing zentral in der Service-Schicht.
        try:
            period_state = parse_statistics_period(request.GET, today)
        except InvalidStatisticsPeriodError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        date_from = period_state.date_from
        date_to = period_state.date_to

        # Refs #1804 (F3): derselbe Filter wie KPI und Tabellen, aus derselben
        # Aufloesung — sonst zeigen Diagramm und Tabelle im selben Screen
        # verschiedene Grundgesamtheiten.
        selected_doc_type = resolve_document_type_filter(facility, request.GET)
        segments = get_statistics_trend(facility, date_from, date_to, selected_doc_type)

        # Build per-segment arrays for the line chart
        labels = []
        totals = []
        anonym = []
        identifiziert = []
        qualifiziert = []
        sources = []

        for seg in segments:
            year, month = seg["label"].split("-")
            labels.append(f"{GERMAN_MONTHS[int(month)]} {year}")
            totals.append(seg["total_contacts"])
            stage = seg.get("by_contact_stage", {})
            anonym.append(stage.get("anonym", 0))
            identifiziert.append(stage.get("identifiziert", 0))
            qualifiziert.append(stage.get("qualifiziert", 0))
            sources.append(seg["source"])

        # Aggregate document_types and age_clusters across all segments
        aggregated = aggregate_trend_segments(segments)

        data = {
            "labels": labels,
            "contacts": {
                "total": totals,
                "anonym": anonym,
                "identifiziert": identifiziert,
                "qualifiziert": qualifiziert,
            },
            "document_types": aggregated.get("by_document_type", []),
            "age_clusters": aggregated.get("by_age_cluster", []),
            "sources": sources,
            # K3-A3 (Refs #1770): Auch das interne Dashboard-JSON benennt seine
            # Grundgesamtheiten — als stabile Codes, wie das Artefakt-JSON.
            # Hier wird bewusst **nicht** unterdrueckt (interner Rohpfad,
            # RLS-Scope, #1311); die Tabellen haben aber dieselbe Bedeutungs-
            # frage, und ``age_clusters`` zaehlt hier — anders als im Artefakt —
            # nur Ereignisse MIT Personenbezug: die K3-A1-Restzeile entsteht
            # erst an der Artefaktgrenze. Genau diese Abweichung muss benannt
            # sein, sonst liest jemand dieselbe Tabelle in zwei Bedeutungen.
            "populations": {
                "document_types": ALL_EVENTS,
                "age_clusters": EVENTS_WITH_PERSON,
            },
        }

        return JsonResponse(data)


def export_document_type_detail(document_type) -> dict:
    """Der Doktyp-Filter als Audit-Detail — leer, wenn ungefiltert exportiert wurde.

    Refs #1804 (R2): Die Freigabe-Uebersicht bewertet Herausgaben ueber ihren
    Zeitraum. Zwei Exporte desselben Zeitraums koennen jetzt verschiedene
    Ausschnitte sein; ohne diese Angabe stuenden sie im Audit als dieselbe
    Herausgabe da. Der Schluessel bleibt weg, wenn nicht gefiltert wurde — so
    unterscheiden sich Alt-Zeilen nicht von neuen ungefilterten.
    """
    if document_type is None:
        return {}
    return {"document_type": document_type.name, "document_type_id": str(document_type.pk)}


class CSVExportView(LeadOrAdminRequiredMixin, ExportRangeMixin, View):
    """Streaming CSV export of all events in the period."""

    @method_decorator(ratelimit(key="user", rate="10/h", method="GET", block=True))
    def get(self, request):
        facility = request.current_facility
        # Refs #1589: gleiche Range-Validierung wie parse_statistics_period —
        # die Export-Views lesen date_from/date_to direkt aus der Query.
        range_result = self.resolve_range(request)
        if isinstance(range_result, HttpResponse):
            return range_result
        date_from, date_to = range_result

        # R2 (Refs #1804): Der Export gibt aus, was am Bildschirm steht. Die
        # Aufloesung laeuft ueber dieselbe fail-closed, facility-skopierte
        # Funktion wie die Seite — ein zweiter Parser waere hier ein
        # Lesepfad in fremde Ereigniszahlen.
        document_type = resolve_document_type_filter(facility, request.GET)

        response = safe_download_response(
            f"export_{date_from}_{date_to}.csv",
            "text/csv; charset=utf-8",
            export_events_csv(facility, date_from, date_to, user=request.user, document_type=document_type),
        )

        # K4a (Refs #1770): Empfaenger/Zweck wandern ins bestehende
        # ``detail``-JSON (kein Schema-Eingriff auf dem append-only,
        # hash-verketteten Audit-Log). Ohne sie erzeugt die
        # Freigabe-Uebersicht Alarme, die niemand bewerten kann.
        log_export(
            request,
            "CSV",
            format="CSV",
            date_from=date_from,
            date_to=date_to,
            extra=export_document_type_detail(document_type),
        )

        return response


class ExternalReportRedirectView(LeadOrAdminRequiredMixin, View):
    """Former external report address: redirects to the "Allgemeine Kennzahlen" card.

    Refs #2505: the on-screen external report and its ``?format=json``
    file are gone. The card is the preview, the generic report export is the
    only way out. This view never renders a file and writes no audit row —
    a redirect is not a release, and previews are not logged either.

    The role guard stays: the redirect target is Lead/Admin only, and an
    unauthorised caller should get the same answer as for any statistics page.
    """

    def get(self, request):
        from core.services.dashboard import parse_statistics_period

        try:
            period_state = parse_statistics_period(request.GET, timezone.localdate())
        except InvalidStatisticsPeriodError as exc:
            return HttpResponse(str(exc), status=400)

        query = urlencode(
            {
                "ansicht": "datensaetze",
                "bericht": "general-key-figures",
                **statistics_state_params(period_state),
            }
        )
        return redirect(f"{reverse('core:statistics')}?{query}")


class DatasetReportPanelView(LeadOrAdminRequiredMixin, View):
    """Berichts-Panel (M5 P5, Refs #1061): HTMX-Vorschau eines Reporting-Backends.

    Rendert dieselbe export-paritaetische (k-anon-suppressierte) Sicht wie der
    Download — ``backend.prepare_export(facility, backend.build_dataset(...))``,
    NIEMALS das rohe ``build_dataset``-Ergebnis (Kleinstfallzahl-Leak). Kein
    Audit-Log (interne Vorschau, Parität zum Dashboard — der Export auditiert).
    """

    template_name = "core/statistics/partials/report_panel.html"

    # Refs #1589/#1061: dieselbe Frequenz wie StatisticsView/ChartDataView
    # (RATELIMIT_FREQUENT, 120/h) — das Panel wird wie das Dashboard bei jedem
    # Filterwechsel (Backend-/Zeitraum-Select) per HTMX nachgeladen, nicht die
    # niedrigere 10/h der Download-Export-Views.
    @method_decorator(ratelimit(key="user", rate=RATELIMIT_FREQUENT, method="GET", block=True))
    def get(self, request):
        from core.services.dashboard import parse_statistics_period

        facility = request.current_facility

        # Backend-Aufloesung VOR allem anderen (Periode, Datensatz-Bau): 5xx-Guard
        # der AuthZ-Matrix ruft diese View ohne Query-Parameter auf — die Fehler
        # muessen deterministisch 400/404 sein, ohne z. B. einen kaputten
        # Custom-Zeitraum zuerst auszuwerten.
        slug = request.GET.get("backend", "").strip()
        if not slug:
            return HttpResponse(_("backend erforderlich"), status=400)
        # Truthiness (nicht ``is False``): dasselbe Praedikat wie der
        # Dropdown-Filter in ``registry.get_available_backends`` — sonst wuerde
        # ein falsy Nicht-``False``-Rueckgabewert (z. B. ``None`` aus einem
        # nullable BooleanField) das Backend nur verstecken, nicht sperren.
        backend = get_backend_or_404_isolated(facility, slug)

        today = timezone.localdate()
        try:
            period_state = parse_statistics_period(request.GET, today)
        except InvalidStatisticsPeriodError as exc:
            return HttpResponse(str(exc), status=400)

        # Refs #2070: Fehlerkarte statt 500 — mit HTTP **200**, weil HTMX nur
        # 2xx-Antworten einswappt. Eine 5xx-Antwort liesse das zuletzt
        # gerenderte Panel stehen: der Fehler waere unsichtbar und die alte
        # Vorschau sieht aus, als gehoerte sie zum neuen Zeitraum.
        context = build_report_panel_context_isolated(facility, backend, period_state)
        template = REPORT_PANEL_ERROR_TEMPLATE if context.get("report_error") else self.template_name
        return render(request, template, context)


class ReportReleaseLogView(LeadOrAdminRequiredMixin, View):
    """Freigabe-Uebersicht herausgegebener Berichtszeitraeume (K4a, Refs #1770).

    Flankierende Massnahme zu einer **akzeptierten Definitionsgrenze**: die
    k-Anonymitaets-Zusicherung gilt je Artefakt und je Zeitraum, und aus
    verschachtelten Freigaben (Monatsberichte plus Jahresbericht) laesst sich
    jede unterdrueckte Zelle exakt zurueckrechnen — artefakt-lokal gibt es dagegen
    nichts zu unterdruecken. Die Uebersicht kann das nicht verhindern, sie macht
    es **sichtbar**.

    Rollen-Guard wie das Berichts-Panel selbst (``LeadOrAdminRequiredMixin``) —
    und das ist nur tragfaehig, weil die Datenquelle **eng gefiltert** ist:
    ``build_release_overview`` liest ausschliesslich Audit-Eintraege MIT
    Berichtszeitraum (``RELEASE_EXPORT_Q``). Ungefiltert liefe ``EXPORT`` als
    Sammelklasse — Art.-15-Datenauskuenfte einzelner Personen und
    DSGVO-Downloads inklusive — auf einer Seite, die eine Leitung sehen darf,
    waehrend ``/audit/`` der Einrichtungsverwaltung vorbehalten ist.

    Gezeigt werden Zeitraum, Format und die frei erfassten Empfaenger-/Zweck-
    Angaben; die **handelnde Person bewusst nicht** (Datensparsamkeit, siehe
    ``release_log.Release``).
    """

    template_name = "core/statistics/report_releases.html"

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_FREQUENT, method="GET", block=True))
    def get(self, request):
        pagination_params = request.GET.copy()
        pagination_params.pop("page", None)
        paginator = Paginator(release_queryset(request.current_facility), RELEASE_PAGE_SIZE)
        page = paginator.get_page(request.GET.get("page"))
        releases = build_release_overview(
            request.current_facility,
            limit=RELEASE_PAGE_SIZE,
            offset=(page.number - 1) * RELEASE_PAGE_SIZE,
        )
        return render(
            request,
            self.template_name,
            {
                "releases": releases,
                "overlap_count": sum(1 for release in releases if release.overlaps),
                "page_obj": page,
                "paginator": paginator,
                "is_paginated": paginator.num_pages > 1,
                "pagination_params": pagination_params.urlencode(),
            },
        )


class DatasetExportView(LeadOrAdminRequiredMixin, ExportRangeMixin, View):
    """Generischer Berichts-Download (M5 P5, Refs #1061): CSV/JSON/XML via ``export_dataset``.

    Format-/Backend-Verfuegbarkeitspruefung analog ``DatasetReportPanelView``;
    ``export_dataset`` wendet ``backend.prepare_export`` intern bereits an
    (Refs #1278) — hier kommt es nicht noch einmal zum Einsatz.
    """

    @method_decorator(ratelimit(key="user", rate="10/h", method="GET", block=True))
    def get(self, request):
        facility = request.current_facility
        # Refs #1589: gleiche Range-Validierung wie die uebrigen Export-Views.
        range_result = self.resolve_range(request)
        if isinstance(range_result, HttpResponse):
            return range_result
        date_from, date_to = range_result

        slug = request.GET.get("backend", "").strip()
        # Truthiness wie im Panel/in der Registry — siehe DatasetReportPanelView.
        backend = get_backend_or_404_isolated(facility, slug)

        fmt = request.GET.get("format", "")
        if fmt not in SUPPORTED_FORMATS:
            return HttpResponse(_("Unbekanntes Export-Format."), status=400)

        # Refs #2070: Ein Export kann nicht degradieren — er muss sauber
        # scheitern. HTTP 502 (nicht 4xx: der Client hat nichts falsch gemacht;
        # nicht 500: der Fehler liegt im nachgelagerten Backend), knapper Text,
        # KEIN Attachment-Header und kein Byte des Datensatzes. Der Fang
        # umschliesst die vollstaendige Kette ``build_dataset`` ->
        # ``prepare_export`` -> ``enforce_suppression_contract`` in
        # ``export_dataset``; ein Teilergebnis verlaesst sie nie.
        try:
            with reporting_statement_timeout():
                response = export_dataset(backend, facility, date_from, date_to, format=fmt)
        except Exception:
            return backend_export_failed_response(f"Export des Berichts-Backends {slug!r}")

        # Erst nach dem erfolgreichen Bau: ein gescheiterter Export darf im
        # append-only Audit nicht als Herausgabe stehen.
        log_export(request, "DatasetExport", format=fmt, date_from=date_from, date_to=date_to, extra={"backend": slug})

        return response
