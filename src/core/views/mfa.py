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

from core.constants import RATELIMIT_MUTATION
from core.models import AuditLog
from core.services.mfa import (
    BACKUP_CODES_COUNT,
    generate_backup_codes,
    remaining_backup_codes,
    verify_backup_code,
)
from core.services.sudo_mode import RequireSudoModeMixin
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
            # Backup-Codes bei Erstaktivierung automatisch ausstellen; sie
            # werden gleich auf der nächsten Seite einmalig angezeigt.
            codes = generate_backup_codes(request.user)
            request.session["mfa_backup_codes"] = codes
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
            AuditLog.objects.create(
                facility=facility,
                user=request.user,
                action=AuditLog.Action.BACKUP_CODES_GENERATED,
                target_type="User",
                target_id=str(request.user.pk),
                detail={"count": len(codes)},
                ip_address=get_client_ip(request),
            )
            messages.success(request, _("Zwei-Faktor-Authentifizierung aktiviert."))
            return redirect("mfa_backup_codes")
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
        mode = request.POST.get("mode", "totp")
        device = TOTPDevice.objects.filter(user=request.user, confirmed=True).first()
        if device is None:
            return redirect("mfa_setup")
        facility = getattr(request.user, "facility", None)

        if mode == "backup":
            # Backup-Code-Eingabe: Refs #790 — neue Codes sind 22 Zeichen
            # URL-safe Base64 (case-sensitive!). Legacy-Codes waren ``xxxx-xxxx``
            # (8 Hex-Chars + Dash) — fuer die ist Lowercase+Dash-Reinsertion
            # tolerant. Wir behandeln beide Formate hier.
            candidate = token
            # Legacy-Heuristik: rein-hex und 8-9 Zeichen -> alter Code, normalisieren.
            stripped = candidate.replace("-", "")
            if stripped and len(stripped) == 8 and all(c in "0123456789abcdefABCDEF" for c in stripped):
                candidate = f"{stripped[:4]}-{stripped[4:]}".lower()
            # Neue 22-Zeichen-Codes bleiben unveraendert (case-sensitive!).
            if candidate and verify_backup_code(request.user, candidate):
                request.session["mfa_verified"] = True
                AuditLog.objects.create(
                    facility=facility,
                    user=request.user,
                    action=AuditLog.Action.BACKUP_CODES_USED,
                    target_type="User",
                    target_id=str(request.user.pk),
                    detail={"remaining": remaining_backup_codes(request.user)},
                    ip_address=get_client_ip(request),
                )
                return redirect(django_settings.LOGIN_REDIRECT_URL)
        elif token and device.verify_token(token):
            request.session["mfa_verified"] = True
            return redirect(django_settings.LOGIN_REDIRECT_URL)

        AuditLog.objects.create(
            facility=facility,
            user=request.user,
            action=AuditLog.Action.MFA_FAILED,
            target_type="User",
            target_id=str(request.user.pk),
            detail={"event": "mfa_token_invalid", "mode": mode},
            ip_address=get_client_ip(request),
        )
        messages.error(request, _("Der Code ist ungültig. Bitte erneut versuchen."))
        return self.render_to_response(self.get_context_data(token_error=True, last_mode=mode))


class MFASettingsView(LoginRequiredMixin, TemplateView):
    """Account-Seite für Aktivieren/Deaktivieren der 2FA."""

    template_name = "auth/mfa_settings.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["has_device"] = user.has_confirmed_totp_device
        context["is_mfa_enforced"] = user.is_mfa_enforced
        context["can_disable"] = context["has_device"] and not user.is_mfa_enforced
        context["backup_codes_remaining"] = remaining_backup_codes(user)
        context["backup_codes_total"] = BACKUP_CODES_COUNT
        context["backup_codes_low"] = context["backup_codes_remaining"] <= 3
        return context


class MFABackupCodesView(LoginRequiredMixin, TemplateView):
    """Einmalige Anzeige frisch generierter Backup-Codes (Refs #588).

    Die Codes landen nach der Generierung in ``request.session["mfa_backup_codes"]``
    und werden beim ersten GET direkt wieder aus der Session entfernt. Ein
    Reload oder zweiter GET zeigt deshalb nichts mehr — bewusste Einmal-
    Ausgabe, die Codes sind an dieser Stelle Passwort-äquivalent.
    """

    template_name = "auth/mfa_backup_codes.html"

    def get(self, request, *args, **kwargs):
        codes = request.session.pop("mfa_backup_codes", None)
        if not codes:
            messages.info(
                request,
                _("Die Codes werden nur einmal angezeigt. Neue Codes lassen sich in den 2FA-Einstellungen generieren."),
            )
            return redirect("mfa_settings")
        context = self.get_context_data(codes=codes, **kwargs)
        return self.render_to_response(context)


class MFARegenerateBackupCodesView(LoginRequiredMixin, View):
    """Neue Backup-Codes erzeugen — alter Satz wird invalidiert.

    Nur per POST; TOTP-Code-Bestätigung ist im Frontend Teil des Flows
    (über den Regenerate-Dialog in mfa_settings.html).
    """

    @method_decorator(ratelimit(key="user", rate="5/m", method="POST", block=True))
    def post(self, request, *args, **kwargs):
        user = request.user
        if not user.has_confirmed_totp_device:
            return redirect("mfa_setup")
        token = (request.POST.get("token") or "").strip().replace(" ", "")
        device = TOTPDevice.objects.filter(user=user, confirmed=True).first()
        if device is None or not token or not device.verify_token(token):
            messages.error(request, _("TOTP-Code fehlt oder ist ungültig."))
            return redirect("mfa_settings")
        codes = generate_backup_codes(user)
        request.session["mfa_backup_codes"] = codes
        facility = getattr(user, "facility", None)
        AuditLog.objects.create(
            facility=facility,
            user=user,
            action=AuditLog.Action.BACKUP_CODES_REGENERATED,
            target_type="User",
            target_id=str(user.pk),
            detail={"count": len(codes)},
            ip_address=get_client_ip(request),
        )
        return redirect("mfa_backup_codes")


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class MFADisableView(LoginRequiredMixin, RequireSudoModeMixin, View):
    """Deaktiviert 2FA für den aktuellen Nutzer, sofern nicht erzwungen.

    Refs #683: ``RequireSudoModeMixin`` zwingt Re-Auth-Pruefung — eine
    gestohlene Session reicht nicht zum 2FA-Disable.
    """

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
