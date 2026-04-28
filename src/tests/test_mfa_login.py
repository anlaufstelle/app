"""Tests für den TOTP-Verify-Flow beim Login (Issue #521)."""

import pytest
from django_otp.oath import totp as oath_totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from core.middleware.mfa import MFAEnforcementMiddleware
from core.models import AuditLog


@pytest.mark.django_db
class TestMFAVerifyFlow:
    def _make_confirmed_device(self, user):
        return TOTPDevice.objects.create(user=user, name="test", confirmed=True)

    def _valid_token_for(self, device):
        token = oath_totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)
        return f"{token:0{device.digits}d}"

    def test_verify_requires_login(self, client):
        response = client.get("/mfa/verify/")
        assert response.status_code == 302
        assert "/login/" in response.url

    def test_verify_redirects_when_no_device(self, client, staff_user):
        client.login(username="teststaff", password="testpass123")
        response = client.get("/mfa/verify/")
        assert response.status_code == 302
        assert response.url == "/mfa/setup/"

    def test_verify_shows_prompt_when_device_exists(self, client, staff_user):
        self._make_confirmed_device(staff_user)
        client.login(username="teststaff", password="testpass123")
        response = client.get("/mfa/verify/")
        assert response.status_code == 200
        assert "mfa-token-input" in response.content.decode("utf-8")

    def test_verify_accepts_valid_token_and_marks_session(self, client, staff_user):
        device = self._make_confirmed_device(staff_user)
        client.login(username="teststaff", password="testpass123")
        response = client.post("/mfa/verify/", {"token": self._valid_token_for(device)})
        assert response.status_code == 302
        assert response.url == "/"
        assert client.session.get("mfa_verified") is True

    def test_verify_rejects_invalid_token_and_logs(self, client, staff_user):
        self._make_confirmed_device(staff_user)
        client.login(username="teststaff", password="testpass123")
        response = client.post("/mfa/verify/", {"token": "000000"})
        assert response.status_code == 200
        assert client.session.get("mfa_verified") is not True
        assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.MFA_FAILED).exists()

    def test_verify_redirects_when_already_verified(self, client, staff_user):
        self._make_confirmed_device(staff_user)
        client.login(username="teststaff", password="testpass123")
        session = client.session
        session["mfa_verified"] = True
        session.save()
        response = client.get("/mfa/verify/")
        assert response.status_code == 302
        assert response.url == "/"


@pytest.mark.django_db
class TestMFAEnforcementMiddleware:
    def _middleware(self):
        return MFAEnforcementMiddleware(lambda r: r)

    def test_anonymous_user_passes_through(self, rf, db):
        from django.contrib.auth.models import AnonymousUser

        request = rf.get("/")
        request.user = AnonymousUser()
        response = self._middleware()(request)
        assert response == request

    def test_authenticated_without_device_without_required_passes(self, rf, staff_user):
        request = rf.get("/")
        request.user = staff_user
        request.session = {"mfa_verified": False}
        response = self._middleware()(request)
        assert response == request

    def test_authenticated_with_mfa_required_redirects_to_setup(self, rf, staff_user):
        staff_user.mfa_required = True
        staff_user.save(update_fields=["mfa_required"])
        request = rf.get("/")
        request.user = staff_user
        request.session = {"mfa_verified": False}
        response = self._middleware()(request)
        assert response.status_code == 302
        assert "/mfa/setup/" in response.url

    def test_authenticated_with_confirmed_device_unverified_redirects_to_verify(self, rf, staff_user):
        TOTPDevice.objects.create(user=staff_user, name="test", confirmed=True)
        request = rf.get("/")
        request.user = staff_user
        request.session = {"mfa_verified": False}
        response = self._middleware()(request)
        assert response.status_code == 302
        assert "/mfa/verify/" in response.url

    def test_authenticated_with_confirmed_device_verified_passes(self, rf, staff_user):
        TOTPDevice.objects.create(user=staff_user, name="test", confirmed=True)
        request = rf.get("/")
        request.user = staff_user
        request.session = {"mfa_verified": True}
        response = self._middleware()(request)
        assert response == request

    def test_exempt_urls_pass_through_even_when_required(self, rf, staff_user):
        staff_user.mfa_required = True
        staff_user.save(update_fields=["mfa_required"])
        for url in ["/login/", "/logout/", "/mfa/setup/", "/mfa/verify/", "/static/foo.css", "/admin-mgmt/"]:
            request = rf.get(url)
            request.user = staff_user
            request.session = {"mfa_verified": False}
            response = self._middleware()(request)
            assert response == request, f"URL {url} sollte exempt sein"


@pytest.fixture
def rf():
    from django.test import RequestFactory

    return RequestFactory()
