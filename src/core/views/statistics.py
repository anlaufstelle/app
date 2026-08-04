"""Views for statistics dashboard and exports."""

import logging
from urllib.parse import quote

from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_FREQUENT
from core.models import AuditLog, DocumentType
from core.reporting.data_source import (
    ALL_EVENTS,
    DATA_SOURCE_EXPLANATIONS,
    DATA_SOURCE_NOTES,
    EVENTS_WITH_PERSON,
    LIVE,
    SNAPSHOT_HYBRID,
    table_note,
)
from core.reporting.export import SUPPORTED_FORMATS, export_dataset
from core.reporting.registry import get_available_backends, get_backend
from core.services.audit import log_audit_event
from core.services.dashboard import (
    InvalidStatisticsPeriodError,
    _merge_stats,
    build_external_report,
    build_release_overview,
    get_statistics_hybrid,
    get_statistics_trend,
    release_detail,
    validate_statistics_range,
)
from core.services.system import export_events_csv, generate_report_pdf, generate_sample_report_1_pdf
from core.utils.downloads import safe_download_response
from core.utils.formatting import parse_date
from core.views.mixins import HTMXPartialMixin, LeadOrAdminRequiredMixin

logger = logging.getLogger(__name__)


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
        from core.services.dashboard import parse_statistics_period

        facility = request.current_facility
        today = timezone.localdate()
        # Refs #816 (C-49): Period-Parsing zentral in der Service-Schicht.
        try:
            period_state = parse_statistics_period(request.GET, today)
        except InvalidStatisticsPeriodError as exc:
            return HttpResponse(str(exc), status=400)

        stats = get_statistics_hybrid(facility, period_state.date_from, period_state.date_to)

        context = {
            "stats": stats,
            "period": period_state.period,
            "date_from": period_state.date_from,
            "date_to": period_state.date_to,
            "can_export": True,
            "selected_year": period_state.selected_year,
            "current_year": today.year,
            # M2 (Refs #1058): nur event-Typen — Statistiken werten Events aus,
            # client-/case-Typen gehoeren nicht in den Filter (Entscheid E21).
            "document_types": DocumentType.objects.filter(
                facility=facility, entity_type=DocumentType.EntityType.EVENT
            ).order_by("name"),
            # M5 P5 (Refs #1061): Berichts-Plugins (z. B. "Beispiel-Sachbericht")
            # fuers Dropdown der Berichte-Sektion — nur fuer diese Facility verfuegbare.
            "report_backends": get_available_backends(facility),
            # K3-A3 (Refs #1770): Auch das interne Dashboard benennt seine
            # Grundgesamtheiten. Es liest hybrid (Monats-Aggregate mit
            # Live-Fallback) und zeigt die Alterstabelle **ohne** die
            # K3-A1-Restzeile — sie zaehlt hier also nur Ereignisse mit
            # Personenbezug, anders als im Artefakt. Genau diese Abweichung
            # muss dastehen, sonst liest jemand dieselbe Tabelle in zwei
            # Bedeutungen. Unterdrueckt wird hier bewusst nichts (interner
            # Rohpfad, RLS-Scope, #1311).
            "population_notes": {
                "by_document_type": table_note(ALL_EVENTS, SNAPSHOT_HYBRID),
                "by_age_cluster": table_note(EVENTS_WITH_PERSON, SNAPSHOT_HYBRID),
            },
        }

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

        segments = get_statistics_trend(facility, date_from, date_to)

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
        segment_stats = [{k: v for k, v in seg.items() if k not in ("label", "source")} for seg in segments]
        aggregated = _merge_stats(segment_stats)

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


