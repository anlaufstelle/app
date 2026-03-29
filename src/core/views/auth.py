"""Custom auth views for Anlaufstelle."""

import logging

from django.contrib.auth import views as auth_views
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit

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
    """Password change with must_change_password reset."""

    template_name = "auth/password_change.html"
    success_url = "/"

    def form_valid(self, form):
        response = super().form_valid(form)
        self.request.user.must_change_password = False
        self.request.user.save(update_fields=["must_change_password"])
        return response
