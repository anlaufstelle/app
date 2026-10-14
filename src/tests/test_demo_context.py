"""Tests fuer den demo()-Context-Processor (Refs #1062)."""

import pytest
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
        DEMO_LOGINS=[{"username": "admin", "role": "facility_admin"}],
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


@override_settings(DEMO_MODE=True)
def test_demo_followup_executes_delivered_recovery_script():
    """Render both pages and execute only the scripts delivered to the second."""
    import json
    import subprocess
    from pathlib import Path
    from types import SimpleNamespace

    from django.template.loader import render_to_string

    from core.demo import DEMO_OFFLINE_BOOTSTRAP_PENDING

    request = RequestFactory().get("/")
    request.user = SimpleNamespace(is_authenticated=True, pk=1, username="demo", get_full_name=lambda: "Demo")
    request.session = {DEMO_OFFLINE_BOOTSTRAP_PENDING: True}
    first_html = render_to_string("base.html", {**demo(request), "user": request.user, "request": request})
    next_html = render_to_string("base.html", {**demo(request), "user": request.user, "request": request})
    assert "data-demo-offline-password=" in first_html
    assert "data-demo-offline-password=" not in next_html
    source = r"""
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const dom = new JSDOM(HTML, {url:'https://example.test/', runScripts:'outside-only'});
const w = dom.window;
w.crypto_session = {isSupported:()=>true, ready:async()=>{}, hasSessionKey:()=>false};
let requests = 0;
w.fetch = async () => { requests++; throw new Error('Unexpected salt request'); };
for (const node of w.document.querySelectorAll('script[src]')) {
    if (new URL(node.src).pathname.endsWith('/js/auth-bootstrap.js')) {
        w.eval(readFileSync('src/static/js/auth-bootstrap.js', 'utf8'));
    }
}
await new Promise(resolve => setTimeout(resolve, 0));
assert.equal(w.document.querySelector('[data-demo-offline-error]').hidden, false);
assert.equal(requests, 0);
w.close();
""".replace("HTML", json.dumps(next_html))
    result = subprocess.run(
        ["node", "--input-type=module"],
        input=source,
        text=True,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("supported", [False, True])
@pytest.mark.parametrize("authenticated", [False, True])
def test_demo_error_visibility_requires_authenticated_session(supported, authenticated):
    """A stale error element must stay hidden for anonymous visitors."""
    import json
    import subprocess
    from pathlib import Path

    source = (
        r"""
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const dom = new JSDOM('<body data-user-id="USER_ID"><div hidden role="alert" data-demo-offline-error></div></body>',
    {url:'https://example.test/', runScripts:'outside-only'});
const w = dom.window;
if (SUPPORTED) {
    w.crypto_session = {isSupported:()=>true, ready:async()=>{}, hasSessionKey:()=>false};
}
let requests = 0;
w.fetch = async () => { requests++; throw new Error('Unexpected salt request'); };
w.eval(readFileSync('src/static/js/auth-bootstrap.js', 'utf8'));
await new Promise(resolve => setTimeout(resolve, 0));
assert.equal(w.document.querySelector('[data-demo-offline-error]').hidden, EXPECTED_HIDDEN);
assert.equal(requests, 0);
w.close();
""".replace("USER_ID", "1" if authenticated else "")
        .replace("SUPPORTED", json.dumps(supported))
        .replace("EXPECTED_HIDDEN", json.dumps(not authenticated))
    )
    result = subprocess.run(
        ["node", "--input-type=module"],
        input=source,
        text=True,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.django_db
def test_demo_login_hints_match_seeded_scenario_accounts(settings):
    import json
    from io import StringIO

    from django.core.management import call_command

    from core.models import Facility, User
    from core.seed.scenarios import SCENARIOS
    from tests.test_demo_settings import _run_demo_import

    result = _run_demo_import(
        "import json;from anlaufstelle.settings.demo import DEMO_LOGINS;print(json.dumps(DEMO_LOGINS))"
    )
    assert result.returncode == 0, result.stderr
    settings.DEMO_MODE = True
    settings.DEMO_LOGINS = json.loads(result.stdout)
    context = demo(RequestFactory().get("/login/"))
    assert str(context["demo_logins"][0]["role"]) == str(User.Role.FACILITY_ADMIN.label)
    call_command("seed", scale="demo", stdout=StringIO())
    from django.template.loader import render_to_string

    html = render_to_string("auth/_demo_credentials.html", context)
    assert "Anwendungsbetreuung" in html
    assert "Zweigstelle Nord" not in html
    assert "mila_2" in html and "vertretung_2" in html
    hints = context["demo_login_scenarios"]
    assert len(hints) == len(SCENARIOS) == 6
    for scenario, hint in zip(SCENARIOS, hints, strict=True):
        facility = Facility.objects.get(name=scenario.facility_name)
        actual = set(User.objects.filter(facility=facility).values_list("username", flat=True))
        assert hint["facility_name"] == scenario.facility_name
        assert scenario.facility_name in html
        assert set(hint["usernames"]) == actual
        assert not User.objects.filter(facility=facility).exclude(email="").exists()
    assert User.objects.count() == 33
    assert Facility.objects.count() == 6
