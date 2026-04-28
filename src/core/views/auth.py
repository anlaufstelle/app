"""Custom auth views for Anlaufstelle."""

import logging

from django.conf import settings
from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_POST
from django.views.i18n import set_language
from django_ratelimit.decorators import ratelimit

from core.models import AuditLog, User
from core.services.offline_keys import ensure_offline_key_salt
from core.signals.audit import get_client_ip

logger = logging.getLogger(__name__)


class CustomLoginView(auth_views.LoginView):
    """Login with session timeout from facility settings."""

    template_name = "auth/login.html"

    @method_decorator(ratelimit(key="ip", rate="5/m", method="POST", block=True))
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        # Load session timeout from facility settings
        user = form.get_user()
        facility = getattr(user, "facility", None)
        if facility is not None:
            try:
                timeout = facility.settings.session_timeout_minutes * 60
                self.request.session.set_expiry(timeout)
            except facility._meta.model.settings.RelatedObjectDoesNotExist:
                pass  # No settings -> default session timeout
        return response


class CustomLogoutView(auth_views.LogoutView):
    """Standard logout with redirect to login and site data clearing."""

    next_page = "/login/"

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        response["Clear-Site-Data"] = '"storage"'
        return response


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
