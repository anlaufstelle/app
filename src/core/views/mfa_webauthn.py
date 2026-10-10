"""Passkey-/WebAuthn-Ceremony-Views als ZWEITER Faktor (ADR-032, Refs #1492).

Duenne Subklassen der ``django_otp_webauthn``-Ceremony-Views. Sie kapseln die
zwei sicherheitskritischen Glue-Punkte, die die Bibliothek allein nicht kennt:

* **``mfa_verified``-Session-Flag:** Unser ``MFAEnforcementMiddleware`` gated
  ueber ``request.session["mfa_verified"]``; die Bibliothek markiert nur ueber
  django-otps ``otp_login``. Das Flag wird ausschliesslich aus einer
  erfolgreichen **Assertion** (Besitznachweis eines bestehenden Faktors,
  ``complete_auth``) abgeleitet — und NUR dann (ein Fehlschlag laesst es
  unberuehrt, sonst entstuende ein Verify-Bypass). Die **Registrierung** eines
  Passkeys setzt das Flag bewusst NICHT: sie ist kein Besitznachweis des bereits
  eingerichteten zweiten Faktors (siehe naechster Punkt).
* **Passkey nur NEBEN TOTP, nur in verifizierter Session:** Ein Passkey ist ein
  *zusaetzlicher* zweiter Faktor. Voraussetzung fuer die Registrierung ist (1)
  eine bereits 2FA-verifizierte Session (``mfa_verified``) und (2) ein
  bestaetigtes TOTP-Geraet. (1) schliesst den Zweitfaktor-Bypass: die
  ``/webauthn/``-Endpunkte sind von der MFA-Enforcement ausgenommen (die
  Assertion muss vor gesetztem ``mfa_verified`` erreichbar sein), also darf die
  Registrierung nicht ihrerseits ueber eine nur per Passwort authentifizierte,
  unverifizierte Session einen neuen Authenticator einschleusen. (2) sorgt dafuer,
  dass der Backup-Code-Recovery-Anker (an der TOTP-Einrichtung gesetzt) existiert
  und niemand passkey-only ohne Wiederherstellungspfad ausgesperrt wird.
  Besteht bereits ein Faktor, verlangt die Registrierung zusaetzlich Sudo
  (Refs #2328) — wie das Loeschen eines Passkeys.

Kein passwordless Login: ``OTP_WEBAUTHN_ALLOW_PASSWORDLESS_LOGIN`` ist False und
der ``WebAuthnBackend`` ist nicht in ``AUTHENTICATION_BACKENDS`` — die Ceremonies
laufen ausschliesslich in einer bereits per Passwort authentifizierten Session.
"""

from __future__ import annotations

from django.utils.decorators import method_decorator
from django_otp_webauthn import exceptions
from django_otp_webauthn.views import (
    BeginCredentialAuthenticationView,
    BeginCredentialRegistrationView,
    CompleteCredentialAuthenticationView,
    CompleteCredentialRegistrationView,
)
from django_ratelimit import UNSAFE

from core.constants import RATELIMIT_MUTATION
from core.models import AuditLog
from core.services.audit import log_audit_event
from core.services.security.session_rotation import rotate_session_after_privilege_gain
from core.services.security.sudo_mode import is_sudo_required
from core.utils.ratelimit import ratelimit
from core.views.auth import resolve_post_login_url


class VerifiedSessionRequiredForPasskey(exceptions.OTPWebAuthnApiError):
    """Passkey-Registrierung in unverifizierter Session abgelehnt (403).

    Schliesst den Zweitfaktor-Bypass (Refs #1492): die ``/webauthn/``-Endpunkte
    sind von der MFA-Enforcement ausgenommen. Ohne diesen Guard koennte ein
    Angreifer, der nur das Passwort des Opfers kennt (das bereits ein
    bestaetigtes TOTP-Geraet hat), in einer nur per Passwort authentifizierten,
    noch nicht verifizierten Session einen EIGENEN Authenticator registrieren
    und sich damit selbst 2FA-verifizieren.
    """

    status_code = 403
    default_code = "mfa_verification_required"


