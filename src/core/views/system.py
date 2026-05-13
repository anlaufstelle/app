"""Views for the installation-wide system area (Refs #867).

Operates outside the per-Facility scope: ``request.current_facility`` is
``None`` for super_admin sessions and MUST NOT be used as filter. The
underlying :class:`AuditLog` queries cross all facilities — RLS-bypass
runs through the ``app.is_super_admin`` Postgres-Setting set by
:class:`core.middleware.facility_scope.FacilityScopeMiddleware` (see
Migration 0085).

Every dispatch into one of these views writes a
``AuditLog.Action.SYSTEM_VIEW`` entry (DSGVO-Rechenschaftspflicht): the
super_admin is a hosting persona without facility context, so each
cross-facility lookup is logged with ``facility=None``.
"""

import csv
import json
import logging
import os
from datetime import date, datetime
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Max, Min, Q
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView
from django_ratelimit.decorators import ratelimit

from core.constants import AUDIT_PAGE_SIZE, DEFAULT_PAGE_SIZE, RATELIMIT_MUTATION
from core.models import AuditLog, Facility, Organization
from core.models.retention import LegalHold, RetentionProposal
from core.models.user import User
from core.services import login_lockout, system_health
from core.services.vvt import get_processing_activities
from core.signals.audit import _set_session_vars, get_client_ip
from core.utils.formatting import parse_date
from core.views.mixins import HTMXPartialMixin, PaginatedListMixin, SuperAdminRequiredMixin

logger = logging.getLogger(__name__)


class SystemAuditMixin(SuperAdminRequiredMixin):
    """Audit-Wrapper fuer alle ``/system/``-Views.

    Schreibt pro Aufruf einen ``AuditLog.Action.SYSTEM_VIEW``-Eintrag mit
    ``facility=None`` und setzt vor dem INSERT die Postgres-Session-
    Variablen so, dass die WITH-CHECK-Policy (Migration 0085) den
    facility-NULL-Eintrag durchlaesst — vgl.
    :func:`core.signals.audit._set_session_vars`.

    Reihenfolge:

    1. ``SuperAdminRequiredMixin.dispatch`` (via super) prueft Login und
       Rolle. Anonyme User landen am Login-Redirect, normale User am
       403, bevor wir hier den Audit-Eintrag schreiben — wir loggen also
       nur autorisierte System-Zugriffe.
    2. Bei autorisiertem Zugriff: Session-Vars setzen, AuditLog
       schreiben, dann zum eigentlichen View-Code.
    """

    def dispatch(self, request, *args, **kwargs):
        # Erst Auth/Role-Check via SuperAdminRequiredMixin laufen lassen.
        # Bei nicht-autorisierten Requests gibt es keinen Audit-Eintrag.
        if not (request.user.is_authenticated and request.user.is_super_admin):
            return super().dispatch(request, *args, **kwargs)

        # Autorisierter super_admin -> Zugriff loggen. ``facility=None``
        # ist ein System-Event; die WITH-CHECK-Policy aus Migration 0083
        # erlaubt INSERT mit NULL-facility. Die Session-Variable
        # ``app.is_super_admin`` ist via Middleware bereits gesetzt;
        # wir refreshen sie defensiv (Cursor-Cache, parallele
        # Connections), damit der INSERT garantiert durchgeht.
        _set_session_vars(None, is_super_admin=True)
        try:
            AuditLog.objects.create(
                facility=None,
                user=request.user,
                action=AuditLog.Action.SYSTEM_VIEW,
                target_type=self.__class__.__name__,
                ip_address=get_client_ip(request),
            )
        except Exception:
            # Audit-Fehler darf den View-Flow nicht kippen — der Zugriff
            # selbst ist primaerer Use-Case (Read-Only-Sicht). Fehler im
            # Log-Insert ist ein Ops-Problem, kein User-Problem.
            logger.exception("SYSTEM_VIEW-Audit-Eintrag fehlgeschlagen")

        return super().dispatch(request, *args, **kwargs)


