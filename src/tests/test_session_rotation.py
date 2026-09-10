"""Guard-Tests: Rotation der Django-Session-ID nach Rechteanhebung (Refs #2034).

Django rotiert die Session-ID (Wert des ``sessionid``-Cookies und zugleich
Primaerschluessel der ``django_session``-Zeile) nur beim Login. Jede weitere
Rechteanhebung INNERHALB derselben Sitzung muss ebenfalls rotieren, damit ein
vor der Anhebung entwendetes oder zurueckgelassenes Cookie die angehobene
Faehigkeit nicht erbt (Szenario A9).

Fuenf Rotationsorte werden hier abgesichert:

1. TOTP-Verify (``MFAVerifyView.post``, ``mode="totp"``),
2. Backup-Code-Verify (``MFAVerifyView.post``, ``mode="backup"``),
3. Setup-Abschluss (``MFABackupCodesView.post``, Zweig ``mfa_setup_pending``),
4. WebAuthn-Assertion (``WebAuthnAuthenticationCompleteView.complete_auth``),
5. Sudo-Grant (``core.services.security.sudo_mode.enter_sudo``),
6. Passwort-Reset-/Invite-Token-Uebergang (``CustomPasswordResetConfirmView``
   und die davon erbende ``InviteConfirmView``) — Ruling R-A1-10 aus
   Fix-Runde 1: Django legt das gepruefte Token in die BESTEHENDE Session und
   leitet auf den tokenfreien Pfad um; ohne Rotation kann ein vorher kopiertes
   Cookie damit das Passwort setzen.

Je Ort drei Zusagen: **rotiert** (alte ID weg, alter Cookie abgemeldet),
**erhaelt** (Fristen-, Login- und OTP-Sessiondaten ueberleben), **rotiert nicht
bei Fehlschlag** (ein Fehlversuch hebt keine Rechte an und rotiert auch nicht —
sonst waere die Rotation ein kostenloser Session-Reset fuer jeden Cookie-Dieb).

Begriff: „Session-Key" meint in ``docs/security-notes.md``/``docs/threat-model.md``
ueberwiegend den Offline-Krypto-Schluessel. Hier geht es ausschliesslich um
Djangos Session-ID (``request.session.session_key``).
"""

from __future__ import annotations

import json
from unittest import mock

import pytest
from django.conf import settings as django_settings
from django.contrib.auth.tokens import default_token_generator
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core.exceptions import DisallowedRedirect
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import totp as oath_totp

from core.middleware.session_idle import ABSOLUTE_DEADLINE_KEY, LAST_RENEWAL_KEY
from core.models import AuditLog
from core.models import EncryptedTOTPDevice as TOTPDevice
from core.services.security import (
    SUDO_SESSION_KEY,
    enter_sudo,
    generate_backup_codes,
    invite_token_generator,
)

STAFF_PASSWORD = "testpass123"
ADMIN_PASSWORD = "test-pw-123"
NEW_PASSWORD = "Frisch-Gesetzt-2026!"

# Djangos ``PasswordResetConfirmView`` legt das gepruefte Token unter diesem
# Schluessel ab (``django.contrib.auth.views.INTERNAL_RESET_SESSION_TOKEN``) und
# leitet auf den tokenfreien ``set-password``-Pfad um.
RESET_TOKEN_KEY = "_password_reset_token"
# Challenge-State der WebAuthn-Assertion (``django_otp_webauthn``).
WEBAUTHN_STATE_KEY = "otp_webauthn_authentication_state"


# --- Helfer ---------------------------------------------------------------


def _confirmed_totp(user):
    return TOTPDevice.objects.create(user=user, name="test", confirmed=True)


def _valid_token(device) -> str:
    token = oath_totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)
    return f"{token:0{device.digits}d}"


def _login(client, username: str, password: str) -> str:
    """Echter Login-POST (stempelt die Fristen-Keys) → Session-ID danach."""
    response = client.post("/login/", {"username": username, "password": password})
    assert response.status_code == 302, response.status_code
    key = client.session.session_key
    assert key
    return key


# Bewusst NICHT der Modell-Default (30 Min): nur so beweist der Vergleich, dass
# ``_session_expiry`` die Rotation ueberlebt und nicht bloss zufaellig auf
# ``SESSION_COOKIE_AGE`` zurueckfaellt (Fix-Runde 1, Befund 4).
NON_DEFAULT_TIMEOUT_MINUTES = 45


def _with_non_default_timeout(settings_obj):
    """Facility-Sitzungslaufzeit auf einen Nicht-Default-Wert setzen."""
    assert settings_obj.session_timeout_minutes != NON_DEFAULT_TIMEOUT_MINUTES
    settings_obj.session_timeout_minutes = NON_DEFAULT_TIMEOUT_MINUTES
    settings_obj.save(update_fields=["session_timeout_minutes"])
    return settings_obj