class SudoRequiredForPasskey(exceptions.OTPWebAuthnApiError):
    """Passkey-Registrierung ohne Sudo bei bestehendem Faktor abgelehnt (403).

    Refs #2328: Ein Passkey ist ein weiterer, dauerhafter zweiter Faktor —
    dieselbe Schutzhoehe wie der TOTP-Faktorwechsel (``MFASetupView``), das
    Loeschen eines Passkeys und das Abschalten der MFA. Sonst reichte ein
    entwendetes, bereits verifiziertes Sitzungs-Cookie, um dem Konto einen
    eigenen Passkey unterzuschieben. JSON statt Weiterleitung, weil die
    Zeremonie per ``fetch`` laeuft; die Einstellungsseite bietet den
    Sudo-Schritt vorab als Link an, der Fehler greift nur, wenn das Fenster
    zwischen Seitenaufbau und Klick ablaeuft.
    """

    status_code = 403
    default_code = "sudo_required"


class TOTPRequiredForPasskey(exceptions.OTPWebAuthnApiError):
    """Passkey-Registrierung ohne bestaetigtes TOTP-Geraet abgelehnt (400).

    Bewusst 400 (nicht 403): der Zugriff auf den Endpoint ist autorisiert, nur
    die fachliche Vorbedingung (TOTP zuerst) fehlt.
    """

    status_code = 400
    default_code = "totp_required"


class _RequireVerifiedSessionAndTOTPMixin:
    """Gated die Passkey-Registrierung an verifizierte Session, Sudo UND TOTP-Geraet.

    ``check_can_register`` wird von den Registrierungs-Ceremony-Views (Begin und
    Complete) in ``initial()`` aufgerufen — der Guard greift also VOR jeder
    Options-Ausgabe bzw. Persistierung. Reihenfolge bewusst: zuerst die
    Session-Verifikation (die Sicherheitsgrenze, 403), bei bestehendem Faktor
    Sudo (403, Refs #2328), dann die fachliche TOTP-Vorbedingung (400).

    Der Session-Guard bleibt neben dem Vorcheck der Bibliothek erhalten (Refs
    #2237): ``_check_user_verified`` schlaegt seit 0.10.3 zwar ebenfalls an,
    aber nur fuer bereits eingeschriebene Konten
    (``user_is_mfa_enrolled``). Ohne bestaetigtes Geraet schweigt er — dann ist
    dieser Guard die einzige Schranke vor der Registrierung. Er traegt ausserdem
    die deutsche Fehlermeldung und den App-Fehlercode.
    """

    def initial(self, request, *args, **kwargs):
        # Die Sicherheitsgrenze, VOR dem Vorcheck der Bibliothek in
        # ``super().initial()`` — der fuer Konten ohne bestaetigtes Geraet nicht
        # anschlaegt und dessen Fehlermeldung nicht die unsere ist.
        if not request.session.get("mfa_verified"):
            raise VerifiedSessionRequiredForPasskey(
                detail=(
                    "Ein Passkey kann nur in einer bereits per zweitem Faktor bestätigten Sitzung hinzugefügt werden."
                ),
            )
        # Refs #2328: Besteht schon ein Faktor, ist der Passkey ein weiterer —
        # das verlangt Sudo. Zweite Sicherheitsgrenze, NACH dem MFA-Nachweis
        # (eine nicht verifizierte Sitzung sieht nie die Sudo-Schranke) und VOR
        # der fachlichen TOTP-Vorbedingung. Ohne bestaetigten Faktor keine
        # Sudo-Pflicht.
        if request.user.has_confirmed_mfa_device and is_sudo_required(request):
            raise SudoRequiredForPasskey(
                detail=(
                    "Bitte bestätige zuerst dein Passwort und deinen zweiten Faktor: "
                    "Lade die Seite neu und wähle „Passkey hinzufügen“."
                ),
            )
        return super().initial(request, *args, **kwargs)

    def check_can_register(self):
        super().check_can_register()
        # Die Sitzungs-Verifikation wird hier bewusst NICHT erneut geprueft
        # (Refs #2237): ``check_can_register`` wird ausschliesslich aus
        # ``RegistrationCeremonyMixin.initial()`` aufgerufen — also hinter dem
        # Guard oben, der ``mfa_verified`` bereits erzwungen hat. Eine zweite
        # Pruefung waere unerreichbar; ``test_mfa_guard_reachability.py`` haelt
        # diese Reihenfolge fest.
        # Fachliche Vorbedingung: Passkey nur NEBEN TOTP.
        if not self.request.user.has_confirmed_totp_device:
            raise TOTPRequiredForPasskey(
                detail="Ein Passkey kann erst nach Einrichtung der Authenticator-App (TOTP) hinzugefügt werden.",
            )


@method_decorator(ratelimit(key="user_or_ip", rate=RATELIMIT_MUTATION, method=UNSAFE, block=True), name="dispatch")
class WebAuthnAuthenticationBeginView(BeginCredentialAuthenticationView):
    """Bound authentication options before the upstream parser and challenge storage."""