class CSVExportView(LeadOrAdminRequiredMixin, View):
    """Streaming CSV export of all events in the period."""

    @method_decorator(ratelimit(key="user", rate="10/h", method="GET", block=True))
    def get(self, request):
        facility = request.current_facility
        date_from = parse_date(request.GET.get("date_from"), None)
        date_to = parse_date(request.GET.get("date_to"), None)
        if not date_from or not date_to:
            return HttpResponse(_("date_from und date_to erforderlich"), status=400)
        # Refs #1589: gleiche Range-Validierung wie parse_statistics_period —
        # die Export-Views lesen date_from/date_to direkt aus der Query.
        try:
            validate_statistics_range(date_from, date_to)
        except InvalidStatisticsPeriodError as exc:
            return HttpResponse(str(exc), status=400)

        response = safe_download_response(
            f"export_{date_from}_{date_to}.csv",
            "text/csv; charset=utf-8",
            export_events_csv(facility, date_from, date_to, user=request.user),
        )

        log_audit_event(
            request,
            AuditLog.Action.EXPORT,
            target_type="CSV",
            # K4a (Refs #1770): Empfaenger/Zweck wandern ins bestehende
            # ``detail``-JSON (kein Schema-Eingriff auf dem append-only,
            # hash-verketteten Audit-Log). Ohne sie erzeugt die
            # Freigabe-Uebersicht Alarme, die niemand bewerten kann.
            detail={
                "format": "CSV",
                "date_from": str(date_from),
                "date_to": str(date_to),
                **release_detail(request.GET, artifact_released=True),
            },
        )

        return response


class PDFExportView(LeadOrAdminRequiredMixin, View):
    """PDF semi-annual report as download."""

    @method_decorator(ratelimit(key="user", rate="10/h", method="GET", block=True))
    def get(self, request):
        facility = request.current_facility
        date_from = parse_date(request.GET.get("date_from"), None)
        date_to = parse_date(request.GET.get("date_to"), None)
        if not date_from or not date_to:
            return HttpResponse(_("date_from und date_to erforderlich"), status=400)
        # Refs #1589: gleiche Range-Validierung wie parse_statistics_period —
        # die Export-Views lesen date_from/date_to direkt aus der Query.
        try:
            validate_statistics_range(date_from, date_to)
        except InvalidStatisticsPeriodError as exc:
            return HttpResponse(str(exc), status=400)

        stats = get_statistics_hybrid(facility, date_from, date_to)
        # Refs #792 (C-24): ``?internal=1`` aktiviert den internen Report-Modus
        # mit Pseudonym-Ranking + INTERN-Banner. Standard ist der externe Modus
        # ohne Top-Pseudonyme — DSGVO-Datenminimierung bei Traegerberichten.
        internal_mode = request.GET.get("internal") in ("1", "true", "yes", "on")
        pdf_bytes = generate_report_pdf(facility, date_from, date_to, stats, internal_mode=internal_mode)

        filename_suffix = "_intern" if internal_mode else ""
        response = safe_download_response(
            f"bericht_{date_from}_{date_to}{filename_suffix}.pdf",
            "application/pdf",
            pdf_bytes,
        )

        log_audit_event(
            request,
            AuditLog.Action.EXPORT,
            target_type="PDF",
            detail={
                "format": "PDF",
                "date_from": str(date_from),
                "date_to": str(date_to),
                **release_detail(request.GET, artifact_released=True),
            },
        )

        return response


class SampleReport1ExportView(LeadOrAdminRequiredMixin, View):
    """Beispiel-Sachbericht als PDF-Download."""

    @method_decorator(ratelimit(key="user", rate="10/h", method="GET", block=True))
    def get(self, request):
        facility = request.current_facility
        date_from = parse_date(request.GET.get("date_from"), None)
        date_to = parse_date(request.GET.get("date_to"), None)
        if not date_from or not date_to:
            return HttpResponse(_("date_from und date_to erforderlich"), status=400)
        # Refs #1589: gleiche Range-Validierung wie parse_statistics_period —
        # die Export-Views lesen date_from/date_to direkt aus der Query.
        try:
            validate_statistics_range(date_from, date_to)
        except InvalidStatisticsPeriodError as exc:
            return HttpResponse(str(exc), status=400)

        pdf_bytes = generate_sample_report_1_pdf(facility, date_from, date_to)

        response = safe_download_response(
            f"sample-report-1_{date_from}_{date_to}.pdf",
            "application/pdf",
            pdf_bytes,
        )

        log_audit_event(
            request,
            AuditLog.Action.EXPORT,
            target_type="Sachbericht-PDF",
            detail={
                "format": "Sachbericht-PDF",
                "date_from": str(date_from),
                "date_to": str(date_to),
                **release_detail(request.GET, artifact_released=True),
            },
        )

        return response


