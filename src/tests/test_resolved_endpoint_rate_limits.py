"""Registered framework endpoints must enforce their mutation budgets (Refs #2529)."""

import pytest
from django.urls import reverse


@pytest.mark.django_db
@pytest.mark.parametrize("route", ["admin:index", "admin:core_documenttype_add"])
def test_admin_mutations_are_limited_before_the_framework_view(client, ratelimit_enabled, route):
    url = reverse(route)
    for _ in range(60):
        assert client.post(url).status_code != 429
    assert client.post(url).status_code == 429
    assert client.get(url).status_code != 429


@pytest.mark.django_db
def test_passkey_authentication_begin_has_a_mutation_budget(client, staff_user, ratelimit_enabled):
    client.force_login(staff_user)
    url = reverse("otp_webauthn:credential-authentication-begin")
    for _ in range(60):
        assert client.post(url, data="{}", content_type="application/json").status_code != 429
    assert client.post(url, data="{}", content_type="application/json").status_code == 429


@pytest.mark.django_db
def test_admin_login_put_and_post_share_the_ip_budget(client, ratelimit_enabled):
    url = reverse("admin:login")
    for attempt in range(5):
        response = client.put(url) if attempt % 2 == 0 else client.post(url)
        assert response.status_code != 429
    assert client.put(url).status_code == 429
    assert client.post(url).status_code == 429


@pytest.mark.django_db
def test_admin_logout_remains_available_after_mutation_exhaustion(client, super_admin_user, ratelimit_enabled):
    client.force_login(super_admin_user)
    url = reverse("admin:index")
    for _ in range(61):
        client.post(url)
    for _ in range(3):
        client.force_login(super_admin_user)
        assert client.post(reverse("admin:logout")).status_code != 429
        assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_admin_login_ip_budget_survives_authentication_state_change(client, super_admin_user, ratelimit_enabled):
    url = reverse("admin:login")
    for _ in range(5):
        assert client.post(url).status_code != 429
    client.force_login(super_admin_user)
    assert client.put(url).status_code == 429


@pytest.mark.django_db
@pytest.mark.parametrize("route", ["demo_scenario_select", "demo_scenario_start"])
def test_disabled_demo_routes_keep_404_with_exhausted_counters(client, settings, ratelimit_enabled, monkeypatch, route):
    settings.DEMO_MODE = False
    monkeypatch.setattr("django_ratelimit.decorators.is_ratelimited", lambda **kwargs: True)
    assert client.post(reverse(route)).status_code == 404