class SystemDashboardView(SystemAuditMixin, TemplateView):
    """Cross-facility-Dashboard fuer super_admin.

    Zeigt die Organisation, alle Einrichtungen sowie Cross-Facility-Counts
    (User pro Rolle, AuditLog-Total). Der View ist Read-Only — Mutationen
    laufen weiterhin ueber das Django-Admin (``/admin-mgmt/``).
    """

    template_name = "core/system/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Aktuelles Datenmodell geht von einer Organisation pro Installation
        # aus. ``.first()`` ist tolerant gegenueber leerer DB (z.B. fresh
        # Setup vor dem Seed) und zeigt dann ``None`` im Template.
        organization = Organization.objects.first()
        facilities = Facility.objects.select_related("organization").order_by("name")

        # User-Counts pro Rolle, in der Reihenfolge der Role-Choices.
        role_counts = []
        for value, label in User.Role.choices:
            role_counts.append(
                {
                    "value": value,
                    "label": label,
                    "count": User.objects.filter(role=value).count(),
                }
            )

        # Refs #871: Health-Card oben im Dashboard. Die Pruefungen sind
        # defensiv geschrieben — Fehler in einem Subcheck duerfen das
        # Dashboard-Render nicht kippen.
        pending = system_health.pending_migrations()
        health = {
            "db": system_health.check_database(),
            "migrations_pending": pending,
            "migrations_pending_count": len(pending),
            "disk": system_health.disk_usage(),
            "backup": system_health.last_backup_info(),
            "versions": system_health.app_versions(),
        }

        context.update(
            {
                "organization": organization,
                "facilities": facilities,
                "facilities_count": facilities.count(),
                "role_counts": role_counts,
                "total_users": User.objects.count(),
                "auditlog_total": AuditLog.objects.count(),
                "health": health,
            }
        )
        return context


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


class SystemOrganizationView(SystemAuditMixin, TemplateView):
    """Read-Only-Ansicht der Organisation und ihrer Einrichtungen.

    Aktuell ist die Organization-Verwaltung Sache des Django-Admin
    (``/admin-mgmt/``). Diese View dient super_admin als kompakte
    Uebersicht ohne Admin-UI-Overhead. Refs #867.
    """

    template_name = "core/system/organization.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        organization = Organization.objects.first()
        if organization is not None:
            facilities = organization.facilities.all().order_by("name")
        else:
            facilities = Facility.objects.none()

        context.update(
            {
                "organization": organization,
                "facilities": facilities,
                "no_organization_hint": _(
                    "Es ist noch keine Organisation angelegt. Bitte ueber die Administration einrichten."
                ),
            }
        )
        return context


# --- Sperrkonten-Uebersicht (Refs #872) -------------------------------------