class ExternalReportView(LeadOrAdminRequiredMixin, View):
    """Datenschutzfreundlicher externer Bericht (Refs #921).

    Wrappt :func:`core.services.dashboard.external_report.build_external_report`:
    keine Pseudonyme, K-Anon-Schwelle aus Settings, Datenschutzprofil-Metadaten.

    Output-Format wird ueber den ``format``-Query-Parameter gewaehlt:
    - ``html`` (Default): rendert Template ``core/statistics/external_report.html``
    - ``json``: liefert JsonResponse mit demselben Datenmodell
    """

    template_name = "core/statistics/external_report.html"

    def get(self, request):
        from core.services.dashboard import parse_statistics_period

        facility = request.current_facility
        today = timezone.localdate()
        # Refs #1589: parse_statistics_period ist Single Source of Truth — das
        # Range-Clamping haertet den externen Bericht automatisch mit.
        try:
            period_state = parse_statistics_period(request.GET, today)
        except InvalidStatisticsPeriodError as exc:
            return HttpResponse(str(exc), status=400)

        report = build_external_report(facility, period_state.date_from, period_state.date_to)

        output_format = request.GET.get("format", "html")
        # Audit-Log: jeder externe Bericht ist ein DSGVO-relevanter Export.
        #
        # K4a (Refs #1770): Eine **Freigabe** ist nur der Abruf, bei dem ein
        # Artefakt das Haus verlaesst — hier ``?format=json``. Die HTML-Ansicht
        # wird bei JEDEM Aufruf auditiert, auch bei jedem Reload; sie als
        # Herausgabe zu fuehren liesse den Log unbegrenzt und dauerhaft
        # (prune-exempt) wachsen und verdraengte in der Freigabe-Uebersicht die
        # echten Downloads aus dem Auswertungsfenster. Die Audit-Zeile bleibt,
        # sie ist nur eine normale, befristete Zeile.
        log_audit_event(
            request,
            AuditLog.Action.EXPORT,
            target_type="ExternalReport",
            detail={
                "format": output_format,
                "date_from": str(period_state.date_from),
                "date_to": str(period_state.date_to),
                "k_threshold": report["metadata"]["k_anonymity_threshold"],
                **release_detail(request.GET, artifact_released=output_format == "json"),
            },
        )

        if output_format == "json":
            return JsonResponse(report)
        return render(
            request,
            self.template_name,
            {
                "report": report,
                "period": period_state.period,
                "date_from": period_state.date_from,
                "date_to": period_state.date_to,
                "selected_year": period_state.selected_year,
                "current_year": today.year,
                # Quellenausweis (Refs #1743): der Erklaersatz gehoert zur
                # Darstellung, der Code/die Kurzform stehen in
                # ``report["metadata"]`` und damit auch im JSON-Format.
                "data_source_explanation": DATA_SOURCE_EXPLANATIONS[LIVE],
                # K3-A3 (Refs #1770): uebersetzter Grundgesamtheits-/Quellen-
                # Ausweis je Tabelle; die stabilen Codes stehen in
                # ``report["metadata"]["populations"]`` und damit im JSON.
                "population_notes": {
                    "by_document_type": table_note(ALL_EVENTS, LIVE),
                    "by_age_cluster": table_note(ALL_EVENTS, LIVE),
                },
            },
        )


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
        try:
            backend = get_backend(slug)
        except KeyError:
            raise Http404(_("Unbekanntes Berichts-Backend.")) from None
        # Truthiness (nicht ``is False``): dasselbe Praedikat wie der
        # Dropdown-Filter in ``registry.get_available_backends`` — sonst wuerde
        # ein falsy Nicht-``False``-Rueckgabewert (z. B. ``None`` aus einem
        # nullable BooleanField) das Backend nur verstecken, nicht sperren.
        if not backend.is_available(facility):
            raise Http404(_("Bericht für diese Einrichtung nicht verfügbar."))

        today = timezone.localdate()
        try:
            period_state = parse_statistics_period(request.GET, today)
        except InvalidStatisticsPeriodError as exc:
            return HttpResponse(str(exc), status=400)

        # Sicherheitskritisch (K-Anon, Refs #1278/T1): niemals build_dataset()
        # allein rendern — prepare_export() ist die export-paritaetische Sicht.
        result = backend.prepare_export(
            facility, backend.build_dataset(facility, period_state.date_from, period_state.date_to)
        )

        field_specs = backend.get_field_specs()
        preview_records = result.records if result.mode == "aggregate" else result.records[:10]
        preview_rows = [[record.get(spec.key) for spec in field_specs] for record in preview_records]

        context = {
            "backend": backend,
            "date_from": period_state.date_from,
            "date_to": period_state.date_to,
            "field_specs": field_specs,
            "result": result,
            "preview_rows": preview_rows,
            "preview_total": len(result.records),
            "k_anonymity_threshold": result.summary.get("k_anonymity_threshold"),
            # Quellenausweis (Refs #1743): die Vorschau ruft ``build_dataset``,
            # liest also live — anders als das hybrid gespeiste Sachbericht-PDF.
            "data_source_note": DATA_SOURCE_NOTES[LIVE],
            "data_source_explanation": DATA_SOURCE_EXPLANATIONS[LIVE],
            # K3-A3 (Refs #1770): Die Vorschau zeigt die tabellarische
            # Projektion des Backends — ihre Grundgesamtheit gehoert dazu.
            # ``backend.population`` ist der vom Backend deklarierte Code
            # (Default: alle Ereignisse des Zeitraums).
            "population_note": table_note(backend.population, LIVE),
            # K4a (Refs #1770): Empfaenger/Zweck aus dem Berichte-Formular in die
            # Export-Links durchreichen — nur so erreichen sie das
            # EXPORT-Audit-Detail. ``quote`` verhindert, dass ein Ampersand in
            # "Stadt X & Kreis Y" den Querystring zerlegt; das Ergebnis geht als
            # **fertiges** Fragment ins Template, damit dort keine
            # Escaping-Entscheidung mehr zu treffen ist.
            # ``artifact_released=False``: hier wird nichts herausgegeben, die
            # Vorschau reicht nur Empfaenger/Zweck an die Download-Links weiter.
            "release_query": "".join(
                f"&{key}={quote(value)}" for key, value in release_detail(request.GET, artifact_released=False).items()
            ),
        }
        return render(request, self.template_name, context)


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
        releases = build_release_overview(request.current_facility)
        return render(
            request,
            self.template_name,
            {
                "releases": releases,
                "overlap_count": sum(1 for release in releases if release.overlaps),
            },
        )


