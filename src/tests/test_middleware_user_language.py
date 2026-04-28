"""Dedizierte Unit-Tests fuer UserLanguageMiddleware (Refs #670 FND-13).

Verifiziert das Verhalten der App-eigenen Locale-Bestimmung:
- Authentifizierte User mit gesetztem ``preferred_language`` bekommen ihre
  Praeferenz aktiviert.
- Authentifizierte User ohne Praeferenz und anonyme User fallen auf den
  ``LANGUAGE_CODE`` der App zurueck — Accept-Language wird bewusst
  ignoriert, sodass die Login-/Password-Reset-Seiten in der Default-Sprache
  der App rendern (FND-13: User sahen englische Labels USERNAME/PASSWORD,
  weil Accept-Language: en die Django-LocaleMiddleware umstellte).
"""

from types import SimpleNamespace

import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.utils import translation

from core.middleware.user_language import UserLanguageMiddleware


def _call_middleware(request):
    return UserLanguageMiddleware(lambda r: "ok")(request)


@pytest.fixture(autouse=True)
def _reset_translation():
    """Sicherstellen, dass jeder Test mit Default-Locale startet und nichts leakt."""
    translation.activate(settings.LANGUAGE_CODE)
    yield
    translation.deactivate_all()


class TestAnonymousFallsBackToDefault:
    def test_anonymous_user_gets_language_code(self):
        request = RequestFactory().get("/login/")
        request.user = AnonymousUser()
        # Simuliere LocaleMiddleware: Accept-Language: en hat schon "en" aktiviert.
        translation.activate("en")
        _call_middleware(request)
        assert request.LANGUAGE_CODE == settings.LANGUAGE_CODE
        assert translation.get_language() == settings.LANGUAGE_CODE

    def test_authenticated_user_without_preference_gets_language_code(self):
        request = RequestFactory().get("/")
        request.user = SimpleNamespace(is_authenticated=True, preferred_language="")
        translation.activate("en")
        _call_middleware(request)
        assert request.LANGUAGE_CODE == settings.LANGUAGE_CODE


class TestAuthenticatedUserPreference:
    def test_user_with_preferred_language_overrides(self):
        request = RequestFactory().get("/")
        request.user = SimpleNamespace(is_authenticated=True, preferred_language="en")
        # Default ist de, User will en
        translation.activate("de")
        _call_middleware(request)
        assert request.LANGUAGE_CODE == "en"
        assert translation.get_language() == "en"

    def test_user_with_preferred_de(self):
        request = RequestFactory().get("/")
        request.user = SimpleNamespace(is_authenticated=True, preferred_language="de")
        translation.activate("en")  # LocaleMiddleware-en wird ueberschrieben
        _call_middleware(request)
        assert request.LANGUAGE_CODE == "de"