def _seed_extra_payload(client) -> None:
    """Einen fremden, app-unbekannten Session-Wert einsaeen (Befund 4).

    Ein Voll-Dict-Vergleich beweist die Erhaltung unbekannter Schluessel nur,
    wenn ueberhaupt einer da ist.
    """
    session = client.session
    session["_review_probe"] = {"nested": [1, 2, 3], "text": "unveraendert"}
    session.save()


def _mark_verified(client) -> None:
    """Session ohne TOTP-Verbrauch auf 2FA-verifiziert setzen.

    Der Verify-POST wuerde ``last_t`` des Geraets fortschreiben; ein zweiter
    Test-Schritt koennte denselben Zeitschritt-Token dann nicht mehr nutzen
    (django-otp lehnt Wiederverwendung ab). Die Session-ID bleibt dabei
    unveraendert — genau das braucht die jeweilige Vorbereitung.
    """
    session = client.session
    session["mfa_verified"] = True
    session.save()


def _session_row_exists(session_key: str) -> bool:
    return Session.objects.filter(session_key=session_key).exists()


def _client_with_stolen_cookie(session_key: str) -> Client:
    """Zweiter Client, der ausschliesslich den ALTEN Cookie-Wert traegt."""
    stolen = Client()
    stolen.cookies[django_settings.SESSION_COOKIE_NAME] = session_key
    return stolen


def _assert_old_cookie_is_dead(old_key: str, new_key: str) -> None:
    """Vollstaendiger Nachweis: ID gewechselt, Zeile weg, alter Cookie abgemeldet."""
    assert new_key != old_key, "Session-ID wurde nicht rotiert"
    assert _session_row_exists(old_key) is False, "Alte django_session-Zeile existiert noch"
    stolen = _client_with_stolen_cookie(old_key)
    response = stolen.get("/")
    assert response.status_code == 302
    assert "/login/" in response.url
    assert stolen.session.get("mfa_verified") is not True
    assert SUDO_SESSION_KEY not in stolen.session


# --- 1./2. TOTP- und Backup-Code-Verify -----------------------------------


@pytest.mark.django_db
class TestRotationAtMfaVerify:
    """``MFAVerifyView.post`` — TOTP- und Backup-Code-Zweig."""

    def test_totp_verify_rotates_session_id_and_kills_old_cookie(self, client, staff_user):
        device = _confirmed_totp(staff_user)
        old_key = _login(client, "teststaff", STAFF_PASSWORD)

        response = client.post("/mfa/verify/", {"token": _valid_token(device)})

        assert response.status_code == 302
        assert client.session.get("mfa_verified") is True
        _assert_old_cookie_is_dead(old_key, client.session.session_key)

    def test_totp_verify_preserves_session_payload(self, client, staff_user, settings_obj):
        device = _confirmed_totp(staff_user)
        _with_non_default_timeout(settings_obj)
        _login(client, "teststaff", STAFF_PASSWORD)
        _seed_extra_payload(client)
        before = dict(client.session.items())
        # Der Login stempelt beide Fristen …
        assert LAST_RENEWAL_KEY in before
        assert ABSOLUTE_DEADLINE_KEY in before
        # … und die facility-eigene Sitzungslaufzeit (Nicht-Default-Wert!).
        assert before["_session_expiry"] == NON_DEFAULT_TIMEOUT_MINUTES * 60

        response = client.post("/mfa/verify/", {"token": _valid_token(device)})

        assert response.status_code == 302
        assert response.url == "/"
        after = dict(client.session.items())
        assert after[LAST_RENEWAL_KEY] == before[LAST_RENEWAL_KEY]
        assert after[ABSOLUTE_DEADLINE_KEY] == before[ABSOLUTE_DEADLINE_KEY]
        assert after["_session_expiry"] == before["_session_expiry"]
        assert after["_auth_user_id"] == before["_auth_user_id"]
        assert after["_auth_user_backend"] == before["_auth_user_backend"]
        assert after["_auth_user_hash"] == before["_auth_user_hash"]
        assert after["_review_probe"] == {"nested": [1, 2, 3], "text": "unveraendert"}
        # Genau EIN Unterschied: das angehobene Recht.
        assert after == {**before, "mfa_verified": True}

    def test_backup_code_verify_rotates_session_id(self, client, staff_user):
        _confirmed_totp(staff_user)
        codes = generate_backup_codes(staff_user)
        old_key = _login(client, "teststaff", STAFF_PASSWORD)

        response = client.post("/mfa/verify/", {"token": codes[0], "mode": "backup"})

        assert response.status_code == 302
        assert client.session.get("mfa_verified") is True
        _assert_old_cookie_is_dead(old_key, client.session.session_key)

    def test_backup_code_verify_preserves_deadlines(self, client, staff_user, settings_obj):
        _confirmed_totp(staff_user)
        codes = generate_backup_codes(staff_user)
        _with_non_default_timeout(settings_obj)
        _login(client, "teststaff", STAFF_PASSWORD)
        _seed_extra_payload(client)
        before = dict(client.session.items())
        assert before["_session_expiry"] == NON_DEFAULT_TIMEOUT_MINUTES * 60

        response = client.post("/mfa/verify/", {"token": codes[0], "mode": "backup"})

        assert response.status_code == 302
        assert response.url == "/"
        after = dict(client.session.items())
        assert after[LAST_RENEWAL_KEY] == before[LAST_RENEWAL_KEY]
        assert after[ABSOLUTE_DEADLINE_KEY] == before[ABSOLUTE_DEADLINE_KEY]
        assert after["_session_expiry"] == before["_session_expiry"]
        assert after["_review_probe"] == before["_review_probe"]
        assert after == {**before, "mfa_verified": True}

    def test_invalid_totp_neither_rotates_nor_elevates(self, client, staff_user):
        _confirmed_totp(staff_user)
        old_key = _login(client, "teststaff", STAFF_PASSWORD)

        response = client.post("/mfa/verify/", {"token": "000000"})

        assert response.status_code == 200
        assert client.session.session_key == old_key
        assert _session_row_exists(old_key) is True
        assert client.session.get("mfa_verified") is not True
        assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.MFA_FAILED).exists()

    def test_invalid_backup_code_neither_rotates_nor_elevates(self, client, staff_user):
        _confirmed_totp(staff_user)
        generate_backup_codes(staff_user)
        old_key = _login(client, "teststaff", STAFF_PASSWORD)

        response = client.post("/mfa/verify/", {"token": "nicht-echt-nicht-echt-x", "mode": "backup"})

        assert response.status_code == 200
        assert client.session.session_key == old_key
        assert _session_row_exists(old_key) is True
        assert client.session.get("mfa_verified") is not True
        assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.MFA_FAILED).exists()