class DatasetExportView(LeadOrAdminRequiredMixin, View):
    """Generischer Berichts-Download (M5 P5, Refs #1061): CSV/JSON/XML via ``export_dataset``.

    Format-/Backend-Verfuegbarkeitspruefung analog ``DatasetReportPanelView``;
    ``export_dataset`` wendet ``backend.prepare_export`` intern bereits an
    (Refs #1278) — hier kommt es nicht noch einmal zum Einsatz.
    """

    @method_decorator(ratelimit(key="user", rate="10/h", method="GET", block=True))
    def get(self, request):
        facility = request.current_facility
        date_from = parse_date(request.GET.get("date_from"), None)
        date_to = parse_date(request.GET.get("date_to"), None)
        if not date_from or not date_to:
            return HttpResponse(_("date_from und date_to erforderlich"), status=400)
        # Refs #1589: gleiche Range-Validierung wie die uebrigen Export-Views.
        try:
            validate_statistics_range(date_from, date_to)
        except InvalidStatisticsPeriodError as exc:
            return HttpResponse(str(exc), status=400)

        slug = request.GET.get("backend", "").strip()
        try:
            backend = get_backend(slug)
        except KeyError:
            raise Http404(_("Unbekanntes Berichts-Backend.")) from None
        # Truthiness wie im Panel/in der Registry — siehe DatasetReportPanelView.
        if not backend.is_available(facility):
            raise Http404(_("Bericht für diese Einrichtung nicht verfügbar."))

        fmt = request.GET.get("format", "")
        if fmt not in SUPPORTED_FORMATS:
            return HttpResponse(_("Unbekanntes Export-Format."), status=400)

        response = export_dataset(backend, facility, date_from, date_to, format=fmt)

        log_audit_event(
            request,
            AuditLog.Action.EXPORT,
            target_type="DatasetExport",
            detail={
                "backend": slug,
                "format": fmt,
                "date_from": str(date_from),
                "date_to": str(date_to),
                **release_detail(request.GET, artifact_released=True),
            },
        )

        return response
