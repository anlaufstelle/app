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


@pytest.mark.django_db
@pytest.mark.parametrize(
    "route,limit,authenticated",
    [
        ("login", 5, False),
        ("password_reset", 5, False),
        ("core:lockout_recovery_request", 5, False),
        ("core:lockout_recovery_backup_code", 5, False),
        ("password_change", 60, True),
    ],
)
def test_inherited_put_and_post_share_the_mutation_budget(
    client, staff_user, ratelimit_enabled, route, limit, authenticated
):
    if authenticated:
        client.force_login(staff_user)
    url = reverse(route)
    for attempt in range(limit):
        response = client.put(url) if attempt % 2 == 0 else client.post(url)
        assert response.status_code != 429
    assert client.put(url).status_code == 429
    assert client.post(url).status_code == 429


@pytest.mark.django_db
@pytest.mark.parametrize("route", ["password_reset_confirm", "invite_confirm"])
def test_inherited_password_setup_put_is_throttled(client, staff_user, ratelimit_enabled, route):
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
    for attempt in range(5):
        response = client.put(url) if attempt % 2 == 0 else client.post(url)
        assert response.status_code != 429
    assert client.put(url).status_code == 429
    assert client.post(url).status_code == 429


@pytest.mark.django_db
def test_logout_remains_available_after_repeated_session_termination(client, staff_user, ratelimit_enabled):
    for _ in range(65):
        client.force_login(staff_user)
        response = client.post(reverse("logout"))
        assert response.status_code == 302
        assert response.url == "/login/"
        assert "_auth_user_id" not in client.session
        assert response["Clear-Site-Data"] == '"storage", "cache"'
