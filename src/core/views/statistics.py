"""Views for statistics dashboard and exports."""

import logging
from datetime import date, timedelta

from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.models import AuditLog, DocumentType
from core.services.export import export_events_csv, generate_jugendamt_pdf, generate_report_pdf
from core.services.snapshot import _merge_stats, get_statistics_hybrid, get_statistics_trend
from core.signals.audit import get_client_ip
from core.views.mixins import LeadOrAdminRequiredMixin

logger = logging.getLogger(__name__)


class StatisticsView(LeadOrAdminRequiredMixin, View):
    """Statistics dashboard with period selection."""

    def get(self, request):
        facility = request.current_facility
        period = request.GET.get("period", "month")
        today = timezone.localdate()

        if period == "custom":
            date_from = self._parse_date(request.GET.get("date_from"), today - timedelta(days=30))
            date_to = self._parse_date(request.GET.get("date_to"), today)
        elif period == "year":
            selected_year = self._parse_year(request.GET.get("year"), today.year)
            date_from = date(selected_year, 1, 1)
            date_to = today if selected_year == today.year else date(selected_year, 12, 31)
        elif period == "quarter":
            date_from = today - timedelta(days=90)
            date_to = today
        elif period == "half":
            date_from = today - timedelta(days=182)
            date_to = today
        else:  # month
            date_from = today - timedelta(days=30)
            date_to = today

        stats = get_statistics_hybrid(facility, date_from, date_to)

        context = {
            "stats": stats,
            "period": period,
            "date_from": date_from,
            "date_to": date_to,
            "can_export": True,
            "selected_year": selected_year if period == "year" else None,
            "current_year": today.year,
            "document_types": DocumentType.objects.filter(facility=facility).order_by("name"),
        }

        if request.headers.get("HX-Request"):
            return render(request, "core/statistics/partials/full_content.html", context)
        return render(request, "core/statistics/index.html", context)

    @staticmethod
    def _parse_date(value, default):
        if not value:
            return default
        try:
            return date.fromisoformat(value)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _parse_year(value, default):
        if not value:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default


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
        facility = request.current_facility
        period = request.GET.get("period", "month")
        today = timezone.localdate()

        if period == "custom":
            date_from = StatisticsView._parse_date(request.GET.get("date_from"), today - timedelta(days=30))
            date_to = StatisticsView._parse_date(request.GET.get("date_to"), today)
        elif period == "year":
            selected_year = StatisticsView._parse_year(request.GET.get("year"), today.year)
            date_from = date(selected_year, 1, 1)
            date_to = today if selected_year == today.year else date(selected_year, 12, 31)
        elif period == "quarter":
            date_from = today - timedelta(days=90)
            date_to = today
        elif period == "half":
            date_from = today - timedelta(days=182)
            date_to = today
        else:
            date_from = today - timedelta(days=30)
            date_to = today

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
        date_from = StatisticsView._parse_date(request.GET.get("date_from"), None)
        date_to = StatisticsView._parse_date(request.GET.get("date_to"), None)
        if not date_from or not date_to:
            return HttpResponse(_("date_from und date_to erforderlich"), status=400)

        response = StreamingHttpResponse(
            export_events_csv(facility, date_from, date_to, user=request.user),
            content_type="text/csv; charset=utf-8",
        )
        filename = f"export_{date_from}_{date_to}.csv"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'

        AuditLog.objects.create(
            facility=facility,
            user=request.user,
            action=AuditLog.Action.EXPORT,
            target_type="CSV",
            detail={"format": "CSV", "date_from": str(date_from), "date_to": str(date_to)},
            ip_address=get_client_ip(request),
        )

        return response


class PDFExportView(LeadOrAdminRequiredMixin, View):
    """PDF semi-annual report as download."""

    @method_decorator(ratelimit(key="user", rate="10/h", method="GET", block=True))
    def get(self, request):
        facility = request.current_facility
        date_from = StatisticsView._parse_date(request.GET.get("date_from"), None)
        date_to = StatisticsView._parse_date(request.GET.get("date_to"), None)
        if not date_from or not date_to:
            return HttpResponse(_("date_from und date_to erforderlich"), status=400)

        stats = get_statistics_hybrid(facility, date_from, date_to)
        pdf_bytes = generate_report_pdf(facility, date_from, date_to, stats)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        filename = f"bericht_{date_from}_{date_to}.pdf"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'

        AuditLog.objects.create(
            facility=facility,
            user=request.user,
            action=AuditLog.Action.EXPORT,
            target_type="PDF",
            detail={"format": "PDF", "date_from": str(date_from), "date_to": str(date_to)},
            ip_address=get_client_ip(request),
        )

        return response


class JugendamtExportView(LeadOrAdminRequiredMixin, View):
    """Youth welfare office report as PDF download."""

    @method_decorator(ratelimit(key="user", rate="10/h", method="GET", block=True))
    def get(self, request):
        facility = request.current_facility
        date_from = StatisticsView._parse_date(request.GET.get("date_from"), None)
        date_to = StatisticsView._parse_date(request.GET.get("date_to"), None)
        if not date_from or not date_to:
            return HttpResponse(_("date_from und date_to erforderlich"), status=400)

        pdf_bytes = generate_jugendamt_pdf(facility, date_from, date_to)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        filename = f"jugendamt_{date_from}_{date_to}.pdf"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'

        AuditLog.objects.create(
            facility=facility,
            user=request.user,
            action=AuditLog.Action.EXPORT,
            target_type="Jugendamt-PDF",
            detail={"format": "Jugendamt-PDF", "date_from": str(date_from), "date_to": str(date_to)},
            ip_address=get_client_ip(request),
        )

        return response
