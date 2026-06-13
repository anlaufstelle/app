"""SudoMode-View — Re-Auth-Form fuer sensible Aktionen (Refs #683)."""

from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.models import AuditLog
from core.services.audit import log_audit_event
from core.services.security import enter_sudo, verify_totp_or_backup
from core.views.utils import safe_redirect_path


@method_decorator(ratelimit(key="user", rate="5/m", method="POST", block=True), name="post")
class SudoModeView(LoginRequiredMixin, View):
    """GET zeigt Form, POST prueft Passwort (+ ggf. 2. Faktor) + setzt SudoMode.

    Rate-Limit 5/min/User: schuetzt gegen Brute-Force des aktuellen
    Passworts ueber eine gestohlene Session.

    A3.2 (Refs #1024): Hat der User ein bestätigtes TOTP-Gerät, verlangt der
    Sudo-Mode zusätzlich einen frischen 2. Faktor (OTP oder Backup-Code) — ein
    über eine gestohlene Session erbeutetes Passwort allein schaltet sensible
    Aktionen dann nicht frei.
    """

    template_name = "auth/sudo_mode.html"

    def _render_form(self, request, next_url, status=200):
        return render(
            request,
            self.template_name,
            {"next": next_url, "needs_otp": request.user.has_confirmed_totp_device},
            status=status,
        )

    def get(self, request):
        return self._render_form(request, safe_redirect_path(request.GET.get("next")))

    def post(self, request):
        next_url = safe_redirect_path(request.POST.get("next"))
        password = request.POST.get("password", "")
        user = authenticate(request, username=request.user.username, password=password)
        if user is None or user.pk != request.user.pk:
            # S2 (Refs #1084): Fehlversuche auditieren — symmetrisch zu
            # LOGIN_FAILED/MFA_FAILED, sonst bleibt Brute-Force ueber eine
            # gestohlene Session im Audit-Trail unsichtbar.
            log_audit_event(
                request,
                AuditLog.Action.SUDO_MODE_FAILED,
                target_obj=request.user,
                detail={"factor": "password"},
            )
            messages.error(request, _("Passwort ist nicht korrekt."))
            return self._render_form(request, next_url, status=403)

        # A3.2: bei aktivem TOTP zusätzlich einen frischen 2. Faktor verlangen.
        if request.user.has_confirmed_totp_device and not verify_totp_or_backup(
            request.user, request.POST.get("otp_token", "")
        ):
            log_audit_event(
                request,
                AuditLog.Action.SUDO_MODE_FAILED,
                target_obj=request.user,
                detail={"factor": "otp"},
            )
            messages.error(request, _("Der Bestätigungscode (2FA) ist nicht korrekt."))
            return self._render_form(request, next_url, status=403)

        enter_sudo(request)
        log_audit_event(
            request,
            AuditLog.Action.SUDO_MODE_ENTERED,
            target_obj=request.user,
            detail={"next": next_url},
        )
        return redirect(next_url)
