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

Kein passwordless Login: ``OTP_WEBAUTHN_ALLOW_PASSWORDLESS_LOGIN`` ist False und
der ``WebAuthnBackend`` ist nicht in ``AUTHENTICATION_BACKENDS`` — die Ceremonies
laufen ausschliesslich in einer bereits per Passwort authentifizierten Session.
"""

from __future__ import annotations

from django.utils.decorators import method_decorator
from django_otp_webauthn import exceptions
from django_otp_webauthn.views import (
    BeginCredentialRegistrationView,
    CompleteCredentialAuthenticationView,
    CompleteCredentialRegistrationView,
)
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_MUTATION
from core.models import AuditLog
from core.services.audit import log_audit_event


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


class TOTPRequiredForPasskey(exceptions.OTPWebAuthnApiError):
    """Passkey-Registrierung ohne bestaetigtes TOTP-Geraet abgelehnt (400).

    Bewusst 400 (nicht 403): der Zugriff auf den Endpoint ist autorisiert, nur
    die fachliche Vorbedingung (TOTP zuerst) fehlt.
    """

    status_code = 400
    default_code = "totp_required"


class _RequireVerifiedSessionAndTOTPMixin:
    """Gated die Passkey-Registrierung an verifizierte Session UND TOTP-Geraet.

    ``check_can_register`` wird von den Registrierungs-Ceremony-Views (Begin und
    Complete) in ``initial()`` aufgerufen — der Guard greift also VOR jeder
    Options-Ausgabe bzw. Persistierung. Reihenfolge bewusst: zuerst die
    Session-Verifikation (die Sicherheitsgrenze, 403), dann die fachliche
    TOTP-Vorbedingung (400).
    """

    def check_can_register(self):
        super().check_can_register()
        # (1) Sicherheitsgrenze: Registrierung nur in bereits 2FA-verifizierter
        # Session — sonst waere sie ein Bypass ueber die exempt /webauthn/-Route.
        if not self.request.session.get("mfa_verified"):
            raise VerifiedSessionRequiredForPasskey(
                detail=(
                    "Ein Passkey kann nur in einer bereits per zweitem Faktor bestätigten Sitzung hinzugefügt werden."
                ),
            )
        # (2) Fachliche Vorbedingung: Passkey nur NEBEN TOTP.
        if not self.request.user.has_confirmed_totp_device:
            raise TOTPRequiredForPasskey(
                detail="Ein Passkey kann erst nach Einrichtung der Authenticator-App (TOTP) hinzugefügt werden.",
            )


class WebAuthnRegistrationBeginView(_RequireVerifiedSessionAndTOTPMixin, BeginCredentialRegistrationView):
    """Registrierungs-Options — nur in verifizierter Session mit bestaetigtem TOTP-Geraet."""


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
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


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class WebAuthnAuthenticationCompleteView(CompleteCredentialAuthenticationView):
    """Assertion abschliessen — setzt ``mfa_verified`` und auditiert Erfolg/Fehler.

    ``complete_auth`` wird ausschliesslich nach erfolgreicher, verifizierter
    Assertion aufgerufen (nach ``authenticate_complete`` + ``check_device_usable``).
    Genau dort — und nur dort — heben wir die Session auf 2FA-verifiziert an.
    """

    def complete_auth(self, device):
        super().complete_auth(device)
        # Erfolg wird — analog zum TOTP-Verify — nicht eigens auditiert; nur das
        # Anheben der Session auf 2FA-verifiziert ist hier sicherheitskritisch.
        self.request.session["mfa_verified"] = True

    # Vor-Verifikations-Fehler (kein begonnener Ablauf / kaputtes JSON / fehlende
    # Auth) sind kein fehlgeschlagener zweiter Faktor und werden nicht als
    # WEBAUTHN_FAILED auditiert — sonst rauschte jeder verwaiste Complete-Aufruf
    # in den Audit-Trail.
    _NON_ASSERTION_ERRORS = (
        exceptions.InvalidState,
        exceptions.MalformedRequest,
        exceptions.NotAuthenticated,
        exceptions.PasswordlessLoginDisabled,
    )

    def handle_exception(self, exc):
        # Echte Assertion-Fehler auditieren (symmetrisch zu MFA_FAILED), damit
        # Brute-Force ueber eine gestohlene Session sichtbar bleibt.
        if (
            isinstance(exc, exceptions.OTPWebAuthnApiError)
            and not isinstance(exc, self._NON_ASSERTION_ERRORS)
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