class SystemLockoutListView(SystemAuditMixin, TemplateView):
    """Cross-Facility-Uebersicht der gesperrten Konten.

    Heuristik analog ``core.services.login_lockout.is_locked``: ein User
    gilt als gesperrt, wenn die Anzahl ``LOGIN_FAILED``-AuditLog-Eintraege
    seit dem letzten ``LOGIN_UNLOCK`` und innerhalb des aktiven
    ``LOCKOUT_WINDOW`` den ``LOCKOUT_THRESHOLD`` erreicht. ``super_admin``
    selbst kann sich nicht sperren — die Liste blendet die Rolle aus.

    Performance: bulk-Aggregation pro Useranzahl der Fehlversuche und
    pro User der letzte ``LOGIN_UNLOCK``-Timestamp. Damit n+1 vermieden,
    auch wenn die Installation viele User hat.
    """

    template_name = "core/system/lockouts.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        cutoff = timezone.now() - login_lockout.LOCKOUT_WINDOW

        # Pre-fetch: pro User der letzte LOGIN_UNLOCK-Timestamp. Das
        # vermeidet pro-User-Subquery innerhalb des nachgelagerten
        # ``LOGIN_FAILED``-Counts.
        last_unlocks = dict(
            AuditLog.objects.filter(action=AuditLog.Action.LOGIN_UNLOCK)
            .values_list("user_id")
            .annotate(last_ts=Max("timestamp"))
            .values_list("user_id", "last_ts")
        )

        # Bulk-Aggregation: Failed-Logins pro User im aktuellen Fenster.
        # Filter nach ``timestamp__gt=last_unlock`` machen wir per Code,
        # weil das pro-User-spezifisch ist und in einer einzigen DB-Query
        # aufwaendig auszudruecken waere.
        rows = (
            AuditLog.objects.filter(
                action=AuditLog.Action.LOGIN_FAILED,
                timestamp__gte=cutoff,
                user__isnull=False,
            )
            .values("user_id")
            .annotate(
                fail_count=Count("id"),
                last_attempt=Max("timestamp"),
            )
        )

        # Detail-Lookups (letzter Versuch + IP) pro Kandidat — nur fuer
        # die wenigen, die ueber dem Threshold liegen. Das laeuft also
        # nicht ueber alle User.
        candidate_ids = []
        candidate_data = {}
        for row in rows:
            uid = row["user_id"]
            last_unlock = last_unlocks.get(uid)
            if last_unlock is not None and row["last_attempt"] is not None and row["last_attempt"] <= last_unlock:
                # Alle Fehlversuche liegen vor dem letzten Unlock —
                # zaehlen nicht.
                continue
            # Genauer Count im post-Unlock-Fenster.
            qs = AuditLog.objects.filter(
                user_id=uid,
                action=AuditLog.Action.LOGIN_FAILED,
                timestamp__gte=cutoff,
            )
            if last_unlock is not None:
                qs = qs.filter(timestamp__gt=last_unlock)
            count = qs.count()
            if count < login_lockout.LOCKOUT_THRESHOLD:
                continue
            last_entry = qs.order_by("-timestamp").only("timestamp", "ip_address").first()
            candidate_ids.append(uid)
            candidate_data[uid] = {
                "fail_count": count,
                "last_attempt": last_entry.timestamp if last_entry else row["last_attempt"],
                "last_ip": last_entry.ip_address if last_entry else None,
            }

        # User-Daten in einem Bulk holen, super_admin ausschliessen.
        users = (
            User.objects.filter(pk__in=candidate_ids)
            .exclude(role=User.Role.SUPER_ADMIN)
            .select_related("facility")
            .order_by("username")
        )

        locked_rows = []
        for user in users:
            data = candidate_data[user.pk]
            locked_rows.append(
                {
                    "user": user,
                    "facility": user.facility,
                    "fail_count": data["fail_count"],
                    "last_attempt": data["last_attempt"],
                    "last_ip": data["last_ip"],
                }
            )

        context.update(
            {
                "locked_rows": locked_rows,
                "lockout_threshold": login_lockout.LOCKOUT_THRESHOLD,
                "lockout_window_minutes": int(login_lockout.LOCKOUT_WINDOW.total_seconds() // 60),
            }
        )
        return context


class SystemUnlockView(SystemAuditMixin, View):
    """POST-Handler: hebt die Sperre eines Users auf (Refs #872).

    Schreibt einen ``LOGIN_UNLOCK``-AuditLog-Eintrag mit dem aktuellen
    super_admin als ``unlocked_by``. Anschliessend Redirect zur Liste.
    """

    http_method_names = ["post"]

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True))
    def post(self, request):
        username = request.POST.get("username", "").strip()
        if not username:
            messages.error(request, _("Kein Benutzername uebergeben."))
            return redirect("core:system_lockout_list")

        user = User.objects.filter(username=username).exclude(role=User.Role.SUPER_ADMIN).first()
        if user is None:
            messages.error(request, _("Benutzer nicht gefunden."))
            return redirect("core:system_lockout_list")

        # Session-Vars setzen, damit der RLS-WITH-CHECK greift — der
        # AuditLog wird mit der ``facility`` des Users geschrieben (oder
        # NULL). Analog zum SystemAuditMixin nutzen wir den Bypass.
        _set_session_vars(getattr(user, "facility", None), is_super_admin=True)
        login_lockout.unlock(user, unlocked_by=request.user, ip_address=get_client_ip(request))
        messages.success(request, _("Sperre fuer '%(username)s' aufgehoben.") % {"username": user.username})
        return redirect("core:system_lockout_list")


