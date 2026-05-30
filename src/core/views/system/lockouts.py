"""Sperrkonten-Uebersicht und Unlock-Aktion fuer super_admin (Refs #872)."""

from django.contrib import messages
from django.db.models import Count, Max
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_MUTATION
from core.models import AuditLog
from core.models.user import User
from core.services.security import login_lockout
from core.signals.audit import _set_session_vars, get_client_ip
from core.views.system.mixins import SystemAuditMixin


class SystemLockoutListView(SystemAuditMixin, TemplateView):
    """Cross-Facility-Uebersicht der gesperrten Konten.

    Heuristik analog ``core.services.security.login_lockout.is_locked``: ein User
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
