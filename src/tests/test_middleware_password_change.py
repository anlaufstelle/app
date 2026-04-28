"""Dedizierte Unit-Tests für ForcePasswordChangeMiddleware (Refs #598 T-5).

Prüft: Ein User mit ``must_change_password=True`` wird auf die Password-
Change-Seite umgeleitet — außer auf einer Whitelist von Exempt-URLs
(``/login/``, ``/logout/``, ``/password-change/``, ``/password-reset/``,
``/static/``).
"""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory

from core.middleware.password_change import EXEMPT_URLS, ForcePasswordChangeMiddleware


def _make_middleware():
    return ForcePasswordChangeMiddleware(lambda r: HttpResponse("ok", status=200))


@pytest.mark.django_db
class TestForcePasswordChangeRedirect:
    def test_user_with_must_change_is_redirected(self, staff_user):
        staff_user.must_change_password = True
        staff_user.save(update_fields=["must_change_password"])

        rf = RequestFactory()
        request = rf.get("/clients/")
        request.user = staff_user

        response = _make_middleware()(request)

        assert response.status_code == 302
        # Django redirectet mit "password_change" → URL aus settings/URL-conf.
        assert response["Location"].startswith("/password-change/") or "/password-change/" in response["Location"]

    def test_user_without_must_change_is_not_redirected(self, staff_user):
        staff_user.must_change_password = False
        staff_user.save(update_fields=["must_change_password"])

        rf = RequestFactory()
        request = rf.get("/clients/")
        request.user = staff_user

        response = _make_middleware()(request)

        assert response.status_code == 200

    def test_anonymous_user_is_not_redirected(self):
        rf = RequestFactory()
        request = rf.get("/clients/")
        request.user = AnonymousUser()

        response = _make_middleware()(request)

        assert response.status_code == 200


@pytest.mark.django_db
class TestForcePasswordChangeExemptUrls:
    @pytest.fixture(autouse=True)
    def _staff_with_must_change(self, staff_user):
        staff_user.must_change_password = True
        staff_user.save(update_fields=["must_change_password"])
        return staff_user

    @pytest.mark.parametrize("path", EXEMPT_URLS)
    def test_exempt_url_passes_through(self, path, _staff_with_must_change):
        rf = RequestFactory()
        request = rf.get(path)
        request.user = _staff_with_must_change

        response = _make_middleware()(request)

        assert response.status_code == 200, (
            f"Pfad {path} sollte exempt sein (in EXEMPT_URLS), "
            f"Middleware hat aber zu /password-change/ umgeleitet."
        )

    def test_exempt_url_prefix_matches(self, _staff_with_must_change):
        """Exempt-URL ist Prefix-Match (nicht exakt) — ``/static/foo.css``
        ebenso wie ``/static/`` selbst muss durchgelassen werden."""
        rf = RequestFactory()
        request = rf.get("/static/css/app.css")
        request.user = _staff_with_must_change

        response = _make_middleware()(request)

        assert response.status_code == 200

    def test_non_exempt_path_redirects_despite_similar_prefix(self, _staff_with_must_change):
        """`/login-history/` ist **kein** exempt (anderes Prefix als
        `/login/`) — muss redirecten."""
        rf = RequestFactory()
        request = rf.get("/login-history/")
        request.user = _staff_with_must_change

        response = _make_middleware()(request)

        assert response.status_code == 302
