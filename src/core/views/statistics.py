"""Views for statistics dashboard and exports."""

import logging

from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.models import AuditLog, DocumentType
from core.services.audit import log_audit_event
from core.services.export import export_events_csv, generate_jugendamt_pdf, generate_report_pdf
from core.services.snapshot import _merge_stats, get_statistics_hybrid, get_statistics_trend
from core.utils.downloads import safe_download_response
from core.utils.formatting import parse_date
from core.views.mixins import HTMXPartialMixin, LeadOrAdminRequiredMixin

logger = logging.getLogger(__name__)


class StatisticsView(LeadOrAdminRequiredMixin, HTMXPartialMixin, View):
    """Statistics dashboard with period selection."""

    template_name = "core/statistics/index.html"
    partial_template_name = "core/statistics/partials/full_content.html"

    def get(self, request):
        from core.services.snapshot import is_multi_month_range
        from core.services.statistics import parse_statistics_period

        facility = request.current_facility
        today = timezone.localdate()
        # Refs #816 (C-49): Period-Parsing zentral in der Service-Schicht.
        period_state = parse_statistics_period(request.GET, today)

        stats = get_statistics_hybrid(facility, period_state.date_from, period_state.date_to)

        context = {
            "stats": stats,
            "period": period_state.period,
            "date_from": period_state.date_from,
            "date_to": period_state.date_to,
            "can_export": True,
            "selected_year": period_state.selected_year,
            "current_year": today.year,
            "document_types": DocumentType.objects.filter(facility=facility).order_by("name"),
            # #533: unique_clients is summed across monthly snapshots, so it
            # double-counts clients seen in multiple months. Surface that fact
            # to the template so it can render a "ca." prefix + tooltip.
            "unique_clients_is_approximation": is_multi_month_range(period_state.date_from, period_state.date_to),
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

    def get(self, request):
        from core.services.statistics import parse_statistics_period

        facility = request.current_facility
        today = timezone.localdate()
        # Refs #816 (C-49): Period-Parsing zentral in der Service-Schicht.
        period_state = parse_statistics_period(request.GET, today)
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

        response = safe_download_response(
            f"export_{date_from}_{date_to}.csv",
            "text/csv; charset=utf-8",
            export_events_csv(facility, date_from, date_to, user=request.user),
        )

        log_audit_event(
            request,
            AuditLog.Action.EXPORT,
            target_type="CSV",
            detail={"format": "CSV", "date_from": str(date_from), "date_to": str(date_to)},
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
            detail={"format": "PDF", "date_from": str(date_from), "date_to": str(date_to)},
        )

        return response


class JugendamtExportView(LeadOrAdminRequiredMixin, View):
    """Youth welfare office report as PDF download."""

    @method_decorator(ratelimit(key="user", rate="10/h", method="GET", block=True))
    def get(self, request):
        facility = request.current_facility
        date_from = parse_date(request.GET.get("date_from"), None)
        date_to = parse_date(request.GET.get("date_to"), None)
        if not date_from or not date_to:
            return HttpResponse(_("date_from und date_to erforderlich"), status=400)

        pdf_bytes = generate_jugendamt_pdf(facility, date_from, date_to)

        response = safe_download_response(
            f"jugendamt_{date_from}_{date_to}.pdf",
            "application/pdf",
            pdf_bytes,
        )

        log_audit_event(
            request,
            AuditLog.Action.EXPORT,
            target_type="Jugendamt-PDF",
            detail={"format": "Jugendamt-PDF", "date_from": str(date_from), "date_to": str(date_to)},
        )

        return response
