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
        for url in ["/login/", "/logout/", "/mfa/setup/", "/mfa/verify/", "/static/foo.css"]:
            request = rf.get(url)
            request.user = staff_user
            request.session = {"mfa_verified": False}
            response = self._middleware()(request)
            assert response == request, f"URL {url} sollte exempt sein"

    def test_admin_mgmt_not_exempt_from_mfa(self, rf, staff_user):
        """Django-Admin ist die höchstprivilegierte UI — MFA darf hier nicht
        umgehbar sein (Refs #582)."""
        staff_user.mfa_required = True
        staff_user.save(update_fields=["mfa_required"])
        request = rf.get("/admin-mgmt/")
        request.user = staff_user
        request.session = {"mfa_verified": False}
        response = self._middleware()(request)
        assert response != request, "Admin ohne MFA-Setup muss redirecten"
        assert "/mfa/setup/" in response.url

    def test_admin_url_not_exempt_from_mfa(self, rf, staff_user):
        """Auch die generische Django-Admin-URL ``/admin/`` darf MFA nicht
        umgehen — falls sie jemals gemountet wird, sollte das Middleware-Gate
        greifen. ``EXEMPT_URLS`` listet weder ``/admin/`` noch ``/admin-mgmt/``
        (Refs #591, WP1)."""
        staff_user.mfa_required = True
        staff_user.save(update_fields=["mfa_required"])
        request = rf.get("/admin/")
        request.user = staff_user
        request.session = {"mfa_verified": False}
        response = self._middleware()(request)
        assert response != request, "/admin/ ohne MFA-Setup muss redirecten"
        assert response.status_code == 302
        assert "/mfa/setup/" in response.url

    def test_session_flag_without_device_still_redirects_to_setup(self, rf, staff_user):
        """Security-Regression-Guard: Wenn ein Angreifer per Session-Flag
        ``mfa_verified=True`` setzt, aber KEIN confirmed TOTPDevice existiert
        und der User ``mfa_required=True`` hat → Middleware muss trotzdem auf
        ``/mfa/setup/`` redirecten. Das manuelle Flag darf nicht ausreichen,
        um den Device-Setup zu überspringen (Refs #591, WP1)."""
        staff_user.mfa_required = True
        staff_user.save(update_fields=["mfa_required"])
        # KEIN Device erstellen — nur das Session-Flag manuell setzen.
        request = rf.get("/")
        request.user = staff_user
        request.session = {"mfa_verified": True}
        response = self._middleware()(request)
        assert response != request, "Session-Flag ohne Device darf MFA-Setup-Redirect nicht umgehen."
        assert response.status_code == 302
        assert "/mfa/setup/" in response.url


@pytest.mark.django_db
class TestForcePasswordChangeMiddleware:
    """Regression-Guards für Force-Password-Change-Gate (Commit 4ffe9e5).

    Das Gate läuft als Middleware (``ForcePasswordChangeMiddleware``) und
    redirectet jede geschützte Route auf ``/password-change/``, sobald der User
    ``must_change_password=True`` hat.
    """

    def _middleware(self):
        from core.middleware.password_change import ForcePasswordChangeMiddleware

        return ForcePasswordChangeMiddleware(lambda r: r)

    def test_user_with_must_change_password_redirects_on_admin_url(self, rf, staff_user):
        """User mit ``must_change_password=True`` darf ``/admin/`` (oder
        irgendeine geschützte Route) nicht aufrufen — muss auf
        ``/password-change/`` redirecten."""
        staff_user.must_change_password = True
        staff_user.save(update_fields=["must_change_password"])
        request = rf.get("/admin/")
        request.user = staff_user
        response = self._middleware()(request)
        assert response != request
        assert response.status_code == 302
        assert "/password-change/" in response.url

    def test_user_with_must_change_password_redirects_on_protected_route(self, rf, staff_user):
        """Dasselbe Gate greift auch für beliebige Applikations-Routen."""
        staff_user.must_change_password = True
        staff_user.save(update_fields=["must_change_password"])
        request = rf.get("/clients/")
        request.user = staff_user
        response = self._middleware()(request)
        assert response != request
        assert response.status_code == 302
        assert "/password-change/" in response.url

    def test_password_change_path_itself_is_exempt(self, rf, staff_user):
        """Der ``/password-change/``-Pfad selbst muss exempt sein, sonst
        droht eine Redirect-Schleife."""
        staff_user.must_change_password = True
        staff_user.save(update_fields=["must_change_password"])
        request = rf.get("/password-change/")
        request.user = staff_user
        response = self._middleware()(request)
        assert response == request

    def test_user_without_flag_passes_through(self, rf, staff_user):
        """Ohne ``must_change_password`` wird nicht umgeleitet."""
        assert staff_user.must_change_password is False
        request = rf.get("/admin/")
        request.user = staff_user
        response = self._middleware()(request)
        assert response == request


@pytest.fixture
def rf():
    from django.test import RequestFactory

    return RequestFactory()