# --- AuditLog-Export (Refs #873) --------------------------------------------


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
        AuditLog.objects.create(
            facility=None,
            user=request.user,
            action=AuditLog.Action.AUDIT_EXPORT,
            target_type="AuditLog",
            ip_address=get_client_ip(request),
            detail={
                "format": export_format,
                "filter_count": row_count,
                "filters": applied_filters,
            },
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


# --- Maintenance-Mode-Toggle (Refs #874) ------------------------------------


class SystemMaintenanceView(SystemAuditMixin, View):
    """Wartungsmodus aktivieren/deaktivieren ueber den Systembereich.

    GET zeigt den aktuellen Status (``flag_path`` existiert?). POST mit
    ``action=enable|disable`` mutiert die Flag-Datei. Wenn
    ``MAINTENANCE_FLAG_FILE`` nicht konfiguriert ist, wird ein Hinweis
    angezeigt — Toggle ist dann nicht moeglich.
    """

    template_name = "core/system/maintenance.html"

    def get(self, request):
        return render(request, self.template_name, self._build_context())

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True))
    def post(self, request):
        flag_path = getattr(settings, "MAINTENANCE_FLAG_FILE", None)
        if not flag_path:
            messages.error(
                request,
                _(
                    "MAINTENANCE_FLAG_FILE ist nicht konfiguriert. "
                    "Setze die Umgebungsvariable, um den Wartungsmodus zu nutzen."
                ),
            )
            return redirect("core:system_maintenance")

        action = request.POST.get("action", "")
        if action == "enable":
            note = request.POST.get("note", "").strip()
            try:
                # ``open(..., "w")`` ueberschreibt eine evtl. existierende
                # Datei — gewollt, falls jemand die Notiz aktualisiert.
                with open(flag_path, "w", encoding="utf-8") as fh:
                    fh.write(note)
            except OSError:
                logger.exception("Maintenance-Flag konnte nicht geschrieben werden: %s", flag_path)
                messages.error(request, _("Wartungsmodus konnte nicht aktiviert werden (siehe Server-Log)."))
                return redirect("core:system_maintenance")

            _set_session_vars(None, is_super_admin=True)
            AuditLog.objects.create(
                facility=None,
                user=request.user,
                action=AuditLog.Action.MAINTENANCE_ENABLED,
                target_type="MaintenanceMode",
                ip_address=get_client_ip(request),
                detail={"note": note},
            )
            messages.success(request, _("Wartungsmodus aktiviert."))
            return redirect("core:system_maintenance")

        if action == "disable":
            try:
                if os.path.exists(flag_path):
                    os.remove(flag_path)
            except OSError:
                logger.exception("Maintenance-Flag konnte nicht entfernt werden: %s", flag_path)
                messages.error(request, _("Wartungsmodus konnte nicht deaktiviert werden (siehe Server-Log)."))
                return redirect("core:system_maintenance")

            _set_session_vars(None, is_super_admin=True)
            AuditLog.objects.create(
                facility=None,
                user=request.user,
                action=AuditLog.Action.MAINTENANCE_DISABLED,
                target_type="MaintenanceMode",
                ip_address=get_client_ip(request),
                detail={},
            )
            messages.success(request, _("Wartungsmodus deaktiviert."))
            return redirect("core:system_maintenance")

        messages.error(request, _("Unbekannte Aktion."))
        return redirect("core:system_maintenance")

    def _build_context(self) -> dict:
        flag_path = getattr(settings, "MAINTENANCE_FLAG_FILE", None)
        is_active = bool(flag_path) and os.path.exists(flag_path)
        activated_at = None
        note = ""
        if is_active:
            try:
                stat = os.stat(flag_path)
                activated_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.get_current_timezone())
            except OSError:
                activated_at = None
            try:
                with open(flag_path, encoding="utf-8") as fh:
                    note = fh.read().strip()
            except OSError:
                note = ""
        return {
            "flag_path": flag_path,
            "is_active": is_active,
            "activated_at": activated_at,
            "note": note,
            "configured": bool(flag_path),
        }


# --- Cross-Facility-Retention-Uebersicht (Refs #875) ------------------------


