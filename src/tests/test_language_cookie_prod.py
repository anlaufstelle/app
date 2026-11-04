"""Language cookie hardening in production settings (Refs #2619).

The language cookie only carries a language code, but production sends it with
the same transport flags as the session cookie. The flags are read from the
production settings module as it is actually loaded (subprocess import), then
applied to a real ``POST /i18n/setlang/`` round trip.
"""

import json

import pytest
from django.conf import settings
from django.test import override_settings
from django.urls import reverse

from tests.test_dev_settings import _run_dev_import
from tests.test_prod_settings import _run_prod_import

_PROD_ENV = {
    "DJANGO_SECRET_KEY": "x" * 50,
    "ALLOWED_HOSTS": "example.org",
    "ENCRYPTION_KEYS": "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE=",
    "DJANGO_AUDIT_HASH_KEY": "y" * 50,
}

# Read through django.conf.settings so Django's global defaults apply exactly
# as they would at runtime for the module named in DJANGO_SETTINGS_MODULE.
_READ_FLAGS = (
    "import json; from django.conf import settings as s; "
    "print(json.dumps({"
    "'LANGUAGE_COOKIE_SECURE': s.LANGUAGE_COOKIE_SECURE, "
    "'LANGUAGE_COOKIE_HTTPONLY': s.LANGUAGE_COOKIE_HTTPONLY, "
    "'LANGUAGE_COOKIE_SAMESITE': s.LANGUAGE_COOKIE_SAMESITE}))"
)


def _last_json_line(result):
    assert result.returncode == 0, f"settings import failed: {result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def prod_language_cookie_flags():
    return _last_json_line(_run_prod_import(_PROD_ENV, code=_READ_FLAGS))


def test_prod_settings_harden_language_cookie(prod_language_cookie_flags):
    assert prod_language_cookie_flags == {
        "LANGUAGE_COOKIE_SECURE": True,
        "LANGUAGE_COOKIE_HTTPONLY": True,
        "LANGUAGE_COOKIE_SAMESITE": "Lax",
    }


def test_dev_settings_keep_language_cookie_usable_over_http():
    result = _run_dev_import(expr=_READ_FLAGS)
    assert _last_json_line(result)["LANGUAGE_COOKIE_SECURE"] is False


def _assert_hardened(morsel):
    assert morsel["secure"] is True
    assert morsel["httponly"] is True
    assert morsel["samesite"] == "Lax"


@pytest.mark.django_db
@pytest.mark.parametrize(("start", "target"), [("de", "en"), ("en", "de")])
def test_setlang_with_prod_flags_sets_hardened_cookie_and_keeps_preference(
    client, staff_user, prod_language_cookie_flags, start, target
):
    staff_user.preferred_language = start
    staff_user.save(update_fields=["preferred_language"])
    client.force_login(staff_user)
    with override_settings(**prod_language_cookie_flags):
        response = client.post(reverse("set_language"), {"language": target, "next": "/"})
    assert response.status_code == 302
    morsel = response.cookies[settings.LANGUAGE_COOKIE_NAME]
    assert morsel.value == target
    _assert_hardened(morsel)
    staff_user.refresh_from_db()
    assert staff_user.preferred_language == target


@pytest.mark.django_db
def test_explicit_language_link_with_prod_flags_sets_hardened_cookie(client, prod_language_cookie_flags):
    with override_settings(**prod_language_cookie_flags):
        response = client.get("/login/?lang=en")
    morsel = response.cookies[settings.LANGUAGE_COOKIE_NAME]
    assert morsel.value == "en"
    _assert_hardened(morsel)
