"""Regressions for public language selection and blocked accounts (Refs #2609)."""

from urllib.parse import parse_qs, urlsplit

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("blocked", ["archived", "missing"])
@pytest.mark.parametrize(
    "query,cookie,expected", [("?lang=en", "de", "en"), ("", "en", "en"), ("?lang=de", "en", "de")]
)
def test_facility_block_uses_visit_language(client, staff_user, blocked, query, cookie, expected):
    if blocked == "archived":
        staff_user.facility.is_active = False
        staff_user.facility.save(update_fields=["is_active"])
    else:
        staff_user.facility = None
        staff_user.save(update_fields=["facility"])
    client.force_login(staff_user)
    client.cookies["django_language"] = cookie
    response = client.get("/" + query)
    assert response.status_code == 403
    assert response["Content-Language"] == expected
    assert f'<html lang="{expected}"' in response.content.decode()


@pytest.mark.parametrize("target,expected", [("/login/", "/login/"), ("//evil.com", "/")])
def test_setter_preserves_query_next_and_safety(client, target, expected):
    response = client.post(reverse("set_language"), {"language": "en"}, QUERY_STRING="next=" + target)
    assert response.status_code == 302
    assert response.url == expected


def test_demo_start_preserves_area_from_post_body(client, settings):
    settings.DEMO_MODE = True
    response = client.post("/demo/start/", {"scenario": "invalid", "bereich": "kontaktladen"})
    assert parse_qs(urlsplit(response.url).query)["bereich"] == ["kontaktladen"]


def test_explicit_language_survives_demo_root_redirect(client, settings):
    settings.DEMO_MODE = True
    response = client.get("/?lang=en", follow=True)
    assert urlsplit(response.redirect_chain[0][0]).path == "/demo/"
    assert b'<html lang="en"' in response.content
    assert client.cookies[settings.LANGUAGE_COOKIE_NAME].value == "en"
