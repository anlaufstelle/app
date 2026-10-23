"""Public language selection and authentication transitions (Refs #2609)."""

from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import translation

from core.middleware.maintenance import MaintenanceModeMiddleware
from core.views.errors import csrf_failure, permission_denied, server_error

pytestmark = pytest.mark.django_db

PUBLIC_PATHS = (
    "/login/",
    "/demo/",
    "/impressum/",
    "/datenschutzerklaerung/",
    "/password-reset/",
    "/password-reset/done/",
    "/password-reset/invalid/token/",
    "/password-reset/complete/",
    "/invite/invalid/token/",
    "/missing-language-page/",
)


@pytest.mark.parametrize("path", PUBLIC_PATHS)
@pytest.mark.parametrize(
    "query,cookie,expected",
    [
        ("", "", "de"),
        ("?lang=en", "", "en"),
        ("", "en", "en"),
        ("?lang=invalid", "", "de"),
        ("?lang=de", "en", "de"),
        ("?lang=invalid", "en", "en"),
        ("", "invalid", "de"),
    ],
)
def test_public_pages_resolve_language(client, settings, path, query, cookie, expected):
    settings.DEMO_MODE = True
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = cookie
    response = client.get(path + query)
    assert response.status_code in (200, 404)
    assert f'<html lang="{expected}"' in response.content.decode()
    assert 'data-testid="app-version"' not in response.content.decode()


def test_explicit_language_survives_root_redirect(client):
    response = client.get("/?lang=en", follow=True)
    assert response.redirect_chain
    assert b'<html lang="en"' in response.content
    assert client.cookies[settings.LANGUAGE_COOKIE_NAME].value == "en"


@pytest.mark.parametrize("choice", ["query", "cookie", "setter"])
def test_login_keeps_visit_choice(client, staff_user, choice):
    if choice == "setter":
        client.post(reverse("set_language"), {"language": "en", "next": "/login/"})
    elif choice == "cookie":
        client.cookies[settings.LANGUAGE_COOKIE_NAME] = "en"
    response = client.post(
        "/login/" + ("?lang=en" if choice == "query" else ""),
        {
            "username": staff_user.username,
            "password": "testpass123",
        },
        follow=True,
    )
    assert b'<html lang="en"' in response.content
    staff_user.refresh_from_db()
    assert staff_user.preferred_language == "de"


def test_login_errors_keep_english(client):
    response = client.post("/login/?lang=en", {"username": "missing", "password": "invalid"})
    assert b'<html lang="en"' in response.content
    assert b"Please enter a correct" in response.content


def test_demo_switcher_does_not_change_shared_preference(client, settings, staff_user):
    settings.DEMO_MODE = True
    client.force_login(staff_user)
    response = client.post(reverse("set_language"), {"language": "en", "next": "/demo/"}, follow=True)
    assert b'<html lang="en"' in response.content
    staff_user.refresh_from_db()
    assert staff_user.preferred_language == "de"


@pytest.mark.parametrize("method", ["get", "post"])
def test_demo_start_error_keeps_parameters(client, settings, method):
    settings.DEMO_MODE = True
    url = "/demo/start/?lang=en&bereich=kontaktladen"
    response = client.get(url) if method == "get" else client.post(url, {"scenario": "invalid"})
    query = parse_qs(urlsplit(response.url).query)
    assert query["bereich"] == ["kontaktladen"]
    assert query["lang"] == ["en"]
    assert b'<html lang="en"' in client.get(response.url).content


@pytest.mark.parametrize("handler", [permission_denied, csrf_failure, server_error])
@pytest.mark.parametrize(
    "query,cookie,expected",
    [
        ("", "", "de"),
        ("?lang=en", "", "en"),
        ("", "en", "en"),
        ("?lang=invalid", "", "de"),
        ("?lang=invalid", "en", "en"),
        ("?lang=de", "en", "de"),
    ],
)
def test_error_handlers_follow_request_language(client, handler, query, cookie, expected):
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = cookie

    def fail(request):
        if handler is server_error:
            return handler(request)
        return handler(request, "test")

    with patch("core.views.auth.CustomLoginView.get", lambda self, request, **kwargs: fail(request)):
        response = client.get("/login/" + query)
    assert response.status_code in (403, 500)
    assert f'<html lang="{expected}"' in response.content.decode()
    assert 'data-testid="app-version"' not in response.content.decode()


def test_server_error_without_request_context_uses_active_language():
    request = RequestFactory().get("/failed/")
    with translation.override("en"):
        response = server_error(request)
    assert b'<html lang="en"' in response.content


