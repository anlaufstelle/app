"""Passkeys/WebAuthn als zweiter Faktor (ADR-032, Refs #1492).

Deckt die sicherheitskritische Glue-Schicht ab (Prädikat, Middleware-Redirect,
``mfa_verified``-Hook, „nur neben TOTP"-Guard, Sudo-gegatetes Entfernen). Die
vollständige Krypto-Ceremony (navigator.credentials) wird per E2E gegen einen
Chrome-Virtual-Authenticator geprüft — hier mocken wir die py_webauthn-Schicht,
um die Glue-Logik isoliert und deterministisch zu testen.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest
from django.http import JsonResponse
from django.test import RequestFactory

from core.models import AuditLog
from core.models import EncryptedTOTPDevice as TOTPDevice


def _make_totp(user):
    """Bestätigtes TOTP-Gerät (Voraussetzung für Passkey-Registrierung)."""
    return TOTPDevice.objects.create(user=user, name="totp", confirmed=True, key="a" * 40)


def _make_passkey(user, *, confirmed=True, credential_id=b"cred-1"):
    from django_otp_webauthn.models import WebAuthnCredential

    return WebAuthnCredential.objects.create(
        user=user,
        name="Testschlüssel",
        confirmed=confirmed,
        credential_id=credential_id,
        public_key=b"pubkey-bytes",
        aaguid="00000000-0000-0000-0000-000000000000",
        sign_count=0,
    )


@pytest.mark.django_db
class TestMFADevicePredicate:
    def test_webauthn_predicate_reflects_confirmed_credential(self, staff_user):
        assert staff_user.has_confirmed_webauthn_device is False
        _make_passkey(staff_user, confirmed=False)
        assert staff_user.has_confirmed_webauthn_device is False
        _make_passkey(staff_user, confirmed=True, credential_id=b"cred-2")
        assert staff_user.has_confirmed_webauthn_device is True

    def test_mfa_predicate_is_totp_or_webauthn(self, staff_user):
        assert staff_user.has_confirmed_mfa_device is False
        _make_passkey(staff_user, confirmed=True)
        assert staff_user.has_confirmed_mfa_device is True
        assert staff_user.has_confirmed_totp_device is False

    def test_mfa_predicate_true_with_only_totp(self, staff_user):
        _make_totp(staff_user)
        assert staff_user.has_confirmed_mfa_device is True


@pytest.mark.django_db
class TestMFAMiddlewareWithPasskey:
    def test_confirmed_passkey_triggers_verify_redirect(self, client, staff_user):
        """Ein bestätigter Passkey zählt als Faktor: unverifizierte Session → /mfa/verify/."""
        _make_passkey(staff_user, confirmed=True)
        client.login(username="teststaff", password="testpass123")
        # Session ist nach Login nicht mfa_verified → Middleware leitet auf Verify.
        response = client.get("/")
        assert response.status_code == 302
        assert response.url == "/mfa/verify/"

    def test_verify_page_reachable_for_passkey_only_user(self, client, staff_user):
        _make_passkey(staff_user, confirmed=True)
        client.login(username="teststaff", password="testpass123")
        response = client.get("/mfa/verify/")
        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="passkey-verification-button"' in content


@pytest.mark.django_db
class TestVerifyChooserContext:
    def test_chooser_flags_when_both_methods(self, client, staff_user):
        _make_totp(staff_user)
        _make_passkey(staff_user, confirmed=True)
        client.login(username="teststaff", password="testpass123")
        response = client.get("/mfa/verify/")
        assert response.status_code == 200
        assert response.context["has_totp"] is True
        assert response.context["has_webauthn"] is True
        content = response.content.decode()
        # Passkey-Chooser UND TOTP-Formular sind beide vorhanden.
        assert 'data-testid="mfa-passkey-button"' in content
        assert 'data-testid="mfa-token-input"' in content


@pytest.mark.django_db
class TestPasskeyRegistrationRequiresTOTP:
    def test_registration_begin_rejected_without_totp(self, client, staff_user):
        client.login(username="teststaff", password="testpass123")
        response = client.post("/webauthn/registration/begin/")
        assert response.status_code == 400
        assert json.loads(response.content)["code"] == "totp_required"

    def test_registration_begin_allowed_with_totp(self, client, staff_user):
        _make_totp(staff_user)
        client.login(username="teststaff", password="testpass123")
        response = client.post("/webauthn/registration/begin/")
        assert response.status_code == 200
        # py_webauthn liefert Registrierungs-Optionen mit einer Challenge.
        assert "challenge" in json.loads(response.content)


@pytest.mark.django_db
class TestMfaVerifiedGlue:
    """Der Session-Flag darf NUR bei Erfolg gesetzt werden (sonst Verify-Bypass)."""

    def _request(self, user):
        rf = RequestFactory()
        request = rf.post("/webauthn/authentication/complete/")
        request.user = user
        from django.contrib.sessions.backends.db import SessionStore

        request.session = SessionStore()
        return request

    def test_authentication_complete_marks_session_verified(self, staff_user):
        from core.views.mfa_webauthn import WebAuthnAuthenticationCompleteView

        view = WebAuthnAuthenticationCompleteView()
        view.request = self._request(staff_user)
        device = mock.Mock()
        # Elternteil-complete_auth (otp_login etc.) neutralisieren.
        with mock.patch(
            "django_otp_webauthn.views.CompleteCredentialAuthenticationView.complete_auth",
            return_value=None,
        ):
            view.complete_auth(device)
        assert view.request.session.get("mfa_verified") is True

    def test_registration_complete_sets_flag_and_audits_on_success(self, staff_user):
        from core.views.mfa_webauthn import WebAuthnRegistrationCompleteView

        view = WebAuthnRegistrationCompleteView()
        view.request = self._request(staff_user)
        with mock.patch(
            "django_otp_webauthn.views.CompleteCredentialRegistrationView.post",
            return_value=JsonResponse({"id": 1}),
        ):
            response = view.post(view.request)
        assert response.status_code == 200
        assert view.request.session.get("mfa_verified") is True
        assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.WEBAUTHN_REGISTERED).exists()

    def test_registration_complete_does_not_set_flag_on_error(self, staff_user):
        from core.views.mfa_webauthn import WebAuthnRegistrationCompleteView

        view = WebAuthnRegistrationCompleteView()
        view.request = self._request(staff_user)
        with mock.patch(
            "django_otp_webauthn.views.CompleteCredentialRegistrationView.post",
            return_value=JsonResponse({"detail": "invalid"}, status=400),
        ):
            response = view.post(view.request)
        assert response.status_code == 400
        assert view.request.session.get("mfa_verified") is not True
        assert not AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.WEBAUTHN_REGISTERED).exists()


@pytest.mark.django_db
class TestSettingsPageRendersPasskeySection:
    def test_register_bundle_and_config_present(self, client, staff_user):
        _make_totp(staff_user)
        client.login(username="teststaff", password="testpass123")
        session = client.session
        session["mfa_verified"] = True
        session.save()
        response = client.get("/mfa/settings/")
        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-testid="passkey-register-button"' in content
        # CSP-strikte Einbindung: JSON-Config-Block + externes Modul-Bundle.
        assert 'id="otp_webauthn_config"' in content
        assert "otp_webauthn_register.js" in content

    def test_add_passkey_hidden_without_totp(self, client, staff_user):
        """Ohne TOTP kein Registrierungs-Button (Passkey nur NEBEN TOTP)."""
        _make_passkey(staff_user, confirmed=True)
        client.login(username="teststaff", password="testpass123")
        session = client.session
        session["mfa_verified"] = True
        session.save()
        response = client.get("/mfa/settings/")
        assert response.status_code == 200
        assert 'data-testid="passkey-register-button"' not in response.content.decode()


@pytest.mark.django_db
class TestPasskeyRemoval:
    def test_delete_requires_login(self, client):
        response = client.post("/mfa/passkey/1/delete/")
        assert response.status_code == 302
        assert "/login/" in response.url

    def test_delete_removes_own_passkey_and_audits(self, client, staff_user, settings):
        settings.SUDO_MODE_ENABLED = False
        passkey = _make_passkey(staff_user, confirmed=True)
        _make_totp(staff_user)
        client.login(username="teststaff", password="testpass123")
        session = client.session
        session["mfa_verified"] = True
        session.save()
        response = client.post(f"/mfa/passkey/{passkey.pk}/delete/")
        assert response.status_code == 302
        assert staff_user.has_confirmed_webauthn_device is False
        assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.WEBAUTHN_REMOVED).exists()

    def test_cannot_delete_foreign_passkey(self, client, staff_user, second_facility_user, settings):
        settings.SUDO_MODE_ENABLED = False
        foreign = _make_passkey(second_facility_user, confirmed=True, credential_id=b"foreign")
        _make_totp(staff_user)
        client.login(username="teststaff", password="testpass123")
        session = client.session
        session["mfa_verified"] = True
        session.save()
        response = client.post(f"/mfa/passkey/{foreign.pk}/delete/")
        assert response.status_code == 302
        # Fremder Passkey bleibt bestehen (kein Cross-User-Löschen).
        assert second_facility_user.has_confirmed_webauthn_device is True
        assert not AuditLog.objects.filter(action=AuditLog.Action.WEBAUTHN_REMOVED).exists()


@pytest.mark.django_db
class TestMFADisableRemovesPasskeys:
    def test_disable_deletes_totp_and_passkeys(self, client, staff_user, settings):
        settings.SUDO_MODE_ENABLED = False
        _make_totp(staff_user)
        _make_passkey(staff_user, confirmed=True)
        client.login(username="teststaff", password="testpass123")
        session = client.session
        session["mfa_verified"] = True
        session.save()
        response = client.post("/mfa/disable/")
        assert response.status_code == 302
        assert staff_user.has_confirmed_totp_device is False
        assert staff_user.has_confirmed_webauthn_device is False


@pytest.mark.django_db
class TestAuditActions:
    def test_webauthn_actions_exist(self):
        assert AuditLog.Action.WEBAUTHN_REGISTERED
        assert AuditLog.Action.WEBAUTHN_REMOVED
        assert AuditLog.Action.WEBAUTHN_FAILED
