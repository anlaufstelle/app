"""Inherited password handlers and POST functions must also throttle attempts."""

import pytest
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from core.services.security import invite_token_generator


@pytest.mark.django_db
@pytest.mark.parametrize("route", ["password_change", "set_language"])
def test_authenticated_mutation_attempts_are_throttled(client, staff_user, ratelimit_enabled, route):
    client.force_login(staff_user)
    url = reverse(route)
    for _ in range(60):
        assert client.post(url).status_code != 429
    assert client.post(url).status_code == 429


@pytest.mark.django_db
@pytest.mark.parametrize("route", ["password_reset_confirm", "invite_confirm"])
def test_anonymous_password_setup_attempts_are_throttled(client, staff_user, ratelimit_enabled, route):
    url = reverse(
        route,
        kwargs={
            "uidb64": urlsafe_base64_encode(force_bytes(staff_user.pk)),
            "token": (invite_token_generator if route == "invite_confirm" else default_token_generator).make_token(
                staff_user
            ),
        },
    )
    response = client.get(url)
    assert response.status_code == 302
    url = response.url
    for _ in range(5):
        assert client.post(url).status_code != 429
    assert client.post(url).status_code == 429
