from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse, JsonResponse
from django.test import RequestFactory

from core.middleware.admin_csp_relax import AdminCSPRelaxMiddleware


class _AuthenticatedStubUser:
    """Leichtgewichtiger Stub statt echtem User-Model — die Middleware
    braucht nur ``is_authenticated``, kein DB-Zugriff noetig."""

    is_authenticated = True


def _make_middleware(response: HttpResponse) -> AdminCSPRelaxMiddleware:
    return AdminCSPRelaxMiddleware(lambda req: response)


class TestAdminCSPRelaxMiddleware:
    def test_non_admin_path_response_unchanged(self):
        rf = RequestFactory()
        request = rf.get("/foo/")
        response = HttpResponse("ok")
        response.headers["Content-Security-Policy"] = "script-src 'self'"
        out = _make_middleware(response)(request)
        assert out.headers["Content-Security-Policy"] == "script-src 'self'"

    def test_admin_path_adds_unsafe_eval_to_script_src(self):
        rf = RequestFactory()
        request = rf.get("/admin-mgmt/users/")
        request.user = _AuthenticatedStubUser()
        response = HttpResponse("ok")
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; img-src 'self'"
        out = _make_middleware(response)(request)
        csp = out.headers["Content-Security-Policy"]
        assert "script-src 'self' 'unsafe-eval'" in csp
        assert "img-src 'self'" in csp
        assert "default-src 'self'" in csp

    def test_admin_path_unchanged_when_unsafe_eval_already_present(self):
        rf = RequestFactory()
        request = rf.get("/admin-mgmt/")
        request.user = _AuthenticatedStubUser()
        response = HttpResponse("ok")
        original = "script-src 'self' 'unsafe-eval'"
        response.headers["Content-Security-Policy"] = original
        out = _make_middleware(response)(request)
        assert out.headers["Content-Security-Policy"] == original

    def test_admin_path_handles_report_only_header(self):
        rf = RequestFactory()
        request = rf.get("/admin-mgmt/")
        request.user = _AuthenticatedStubUser()
        response = HttpResponse("ok")
        response.headers["Content-Security-Policy-Report-Only"] = "script-src 'self'"
        out = _make_middleware(response)(request)
        assert "'unsafe-eval'" in out.headers["Content-Security-Policy-Report-Only"]

    def test_admin_path_skips_when_no_csp_header(self):
        rf = RequestFactory()
        request = rf.get("/admin-mgmt/")
        request.user = _AuthenticatedStubUser()
        response = HttpResponse("ok")
        out = _make_middleware(response)(request)
        assert "Content-Security-Policy" not in out.headers

    # S4 (Refs #1084): Relax nur fuer text/html — JSON/CSV-Responses unter
    # /admin-mgmt/ brauchen kein 'unsafe-eval' und behalten die strikte CSP.
    def test_admin_path_json_response_unchanged(self):
        rf = RequestFactory()
        request = rf.get("/admin-mgmt/autocomplete/")
        request.user = _AuthenticatedStubUser()
        response = JsonResponse({"results": []})
        response.headers["Content-Security-Policy"] = "script-src 'self'"
        out = _make_middleware(response)(request)
        assert out.headers["Content-Security-Policy"] == "script-src 'self'"

    def test_admin_path_csv_response_unchanged(self):
        rf = RequestFactory()
        request = rf.get("/admin-mgmt/export/")
        request.user = _AuthenticatedStubUser()
        response = HttpResponse("a;b\n1;2\n", content_type="text/csv")
        response.headers["Content-Security-Policy"] = "script-src 'self'"
        out = _make_middleware(response)(request)
        assert out.headers["Content-Security-Policy"] == "script-src 'self'"

    def test_admin_path_explicit_html_content_type_is_relaxed(self):
        """Positivtest: text/html mit Charset-Suffix bleibt relaxed."""
        rf = RequestFactory()
        request = rf.get("/admin-mgmt/")
        request.user = _AuthenticatedStubUser()
        response = HttpResponse("ok", content_type="text/html; charset=utf-8")
        response.headers["Content-Security-Policy"] = "script-src 'self'"
        out = _make_middleware(response)(request)
        assert "'unsafe-eval'" in out.headers["Content-Security-Policy"]

    # I3 (Refs #1376): Die Lockerung darf NICHT fuer anonyme Requests greifen —
    # die anonym erreichbare /admin-mgmt/login/-Seite braucht kein 'unsafe-eval'.
    def test_admin_login_page_anonymous_user_not_relaxed(self):
        rf = RequestFactory()
        request = rf.get("/admin-mgmt/login/")
        request.user = AnonymousUser()
        response = HttpResponse("ok", content_type="text/html")
        response.headers["Content-Security-Policy"] = "script-src 'self'"
        out = _make_middleware(response)(request)
        assert "'unsafe-eval'" not in out.headers["Content-Security-Policy"]

    def test_admin_path_anonymous_user_not_relaxed(self):
        rf = RequestFactory()
        request = rf.get("/admin-mgmt/users/")
        request.user = AnonymousUser()
        response = HttpResponse("ok", content_type="text/html")
        response.headers["Content-Security-Policy"] = "script-src 'self'"
        out = _make_middleware(response)(request)
        assert "'unsafe-eval'" not in out.headers["Content-Security-Policy"]

    def test_admin_path_missing_user_attribute_not_relaxed(self):
        """Fail-closed: fehlt request.user komplett (z.B. Middleware-Reihenfolge
        kaputt), bleibt die strikte CSP bestehen statt zu relaxen."""
        rf = RequestFactory()
        request = rf.get("/admin-mgmt/")
        response = HttpResponse("ok", content_type="text/html")
        response.headers["Content-Security-Policy"] = "script-src 'self'"
        out = _make_middleware(response)(request)
        assert "'unsafe-eval'" not in out.headers["Content-Security-Policy"]
