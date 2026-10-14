"""The post-login target survives the scripted login (session expiry returns to the work in progress)."""

from urllib.parse import quote

import pytest
from django.urls import reverse

POST_ONLY_TARGETS = [
    pytest.param(lambda: reverse("offline_key_salt"), id="class-view"),
    pytest.param(lambda: reverse("set_language"), id="decorated-function-view"),
    pytest.param(lambda: reverse("core:workitem_bulk_status"), id="htmx-mutation"),
]


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

    @pytest.mark.parametrize("target", POST_ONLY_TARGETS)
    def test_post_only_target_falls_back_to_role_default(self, client, staff_user, target):
        """A mutation endpoint cannot be navigated to; the resume lands on the role default."""
        client.force_login(staff_user)
        data = client.post(f"{reverse('offline_key_salt')}?next={target()}").json()
        assert data["home_url"] == "/"

    def test_post_only_target_for_super_admin_lands_in_system_area(self, client, super_admin_user):
        client.force_login(super_admin_user)
        data = client.post(f"{reverse('offline_key_salt')}?next={reverse('offline_key_salt')}").json()
        assert data["home_url"] == "/system/"

    def test_navigable_target_with_query_is_kept(self, client, staff_user):
        client.force_login(staff_user)
        target = f"{reverse('core:workitem_inbox')}?status=open"
        encoded = quote(target, safe="")
        data = client.post(f"{reverse('offline_key_salt')}?next={encoded}").json()
        assert data["home_url"] == target


@pytest.mark.django_db
class TestNativeLoginHonoursNavigability:
    """The native sign-in shares the target resolution with the salt endpoint."""

    @pytest.mark.parametrize("target", POST_ONLY_TARGETS)
    def test_post_only_next_is_not_followed(self, client, staff_user, target):
        from core.views.auth import resolve_post_login_url

        request = client.get(reverse("login")).wsgi_request
        request.user = staff_user
        assert resolve_post_login_url(request, target()) == "/"

    def test_navigable_next_is_followed(self, client, staff_user):
        from core.views.auth import resolve_post_login_url

        request = client.get(reverse("login")).wsgi_request
        request.user = staff_user
        assert resolve_post_login_url(request, reverse("core:workitem_inbox")) == reverse("core:workitem_inbox")
