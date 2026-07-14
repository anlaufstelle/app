"""Tests fuer die DemoGuardMiddleware (Refs #1062)."""

from django.contrib.messages.storage.fallback import FallbackStorage
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from core.middleware.demo_guard import DemoGuardMiddleware


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

    @override_settings(
        DEMO_MODE=True,
        DEMO_GUARD_BLOCKED_PREFIXES=(
            "/system/maintenance/",
            "/mfa/setup/",
            "/password-change/",
            "/admin-mgmt/core/user/",
        ),
    )
    def test_blocks_full_demo_denylist(self):
        for path in (
            "/system/maintenance/",
            "/mfa/setup/",
            "/password-change/",
            "/admin-mgmt/core/user/123/change/",
        ):
            resp = DemoGuardMiddleware(_passthrough)(_request("POST", path))
            self.assertEqual(resp.status_code, 302, path)
        # Kern-Demo-Aktionen + 2FA-Deaktivieren bleiben erlaubt.
        for path in ("/event/create/", "/client/5/edit/", "/mfa/disable/"):
            resp = DemoGuardMiddleware(_passthrough)(_request("POST", path))
            self.assertEqual(resp.content, b"passed", path)
