"""Refs #1604/#1701: DemoUmamiCSPRelaxMiddleware lockert die CSP eng gescoped.

Architektur-Guard: OHNE ``DEMO_MODE`` greift NIE eine Lockerung — der
Schutzwall ``script-src 'self'`` (ADR-022 § (c), Schutz von
``window.offlineStore``) bleibt auf prod/devlive/dev/test intakt. Auf der Demo
wird der Host des per Env konfigurierten Tracker-Skripts
(``settings.UMAMI_SCRIPT_SRC``, #1701) nur Post-Login und nur für
HTML-Dokumente in ``script-src``/``connect-src`` ergänzt — die Login-/Anon-
Seite bleibt strikt. Ohne konfiguriertes Skript (leerer Default) ist die
Middleware ein No-Op (fail-closed)."""

from types import SimpleNamespace

from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse, JsonResponse
from django.test import RequestFactory, override_settings

from core.middleware.demo_umami_csp_relax import DemoUmamiCSPRelaxMiddleware

_STRICT = "default-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self'"


def _mw(response: HttpResponse) -> DemoUmamiCSPRelaxMiddleware:
    return DemoUmamiCSPRelaxMiddleware(lambda req: response)


def _authed_request(path="/dashboard/"):
    request = RequestFactory().get(path)
    request.user = SimpleNamespace(is_authenticated=True)
    return request


def _anon_request(path="/accounts/login/"):
    request = RequestFactory().get(path)
    request.user = AnonymousUser()
    return request


# Per Env konfiguriertes Tracker-Skript (Beispielwerte, Refs #1701) — der
# CSP-Token ist der ORIGIN des Skripts, ohne Pfad.
_SCRIPT_SRC = "https://umami.example.org/main.js"


class TestDemoModeGate:
    """OHNE DEMO_MODE greift keine Lockerung — der zentrale Guard. Das gilt
    auch bei konfiguriertem Tracker-Skript (devlive setzt UMAMI_SCRIPT_SRC,
    lockert aber global selbst; diese Middleware bleibt dort No-Op)."""

    @override_settings(DEMO_MODE=False, UMAMI_SCRIPT_SRC=_SCRIPT_SRC)
    def test_no_relax_when_demo_mode_off_even_authenticated_html(self):
        response = HttpResponse("ok", content_type="text/html")
        response.headers["Content-Security-Policy"] = _STRICT
        out = _mw(response)(_authed_request())
        assert out.headers["Content-Security-Policy"] == _STRICT

    @override_settings(UMAMI_SCRIPT_SRC=_SCRIPT_SRC)
    def test_no_relax_when_demo_mode_setting_absent(self):
        # override_settings entfernt DEMO_MODE nicht, aber getattr-Default ist
        # False — hier ohne override, base.py hat DEMO_MODE nicht gesetzt.
        response = HttpResponse("ok", content_type="text/html")
        response.headers["Content-Security-Policy"] = _STRICT
        out = _mw(response)(_authed_request())
        assert out.headers["Content-Security-Policy"] == _STRICT


class TestNoScriptConfigured:
    """Refs #1701: ohne konfiguriertes Tracker-Skript (Default leer) ist die
    Middleware auch bei DEMO_MODE ein No-Op — fail-closed."""

    @override_settings(DEMO_MODE=True, UMAMI_SCRIPT_SRC="")
    def test_no_relax_without_script_src(self):
        response = HttpResponse("ok", content_type="text/html")
        response.headers["Content-Security-Policy"] = _STRICT
        out = _mw(response)(_authed_request())
        assert out.headers["Content-Security-Policy"] == _STRICT


class TestDemoRelax:
    @override_settings(DEMO_MODE=True, UMAMI_SCRIPT_SRC=_SCRIPT_SRC)
    def test_authenticated_html_gets_umami_host_in_script_and_connect(self):
        response = HttpResponse("ok", content_type="text/html")
        response.headers["Content-Security-Policy"] = _STRICT
        out = _mw(response)(_authed_request())
        csp = out.headers["Content-Security-Policy"]
        assert "script-src 'self' https://umami.example.org" in csp
        assert "connect-src 'self' https://umami.example.org" in csp
        # Andere Direktiven unberührt.
        assert "img-src 'self'" in csp
        assert "default-src 'self'" in csp

    @override_settings(DEMO_MODE=True, UMAMI_SCRIPT_SRC=_SCRIPT_SRC)
    def test_anonymous_login_page_stays_strict(self):
        response = HttpResponse("ok", content_type="text/html")
        response.headers["Content-Security-Policy"] = _STRICT
        out = _mw(response)(_anon_request())
        assert out.headers["Content-Security-Policy"] == _STRICT

    @override_settings(DEMO_MODE=True, UMAMI_SCRIPT_SRC=_SCRIPT_SRC)
    def test_json_response_unchanged(self):
        response = JsonResponse({"ok": True})
        response.headers["Content-Security-Policy"] = _STRICT
        out = _mw(response)(_authed_request())
        assert out.headers["Content-Security-Policy"] == _STRICT

    @override_settings(DEMO_MODE=True, UMAMI_SCRIPT_SRC=_SCRIPT_SRC)
    def test_idempotent_when_host_already_present(self):
        already = "script-src 'self' https://umami.example.org; connect-src 'self' https://umami.example.org"
        response = HttpResponse("ok", content_type="text/html")
        response.headers["Content-Security-Policy"] = already
        out = _mw(response)(_authed_request())
        csp = out.headers["Content-Security-Policy"]
        assert csp.count("https://umami.example.org") == 2

    @override_settings(DEMO_MODE=True, UMAMI_SCRIPT_SRC=_SCRIPT_SRC)
    def test_report_only_header_is_relaxed_too(self):
        response = HttpResponse("ok", content_type="text/html")
        response.headers["Content-Security-Policy-Report-Only"] = _STRICT
        out = _mw(response)(_authed_request())
        assert "https://umami.example.org" in out.headers["Content-Security-Policy-Report-Only"]

    @override_settings(DEMO_MODE=True, UMAMI_SCRIPT_SRC=_SCRIPT_SRC)
    def test_no_csp_header_is_left_alone(self):
        response = HttpResponse("ok", content_type="text/html")
        out = _mw(response)(_authed_request())
        assert "Content-Security-Policy" not in out.headers
