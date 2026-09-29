"""Mutations-Pins fuer die bedingte Sudo-Pruefung beim Faktorwechsel (Refs #2328).

``sudo_redirect_if_required`` (``core.services.security.sudo_mode``) liefert die
Sudo-Weiterleitung fuer Views, die Sudo nur unter einer Bedingung verlangen;
``MFASetupView.dispatch`` nutzt sie fuer den Faktorwechsel. Jeder Pin hier
toetet eine konkrete Mutante: Toggle-Default und -Negation, der Anonym-Zweig,
der Sudo-Fenster-Zweig, das exakte Ziel samt kodiertem ``next`` und die
Anonym-Klausel des View-Gates.
"""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponseRedirect
from django.test import RequestFactory

from core.services.security.sudo_mode import SUDO_SESSION_KEY, sudo_redirect_if_required

PFAD_MIT_QUERY = "/mfa/setup/?a=1&b=2"


def _request(user, session=None, path="/mfa/setup/"):
    request = RequestFactory().get(path)
    request.user = user
    request.session = {} if session is None else session
    return request


@pytest.fixture
def sudo_an(settings):
    settings.SUDO_MODE_ENABLED = True


@pytest.mark.django_db
class TestToggle:
    def test_toggle_off_returns_none(self, settings, staff_user):
        settings.SUDO_MODE_ENABLED = False
        assert sudo_redirect_if_required(_request(staff_user)) is None

    def test_missing_setting_defaults_to_active(self, settings, staff_user):
        del settings.SUDO_MODE_ENABLED
        response = sudo_redirect_if_required(_request(staff_user))
        assert isinstance(response, HttpResponseRedirect)

    def test_toggle_on_redirects(self, sudo_an, staff_user):
        response = sudo_redirect_if_required(_request(staff_user))
        assert isinstance(response, HttpResponseRedirect)
        assert response.status_code == 302


@pytest.mark.django_db
class TestBranches:
    def test_anonymous_returns_none(self, sudo_an):
        assert sudo_redirect_if_required(_request(AnonymousUser())) is None

    def test_running_sudo_window_returns_none(self, sudo_an, staff_user):
        session = {SUDO_SESSION_KEY: int(time.time()) + 600}
        assert sudo_redirect_if_required(_request(staff_user, session)) is None

    def test_expired_sudo_window_redirects(self, sudo_an, staff_user):
        session = {SUDO_SESSION_KEY: int(time.time()) - 1}
        assert isinstance(sudo_redirect_if_required(_request(staff_user, session)), HttpResponseRedirect)


@pytest.mark.django_db
class TestTarget:
    def test_exact_target_with_encoded_next(self, sudo_an, staff_user):
        response = sudo_redirect_if_required(_request(staff_user))
        assert response["Location"] == "/sudo/?next=%2Fmfa%2Fsetup%2F"

    def test_next_keeps_full_path_including_every_query_parameter(self, sudo_an, staff_user):
        response = sudo_redirect_if_required(_request(staff_user, path=PFAD_MIT_QUERY))
        target = urlsplit(response["Location"])
        assert target.path == "/sudo/"
        assert parse_qs(target.query) == {"next": [PFAD_MIT_QUERY]}


@pytest.mark.django_db
class TestSetupViewAnonymousClause:
    def test_anonymous_setup_request_goes_to_login(self, sudo_an, client):
        """Die Sudo-Klausel des View-Gates wertet den Faktor nur fuer angemeldete Konten aus."""
        response = client.get("/mfa/setup/")
        assert response.status_code == 302
        assert urlsplit(response.url).path == "/login/"
