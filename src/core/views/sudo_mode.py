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
from core.services.sudo_mode import enter_sudo
from core.views.utils import safe_redirect_path


@method_decorator(ratelimit(key="user", rate="5/m", method="POST", block=True), name="post")
class SudoModeView(LoginRequiredMixin, View):
    """GET zeigt Form, POST prueft Passwort + setzt SudoMode-Flag.

    Rate-Limit 5/min/User: schuetzt gegen Brute-Force des aktuellen
    Passworts ueber eine gestohlene Session.
    """

    template_name = "auth/sudo_mode.html"

    def get(self, request):
        return render(
            request,
            self.template_name,
            {"next": safe_redirect_path(request.GET.get("next"))},
        )

    def post(self, request):
        next_url = safe_redirect_path(request.POST.get("next"))
        password = request.POST.get("password", "")
        user = authenticate(request, username=request.user.username, password=password)
        if user is None or user.pk != request.user.pk:
            messages.error(request, _("Passwort ist nicht korrekt."))
            return render(
                request,
                self.template_name,
                {"next": next_url},
                status=403,
            )

        enter_sudo(request)
        AuditLog.objects.create(
            facility=getattr(request.user, "facility", None),
            user=request.user,
            action=AuditLog.Action.SUDO_MODE_ENTERED,
            target_type="User",
            target_id=str(request.user.pk),
            detail={"next": next_url},
        )
        return redirect(next_url)