# --- 3. Setup-Abschluss (Backup-Code-Quittung) ----------------------------


@pytest.mark.django_db
class TestRotationAtMfaSetupCompletion:
    """``MFABackupCodesView.post`` — nur der Setup-Zweig hebt Rechte an."""

    def _walk_to_backup_codes(self, client, staff_user) -> None:
        client.get("/mfa/setup/")
        device = TOTPDevice.objects.get(user=staff_user, confirmed=False)
        response = client.post("/mfa/setup/", {"token": _valid_token(device)})
        assert response.url == "/mfa/backup-codes/"

    def test_setup_confirmation_rotates_session_id(self, client, staff_user):
        _login(client, "teststaff", STAFF_PASSWORD)
        self._walk_to_backup_codes(client, staff_user)
        # Die Session-ID unmittelbar VOR der Quittung ist die zu entwertende.
        old_key = client.session.session_key
        assert client.session.get("mfa_setup_pending") is True

        response = client.post("/mfa/backup-codes/", {"confirm_saved": "1"})

        assert response.status_code == 302
        assert client.session.get("mfa_verified") is True
        assert client.session.get("mfa_setup_pending") is None
        assert TOTPDevice.objects.filter(user=staff_user, confirmed=True).exists()
        _assert_old_cookie_is_dead(old_key, client.session.session_key)

    def test_setup_confirmation_preserves_deadlines(self, client, staff_user, settings_obj):
        _with_non_default_timeout(settings_obj)
        _login(client, "teststaff", STAFF_PASSWORD)
        _seed_extra_payload(client)
        self._walk_to_backup_codes(client, staff_user)
        before = dict(client.session.items())
        assert before["_session_expiry"] == NON_DEFAULT_TIMEOUT_MINUTES * 60

        response = client.post("/mfa/backup-codes/", {"confirm_saved": "1"})

        assert response.status_code == 302
        assert response.url == "/mfa/settings/"
        after = dict(client.session.items())
        assert after[LAST_RENEWAL_KEY] == before[LAST_RENEWAL_KEY]
        assert after[ABSOLUTE_DEADLINE_KEY] == before[ABSOLUTE_DEADLINE_KEY]
        assert after["_session_expiry"] == before["_session_expiry"]
        assert after["_review_probe"] == before["_review_probe"]
        assert after["_auth_user_id"] == before["_auth_user_id"]
        # Der Abbau von ``mfa_setup_pending``/``mfa_backup_codes`` gehoert zum
        # Vorgang und ueberlebt die Rotation als Abbau (nicht als Wiederkehr).
        assert "mfa_setup_pending" not in after
        assert "mfa_backup_codes" not in after

    def test_missing_confirmation_neither_rotates_nor_elevates(self, client, staff_user):
        _login(client, "teststaff", STAFF_PASSWORD)
        self._walk_to_backup_codes(client, staff_user)
        old_key = client.session.session_key

        response = client.post("/mfa/backup-codes/", {})

        assert response.status_code == 200
        assert client.session.session_key == old_key
        assert _session_row_exists(old_key) is True
        assert client.session.get("mfa_verified") is not True
        assert client.session.get("mfa_setup_pending") is True
        assert TOTPDevice.objects.filter(user=staff_user, confirmed=True).exists() is False

    def test_regenerate_acknowledgement_does_not_rotate(self, client, staff_user):
        """Gegenprobe: die Quittung im Regenerate-Flow ist KEINE Rechteanhebung.

        Das Geraet ist dort laengst bestaetigt, die Session laengst verifiziert —
        ohne Anhebung gibt es nichts zu entwerten, und eine Rotation hier wuerde
        nur ohne Nutzen Sessions umschreiben.
        """
        device = _confirmed_totp(staff_user)
        _login(client, "teststaff", STAFF_PASSWORD)
        _mark_verified(client)
        response = client.post("/mfa/backup-codes/regenerate/", {"token": _valid_token(device)})
        assert response.status_code == 302
        old_key = client.session.session_key

        client.post("/mfa/backup-codes/", {"confirm_saved": "1"})

        assert client.session.session_key == old_key
        assert client.session.get("mfa_verified") is True