@pytest.mark.parametrize(
    "query,cookie,expected",
    [
        ("", "", "de"),
        ("?lang=en", "", "en"),
        ("", "en", "en"),
        ("?lang=invalid", "", "de"),
        ("?lang=invalid", "en", "en"),
        ("?lang=de", "en", "de"),
    ],
)
def test_maintenance_ignores_previous_request_language(query, cookie, expected):
    request = RequestFactory().get("/login/" + query)
    request.COOKIES[settings.LANGUAGE_COOKIE_NAME] = cookie
    with (
        override_settings(MAINTENANCE_FLAG_FILE="unused"),
        patch.object(
            MaintenanceModeMiddleware,
            "_is_maintenance_active",
            return_value=True,
        ),
        translation.override("en" if expected == "de" else "de"),
    ):
        response = MaintenanceModeMiddleware(lambda request: HttpResponse())(request)
    assert response.status_code == 503
    assert f'<html lang="{expected}"' in response.content.decode()


@pytest.mark.parametrize(
    "query,cookie,expected",
    [
        ("", "", "de"),
        ("?lang=en", "", "en"),
        ("", "en", "en"),
        ("?lang=invalid", "", "de"),
        ("?lang=invalid", "en", "en"),
        ("?lang=de", "en", "de"),
    ],
)
def test_mfa_prompt_keeps_language_without_version(client, staff_user, query, cookie, expected):
    from django_otp.plugins.otp_totp.models import TOTPDevice

    TOTPDevice.objects.create(user=staff_user, name="test", confirmed=True)
    client.force_login(staff_user)
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = cookie
    response = client.get("/mfa/verify/" + query)
    assert response.status_code == 200
    assert f'<html lang="{expected}"' in response.content.decode()
    assert b'data-testid="app-version"' not in response.content


@pytest.mark.parametrize("shell_language", ["de", "en"])
def test_offline_shell_keeps_its_explicit_language(client, shell_language):
    response = client.get(f"/offline/?lang=en&shell_language={shell_language}")
    assert response.status_code == 200
    assert f'<html lang="{shell_language}"' in response.content.decode()


@pytest.mark.parametrize("failure", ["role", "unavailable", "account", "password", "success"])
def test_demo_start_preserves_visit_and_shared_account(client, settings, staff_user, failure):
    from types import SimpleNamespace

    from core.demo import DEMO_PASSWORD

    settings.DEMO_MODE = True
    staff_user.set_password(DEMO_PASSWORD if failure != "password" else "different")
    staff_user.save(update_fields=["password"])
    scenario = SimpleNamespace(key="kontaktladen", demo_roles=("staff",))
    with (
        patch("core.views.demo_scenarios._lookup_scenario", return_value=scenario),
        patch(
            "core.views.demo_scenarios.facility_for_scenario",
            return_value=(staff_user.facility, failure != "unavailable"),
        ),
        patch("core.views.demo_scenarios.account_for", return_value=None if failure == "account" else staff_user),
    ):
        response = client.post(
            "/demo/start/?lang=en&bereich=kontaktladen",
            {
                "scenario": "kontaktladen",
                "role": "invalid" if failure == "role" else "staff",
            },
        )
    assert response.status_code == 302
    if failure == "success":
        assert response.url == settings.LOGIN_REDIRECT_URL
        assert client.session["_auth_user_id"] == str(staff_user.pk)
        assert "cookies" not in response["Clear-Site-Data"]
    else:
        query = parse_qs(urlsplit(response.url).query)
        assert query["bereich"] == ["kontaktladen"]
        assert query["lang"] == ["en"]
    assert b'<html lang="en"' in client.get("/login/").content
    staff_user.refresh_from_db()
    assert staff_user.preferred_language == "de"


def test_account_preference_applies_without_visit_choice(client, staff_user):
    staff_user.preferred_language = "en"
    staff_user.save(update_fields=["preferred_language"])
    client.force_login(staff_user)
    assert b'<html lang="en"' in client.get("/login/").content


@pytest.mark.parametrize("target", ["https://evil.example/", "http://[invalid"])
def test_setter_keeps_safe_redirect_boundary(client, target):
    response = client.post(reverse("set_language"), {"language": "en", "next": target})
    assert response.status_code == 302
    assert response.url == "/"
    assert response.cookies[settings.LANGUAGE_COOKIE_NAME].value == "en"


@pytest.mark.parametrize("language", ["invalid", "en-us", "EN", ""])
def test_setter_ignores_unsupported_language(client, language):
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = "de"
    response = client.post(reverse("set_language"), {"language": language, "next": "/login/"})
    assert settings.LANGUAGE_COOKIE_NAME not in response.cookies
    assert b'<html lang="de"' in client.get(response.url).content
