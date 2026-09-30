"""Middleware: TOTP-2FA-Enforcement nach erfolgreichem Login.

Logik analog zu ForcePasswordChangeMiddleware. Zwei Fälle:

1. User ist authentifiziert, MFA ist aktiv (``is_mfa_enforced``), und es
   existiert noch kein bestätigter zweiter Faktor (TOTP oder Passkey) →
   Redirect zu ``/mfa/setup/``.
2. User hat einen bestätigten zweiten Faktor, aber die aktuelle Session ist
   noch nicht als 2FA-verifiziert markiert → Redirect zu ``/mfa/verify/``.

Die Session-Flag ``mfa_verified`` wird gesetzt, wenn der Nutzer
erfolgreich einen OTP eingibt. Sie lebt so lange wie die Session —
bei Logout oder Session-Expiry ist die 2FA-Verifikation neu nötig.
"""

from datetime import timedelta
from urllib.parse import urlencode

from django.shortcuts import redirect
from django.utils import timezone

from core.constants import MFA_SETUP_GRACE_DAYS

# Pfade, die auch bei offenem MFA-Setup/Verify erreichbar sein müssen —
# sonst gerät der Nutzer in eine Redirect-Schleife.
EXEMPT_URLS = [
    "/login/",
    "/logout/",
    "/password-change/",
    "/password-reset/",
    # Invite and unlock links are handled by their own guard: a signed-in
    # session gets a notice with the sign-out form there, also while its second
    # factor is pending, instead of a redirect to /mfa/verify/ (Refs #2391).
    # Anonymous sessions never reach the redirect below anyway.
    "/invite/",
    "/account/recovery/",
    "/static/",
    "/mfa/",
    # Passkey-Ceremony-Endpoints (ADR-032, Refs #1492): die WebAuthn-Assertion
    # muss VOR gesetztem ``mfa_verified`` erreichbar sein (analog ``/mfa/``),
    # sonst wuerde die Verify-Seite den Assertion-Fetch auf sich selbst umleiten.
    "/webauthn/",
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
                target = request.get_full_path()
                # ``next`` nur fuer GET-Ziele, die es zu bewahren lohnt. Eine
                # eigene ``/mfa/``-Ausnahme braucht es hier nicht (Refs #2237):
                # ``EXEMPT_URLS`` enthaelt ``/mfa/``, dieser Zweig ist fuer
                # solche Pfade nicht erreichbar. Das Wurzelziel bleibt ohne
                # ``next`` — es ist ohnehin der Rollen-Default, den
                # ``resolve_post_login_url`` nach dem zweiten Faktor setzt.
                if redirect_url == "/mfa/verify/" and request.method == "GET" and target != "/":
                    redirect_url = f"{redirect_url}?{urlencode({'next': target})}"
                return redirect(redirect_url)
        return self.get_response(request)

    @staticmethod
    def _required_redirect(request, user):
        # Methoden-uebergreifend (ADR-032, Refs #1492): TOTP ODER Passkey zaehlt
        # als bestaetigter zweiter Faktor.
        has_device = user.has_confirmed_mfa_device
        # Case 1: Setup needed (MFA enforced, but no device yet).
        if not has_device:
            # SOFORT-Erzwingung ohne Grace (Refs #689): bewusst gesetzter Zwang
            # (mfa_required / facility-weit) ODER privilegierte Rolle
            # (super_admin/facility_admin, wiederhergestelltes #1019-Verhalten).
            # Der Admin hat den Zwang gezielt aktiviert bzw. die Rolle ist per se
            # hochprivilegiert — eine 30-Tage-Frist waere hier ein Loch.
            if user.mfa_enforced_deliberately or user.mfa_enforced_by_role_immediate:
                return "/mfa/setup/"
            # Rollen-Default fuer die mit #689 neu erzwungenen Niedrigrechte-
            # Rollen (staff/assistant [, lead]) laeuft ueber eine 30-Tage-Grace-
            # Frist, damit Bestandsnutzer beim Aktivieren nicht ausgesperrt werden.
            if user.mfa_enforced_by_role_grace:
                deadline = user.mfa_setup_deadline
                now = timezone.now()
                if deadline is None:
                    # Erstkontakt (z.B. Session aus der Zeit vor dem Rollout, in
                    # der das Login-Stamping nicht lief): Grace-Uhr JETZT starten
                    # statt aussperren.
                    from core.models import User

                    deadline = now + timedelta(days=MFA_SETUP_GRACE_DAYS)
                    # ``user`` ist im Request als SimpleLazyObject gewrappt —
                    # daher das konkrete Modell statt ``type(user)``.
                    User.objects.filter(pk=user.pk).update(mfa_setup_deadline=deadline)
                    user.mfa_setup_deadline = deadline
                if deadline > now:
                    return None  # Grace aktiv — Zugang mit Banner
                return "/mfa/setup/"
            return None
        # Case 2: Device exists, but session not yet verified.
        if not request.session.get("mfa_verified"):
            return "/mfa/verify/"
        return None