# --- 4. WebAuthn-Assertion ------------------------------------------------


def _make_passkey(user, *, confirmed=True, credential_id=b"rot-cred-1"):
    from django_otp_webauthn.models import WebAuthnCredential

    return WebAuthnCredential.objects.create(
        user=user,
        name="Testschluessel",
        confirmed=confirmed,
        credential_id=credential_id,
        public_key=b"pubkey-bytes",
        aaguid="00000000-0000-0000-0000-000000000000",
        sign_count=0,
    )


def _arm_webauthn_challenge(client) -> None:
    """Begonnene Assertion simulieren: Challenge-State in die Session legen.

    Nur der State wird gesetzt — die Krypto-Ceremony selbst wird an der
    Helfer-Grenze (`WebAuthnHelper.authenticate_complete`) gemockt.
    """
    session = client.session
    session[WEBAUTHN_STATE_KEY] = {"challenge": "cmV2aWV3LWNoYWxsZW5nZQ", "require_user_verification": False}
    session.save()


@pytest.mark.django_db
class TestRotationAtWebAuthnAssertion:
    """``WebAuthnAuthenticationCompleteView`` — ueber den ECHTEN Endpunkt.

    Fix-Runde 1, Befund 2: der erste Entwurf ersetzte die komplette
    Elternmethode und arbeitete auf einer kuenstlichen, nie angemeldeten
    Session — damit war der Nachweis „alter Cookie abgemeldet" (R-A1-3) an
    diesem Ort nicht erbracht. Jetzt laeuft der echte
    ``super().complete_auth()``/``otp_login`` in einer echten, angemeldeten
    Client-Session; gemockt wird ausschliesslich die kryptografische
    Helfer-Grenze.
    """

    def test_complete_auth_rotates_and_keeps_session_usable(self, client, staff_user, settings_obj):
        credential = _make_passkey(staff_user)
        _with_non_default_timeout(settings_obj)
        old_key = _login(client, "teststaff", STAFF_PASSWORD)
        _seed_extra_payload(client)
        before = dict(client.session.items())
        # Nicht-Default-Sitzungslaufzeit aus den Facility-Settings (Befund 4).
        assert before["_session_expiry"] == NON_DEFAULT_TIMEOUT_MINUTES * 60
        _arm_webauthn_challenge(client)

        with mock.patch(
            "django_otp_webauthn.helpers.WebAuthnHelper.authenticate_complete",
            return_value=credential,
        ):
            response = client.post(
                reverse("otp_webauthn:credential-authentication-complete"),
                data="{}",
                content_type="application/json",
            )

        assert response.status_code == 200
        payload = json.loads(response.content)
        assert payload["id"] == credential.pk
        assert payload["redirect_url"] == "/"

        session = client.session
        assert session.get("mfa_verified") is True
        # ``otp_login`` hat VOR der Rotation gestempelt — der Wert muss mitwandern.
        assert session.get(DEVICE_ID_SESSION_KEY) == credential.persistent_id
        # Anmeldung, Fristen und die facility-eigene Laufzeit ueberleben.
        assert session["_auth_user_id"] == before["_auth_user_id"]
        assert session["_auth_user_backend"] == before["_auth_user_backend"]
        assert session["_auth_user_hash"] == before["_auth_user_hash"]
        assert session[LAST_RENEWAL_KEY] == before[LAST_RENEWAL_KEY]
        assert session[ABSOLUTE_DEADLINE_KEY] == before[ABSOLUTE_DEADLINE_KEY]
        assert session["_session_expiry"] == before["_session_expiry"]
        assert session["_review_probe"] == before["_review_probe"]
        # Der Challenge-State ist verbraucht (Replay-Schutz der Bibliothek).
        assert WEBAUTHN_STATE_KEY not in session
        # Der NEUE Cookie traegt eine benutzbare, verifizierte Anmeldung …
        follow_up = client.get("/")
        assert follow_up.status_code == 200
        # … und der alte ist tot.
        _assert_old_cookie_is_dead(old_key, session.session_key)

    def test_failed_assertion_with_armed_challenge_audits_without_rotating(self, client, staff_user):
        """Echter Assertion-Fehler (Fix-Runde 1, Befund 3).

        Die Ceremony laeuft bis in ``check_device_usable`` und scheitert dort an
        einem deaktivierten Credential — ``CredentialDisabled`` gehoert NICHT zu
        ``_NON_ASSERTION_ERRORS``, der ``WEBAUTHN_FAILED``-Audit-Zweig wird also
        wirklich erreicht.
        """
        credential = _make_passkey(staff_user, confirmed=False, credential_id=b"rot-cred-2")
        old_key = _login(client, "teststaff", STAFF_PASSWORD)
        _arm_webauthn_challenge(client)

        with mock.patch(
            "django_otp_webauthn.helpers.WebAuthnHelper.authenticate_complete",
            return_value=credential,
        ):
            response = client.post(
                reverse("otp_webauthn:credential-authentication-complete"),
                data="{}",
                content_type="application/json",
            )

        assert response.status_code == 403
        assert json.loads(response.content)["code"] == "credential_disabled"
        session = client.session
        assert session.session_key == old_key
        assert _session_row_exists(old_key) is True
        assert session.get("mfa_verified") is not True
        assert session.get(DEVICE_ID_SESSION_KEY) is None
        # Der Challenge-State ist trotzdem verbraucht — ``get_state`` popt und
        # speichert VOR der Verifikation (Replay-Schutz).
        assert WEBAUTHN_STATE_KEY not in session
        audit = AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.WEBAUTHN_FAILED).latest("timestamp")
        assert audit.detail == {"event": "webauthn_assertion_failed", "code": "credential_disabled"}

    def test_failed_assertion_neither_rotates_nor_elevates(self, client, staff_user):
        """Kein begonnener Ablauf → ``InvalidState`` (400), Session unveraendert."""
        old_key = _login(client, "teststaff", STAFF_PASSWORD)

        response = client.post(
            reverse("otp_webauthn:credential-authentication-complete"),
            data="{}",
            content_type="application/json",
        )

        assert response.status_code == 400
        assert client.session.session_key == old_key
        assert _session_row_exists(old_key) is True
        assert client.session.get("mfa_verified") is not True


