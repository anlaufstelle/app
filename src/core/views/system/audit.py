"""Cross-Facility AuditLog-Views fuer super_admin.

- :class:`SystemAuditLogListView` — paginierte Liste (Refs #867).
- :class:`SystemAuditLogDetailView` — Einzelansicht.
- :class:`SystemAuditLogExportView` — Streaming CSV/JSON-Export (Refs #873).
"""

import csv
import json
from urllib.parse import urlencode

from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views import View

from core.constants import AUDIT_PAGE_SIZE
from core.models import AuditLog, Facility
from core.models.user import User
from core.services.audit import audit_system_view
from core.signals.audit import _set_session_vars
from core.utils.formatting import parse_date
from core.views.mixins import HTMXPartialMixin, PaginatedListMixin
from core.views.system.mixins import SystemAuditMixin


class SystemAuditLogListView(SystemAuditMixin, PaginatedListMixin, HTMXPartialMixin, View):
    """Cross-Facility-Audit-Log fuer super_admin.

    Im Gegensatz zur Facility-Audit-View (``core.views.audit``) wird
    *nicht* per ``for_facility`` eingeschraenkt — die RLS-Policy gibt
    super_admin alle Zeilen frei. Filter analog zur Facility-Variante,
    plus zusaetzlich ``facility``-Dropdown (mit Sentinel ``__null__``
    fuer System-Events ohne Facility-Bezug).
    """

    template_name = "core/system/auditlog_list.html"
    partial_template_name = "core/system/partials/auditlog_table.html"
    page_size = AUDIT_PAGE_SIZE

    # Sentinel-Wert fuer den Facility-Filter "NULL/System". Ein leerer
    # String steht bereits fuer "kein Filter" — daher brauchen wir einen
    # zweiten, expliziten Wert fuer den NULL-Branch.
    FACILITY_NULL_SENTINEL = "__null__"

    def get(self, request):
        queryset = AuditLog.objects.all().select_related("user", "facility")

        # Filter: action
        action = request.GET.get("action", "")
        if action:
            queryset = queryset.filter(action=action)

        # Filter: user
        user_id = request.GET.get("user", "")
        if user_id:
            queryset = queryset.filter(user_id=user_id)

        # Filter: facility (mit NULL-Sentinel fuer System-Events)
        facility_id = request.GET.get("facility", "")
        if facility_id == self.FACILITY_NULL_SENTINEL:
            queryset = queryset.filter(facility__isnull=True)
        elif facility_id:
            queryset = queryset.filter(facility_id=facility_id)

        # Filter: date_from
        date_from_str = request.GET.get("date_from", "")
        date_from = parse_date(date_from_str)
        if date_from:
            queryset = queryset.filter(timestamp__date__gte=date_from)

        # Filter: date_to
        date_to_str = request.GET.get("date_to", "")
        date_to = parse_date(date_to_str)
        if date_to:
            queryset = queryset.filter(timestamp__date__lte=date_to)

        page = self.paginate(queryset, request)

        # Cross-facility User- und Facility-Listen fuer Dropdowns.
        all_users = User.objects.order_by("last_name", "first_name", "username")
        all_facilities = Facility.objects.order_by("name")

        pagination_params = urlencode(
            {
                k: v
                for k, v in [
                    ("action", action),
                    ("user", user_id),
                    ("facility", facility_id),
                    ("date_from", date_from_str),
                    ("date_to", date_to_str),
                ]
                if v
            }
        )

        context = {
            "page_obj": page,
            "action_choices": AuditLog.Action.choices,
            "all_users": all_users,
            "all_facilities": all_facilities,
            "facility_null_sentinel": self.FACILITY_NULL_SENTINEL,
            "filter_action": action,
            "filter_user": user_id,
            "filter_facility": facility_id,
            "filter_date_from": date_from_str,
            "filter_date_to": date_to_str,
            "pagination_params": pagination_params,
        }

        return self.render_htmx_or_full(context)


class SystemAuditLogDetailView(SystemAuditMixin, View):
    """Detail-Ansicht eines AuditLog-Eintrags inkl. Facility-Spalte.

    Im Gegensatz zur Facility-Detail-View kein ``facility=...``-Filter im
    ``get_object_or_404`` — RLS-Bypass gibt super_admin alle Zeilen frei.
    """

    def get(self, request, pk):
        entry = AuditLog.objects.select_related("user", "facility").filter(pk=pk).first()
        if entry is None:
            # ``get_object_or_404`` haette die ``select_related`` verloren.
            entry = get_object_or_404(AuditLog, pk=pk)
        return render(request, "core/system/auditlog_detail.html", {"entry": entry})


# --- Export (Refs #873) -----------------------------------------------------


