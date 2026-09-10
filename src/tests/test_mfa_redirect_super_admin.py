"""Post-MFA redirects preserve safe targets and the super-admin landing page.

Refs #2206.
"""

from unittest import mock

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory
from django_otp.oath import totp

from core.middleware.mfa import MFAEnforcementMiddleware
from core.models import EncryptedTOTPDevice as TOTPDevice
from core.services.security import generate_backup_codes


def _confirmed_totp(user):
    return TOTPDevice.objects.create(user=user, name="test", confirmed=True)


def _valid_token(device):
    token = totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)
    return f"{token:0{device.digits}d}"


@pytest.mark.django_db
class TestSuperAdminMFARedirect:
    def test_login_target_survives_middleware_and_totp_verify(self, client, super_admin_user):
        device = _confirmed_totp(super_admin_user)
        super_admin_user.set_password("testpass123")
        super_admin_user.save(update_fields=["password"])

        login_response = client.post(
            "/login/",
            {"username": super_admin_user.username, "password": "testpass123"},
        )
        assert login_response.url == "/system/"

        gated_response = client.get(login_response.url)
        assert gated_response.status_code == 302
        assert gated_response.url == "/mfa/verify/?next=%2Fsystem%2F"

        verify_response = client.post(gated_response.url, {"token": _valid_token(device)})
        assert verify_response.status_code == 302
        assert verify_response.url == "/system/"

    def test_next_root_uses_super_admin_default(self, client, super_admin_user):
        device = _confirmed_totp(super_admin_user)
        client.force_login(super_admin_user)

        response = client.post("/mfa/verify/?next=/", {"token": _valid_token(device)})

        assert response.url == "/system/"

    def test_external_next_is_ignored(self, client, super_admin_user):
        device = _confirmed_totp(super_admin_user)
        client.force_login(super_admin_user)

        response = client.post(
            "/mfa/verify/?next=https://evil.example/",
            {"token": _valid_token(device)},
        )

        assert response.url == "/system/"

    def test_backup_code_uses_super_admin_default(self, client, super_admin_user):
        _confirmed_totp(super_admin_user)
        code = generate_backup_codes(super_admin_user)[0]
        client.force_login(super_admin_user)

        response = client.post("/mfa/verify/", {"mode": "backup", "token": code})

        assert response.url == "/system/"

    def test_already_verified_get_uses_role_default(self, client, super_admin_user):
        _confirmed_totp(super_admin_user)
        client.force_login(super_admin_user)
        session = client.session
        session["mfa_verified"] = True
        session.save()

        response = client.get("/mfa/verify/")

        assert response.url == "/system/"


@pytest.mark.django_db
class TestFacilityUserMFARedirect:
    def test_default_stays_root(self, client, staff_user):
        device = _confirmed_totp(staff_user)
        client.force_login(staff_user)

        response = client.post("/mfa/verify/", {"token": _valid_token(device)})

        assert response.url == "/"

    def test_safe_next_is_preserved_from_verify_query(self, client, staff_user):
        device = _confirmed_totp(staff_user)
        client.force_login(staff_user)

        response = client.post("/mfa/verify/?next=/clients/", {"token": _valid_token(device)})

        assert response.url == "/clients/"


@pytest.mark.django_db
class TestMFAMiddlewareNext:
    def test_get_redirect_carries_full_target(self, client, staff_user):
        _confirmed_totp(staff_user)
        client.force_login(staff_user)

        response = client.get("/clients/?page=2")

        assert response.url == "/mfa/verify/?next=%2Fclients%2F%3Fpage%3D2"

    def test_non_get_redirect_does_not_carry_target(self, rf, staff_user):
        _confirmed_totp(staff_user)
        request = rf.post("/clients/?page=2")
        request.user = staff_user
        request.session = {}

        response = MFAEnforcementMiddleware(lambda req: req)(request)

        assert response.url == "/mfa/verify/"


@pytest.mark.django_db
class TestWebAuthnSuperAdminRedirect:
    def test_success_url_uses_shared_role_default(self, super_admin_user):
        from core.views.mfa_webauthn import WebAuthnAuthenticationCompleteView

        request = RequestFactory().get("/webauthn/authentication/complete/")
        request.user = super_admin_user
        request.session = SessionStore()
        view = WebAuthnAuthenticationCompleteView()
        view.request = request

        assert view.get_success_url() == "/system/"

    def test_success_url_preserves_safe_next(self, super_admin_user):
        from core.views.mfa_webauthn import WebAuthnAuthenticationCompleteView

        request = RequestFactory().get("/webauthn/authentication/complete/?next=/system/audit/")
        request.user = super_admin_user
        request.session = SessionStore()
        view = WebAuthnAuthenticationCompleteView()
        view.request = request

        with mock.patch.object(view, "get_redirect_url", wraps=view.get_redirect_url) as get_redirect_url:
            assert view.get_success_url() == "/system/audit/"
        get_redirect_url.assert_called_once_with()