# --- 5. Sudo-Grant --------------------------------------------------------


@pytest.mark.django_db
class TestRotationAtSudoGrant:
    """``enter_sudo`` (Service) — der einzige Ort, an dem Sudo gewaehrt wird."""

    def _prepare_admin(self, admin_user):
        admin_user.set_password(ADMIN_PASSWORD)
        admin_user.save()
        return admin_user

    def test_enter_sudo_service_rotates_and_preserves_payload(self):
        """Der Rotationsort ist der Service, nicht die View.

        ``SudoModeView.post`` ist heute der einzige Aufrufer; sitzt die Rotation
        im Service, erbt jeder kuenftige Aufrufer sie automatisch.
        """
        request = RequestFactory().post("/sudo/")
        session = SessionStore()
        session["mfa_verified"] = True
        session[LAST_RENEWAL_KEY] = 1_700_000_000.0
        session.save()
        request.session = session
        old_key = session.session_key

        enter_sudo(request)

        assert request.session.session_key != old_key
        assert _session_row_exists(old_key) is False
        assert request.session.get("mfa_verified") is True
        assert request.session.get(LAST_RENEWAL_KEY) == 1_700_000_000.0
        assert SUDO_SESSION_KEY in request.session

    def test_sudo_grant_rotates_session_id_and_kills_old_cookie(self, client, admin_user):
        self._prepare_admin(admin_user)
        old_key = _login(client, admin_user.username, ADMIN_PASSWORD)

        response = client.post(
            reverse("sudo_mode"),
            {"password": ADMIN_PASSWORD, "next": "/clients/"},
        )

        assert response.status_code == 302
        assert SUDO_SESSION_KEY in client.session
        _assert_old_cookie_is_dead(old_key, client.session.session_key)
        assert AuditLog.objects.filter(user=admin_user, action=AuditLog.Action.SUDO_MODE_ENTERED).exists()

    def test_sudo_grant_preserves_session_payload(self, client, admin_user, settings_obj):
        self._prepare_admin(admin_user)
        _with_non_default_timeout(settings_obj)
        _login(client, admin_user.username, ADMIN_PASSWORD)
        _seed_extra_payload(client)
        before = dict(client.session.items())
        assert before["_session_expiry"] == NON_DEFAULT_TIMEOUT_MINUTES * 60

        response = client.post(reverse("sudo_mode"), {"password": ADMIN_PASSWORD, "next": "/clients/"})

        assert response.status_code == 302
        assert response.url == "/clients/"
        after = dict(client.session.items())
        assert after[LAST_RENEWAL_KEY] == before[LAST_RENEWAL_KEY]
        assert after[ABSOLUTE_DEADLINE_KEY] == before[ABSOLUTE_DEADLINE_KEY]
        assert after["_session_expiry"] == before["_session_expiry"]
        assert after["_auth_user_id"] == before["_auth_user_id"]
        assert after["_auth_user_hash"] == before["_auth_user_hash"]
        assert after["_review_probe"] == before["_review_probe"]
        # Voll-Dict-Delta: genau EIN Zugang, der Sudo-Stempel, sonst wertgleich.
        assert {k: v for k, v in after.items() if k != SUDO_SESSION_KEY} == before
        assert isinstance(after[SUDO_SESSION_KEY], int)

    def test_sudo_grant_with_second_factor_rotates(self, client, admin_user):
        """A3.2-Pfad (TOTP-Geraet vorhanden): Passwort + frischer OTP."""
        self._prepare_admin(admin_user)
        device = _confirmed_totp(admin_user)
        _login(client, admin_user.username, ADMIN_PASSWORD)
        _mark_verified(client)
        pre_sudo_key = client.session.session_key

        response = client.post(
            reverse("sudo_mode"),
            {"password": ADMIN_PASSWORD, "otp_token": _valid_token(device), "next": "/clients/"},
        )

        assert response.status_code == 302
        assert SUDO_SESSION_KEY in client.session
        assert client.session.get("mfa_verified") is True
        _assert_old_cookie_is_dead(pre_sudo_key, client.session.session_key)

    def test_wrong_password_neither_rotates_nor_grants(self, client, admin_user):
        self._prepare_admin(admin_user)
        old_key = _login(client, admin_user.username, ADMIN_PASSWORD)

        response = client.post(reverse("sudo_mode"), {"password": "WRONG", "next": "/clients/"})

        assert response.status_code == 403
        assert client.session.session_key == old_key
        assert _session_row_exists(old_key) is True
        assert SUDO_SESSION_KEY not in client.session
        assert AuditLog.objects.filter(user=admin_user, action=AuditLog.Action.SUDO_MODE_FAILED).exists()

    def test_wrong_second_factor_neither_rotates_nor_grants(self, client, admin_user):
        self._prepare_admin(admin_user)
        _confirmed_totp(admin_user)
        _login(client, admin_user.username, ADMIN_PASSWORD)
        _mark_verified(client)
        old_key = client.session.session_key

        response = client.post(
            reverse("sudo_mode"),
            {"password": ADMIN_PASSWORD, "otp_token": "000000", "next": "/clients/"},
        )

        assert response.status_code == 403
        assert client.session.session_key == old_key
        assert _session_row_exists(old_key) is True
        assert SUDO_SESSION_KEY not in client.session
        assert AuditLog.objects.filter(user=admin_user, action=AuditLog.Action.SUDO_MODE_FAILED).latest(
            "timestamp"
        ).detail == {"factor": "otp"}


