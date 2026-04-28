"""Dedizierte Unit-Tests für HtmxSessionMiddleware (Refs #598 T-5).

Prüft die Übersetzung von 302-Redirects zur Login-Page in HX-Redirect-
Header bei HTMX-Requests — und dass alle anderen Fälle unverändert
durchgereicht werden.
"""

from django.http import HttpResponse, HttpResponseRedirect
from django.test import RequestFactory, override_settings

from core.middleware.htmx_session import HtmxSessionMiddleware


def _make_middleware(response):
    """Baut eine Middleware mit festem Response-Callback für Tests."""
    return HtmxSessionMiddleware(lambda r: response)


def _htmx_request(path="/"):
    rf = RequestFactory()
    request = rf.get(path, HTTP_HX_REQUEST="true")
    return request


def _normal_request(path="/"):
    rf = RequestFactory()
    request = rf.get(path)
    return request


class TestHtmxSessionRewrite:
    def test_htmx_302_to_login_is_rewritten_to_hx_redirect(self):
        middleware = _make_middleware(HttpResponseRedirect("/login/?next=/events/"))
        request = _htmx_request("/events/")

        response = middleware(request)

        assert response.status_code == 200
        assert response["HX-Redirect"] == "/login/?next=/events/"

    def test_non_htmx_302_passes_through(self):
        redirect = HttpResponseRedirect("/login/")
        middleware = _make_middleware(redirect)
        request = _normal_request("/events/")

        response = middleware(request)

        # Nicht umgeschrieben — Django-Standard-Redirect.
        assert response.status_code == 302
        assert response["Location"] == "/login/"
        assert "HX-Redirect" not in response

    def test_htmx_302_to_other_url_passes_through(self):
        """Nur Redirects zur Login-URL werden in HX-Redirect umgewandelt;
        andere 302s bleiben unverändert (z.B. Post-Redirect-Get)."""
        redirect = HttpResponseRedirect("/clients/123/")
        middleware = _make_middleware(redirect)
        request = _htmx_request("/clients/")

        response = middleware(request)

        assert response.status_code == 302
        assert response["Location"] == "/clients/123/"
        assert "HX-Redirect" not in response

    def test_htmx_200_passes_through(self):
        ok = HttpResponse("partial", status=200)
        middleware = _make_middleware(ok)
        request = _htmx_request("/events/")

        response = middleware(request)

        assert response.status_code == 200
        assert response.content == b"partial"

    def test_non_htmx_200_passes_through(self):
        ok = HttpResponse("full", status=200)
        middleware = _make_middleware(ok)
        request = _normal_request("/events/")

        response = middleware(request)

        assert response.status_code == 200

    @override_settings(LOGIN_URL="/auth/sign-in/")
    def test_respects_custom_login_url(self):
        """Umgeschrieben wird nur, wenn der Redirect auf die konfigurierte
        LOGIN_URL zeigt. Bei abweichender Settings-Konfiguration anderer
        Präfix greift der Umschreib-Branch nicht."""
        # Default-/login/-Redirect: wird bei /auth/sign-in/ NICHT umgeschrieben.
        middleware = _make_middleware(HttpResponseRedirect("/login/"))
        request = _htmx_request()
        response = middleware(request)
        assert response.status_code == 302

        # Explizit auf configured URL: wird umgeschrieben.
        middleware = _make_middleware(HttpResponseRedirect("/auth/sign-in/?next=/"))
        request = _htmx_request()
        response = middleware(request)
        assert response.status_code == 200
        assert response["HX-Redirect"] == "/auth/sign-in/?next=/"
