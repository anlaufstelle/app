"""Custom auth views for Anlaufstelle."""

import logging

from django.conf import settings
from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.decorators.http import require_POST
from django.views.i18n import set_language
from django_ratelimit.decorators import ratelimit

from core.models import AuditLog, User
from core.services.audit_hash import hmac_hash_email
from core.services.login_lockout import is_locked
from core.services.offline_keys import ensure_offline_key_salt
from core.signals.audit import get_client_ip

logger = logging.getLogger(__name__)


def _login_username_key(group, request):
    """Ratelimit-Key aus Username: lowercased + gestrippt, damit 'Alice',
    ' alice ' und 'alice' nicht als drei unabhängige Buckets zählen."""
    return (request.POST.get("username") or "").lower().strip()


class CustomLoginView(auth_views.LoginView):
    """Login with session timeout from facility settings.

    Zwei-Ebenen-Ratelimit (Refs #598 S-3):
    - IP-Limit (5/m): schützt vor klassischem Brute-Force von einer IP.
    - Username-Limit (10/h): schützt vor verteilten Angriffen auf einen Account
      (Botnet mit rotierenden IPs). Ein echter Nutzer tippt nicht 10× in einer
      Stunde das falsche Passwort — dann geht er auf Password-Reset.
    """

    template_name = "auth/login.html"

    @method_decorator(ratelimit(key="ip", rate="5/m", method="POST", block=True))
    @method_decorator(ratelimit(key=_login_username_key, rate="10/h", method="POST", block=True))
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        # Account-Lockout (Refs #612): Nach 10 Fehlversuchen in 15 Minuten
        # auch bei korrektem Passwort sperren. Der View-Level-Ratelimit
        # schützt bereits vor Spam-Versuchen, der Lockout verhindert das
        # „nach Cooldown sofort wieder durchkommen"-Muster.  Der Check greift
        # VOR super().form_valid() — an dieser Stelle hat AuthenticationForm
        # den User zwar geprüft, aber auth_login() noch nicht ausgeführt; es
        # existiert also noch keine gebundene Session, die wir revoken müssten.
        user = form.get_user()
        if is_locked(user):
            AuditLog.objects.create(
                facility=getattr(user, "facility", None),
                user=user,
                action=AuditLog.Action.LOGIN_FAILED,
                detail={
                    "message": "Login blockiert durch Account-Lockout",
                    "username": user.username,
                    "reason": "locked",
                },
                ip_address=get_client_ip(self.request),
            )
            form.add_error(
                None,
                _(
                    "Ihr Konto ist nach mehreren fehlgeschlagenen Versuchen "
                    "temporär gesperrt. Bitte später erneut versuchen oder "
                    "Administration kontaktieren."
                ),
            )
            return self.form_invalid(form)

        response = super().form_valid(form)
        # Load session timeout from facility settings
        facility = getattr(user, "facility", None)
        if facility is not None:
            try:
                timeout = facility.settings.session_timeout_minutes * 60
                self.request.session.set_expiry(timeout)
            except facility._meta.model.settings.RelatedObjectDoesNotExist:
                pass  # No settings -> default session timeout
        # 2FA: Die Session startet immer unverifiziert — die MFA-Middleware
        # leitet bei Bedarf nach /mfa/verify/ oder /mfa/setup/ weiter.
        self.request.session["mfa_verified"] = False
        return response


class CustomLogoutView(auth_views.LogoutView):
    """Standard logout with redirect to login and site data clearing."""

    next_page = "/login/"

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        response["Clear-Site-Data"] = '"storage"'
        return response


class RateLimitedPasswordResetView(auth_views.PasswordResetView):
    """Password reset with rate limiting and audit logging."""

    @method_decorator(ratelimit(key="ip", rate="5/m", method="POST", block=True))
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        """Audit each accepted reset request (Refs #598 S-9).

        Response-Verhalten bleibt identisch: gleiche Antwort egal ob die Email
        einem Account zugeordnet ist (Anti-Enumeration). Im Audit-Log wird der
        User nur eingetragen, wenn genau ein eindeutiger Treffer existiert —
        das ist für Admin-Forensik relevant, ohne PII-Enumeration via Logs.
        """
        email = form.cleaned_data.get("email", "")
        matched_user = None
        facility = None
        try:
            matched_user = User.objects.filter(email__iexact=email, is_active=True).first()
            if matched_user is not None:
                facility = getattr(matched_user, "facility", None)
        except Exception:
            # Auth-Signal-Receiver dürfen den Flow nie kippen.
            logger.exception("Password-Reset User-Lookup fehlgeschlagen")

        # Refs #791: Klartext-E-Mails im append-only AuditLog widersprechen
        # DSGVO-Datenminimierung. Stattdessen HMAC-Hash schreiben — Lookup
        # bei bekannter E-Mail bleibt moeglich (gleiche E-Mail -> gleicher
        # Hash), eingegebene Adressen leben aber nicht 24 Monate weiter.
        AuditLog.objects.create(
            facility=facility,
            user=matched_user,
            action=AuditLog.Action.PASSWORD_RESET_REQUESTED,
            target_type="User" if matched_user else "",
            target_id=str(matched_user.pk) if matched_user else "",
            detail={"email_hash": hmac_hash_email(email)} if email else {},
            ip_address=get_client_ip(self.request),
        )
        return super().form_valid(form)


class CustomPasswordChangeView(auth_views.PasswordChangeView):
    """Password change with must_change_password reset and offline-salt rotation."""

    template_name = "auth/password_change.html"
    success_url = "/"

    def form_valid(self, form):
        response = super().form_valid(form)
        # Rotate offline_key_salt: client-side keys derived with the old password
        # would not match anyway, so all old IndexedDB-encrypted records become
        # garbage that the next login will purge.
        self.request.user.must_change_password = False
        self.request.user.offline_key_salt = ""
        self.request.user.save(update_fields=["must_change_password", "offline_key_salt"])
        return response


class OfflineKeySaltView(LoginRequiredMixin, View):
    """Deliver the per-user PBKDF2 salt for client-side AES-GCM key derivation.

    The salt is the only server-side artefact of the offline encryption layer.
    POST-only because clients call it after login, not via navigation; this
    keeps it out of the browser history and lets us audit each fetch.
    """

    @method_decorator(ratelimit(key="user", rate="10/m", method="POST", block=True))
    def post(self, request, *args, **kwargs):
        salt = ensure_offline_key_salt(request.user)
        AuditLog.objects.create(
            facility=request.user.facility,
            user=request.user,
            action=AuditLog.Action.OFFLINE_KEY_FETCH,
            target_type="User",
            target_id=str(request.user.pk),
            detail={"event": "offline_key_salt_fetched"},
            ip_address=get_client_ip(request),
        )
        return JsonResponse({"salt": salt})


@require_POST
def set_user_language(request):
    """Save language preference to user model, then delegate to Django's set_language."""
    language = request.POST.get("language")
    if language and request.user.is_authenticated:
        valid_langs = [code for code, _ in settings.LANGUAGES]
        if language in valid_langs:
            User.objects.filter(pk=request.user.pk).update(preferred_language=language)
    return set_language(request)
