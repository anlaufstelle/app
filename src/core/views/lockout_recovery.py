"""Views fuer Lockout-Recovery (Refs #869).

Drei Pfade:
  - ``LockoutRecoveryRequestView`` (B2): Form fuer E-Mail-Eingabe, versendet
    Token-Mail; Anti-Enumeration via konstanter Response.
  - ``LockoutRecoverySentView``: statische Bestaetigungsseite.
  - ``LockoutRecoveryConfirmView``: Token-Validation + LOGIN_UNLOCK.
  - ``LockoutRecoveryBackupCodeView`` (C): Username + MFA-Backup-Code ->
    LOGIN_UNLOCK + BACKUP_CODES_USED.
"""

from __future__ import annotations

import logging

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.mail import send_mail
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, TemplateView, View
from django_ratelimit.decorators import ratelimit

from core.models import AuditLog, User
from core.services.audit import log_audit_event
from core.services.lockout_recovery import build_recovery_token, verify_recovery_token
from core.services.login_lockout import unlock as unlock_user
from core.services.mfa import verify_backup_code

logger = logging.getLogger(__name__)


class _RecoveryEmailForm(forms.Form):
    email = forms.EmailField(
        label=_("E-Mail-Adresse"),
        widget=forms.EmailInput(
            attrs={
                "class": "block w-full rounded-md border border-subtle bg-canvas text-[14px] text-ink px-3 py-2.5",
                "autocomplete": "email",
                "required": "required",
            }
        ),
    )


class LockoutRecoveryRequestView(FormView):
    """Form: E-Mail eingeben, Recovery-Token-Mail versenden (Anti-Enumeration)."""

    template_name = "auth/lockout_recovery_request.html"
    form_class = _RecoveryEmailForm
    success_url = reverse_lazy("core:lockout_recovery_sent")

    @method_decorator(ratelimit(key="ip", rate="5/m", method="POST", block=True))
    def post(self, request: HttpRequest, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        email = form.cleaned_data["email"]
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user is not None:
            token = build_recovery_token(user)
            confirm_path = reverse_lazy("core:lockout_recovery_confirm", kwargs={"token": token})
            confirm_url = self.request.build_absolute_uri(str(confirm_path))
            body = render_to_string(
                "auth/lockout_recovery_email.txt",
                {"user": user, "confirm_url": confirm_url},
            )
            subject = str(_("Konto entsperren — Anlaufstelle"))
            try:
                send_mail(
                    subject,
                    body,
                    settings.DEFAULT_FROM_EMAIL,
                    [email],
                    fail_silently=False,
                )
            except Exception:
                logger.exception("Recovery-Mail-Versand fehlgeschlagen")
            log_audit_event(
                self.request,
                AuditLog.Action.PASSWORD_RESET_REQUESTED,
                user=user,
                facility=getattr(user, "facility", None),
                detail={"flow": "lockout_recovery_token"},
            )
        # Anti-Enumeration: gleiche Response, egal ob die E-Mail existiert.
        return super().form_valid(form)


class LockoutRecoverySentView(TemplateView):
    template_name = "auth/lockout_recovery_sent.html"


class LockoutRecoveryConfirmView(View):
    """Token-Klick: validiert + schreibt LOGIN_UNLOCK."""

    @method_decorator(ratelimit(key="ip", rate="10/m", method="GET", block=True))
    def get(self, request: HttpRequest, token: str, *args, **kwargs) -> HttpResponse:
        user = verify_recovery_token(token)
        if user is None:
            return HttpResponse(
                render_to_string("auth/lockout_recovery_invalid.html", {}, request=request),
                status=400,
            )
        unlock_user(user, unlocked_by=None, ip_address=None, trigger="recovery_token")
        messages.success(request, _("Konto entsperrt. Sie koennen sich jetzt anmelden."))
        return HttpResponseRedirect("/login/?recovered=1")


class _BackupCodeForm(forms.Form):
    username = forms.CharField(
        label=_("Benutzername"),
        max_length=150,
        widget=forms.TextInput(
            attrs={
                "class": "block w-full rounded-md border border-subtle bg-canvas text-[14px] text-ink px-3 py-2.5",
                "autocomplete": "username",
                "required": "required",
            }
        ),
    )
    backup_code = forms.CharField(
        label=_("Backup-Code"),
        max_length=64,
        widget=forms.TextInput(
            attrs={
                "class": "block w-full rounded-md border border-subtle bg-canvas text-[14px] text-ink px-3 py-2.5",
                "autocomplete": "one-time-code",
                "required": "required",
            }
        ),
    )


class LockoutRecoveryBackupCodeView(FormView):
    """User + MFA-Backup-Code -> LOGIN_UNLOCK + BACKUP_CODES_USED (Refs #869, Var. C)."""

    template_name = "auth/lockout_recovery_backup_code.html"
    form_class = _BackupCodeForm

    @method_decorator(ratelimit(key="ip", rate="5/m", method="POST", block=True))
    def post(self, request: HttpRequest, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        username = form.cleaned_data["username"]
        code = form.cleaned_data["backup_code"]
        user = User.objects.filter(username=username, is_active=True).first()
        if user is not None and verify_backup_code(user, code):
            unlock_user(user, unlocked_by=None, ip_address=None, trigger="backup_code")
            log_audit_event(
                self.request,
                AuditLog.Action.BACKUP_CODES_USED,
                user=user,
                facility=getattr(user, "facility", None),
                detail={"flow": "lockout_recovery"},
            )
            return HttpResponseRedirect("/login/?recovered=1")

        # Anti-Enumeration: gleicher Fehler, egal ob User existiert oder Code falsch ist.
        form.add_error(None, _("Code ungueltig oder Konto nicht gefunden."))
        return self.form_invalid(form)
