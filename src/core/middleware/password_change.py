"""Middleware: Forces password change when must_change_password is set."""

from django.shortcuts import redirect

EXEMPT_URLS = ["/login/", "/logout/", "/password-change/", "/password-reset/", "/static/", "/admin-mgmt/"]


class ForcePasswordChangeMiddleware:
    """Redirects to password change when the user must change their password."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            hasattr(request, "user")
            and request.user.is_authenticated
            and request.user.must_change_password
            and not any(request.path.startswith(url) for url in EXEMPT_URLS)
        ):
            return redirect("password_change")
        return self.get_response(request)
