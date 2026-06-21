"""Cross-Facility AuditLog-Views fuer super_admin.

- :class:`SystemAuditLogListView` — paginierte Liste (Refs #867).
- :class:`SystemAuditLogDetailView` — Einzelansicht.
- :class:`SystemAuditLogExportView` — Streaming CSV/JSON-Export (Refs #873).
"""

import csv
import json
from urllib.parse import urlencode

from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import AUDIT_PAGE_SIZE, RATELIMIT_BULK_ACTION
from core.models import AuditLog, Facility
from core.models.user import User
from core.services.audit import audit_system_view
from core.services.system import _sanitize_csv_cell
from core.signals.audit import _set_session_vars
from core.utils.downloads import safe_download_response
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
        # Refs #1022 (B3): Filter-Logik zentral in ``_apply_auditlog_filters``
        # (Single Source of Truth mit dem Export). ``raw`` liefert die
        # Roh-GET-Werte fuer Context + ``pagination_params``.
        queryset, raw, _applied = _apply_auditlog_filters(request)

        page = self.paginate(queryset, request)

        # Cross-facility User- und Facility-Listen fuer Dropdowns.
        all_users = User.objects.order_by("last_name", "first_name", "username")
        all_facilities = Facility.objects.order_by("name")

        pagination_params = urlencode({k: v for k, v in raw.items() if v})

        context = {
            "page_obj": page,
            "action_choices": AuditLog.Action.choices,
            "all_users": all_users,
            "all_facilities": all_facilities,
            "facility_null_sentinel": self.FACILITY_NULL_SENTINEL,
            "filter_action": raw["action"],
            "filter_user": raw["user"],
            "filter_facility": raw["facility"],
            "filter_date_from": raw["date_from"],
            "filter_date_to": raw["date_to"],
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


def _apply_auditlog_filters(request) -> tuple:
    """Single Source of Truth fuer die AuditLog-Filter — genutzt von
    ``SystemAuditLogListView`` (Liste) und ``SystemAuditLogExportView``
    (Export). Refs #1022 (B3): vorher in beiden Consumern dupliziert.

    Returns ``(queryset, raw, applied)``:

    * ``queryset`` — gefiltertes ``AuditLog``-Queryset (``select_related``).
    * ``raw`` — dict **aller** Filter-Roh-GET-Werte (leere Strings inklusive)
      fuer Template-Context + ``pagination_params`` der Liste.
    * ``applied`` — dict nur der **tatsaechlich gesetzten** Filter (Werte
      normalisiert) fuer den Export-Audit-Eintrag (``filter_count``).
    """
    queryset = AuditLog.objects.all().select_related("user", "facility")
    raw = {
        "action": request.GET.get("action", ""),
        "user": request.GET.get("user", ""),
        "facility": request.GET.get("facility", ""),
        "date_from": request.GET.get("date_from", ""),
        "date_to": request.GET.get("date_to", ""),
    }
    applied = {}

    if raw["action"]:
        queryset = queryset.filter(action=raw["action"])
        applied["action"] = raw["action"]

    if raw["user"]:
        queryset = queryset.filter(user_id=raw["user"])
        applied["user"] = raw["user"]

    if raw["facility"] == SystemAuditLogListView.FACILITY_NULL_SENTINEL:
        queryset = queryset.filter(facility__isnull=True)
        applied["facility"] = SystemAuditLogListView.FACILITY_NULL_SENTINEL
    elif raw["facility"]:
        queryset = queryset.filter(facility_id=raw["facility"])
        applied["facility"] = raw["facility"]

    date_from = parse_date(raw["date_from"])
    if date_from:
        queryset = queryset.filter(timestamp__date__gte=date_from)
        applied["date_from"] = str(date_from)

    date_to = parse_date(raw["date_to"])
    if date_to:
        queryset = queryset.filter(timestamp__date__lte=date_to)
        applied["date_to"] = str(date_to)

    return queryset, raw, applied


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

    # Refs #1158, #1084: konservatives Ratelimit (30/h/User) auf den
    # Cross-Facility-Audit-Export — letzte Luecke im sonst dichten
    # Drossel-Netz. super_admin-only ⇒ geringe Angriffsflaeche, daher
    # konsistent mit den uebrigen Bulk-Exporten (analog DSGVO-Views).
    @method_decorator(ratelimit(key="user", rate=RATELIMIT_BULK_ACTION, method="GET", block=True))
    def get(self, request):
        export_format = request.GET.get("format", "csv").lower()
        if export_format not in ("csv", "json"):
            export_format = "csv"

        queryset, _raw, applied_filters = _apply_auditlog_filters(request)
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

        # Filename mit Timestamp + Format. Der zentrale Download-Builder setzt
        # RFC-5987-Content-Disposition (attachment) + X-Content-Type-Options:
        # nosniff — konsistent mit allen anderen Downloads (Refs #1011).
        ts = timezone.now().strftime("%Y%m%d-%H%M%S")
        if export_format == "json":
            return safe_download_response(
                f"auditlog-{ts}.json",
                "application/json",
                self._iter_json(queryset),
            )
        return safe_download_response(
            f"auditlog-{ts}.csv",
            "text/csv; charset=utf-8",
            self._iter_csv(queryset),
        )

    def _iter_csv(self, queryset):
        """Generator yielding CSV bytes for ``StreamingHttpResponse``.

        Wir wandeln das ``detail``-JSON-Feld via ``json.dumps`` zu einem
        String um — andernfalls schreibt der CSV-Writer ``{'k': 'v'}``,
        was bei Re-Import wieder geparst werden muss.

        Refs #1064: alle dynamischen Zellen laufen durch
        ``_sanitize_csv_cell`` (OWASP-Formel-Praefix-Neutralisierung),
        analog zum Events-Export (#719). Realer Vektor ist ``facility``
        (von facility_admin umbenennbar, die CSV oeffnet der
        super_admin); ``timestamp`` bleibt roh (isoformat beginnt
        strukturell mit Ziffer).
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
                    _sanitize_csv_cell(row["user"]),
                    _sanitize_csv_cell(row["action"]),
                    _sanitize_csv_cell(row["target_type"]),
                    _sanitize_csv_cell(row["target_id"]),
                    _sanitize_csv_cell(row["facility"]),
                    _sanitize_csv_cell(row["ip_address"]),
                    _sanitize_csv_cell(json.dumps(row["detail"], ensure_ascii=False)),
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
