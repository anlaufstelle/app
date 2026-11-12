"""Tests fuer die DemoGuardMiddleware (Refs #1062)."""

import ast
from functools import cache

import pytest
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import reverse

from core.middleware.demo_guard import DemoGuardMiddleware
from tests.test_demo_settings import _run_demo_import


def _request(method, path, **extra):
    rf = RequestFactory()
    req = getattr(rf, method.lower())(path, **extra)
    req.session = {}
    req._messages = FallbackStorage(req)
    return req


def _passthrough(request):
    return HttpResponse("passed")


class DemoGuardTests(SimpleTestCase):
    @override_settings(DEMO_MODE=True)
    def test_blocks_maintenance_toggle_post(self):
        resp = DemoGuardMiddleware(_passthrough)(_request("POST", "/system/maintenance/"))
        self.assertEqual(resp.status_code, 302)

    @override_settings(DEMO_MODE=True)
    def test_external_referer_is_not_used_as_redirect_target(self):
        """L6 (Refs #1375): Ein externer Referer darf nicht als Redirect-Ziel
        dienen (Open Redirect). Die Middleware laeuft vor CSRF/Auth."""
        resp = DemoGuardMiddleware(_passthrough)(
            _request("POST", "/system/maintenance/", HTTP_REFERER="https://evil.example/phish")
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/")

    @override_settings(DEMO_MODE=True)
    def test_protocol_relative_referer_is_not_used(self):
        """``//evil`` (protokoll-relativ) wird vom Browser als externer Host
        gelesen -> muss auf ``/`` normalisiert werden."""
        resp = DemoGuardMiddleware(_passthrough)(
            _request("POST", "/system/maintenance/", HTTP_REFERER="//evil.example/phish")
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/")

    @override_settings(DEMO_MODE=True)
    def test_same_origin_relative_referer_is_preserved(self):
        """Ein interner relativer Pfad bleibt als Redirect-Ziel erhalten."""
        resp = DemoGuardMiddleware(_passthrough)(_request("POST", "/system/maintenance/", HTTP_REFERER="/system/"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/system/")

    @override_settings(DEMO_MODE=True)
    def test_allows_get_on_blocked_path(self):
        resp = DemoGuardMiddleware(_passthrough)(_request("GET", "/system/maintenance/"))
        self.assertEqual(resp.content, b"passed")

    @override_settings(DEMO_MODE=True)
    def test_allows_other_post(self):
        resp = DemoGuardMiddleware(_passthrough)(_request("POST", "/event/create/"))
        self.assertEqual(resp.content, b"passed")

    @override_settings(DEMO_MODE=False)
    def test_noop_when_not_demo(self):
        resp = DemoGuardMiddleware(_passthrough)(_request("POST", "/system/maintenance/"))
        self.assertEqual(resp.content, b"passed")

    @override_settings(DEMO_MODE=True, DEMO_GUARD_BLOCKED_PREFIXES=("/foo/",))
    def test_denylist_configurable(self):
        blocked = DemoGuardMiddleware(_passthrough)(_request("POST", "/foo/bar/"))
        self.assertEqual(blocked.status_code, 302)
        allowed = DemoGuardMiddleware(_passthrough)(_request("POST", "/system/maintenance/"))
        self.assertEqual(allowed.content, b"passed")

    def test_blocks_full_demo_denylist(self):
        with override_settings(DEMO_MODE=True, DEMO_GUARD_BLOCKED_PREFIXES=_demo_blocked_prefixes()):
            for path in (
                "/system/maintenance/",
                "/mfa/setup/",
                "/password-change/",
                "/admin-mgmt/core/user/123/change/",
            ):
                resp = DemoGuardMiddleware(_passthrough)(_request("POST", path))
                self.assertEqual(resp.status_code, 302, path)
            # Core demo actions stay allowed.
            for path in ("/event/create/", "/client/5/edit/"):
                resp = DemoGuardMiddleware(_passthrough)(_request("POST", path))
                self.assertEqual(resp.content, b"passed", path)


@cache
def _demo_blocked_prefixes():
    """The denylist exactly as the demo settings module defines it."""
    result = _run_demo_import(
        "from anlaufstelle.settings.demo import DEMO_GUARD_BLOCKED_PREFIXES;print(repr(DEMO_GUARD_BLOCKED_PREFIXES))"
    )
    assert result.returncode == 0, result.stderr
    return ast.literal_eval(result.stdout.strip())


def _demo_post(path):
    request = _request("POST", path, HTTP_REFERER="/mfa/settings/")
    with override_settings(DEMO_MODE=True, DEMO_GUARD_BLOCKED_PREFIXES=_demo_blocked_prefixes()):
        response = DemoGuardMiddleware(_passthrough)(request)
    return request, response


# Every path that changes the second factor or the passkeys of a shared demo account.
FACTOR_CHANGING_ROUTES = (
    ("mfa_setup", {}),
    ("mfa_device_change", {}),
    ("otp_webauthn:credential-registration-begin", {}),
    ("otp_webauthn:credential-registration-complete", {}),
    ("mfa_backup_codes_regenerate", {}),
    ("mfa_disable", {}),
    ("mfa_passkey_delete", {"pk": 1}),
)

# Signing in with a second factor stays possible on the demo.
SIGN_IN_ROUTES = (
    ("login", {}),
    ("mfa_verify", {}),
    ("otp_webauthn:credential-authentication-begin", {}),
    ("otp_webauthn:credential-authentication-complete", {}),
)


@pytest.mark.parametrize(("name", "kwargs"), FACTOR_CHANGING_ROUTES)
def test_demo_blocks_factor_change(name, kwargs):
    request, response = _demo_post(reverse(name, kwargs=kwargs))

    assert response.status_code == 302
    assert response["Location"] == "/mfa/settings/"
    assert [str(message) for message in get_messages(request)] == ["Diese Aktion ist im Demo-Modus deaktiviert."]


@pytest.mark.parametrize(("name", "kwargs"), SIGN_IN_ROUTES)
def test_demo_keeps_sign_in_with_second_factor_open(name, kwargs):
    _request_obj, response = _demo_post(reverse(name, kwargs=kwargs))

    assert response.content == b"passed"


@pytest.mark.django_db
def test_demo_sign_in_with_totp_completes(client, staff_user):
    from django_otp.oath import totp

    from core.models import EncryptedTOTPDevice

    device = EncryptedTOTPDevice.objects.create(user=staff_user, name="demo", confirmed=True)
    token = f"{totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits):0{device.digits}d}"

    with override_settings(DEMO_MODE=True, DEMO_GUARD_BLOCKED_PREFIXES=_demo_blocked_prefixes()):
        login = client.post(reverse("login"), {"username": "teststaff", "password": "testpass123"})
        verify = client.post(reverse("mfa_verify"), {"token": token})

    assert login.status_code == 302
    assert verify.status_code == 302
    assert verify.url != reverse("mfa_verify")
    assert client.session.get("mfa_verified") is True
