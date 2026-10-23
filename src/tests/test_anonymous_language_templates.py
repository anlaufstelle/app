"""Public language switcher and navigation boundaries (Refs #2609)."""

from urllib.parse import parse_qs, urlsplit

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("path", ["/login/", "/demo/"])
def test_anonymous_switcher_preserves_parameters(client, settings, path):
    settings.DEMO_MODE = True
    response = client.get(path + "?lang=en&next=%2Fclients%2F&bereich=kontaktladen")
    html = response.content.decode()
    assert 'action="/i18n/setlang/"' in html
    assert 'name="language" value="en" aria-pressed="true"' in html
    assert 'name="language" value="de" aria-pressed="false"' in html
    response = client.post(
        reverse("set_language"),
        {
            "language": "de",
            "next": path + "?lang=en&next=%2Fclients%2F&bereich=kontaktladen",
        },
        follow=True,
    )
    assert b'<html lang="de"' in response.content
    query = parse_qs(urlsplit(response.redirect_chain[0][0]).query)
    assert query["next"] == ["/clients/"]
    assert query["bereich"] == ["kontaktladen"]


@pytest.mark.parametrize("path", ["/impressum/", "/datenschutzerklaerung/", "/missing-language-page/"])
def test_public_layout_does_not_expose_navigation(client, settings, path):
    settings.DEMO_MODE = True
    response = client.get(path + "?lang=en")
    assert b'aria-label="Main navigation"' not in response.content
    assert b'aria-label="Hauptnavigation"' not in response.content