# --- 6. Passwort-Reset-/Invite-Token-Uebergang (R-A1-10) -------------------


@pytest.mark.django_db
class TestRotationAtPasswordResetTokenHandover:
    """``CustomPasswordResetConfirmView.dispatch`` und ``InviteConfirmView``.

    Fix-Runde 1, Befund 1 (BLOCKER): Django speichert das gepruefte Token unter
    ``_password_reset_token`` in der bestehenden Session und leitet auf den
    tokenfreien ``set-password``-Pfad um. Das ist eine Rechteanhebung — die
    Sitzung darf danach ein neues Passwort setzen. Ohne Rotation erbt ein vor
    dem Klick kopiertes Cookie genau diese Faehigkeit, ohne den Reset-Link je
    gesehen zu haben.
    """

    def _uid(self, user) -> str:
        return urlsafe_base64_encode(force_bytes(user.pk))

    def _open_link(self, client, user, url_name: str, generator):
        """Reset-/Invite-Link im legitimen Client oeffnen (Token-Uebergang)."""
        # ``last_login`` fliesst in den Token-Hash — nach dem Login neu laden,
        # sonst erzeugt der Generator ein Token fuer einen veralteten Stand.
        user.refresh_from_db()
        token = generator.make_token(user)
        response = client.get(reverse(url_name, kwargs={"uidb64": self._uid(user), "token": token}))
        assert response.status_code == 302, response.status_code
        assert response.url.endswith("/set-password/")
        return response.url

    def _assert_handover_rotates(self, client, user, url_name, generator, settings_obj):
        _with_non_default_timeout(settings_obj)
        old_key = _login(client, user.username, STAFF_PASSWORD)
        _seed_extra_payload(client)
        before = dict(client.session.items())
        assert before["_session_expiry"] == NON_DEFAULT_TIMEOUT_MINUTES * 60

        form_url = self._open_link(client, user, url_name, generator)

        new_key = client.session.session_key
        # (a) Rotiert, alte Zeile weg, alter Cookie abgemeldet.
        _assert_old_cookie_is_dead(old_key, new_key)
        # (b) Die Reset-Faehigkeit haengt an der NEUEN Sitzung.
        assert client.session.get(RESET_TOKEN_KEY)
        # (c) Anmeldung und Fristen ueberleben die Rotation.
        assert client.session["_auth_user_id"] == before["_auth_user_id"]
        assert client.session[LAST_RENEWAL_KEY] == before[LAST_RENEWAL_KEY]
        assert client.session[ABSOLUTE_DEADLINE_KEY] == before[ABSOLUTE_DEADLINE_KEY]
        assert client.session["_session_expiry"] == before["_session_expiry"]
        assert client.session["_review_probe"] == before["_review_probe"]

        # (d) Der zweite Client mit dem ALTEN Cookie sieht das Formular nicht …
        stolen = _client_with_stolen_cookie(old_key)
        stolen_get = stolen.get(form_url)
        assert stolen_get.status_code == 200
        assert stolen_get.context["validlink"] is False
        # … und kann erst recht kein Passwort setzen.
        stolen_post = stolen.post(form_url, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
        assert stolen_post.status_code == 200
        assert stolen_post.context["validlink"] is False
        user.refresh_from_db()
        assert user.check_password(NEW_PASSWORD) is False, (
            "Alter Cookie erbte die Reset-Berechtigung und konnte das Passwort setzen"
        )

        # (e) Der legitime Client kann setzen — die Rotation bricht den Ablauf nicht.
        legit_get = client.get(form_url)
        assert legit_get.status_code == 200
        assert legit_get.context["validlink"] is True
        legit_post = client.post(form_url, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
        assert legit_post.status_code == 302
        user.refresh_from_db()
        assert user.check_password(NEW_PASSWORD) is True

    def test_password_reset_handover_rotates(self, client, staff_user, settings_obj):
        self._assert_handover_rotates(
            client, staff_user, "password_reset_confirm", default_token_generator, settings_obj
        )

    def test_invite_handover_rotates(self, client, staff_user, settings_obj):
        self._assert_handover_rotates(client, staff_user, "invite_confirm", invite_token_generator, settings_obj)

    def test_invalid_reset_token_neither_rotates_nor_grants(self, client, staff_user):
        """Fehlschlag: ungueltiges Token → keine Rotation, keine Faehigkeit."""
        old_key = _login(client, "teststaff", STAFF_PASSWORD)

        response = client.get(
            reverse(
                "password_reset_confirm",
                kwargs={"uidb64": self._uid(staff_user), "token": "aaaaaa-bbbbbbbbbbbbbbbbbbbbbb"},
            )
        )

        assert response.status_code == 200
        assert response.context["validlink"] is False
        assert client.session.session_key == old_key
        assert _session_row_exists(old_key) is True
        assert RESET_TOKEN_KEY not in client.session

    def test_reset_link_for_anonymous_visitor_still_works(self, client, staff_user):
        """Der Normalfall (abgemeldet) darf durch die Rotation nicht brechen."""
        token = default_token_generator.make_token(staff_user)
        response = client.get(
            reverse("password_reset_confirm", kwargs={"uidb64": self._uid(staff_user), "token": token})
        )
        assert response.status_code == 302
        form_url = response.url
        assert client.session.get(RESET_TOKEN_KEY)
        assert client.get(form_url).context["validlink"] is True
        assert client.post(form_url, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD}).status_code == 302
        staff_user.refresh_from_db()
        assert staff_user.check_password(NEW_PASSWORD) is True


@pytest.mark.django_db
class TestRotationSurvivesExceptionAfterTokenWrite:
    """Fix-Runde 2, Ruling R-A1-12: die Rotation muss ausnahmesicher sein.

    Djangos ``PasswordResetConfirmView.dispatch`` schreibt das gueltige Token in
    die Sitzung und baut ERST DANACH den Redirect. Wirft dieser Schritt (oder
    irgendetwas dahinter), wurde die Rotation im ersten Entwurf uebersprungen —
    die Session-Middleware speicherte das Token beim Antwortbau aber trotzdem,
    und zwar unter der ALTEN ID. Ein zweiter Client mit dem alten Cookie konnte
    danach das Passwort setzen.

    Die Ausnahme wird hier per ``monkeypatch`` an genau der Stelle ausgeloest,
    an der die Elternklasse den Redirect baut — unabhaengig davon, welcher
    konkrete Ausloeser (ueberlanger Pfad, Fehlkonfiguration, Bibliotheks-Bug)
    sie in Produktion erzeugt.
    """

    def _uid(self, user) -> str:
        return urlsafe_base64_encode(force_bytes(user.pk))

    def _link(self, user, url_name, generator) -> str:
        user.refresh_from_db()
        return reverse(url_name, kwargs={"uidb64": self._uid(user), "token": generator.make_token(user)})

    def _assert_capability_did_not_survive_in_old_session(self, user, old_key):
        # Die alte Sitzungszeile ist weg — die Faehigkeit kann nicht an ihr haengen.
        assert _session_row_exists(old_key) is False, (
            "Alte django_session-Zeile ueberlebte die Ausnahme und traegt das Reset-Token"
        )
        form_url = reverse("password_reset_confirm", kwargs={"uidb64": self._uid(user), "token": "set-password"})
        stolen = _client_with_stolen_cookie(old_key)
        stolen_get = stolen.get(form_url)
        assert stolen_get.status_code == 200
        assert stolen_get.context["validlink"] is False
        stolen_post = stolen.post(form_url, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
        assert stolen_post.status_code == 200
        assert stolen_post.context["validlink"] is False
        user.refresh_from_db()
        assert user.check_password(NEW_PASSWORD) is False, (
            "Alter Cookie erbte die Reset-Berechtigung ueber den Ausnahmepfad und konnte das Passwort setzen"
        )

    def test_reset_rotates_even_when_redirect_build_raises(self, client, staff_user, monkeypatch):
        """``DisallowedRedirect`` nach dem Token-Write → 400, Faehigkeit rotiert mit."""

        def _raise_disallowed(*args, **kwargs):
            raise DisallowedRedirect("Unsafe redirect")

        old_key = _login(client, "teststaff", STAFF_PASSWORD)
        link = self._link(staff_user, "password_reset_confirm", default_token_generator)
        monkeypatch.setattr("django.contrib.auth.views.HttpResponseRedirect", _raise_disallowed)

        response = client.get(link)

        # SuspiciousOperation → 400; die Antwort entsteht, die Session wird
        # ausgangsseitig gespeichert. Genau hier lag die Luecke.
        assert response.status_code == 400
        self._assert_capability_did_not_survive_in_old_session(staff_user, old_key)

    def test_invite_rotates_even_when_dispatch_raises(self, client, staff_user, monkeypatch):
        """Beliebige Ausnahme (500-Pfad) nach dem Token-Write — Invite-Route."""

        def _raise_runtime(*args, **kwargs):
            raise RuntimeError("Antwortbau gescheitert")

        old_key = _login(client, "teststaff", STAFF_PASSWORD)
        link = self._link(staff_user, "invite_confirm", invite_token_generator)
        monkeypatch.setattr("django.contrib.auth.views.HttpResponseRedirect", _raise_runtime)

        with pytest.raises(RuntimeError):
            client.get(link)

        self._assert_capability_did_not_survive_in_old_session(staff_user, old_key)

    def test_exception_before_token_write_does_not_rotate(self, client, staff_user, monkeypatch):
        """Gegenprobe: bricht es VOR dem Write ab, wurde nichts angehoben — also keine Rotation."""

        def _raise_runtime(*args, **kwargs):
            raise RuntimeError("Nutzeraufloesung gescheitert")

        old_key = _login(client, "teststaff", STAFF_PASSWORD)
        link = self._link(staff_user, "password_reset_confirm", default_token_generator)
        monkeypatch.setattr(
            "django.contrib.auth.views.PasswordResetConfirmView.get_user",
            _raise_runtime,
        )

        with pytest.raises(RuntimeError):
            client.get(link)

        assert _session_row_exists(old_key) is True
        assert client.session.session_key == old_key
        assert RESET_TOKEN_KEY not in client.session