class SystemRetentionView(SystemAuditMixin, TemplateView):
    """Cross-Facility-Aggregation der ``RetentionProposal``-Statistik.

    Zeigt pro Einrichtung die Anzahl Vorschlaege je Status, das naechste
    Faelligkeitsdatum (``min(deletion_due_at)`` der PENDING) und die Zahl
    bereits ueberfaelliger PENDING-Vorschlaege. Read-Only — Aktionen
    laufen weiterhin im Facility-Kontext (``/retention/``).

    Datenfluss: ein einziger ``GROUP BY``-Query mit ``Count(case=...)``-
    Aggregation pro Status. Das skaliert gut, solange das Volumen klein
    bleibt (selten >1000 Proposals pro Facility) — bei groesserem Volumen
    waere ein dedicated dashboard-aggregations-table sinnvoll.

    Der RLS-Bypass laeuft ueber ``app.is_super_admin='true'`` (Migration
    0085) und ist im :class:`SystemAuditMixin` bereits gesetzt.
    """

    template_name = "core/system/retention.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        today = date.today()

        # Eine GROUP-BY-Query pro Facility liefert Counts je Status,
        # plus min(deletion_due_at) ueber alle PENDING und Count der
        # ueberfaelligen PENDING. ``Count(filter=...)`` ist seit Django
        # 2.0 verfuegbar — wir bauen damit eine Pivot-Tabelle direkt im
        # ORM, ohne pro Status eine eigene Subquery zu brauchen.
        rows_qs = RetentionProposal.objects.values("facility_id").annotate(
            count_pending=Count("id", filter=Q(status=RetentionProposal.Status.PENDING)),
            count_approved=Count("id", filter=Q(status=RetentionProposal.Status.APPROVED)),
            count_held=Count("id", filter=Q(status=RetentionProposal.Status.HELD)),
            count_deferred=Count("id", filter=Q(status=RetentionProposal.Status.DEFERRED)),
            count_rejected=Count("id", filter=Q(status=RetentionProposal.Status.REJECTED)),
            next_due_date=Min(
                "deletion_due_at",
                filter=Q(status=RetentionProposal.Status.PENDING),
            ),
            overdue_count=Count(
                "id",
                filter=Q(
                    status=RetentionProposal.Status.PENDING,
                    deletion_due_at__lt=today,
                ),
            ),
        )

        # Aus der Aggregation einen Lookup nach facility_id bauen, dann
        # ueber alle Facilities iterieren — auch jene ohne Proposals
        # tauchen so mit Nullen auf. Das ist explizit gewollt, um zu
        # zeigen, dass dort schlicht nichts ansteht.
        agg_by_facility = {row["facility_id"]: row for row in rows_qs}
        facilities = Facility.objects.select_related("organization").order_by("name")

        rows = []
        totals = {
            "count_pending": 0,
            "count_approved": 0,
            "count_held": 0,
            "count_deferred": 0,
            "count_rejected": 0,
            "overdue_count": 0,
        }
        critical_count = 0
        for facility in facilities:
            agg = agg_by_facility.get(facility.pk, {})
            row = {
                "facility": facility,
                "count_pending": agg.get("count_pending", 0) or 0,
                "count_approved": agg.get("count_approved", 0) or 0,
                "count_held": agg.get("count_held", 0) or 0,
                "count_deferred": agg.get("count_deferred", 0) or 0,
                "count_rejected": agg.get("count_rejected", 0) or 0,
                "next_due_date": agg.get("next_due_date"),
                "overdue_count": agg.get("overdue_count", 0) or 0,
            }
            row["is_critical"] = row["overdue_count"] > 0
            if row["is_critical"]:
                critical_count += 1
            for key in totals:
                totals[key] += row[key]
            rows.append(row)

        context.update(
            {
                "rows": rows,
                "totals": totals,
                "critical_count": critical_count,
                "today": today,
            }
        )
        return context


# --- Verzeichnis Verarbeitungstaetigkeiten (Art. 30) (Refs #876) -------------


class SystemVVTView(SystemAuditMixin, TemplateView):
    """Read-Only Verzeichnis aller Verarbeitungstaetigkeiten der Installation.

    Quelle ist die statische Konstante in
    :mod:`core.services.vvt`. MVP ohne PDF-Export — der Browser-Druck
    (mit Print-CSS-Klassen) reicht aus, um eine PDF zu erzeugen.
    Refs #876.
    """

    template_name = "core/system/vvt.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["activities"] = get_processing_activities()
        return context


# --- Cross-Facility-Legal-Hold-Uebersicht (Refs #877) -----------------------


class SystemLegalHoldListView(SystemAuditMixin, PaginatedListMixin, TemplateView):
    """Cross-Facility-Liste der ``LegalHold``-Eintraege.

    Filter:
    - ``facility``: Dropdown mit allen Einrichtungen.
    - ``status``: ``active`` (``dismissed_at IS NULL``) / ``dismissed``
      (``dismissed_at IS NOT NULL``).

    Sortierung: ``created_at DESC`` (Default). Pagination ueber
    ``PaginatedListMixin``.
    """

    template_name = "core/system/legal_holds.html"
    page_size = DEFAULT_PAGE_SIZE

    # Wir bauen GET-basiert — ein direktes ``get_context_data`` ohne
    # eigene ``get``-Methode reicht, da ``request`` ueber ``self.request``
    # erreichbar ist und der ``PaginatedListMixin`` per ``self.paginate``
    # damit umgeht.
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        request = self.request

        queryset = (
            LegalHold.objects.all().select_related("facility", "created_by", "dismissed_by").order_by("-created_at")
        )

        # Filter: facility
        facility_id = request.GET.get("facility", "")
        if facility_id:
            queryset = queryset.filter(facility_id=facility_id)

        # Filter: status (active/dismissed)
        status = request.GET.get("status", "")
        if status == "active":
            queryset = queryset.filter(dismissed_at__isnull=True)
        elif status == "dismissed":
            queryset = queryset.filter(dismissed_at__isnull=False)

        page = self.paginate(queryset, request)

        all_facilities = Facility.objects.order_by("name")

        pagination_params = urlencode({k: v for k, v in [("facility", facility_id), ("status", status)] if v})

        context.update(
            {
                "page_obj": page,
                "all_facilities": all_facilities,
                "filter_facility": facility_id,
                "filter_status": status,
                "pagination_params": pagination_params,
            }
        )
        return context
