"""The post-login target survives the scripted login (session expiry returns to the work in progress)."""

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestHomeUrlHonoursNext:
    """The scripted login cannot read the redirect, so the salt endpoint answers with the target."""

    def test_safe_next_is_returned_for_regular_roles(self, client, staff_user):
        client.force_login(staff_user)
        data = client.post(f"{reverse('offline_key_salt')}?next=/events/new/%3Fclient%3D1").json()
        assert data["home_url"] == "/events/new/?client=1"

    @pytest.mark.parametrize("target", ["https://evil.example/x", "//evil.example/x", "javascript:alert(1)"])
    def test_foreign_next_falls_back_to_home(self, client, staff_user, target):
        client.force_login(staff_user)
        data = client.post(f"{reverse('offline_key_salt')}?next={target}").json()
        assert data["home_url"] == "/"

    def test_without_next_home_is_unchanged(self, client, staff_user):
        client.force_login(staff_user)
        assert client.post(reverse("offline_key_salt")).json()["home_url"] == "/"

    def test_super_admin_keeps_system_area_for_implicit_root(self, client, super_admin_user):
        client.force_login(super_admin_user)
        assert client.post(f"{reverse('offline_key_salt')}?next=/").json()["home_url"] == "/system/"
