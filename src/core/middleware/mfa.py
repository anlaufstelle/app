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

The decision itself is the shared policy in
:mod:`core.services.security.mfa_policy` (Refs #2568); this middleware turns
its step into a redirect and starts a missing grace period.
"""

from dataclasses import replace
from datetime import timedelta
from urllib.parse import urlencode

from django.shortcuts import redirect
from django.utils import timezone

from core.constants import MFA_SETUP_GRACE_DAYS
from core.services.security.mfa_policy import MFAStep, decide_mfa_step, read_mfa_state

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

_REDIRECTS = {MFAStep.SETUP: "/mfa/setup/", MFAStep.VERIFY: "/mfa/verify/"}


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
        """Return the MFA redirect target for *user*, or ``None`` for access.

        The decision is the shared policy (Refs #2568). The one write of this
        path happens here and nowhere in the policy: a grace-enforced account
        without a deadline gets its grace period started now (Refs #689), and
        the decision then runs on the stamped value.
        """
        now = timezone.now()
        state = read_mfa_state(user, request.session)
        if state.grace_period_missing:
            state = replace(state, setup_deadline=_start_grace_period(user, now))
        return _REDIRECTS.get(decide_mfa_step(state, now=now))


def _start_grace_period(user, now):
    """Stamp the MFA setup deadline of *user* (first enforcement contact)."""
    from core.models import User

    deadline = now + timedelta(days=MFA_SETUP_GRACE_DAYS)
    # ``user`` is wrapped in a SimpleLazyObject on the request, hence the
    # concrete model instead of ``type(user)``.
    User.objects.filter(pk=user.pk).update(mfa_setup_deadline=deadline)
    # Visible to the rest of this request (e.g. the setup reminder context).
    user.mfa_setup_deadline = deadline
    return deadline