@method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method=UNSAFE, block=True), name="dispatch")
class WebAuthnRegistrationBeginView(_RequireVerifiedSessionAndTOTPMixin, BeginCredentialRegistrationView):
    """Registrierungs-Options — nur in verifizierter Session mit bestaetigtem TOTP-Geraet."""


@method_decorator(
    ratelimit(
        key="user",
        rate=RATELIMIT_MUTATION,
        method=UNSAFE,
        block=True,
        group="core.views.mfa_webauthn.WebAuthnRegistrationCompleteView.post",
    ),
    name="dispatch",
)
class WebAuthnRegistrationCompleteView(_RequireVerifiedSessionAndTOTPMixin, CompleteCredentialRegistrationView):
    """Registrierung abschliessen und auditieren.

    Setzt bewusst KEIN ``mfa_verified`` — die Registrierung laeuft ohnehin nur in
    einer bereits verifizierten Session (``check_can_register``) und ist kein
    Besitznachweis des zweiten Faktors. Das Flag aus ihr abzuleiten waere ein
    Verify-Bypass (Refs #1492).
    """

    def post(self, *args, **kwargs):
        response = super().post(*args, **kwargs)
        # Nur bei Erfolg (2xx) auditieren; Fehlerpfade der Basisklasse liefern
        # 4xx JsonResponses und werden nicht als Registrierung auditiert.
        if 200 <= response.status_code < 300:
            log_audit_event(
                self.request,
                AuditLog.Action.WEBAUTHN_REGISTERED,
                target_obj=self.request.user,
                detail={"event": "webauthn_registered"},
                facility=getattr(self.request.user, "facility", None),
            )
        return response


@method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method=UNSAFE, block=True), name="dispatch")
class WebAuthnAuthenticationCompleteView(CompleteCredentialAuthenticationView):
    """Assertion abschliessen — setzt ``mfa_verified`` und auditiert Erfolg/Fehler.

    ``complete_auth`` wird ausschliesslich nach erfolgreicher, verifizierter
    Assertion aufgerufen (nach ``authenticate_complete`` + ``check_device_usable``).
    Genau dort — und nur dort — heben wir die Session auf 2FA-verifiziert an.
    """

    def dispatch(self, request, *args, **kwargs):
        # Parsing happens before post(); remember whether this was an assertion.
        self._assertion_started = request.method == "POST" and bool(
            request.session.get("otp_webauthn_authentication_state")
        )
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        return resolve_post_login_url(self.request, self.get_redirect_url())

    def complete_auth(self, device):
        super().complete_auth(device)
        # Refs #2034: Session-ID NACH ``super()`` rotieren — ``otp_login`` hat
        # dort gerade ``otp_device_id`` gestempelt, und ``cycle_key()`` traegt
        # es in die neue Sitzung mit. Rotation vor dem Flag, damit ein vor der
        # Assertion entwendetes Cookie die verifizierte Sitzung nicht erbt.
        rotate_session_after_privilege_gain(self.request)
        log_audit_event(
            self.request,
            AuditLog.Action.MFA_VERIFIED,
            target_obj=self.request.user,
            detail={"mode": "passkey"},
            facility=getattr(self.request.user, "facility", None),
        )
        self.request.session["mfa_verified"] = True

    # Requests without an authentication ceremony are not factor failures.
    _NON_ASSERTION_ERRORS = (
        exceptions.InvalidState,
        exceptions.MalformedRequest,
        exceptions.NotAuthenticated,
        exceptions.PasswordlessLoginDisabled,
    )

    def handle_exception(self, exc):
        interrupted = isinstance(exc, exceptions.MalformedRequest) and self._assertion_started
        if interrupted:
            # A malformed completion consumes the challenge just like an invalid
            # cryptographic assertion; retrying requires a fresh ceremony.
            self.request.session.pop("otp_webauthn_authentication_state", None)
            self.request.session.save()
        if (
            isinstance(exc, exceptions.OTPWebAuthnApiError)
            and (interrupted or not isinstance(exc, self._NON_ASSERTION_ERRORS))
            and getattr(self.request.user, "is_authenticated", False)
        ):
            log_audit_event(
                self.request,
                AuditLog.Action.WEBAUTHN_FAILED,
                target_obj=self.request.user,
                detail={"event": "webauthn_assertion_failed", "code": exc.code},
                facility=getattr(self.request.user, "facility", None),
            )
        return super().handle_exception(exc)
