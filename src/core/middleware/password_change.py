"""Middleware: Forces password change when must_change_password is set."""

from django.shortcuts import redirect

EXEMPT_URLS = [
    "/login/",
    "/logout/",
    "/password-change/",
    "/password-reset/",
    "/static/",
    # Refs #2025: der Offline-Login-Bootstrap holt den Schluessel-Salt direkt
    # nach dem Login per fetch(). Wuerde der Aufruf auf /password-change/
    # umgeleitet, bekaeme das Skript HTML statt JSON, fiele auf einen nativen
    # Re-Submit mit dem beim Login rotierten CSRF-Token zurueck und der erste
    # Login eines Kontos mit Zwangswechsel endete auf der 403-Seite. Dieselbe
    # Ausnahme nimmt die MFA-Middleware (Refs #573/#588).
    "/auth/offline-key-salt/",
]


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
