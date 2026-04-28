"""TOTP-Zweifaktor-Authentifizierung.

Implementiert drei Views:

* ``MFASetupView`` — QR-Code + Testcode → bestätigtes ``TOTPDevice``.
* ``MFAVerifyView`` — OTP-Eingabe direkt nach Login, markiert die Session
  als ``mfa_verified``.
* ``MFASettingsView`` — Status im Account-Bereich, Button zum
  Deaktivieren (solange nicht facility-weit erzwungen).

Die Views sind bewusst schlank und nutzen den ``django-otp``-Standard;
wir rendern den QR-Code selbst als PNG über die ``qrcode``-Lib, weil die
Standard-Admin-Integration von django-otp nicht in unseren Tailwind-Stack
passt.
"""

from __future__ import annotations

import base64
import io
import logging

import qrcode
from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_ratelimit.decorators import ratelimit

from core.models import AuditLog
from core.signals.audit import get_client_ip

logger = logging.getLogger(__name__)


def _totp_png_data_url(device: TOTPDevice) -> str:
    """Render the TOTP device's provisioning URI as a base64-encoded PNG data URL."""
    img = qrcode.make(device.config_url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _get_unconfirmed_device(user) -> TOTPDevice:
    """Return the user's pending (unconfirmed) TOTP device, creating one if needed."""
    device = TOTPDevice.objects.filter(user=user, confirmed=False).first()
    if device is None:
        device = TOTPDevice.objects.create(
            user=user,
            name=django_settings.OTP_TOTP_ISSUER,
            confirmed=False,
        )
    return device


class MFASetupView(LoginRequiredMixin, TemplateView):
    """Initial TOTP-Setup: QR-Code anzeigen und ersten Code bestätigen."""

    template_name = "auth/mfa_setup.html"

    def get(self, request, *args, **kwargs):
        if request.user.has_confirmed_totp_device:
            return redirect("mfa_settings")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        device = _get_unconfirmed_device(self.request.user)
        context["qr_data_url"] = _totp_png_data_url(device)
        # Authenticator-Apps (FreeOTP+, Google Authenticator, …) erwarten Base32 (RFC 6238/3548).
        context["secret"] = base64.b32encode(device.bin_key).decode("ascii").rstrip("=")
        context["issuer"] = django_settings.OTP_TOTP_ISSUER
        return context

    @method_decorator(ratelimit(key="user", rate="10/m", method="POST", block=True))
    def post(self, request, *args, **kwargs):
        token = (request.POST.get("token") or "").strip().replace(" ", "")
        device = _get_unconfirmed_device(request.user)
        if token and device.verify_token(token):
            device.confirmed = True
            device.save(update_fields=["confirmed"])
            request.session["mfa_verified"] = True
            # Wenn der User bisher mfa_required war, lassen wir das Flag stehen —
            # es markiert nur, dass 2FA verpflichtend ist, nicht dass es fehlt.
            facility = getattr(request.user, "facility", None)
            AuditLog.objects.create(
                facility=facility,
                user=request.user,
                action=AuditLog.Action.MFA_ENABLED,
                target_type="User",
                target_id=str(request.user.pk),
                detail={"event": "mfa_setup_confirmed"},
                ip_address=get_client_ip(request),
            )
            messages.success(request, _("Zwei-Faktor-Authentifizierung aktiviert."))
            return redirect("mfa_settings")
        messages.error(request, _("Der Code ist ungültig. Bitte erneut versuchen."))
        return self.render_to_response(self.get_context_data(token_error=True))


class MFAVerifyView(LoginRequiredMixin, TemplateView):
    """OTP-Prompt direkt nach Login, markiert Session als verifiziert."""

    template_name = "auth/mfa_login.html"

    def get(self, request, *args, **kwargs):
        if request.session.get("mfa_verified"):
            return redirect(django_settings.LOGIN_REDIRECT_URL)
        if not request.user.has_confirmed_totp_device:
            return redirect("mfa_setup")
        return super().get(request, *args, **kwargs)

    @method_decorator(ratelimit(key="user", rate="5/m", method="POST", block=True))
    def post(self, request, *args, **kwargs):
        token = (request.POST.get("token") or "").strip().replace(" ", "")
        device = TOTPDevice.objects.filter(user=request.user, confirmed=True).first()
        if device is None:
            return redirect("mfa_setup")
        if token and device.verify_token(token):
            request.session["mfa_verified"] = True
            return redirect(django_settings.LOGIN_REDIRECT_URL)
        facility = getattr(request.user, "facility", None)
        AuditLog.objects.create(
            facility=facility,
            user=request.user,
            action=AuditLog.Action.MFA_FAILED,
            target_type="User",
            target_id=str(request.user.pk),
            detail={"event": "mfa_token_invalid"},
            ip_address=get_client_ip(request),
        )
        messages.error(request, _("Der Code ist ungültig. Bitte erneut versuchen."))
        return self.render_to_response(self.get_context_data(token_error=True))


class MFASettingsView(LoginRequiredMixin, TemplateView):
    """Account-Seite für Aktivieren/Deaktivieren der 2FA."""

    template_name = "auth/mfa_settings.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["has_device"] = user.has_confirmed_totp_device
        context["is_mfa_enforced"] = user.is_mfa_enforced
        context["can_disable"] = context["has_device"] and not user.is_mfa_enforced
        return context


class MFADisableView(LoginRequiredMixin, View):
    """Deaktiviert 2FA für den aktuellen Nutzer, sofern nicht erzwungen."""

    def post(self, request, *args, **kwargs):
        user = request.user
        if user.is_mfa_enforced:
            messages.error(
                request,
                _("Zwei-Faktor-Authentifizierung ist für dein Konto verpflichtend."),
            )
            return redirect("mfa_settings")
        deleted, _info = TOTPDevice.objects.filter(user=user).delete()
        request.session.pop("mfa_verified", None)
        if deleted:
            facility = getattr(user, "facility", None)
            AuditLog.objects.create(
                facility=facility,
                user=user,
                action=AuditLog.Action.MFA_DISABLED,
                target_type="User",
                target_id=str(user.pk),
                detail={"event": "mfa_disabled"},
                ip_address=get_client_ip(request),
            )
            messages.success(request, _("Zwei-Faktor-Authentifizierung deaktiviert."))
        return redirect("mfa_settings")


def mfa_settings_url() -> str:
    """Helper for templates/tests: absolute URL to the MFA settings page."""
    return reverse("mfa_settings")
