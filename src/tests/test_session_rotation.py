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
5. Sudo-Grant (``core.services.security.sudo_mode.enter_sudo``).

Je Ort drei Zusagen: **rotiert** (alte ID weg, alter Cookie abgemeldet),
**erhaelt** (Fristen-, Login- und OTP-Sessiondaten ueberleben), **rotiert nicht
bei Fehlschlag** (ein Fehlversuch hebt keine Rechte an und rotiert auch nicht —
sonst waere die Rotation ein kostenloser Session-Reset fuer jeden Cookie-Dieb).

Begriff: „Session-Key" meint in ``docs/security-notes.md``/``docs/threat-model.md``
ueberwiegend den Offline-Krypto-Schluessel. Hier geht es ausschliesslich um
Djangos Session-ID (``request.session.session_key``).
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.conf import settings as django_settings
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.test import Client, RequestFactory
from django.urls import reverse
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import totp as oath_totp

from core.middleware.session_idle import ABSOLUTE_DEADLINE_KEY, LAST_RENEWAL_KEY
from core.models import AuditLog
from core.models import EncryptedTOTPDevice as TOTPDevice
from core.services.security import SUDO_SESSION_KEY, enter_sudo, generate_backup_codes

STAFF_PASSWORD = "testpass123"
ADMIN_PASSWORD = "test-pw-123"


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

    def test_totp_verify_preserves_session_payload(self, client, staff_user):
        device = _confirmed_totp(staff_user)
        _login(client, "teststaff", STAFF_PASSWORD)
        before = dict(client.session.items())
        # Der Login stempelt beide Fristen — sie sind die einzigen
        # Session-getragenen Werte, die eine Rotation kosten koennte.
        assert LAST_RENEWAL_KEY in before
        assert ABSOLUTE_DEADLINE_KEY in before

        client.post("/mfa/verify/", {"token": _valid_token(device)})

        after = dict(client.session.items())
        assert after[LAST_RENEWAL_KEY] == before[LAST_RENEWAL_KEY]
        assert after[ABSOLUTE_DEADLINE_KEY] == before[ABSOLUTE_DEADLINE_KEY]
        assert after["_auth_user_id"] == before["_auth_user_id"]
        assert after["_auth_user_backend"] == before["_auth_user_backend"]
        assert after["_auth_user_hash"] == before["_auth_user_hash"]
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

    def test_backup_code_verify_preserves_deadlines(self, client, staff_user):
        _confirmed_totp(staff_user)
        codes = generate_backup_codes(staff_user)
        _login(client, "teststaff", STAFF_PASSWORD)
        before = dict(client.session.items())

        client.post("/mfa/verify/", {"token": codes[0], "mode": "backup"})

        after = dict(client.session.items())
        assert after[LAST_RENEWAL_KEY] == before[LAST_RENEWAL_KEY]
        assert after[ABSOLUTE_DEADLINE_KEY] == before[ABSOLUTE_DEADLINE_KEY]
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

    def test_setup_confirmation_preserves_deadlines(self, client, staff_user):
        _login(client, "teststaff", STAFF_PASSWORD)
        self._walk_to_backup_codes(client, staff_user)
        before = dict(client.session.items())

        client.post("/mfa/backup-codes/", {"confirm_saved": "1"})

        after = dict(client.session.items())
        assert after[LAST_RENEWAL_KEY] == before[LAST_RENEWAL_KEY]
        assert after[ABSOLUTE_DEADLINE_KEY] == before[ABSOLUTE_DEADLINE_KEY]
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


def _library_complete_auth(view, device):
    """Ersatz fuer ``CompleteCredentialAuthenticationView.complete_auth``.

    Bildet den einzigen Session-Effekt der Bibliothek nach (``otp_login``
    stempelt den django-otp-Geraeteschluessel), ohne die Krypto-Ceremony.
    """
    view.request.session[DEVICE_ID_SESSION_KEY] = "otp_webauthn.webauthncredential/1"


@pytest.mark.django_db
class TestRotationAtWebAuthnAssertion:
    """``WebAuthnAuthenticationCompleteView.complete_auth``."""

    def _request(self, user):
        request = RequestFactory().post("/webauthn/authentication/complete/")
        request.user = user
        session = SessionStore()
        session[LAST_RENEWAL_KEY] = 1_700_000_000.0
        session[ABSOLUTE_DEADLINE_KEY] = 1_700_043_200.0
        session["mfa_verified"] = False
        session.save()
        request.session = session
        return request

    def test_complete_auth_rotates_and_preserves_otp_device(self, staff_user):
        from core.views.mfa_webauthn import WebAuthnAuthenticationCompleteView

        view = WebAuthnAuthenticationCompleteView()
        view.request = self._request(staff_user)
        old_key = view.request.session.session_key

        with mock.patch(
            "django_otp_webauthn.views.CompleteCredentialAuthenticationView.complete_auth",
            _library_complete_auth,
        ):
            view.complete_auth(mock.Mock())

        session = view.request.session
        assert session.get("mfa_verified") is True
        assert session.session_key != old_key
        assert _session_row_exists(old_key) is False
        # Der von ``otp_login`` gesetzte Geraeteschluessel darf die Rotation
        # ueberleben — sonst gilt der Faktor serverseitig als nicht geprueft.
        assert session.get(DEVICE_ID_SESSION_KEY) == "otp_webauthn.webauthncredential/1"
        assert session.get(LAST_RENEWAL_KEY) == 1_700_000_000.0
        assert session.get(ABSOLUTE_DEADLINE_KEY) == 1_700_043_200.0

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

    def test_sudo_grant_preserves_session_payload(self, client, admin_user):
        self._prepare_admin(admin_user)
        _login(client, admin_user.username, ADMIN_PASSWORD)
        before = dict(client.session.items())

        client.post(reverse("sudo_mode"), {"password": ADMIN_PASSWORD, "next": "/clients/"})

        after = dict(client.session.items())
        assert after[LAST_RENEWAL_KEY] == before[LAST_RENEWAL_KEY]
        assert after[ABSOLUTE_DEADLINE_KEY] == before[ABSOLUTE_DEADLINE_KEY]
        assert after["_auth_user_id"] == before["_auth_user_id"]
        assert after["_auth_user_hash"] == before["_auth_user_hash"]
        # Genau EIN Zugang: der Sudo-Stempel.
        assert set(after) - set(before) == {SUDO_SESSION_KEY}

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
