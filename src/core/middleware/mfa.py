"""Middleware: TOTP-2FA-Enforcement nach erfolgreichem Login.

Logik analog zu ForcePasswordChangeMiddleware. Zwei Fälle:

1. User ist authentifiziert, MFA ist aktiv (``is_mfa_enforced``), und es
   existiert noch kein bestätigtes ``TOTPDevice`` → Redirect zu ``/mfa/setup/``.
2. User hat ein bestätigtes ``TOTPDevice``, aber die aktuelle Session ist
   noch nicht als 2FA-verifiziert markiert → Redirect zu ``/mfa/verify/``.

Die Session-Flag ``mfa_verified`` wird gesetzt, wenn der Nutzer
erfolgreich einen OTP eingibt. Sie lebt so lange wie die Session —
bei Logout oder Session-Expiry ist die 2FA-Verifikation neu nötig.
"""

from django.shortcuts import redirect

# Pfade, die auch bei offenem MFA-Setup/Verify erreichbar sein müssen —
# sonst gerät der Nutzer in eine Redirect-Schleife.
EXEMPT_URLS = [
    "/login/",
    "/logout/",
    "/password-change/",
    "/password-reset/",
    "/static/",
    "/mfa/",
    "/i18n/",
    "/health/",
    "/sw.js",
    "/manifest.json",
    # Post-Login Salt-Fetch für client-seitige Offline-Crypto (Refs #573/#588).
    # Der Endpoint liefert nur den User-spezifischen Salt (kein MFA-Gate nötig);
    # wenn er auf /mfa/verify/ umgeleitet würde, scheitert der auth-bootstrap
    # am HTML-Response und fällt auf native form.submit mit rotiertem CSRF
    # zurück — Ergebnis ist eine CSRF-Fehlerseite statt des MFA-Prompts.
    "/auth/offline-key-salt/",
]


class MFAEnforcementMiddleware:
    """Redirects to MFA setup or verify as long as the session lacks 2FA."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and not any(request.path.startswith(url) for url in EXEMPT_URLS):
            redirect_url = self._required_redirect(request, user)
            if redirect_url is not None:
                return redirect(redirect_url)
        return self.get_response(request)

    @staticmethod
    def _required_redirect(request, user):
        has_device = user.has_confirmed_totp_device
        # Case 1: Setup needed (user or facility requires MFA, but no device yet).
        if user.is_mfa_enforced and not has_device:
            return "/mfa/setup/"
        # Case 2: Device exists, but session not yet verified.
        if has_device and not request.session.get("mfa_verified"):
            return "/mfa/verify/"
        return None
