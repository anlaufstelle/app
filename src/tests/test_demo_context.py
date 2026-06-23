"""Tests fuer den demo()-Context-Processor (Refs #1062)."""

from django.test import RequestFactory, SimpleTestCase, override_settings
from django.utils import timezone

from core.context_processors import demo


class DemoContextProcessorTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get("/login/")

    @override_settings(DEMO_MODE=False)
    def test_inactive_returns_flag_only(self):
        self.assertEqual(demo(self.request), {"demo_mode": False})

    def test_default_settings_inactive(self):
        # Ohne gesetztes DEMO_MODE darf der Processor nichts exponieren.
        self.assertEqual(demo(self.request), {"demo_mode": False})

    @override_settings(
        DEMO_MODE=True,
        DEMO_LOGINS=[{"username": "admin", "role": "Einrichtungs-Admin"}],
        DEMO_PASSWORD="anlaufstelle2026",
    )
    def test_active_exposes_logins_password_and_next_reset(self):
        ctx = demo(self.request)
        self.assertTrue(ctx["demo_mode"])
        self.assertEqual(ctx["demo_password"], "anlaufstelle2026")
        self.assertEqual(ctx["demo_logins"][0]["username"], "admin")
        nxt = ctx["demo_next_reset"]
        # Naechster Reset ist die kommende volle Stunde, in der Zukunft.
        self.assertEqual((nxt.minute, nxt.second, nxt.microsecond), (0, 0, 0))
        self.assertGreater(nxt, timezone.localtime())

    @override_settings(DEMO_MODE=True, DEMO_LOGINS=[], DEMO_PASSWORD="")
    def test_active_without_logins_is_safe(self):
        ctx = demo(self.request)
        self.assertTrue(ctx["demo_mode"])
        self.assertEqual(ctx["demo_logins"], [])
