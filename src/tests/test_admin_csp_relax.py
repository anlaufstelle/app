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

    # I3-Revert (Refs #1376, Review 2026-07-17): Die anonym erreichbare
    # /admin-mgmt/login/-Seite erbt via login.html -> unauthenticated.html
    # -> skeleton.html DASSELBE x-data="theme()" + Alpine-Bundle wie jede
    # andere Unfold-Seite. Ohne 'unsafe-eval' wirft Alpine beim Auswerten
    # von theme() auf <html> einen EvalError, bevor x-cloak entfernt wird —
    # die Login-Seite bleibt komplett display:none (leere Seite). Die
    # fruehere Annahme, die Login-Seite brauche keinen Unfold-Alpine-Build,
    # war empirisch falsch; die Lockerung muss daher unabhaengig vom
    # Auth-Status greifen.
    def test_admin_login_page_anonymous_user_is_relaxed(self):
        rf = RequestFactory()
        request = rf.get("/admin-mgmt/login/")
        request.user = AnonymousUser()
        response = HttpResponse("ok", content_type="text/html")
        response.headers["Content-Security-Policy"] = "script-src 'self'"
        out = _make_middleware(response)(request)
        assert "'unsafe-eval'" in out.headers["Content-Security-Policy"]

    def test_admin_path_anonymous_user_is_relaxed(self):
        rf = RequestFactory()
        request = rf.get("/admin-mgmt/users/")
        request.user = AnonymousUser()
        response = HttpResponse("ok", content_type="text/html")
        response.headers["Content-Security-Policy"] = "script-src 'self'"
        out = _make_middleware(response)(request)
        assert "'unsafe-eval'" in out.headers["Content-Security-Policy"]

    def test_admin_path_missing_user_attribute_is_relaxed(self):
        """Die Middleware liest request.user nicht mehr — fehlt das Attribut
        komplett (z.B. Middleware-Reihenfolge kaputt), darf das nicht crashen
        und die Lockerung bleibt unveraendert wirksam."""
        rf = RequestFactory()
        request = rf.get("/admin-mgmt/")
        response = HttpResponse("ok", content_type="text/html")
        response.headers["Content-Security-Policy"] = "script-src 'self'"
        out = _make_middleware(response)(request)
        assert "'unsafe-eval'" in out.headers["Content-Security-Policy"]


class TestAdminCSPRelaxStyleSrc:
    """Refs #1660 (I5 aus #1376): Die globale CSP setzt ``style-src`` ohne
    ``'unsafe-inline'``. Das gevendorte django-unfold rendert jedoch einen aus
    ``UNFOLD['COLORS']`` zusammengesetzten ``<style id="unfold-theme-colors">``
    (dynamisch → nicht hashbar) plus rund ein Dutzend ``style=``-Attribute.
    Ohne Lockerung verlöre die Admin-UI ihr komplettes Farbschema.
    """

    def test_admin_path_adds_unsafe_inline_to_style_src(self):
        rf = RequestFactory()
        request = rf.get("/admin-mgmt/users/")
        response = HttpResponse("ok", content_type="text/html")
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'sha256-abc='; img-src 'self'"
        )
        out = _make_middleware(response)(request)
        csp = out.headers["Content-Security-Policy"]
        assert "style-src 'self' 'sha256-abc=' 'unsafe-inline'" in csp
        assert "script-src 'self' 'unsafe-eval'" in csp
        assert "img-src 'self'" in csp

    def test_non_admin_path_style_src_untouched(self):
        rf = RequestFactory()
        request = rf.get("/clients/")
        response = HttpResponse("ok", content_type="text/html")
        original = "script-src 'self'; style-src 'self' 'sha256-abc='"
        response.headers["Content-Security-Policy"] = original
        out = _make_middleware(response)(request)
        assert out.headers["Content-Security-Policy"] == original

    def test_relax_is_idempotent_per_directive(self):
        """Doppelt durchlaufen darf keinen Token verdoppeln — und ein bereits
        vorhandenes ``'unsafe-eval'`` darf die style-src-Lockerung nicht
        ueberspringen (frueher brach die Schleife bei erstem Treffer ab)."""
        rf = RequestFactory()
        request = rf.get("/admin-mgmt/")
        response = HttpResponse("ok", content_type="text/html")
        response.headers["Content-Security-Policy"] = "script-src 'self' 'unsafe-eval'; style-src 'self'"
        out = _make_middleware(response)(request)
        csp = out.headers["Content-Security-Policy"]
        assert csp.count("'unsafe-eval'") == 1
        assert "style-src 'self' 'unsafe-inline'" in csp

        out2 = _make_middleware(out)(request)
        csp2 = out2.headers["Content-Security-Policy"]
        assert csp2.count("'unsafe-inline'") == 1
        assert csp2.count("'unsafe-eval'") == 1

    def test_json_response_style_src_untouched(self):
        rf = RequestFactory()
        request = rf.get("/admin-mgmt/autocomplete/")
        response = JsonResponse({"results": []})
        original = "script-src 'self'; style-src 'self'"
        response.headers["Content-Security-Policy"] = original
        out = _make_middleware(response)(request)
        assert out.headers["Content-Security-Policy"] == original