def _filter_auditlog_queryset(request) -> tuple:
    """Apply die gleichen Filter wie ``SystemAuditLogListView``.

    Returns ``(queryset, filter_dict)`` — das ``filter_dict`` enthaelt
    nur tatsaechlich gesetzte Filter und ist fuer den Audit-Eintrag
    (``filter_count``) gedacht.
    """
    queryset = AuditLog.objects.all().select_related("user", "facility")
    filters = {}

    action = request.GET.get("action", "")
    if action:
        queryset = queryset.filter(action=action)
        filters["action"] = action

    user_id = request.GET.get("user", "")
    if user_id:
        queryset = queryset.filter(user_id=user_id)
        filters["user"] = user_id

    facility_id = request.GET.get("facility", "")
    if facility_id == SystemAuditLogListView.FACILITY_NULL_SENTINEL:
        queryset = queryset.filter(facility__isnull=True)
        filters["facility"] = "__null__"
    elif facility_id:
        queryset = queryset.filter(facility_id=facility_id)
        filters["facility"] = facility_id

    date_from = parse_date(request.GET.get("date_from", ""))
    if date_from:
        queryset = queryset.filter(timestamp__date__gte=date_from)
        filters["date_from"] = str(date_from)

    date_to = parse_date(request.GET.get("date_to", ""))
    if date_to:
        queryset = queryset.filter(timestamp__date__lte=date_to)
        filters["date_to"] = str(date_to)

    return queryset, filters


def _audit_export_row(entry: AuditLog) -> dict:
    """Map a single ``AuditLog`` row to the export schema."""
    return {
        "timestamp": entry.timestamp.isoformat(),
        "user": entry.user.username if entry.user else "",
        "action": entry.action,
        "target_type": entry.target_type,
        "target_id": entry.target_id,
        "facility": entry.facility.name if entry.facility else "System",
        "ip_address": entry.ip_address or "",
        "detail": entry.detail or {},
    }


class _CsvEcho:
    """csv.writer-kompatibler Stream-Buffer.

    ``csv.writer`` ruft ``write()`` auf einem File-Like Objekt auf und
    erwartet keinen Rueckgabewert. Hier geben wir die zuletzt geschriebene
    Zeile zurueck, damit ``StreamingHttpResponse`` einen Iterator
    konsumieren kann.
    """

    def write(self, value):
        return value


class SystemAuditLogExportView(SystemAuditMixin, View):
    """Streaming CSV/JSON Export des Cross-Facility-AuditLogs.

    Vor dem Streaming wird ein ``AUDIT_EXPORT``-AuditLog-Eintrag
    geschrieben (DSGVO-Spur, da der Export potentiell qualifizierte
    Daten enthaelt). Format wird per ``?format=csv|json`` ausgewaehlt.
    """

    FIELDS = ["timestamp", "user", "action", "target_type", "target_id", "facility", "ip_address", "detail"]

    def get(self, request):
        export_format = request.GET.get("format", "csv").lower()
        if export_format not in ("csv", "json"):
            export_format = "csv"

        queryset, applied_filters = _filter_auditlog_queryset(request)
        # Stabile Reihenfolge fuer den Export — neueste zuerst, analog
        # zur Liste.
        queryset = queryset.order_by("-timestamp")
        # ``count()`` ist eine extra Query, aber harmlos (Index auf
        # timestamp). Die Zahl landet im Audit-Eintrag.
        try:
            row_count = queryset.count()
        except Exception:  # pragma: no cover — Defensiv: Count-Fehler
            row_count = -1

        # AuditLog-Eintrag VOR dem Streaming. Damit ist der Export auch
        # dann auditiert, wenn der Stream mittendrin abbricht.
        _set_session_vars(None, is_super_admin=True)
        audit_system_view(
            request,
            AuditLog.Action.AUDIT_EXPORT,
            target_type="AuditLog",
            format=export_format,
            filter_count=row_count,
            filters=applied_filters,
        )

        # Filename mit Timestamp + Format.
        ts = timezone.now().strftime("%Y%m%d-%H%M%S")
        if export_format == "json":
            response = StreamingHttpResponse(self._iter_json(queryset), content_type="application/json")
            response["Content-Disposition"] = f'attachment; filename="auditlog-{ts}.json"'
        else:
            response = StreamingHttpResponse(self._iter_csv(queryset), content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = f'attachment; filename="auditlog-{ts}.csv"'
        return response

    def _iter_csv(self, queryset):
        """Generator yielding CSV bytes for ``StreamingHttpResponse``.

        Wir wandeln das ``detail``-JSON-Feld via ``json.dumps`` zu einem
        String um — andernfalls schreibt der CSV-Writer ``{'k': 'v'}``,
        was bei Re-Import wieder geparst werden muss.
        """
        echo = _CsvEcho()
        writer = csv.writer(echo)
        # Header
        yield writer.writerow(self.FIELDS)
        for entry in queryset.iterator(chunk_size=500):
            row = _audit_export_row(entry)
            yield writer.writerow(
                [
                    row["timestamp"],
                    row["user"],
                    row["action"],
                    row["target_type"],
                    row["target_id"],
                    row["facility"],
                    row["ip_address"],
                    json.dumps(row["detail"], ensure_ascii=False),
                ]
            )

    def _iter_json(self, queryset):
        """Generator yielding JSON-Array bytes for ``StreamingHttpResponse``.

        Manuelle Kommas (statt ``json.dumps`` auf der Liste), damit
        die Antwort tatsaechlich gestreamt wird und nicht erst die
        komplette Liste materialisiert.
        """
        yield "["
        first = True
        for entry in queryset.iterator(chunk_size=500):
            chunk = json.dumps(_audit_export_row(entry), ensure_ascii=False, default=str)
            if first:
                yield chunk
                first = False
            else:
                yield "," + chunk
        yield "]"
