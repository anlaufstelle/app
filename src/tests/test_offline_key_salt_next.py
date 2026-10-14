"""The post-login target survives the scripted login (session expiry returns to the work in progress)."""

from urllib.parse import quote, urljoin

import pytest
from django.urls import reverse

POST_ONLY_TARGETS = [
    pytest.param(lambda: reverse("offline_key_salt"), id="class-view"),
    pytest.param(lambda: reverse("set_language"), id="decorated-function-view"),
    pytest.param(lambda: reverse("core:workitem_bulk_status"), id="htmx-mutation"),
]

# Spellings of the POST-only salt endpoint that a browser still navigates to: the server
# appends the slash and decodes escapes, the browser drops dot segments and reads a
# backslash as a slash.
NON_CANONICAL_SALT_TARGETS = [
    pytest.param("/auth/offline-key-salt", id="missing-slash"),
    pytest.param("/auth/offline-key%2Dsalt/", id="percent-escape"),
    pytest.param("/auth/./offline-key-salt/", id="dot-segment"),
    pytest.param("/auth/../auth/offline-key-salt/", id="parent-segment"),
    pytest.param("/auth\\offline-key-salt/", id="backslash"),
]


def _navigated_status(client, target):
    """Status of the page a browser lands on when it follows ``target`` from the login page."""
    url = urljoin("http://testserver/login/", target.replace("\\", "/"))
    return client.get(url, follow=True).status_code


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

    @pytest.mark.parametrize("target", NON_CANONICAL_SALT_TARGETS)
    def test_non_canonical_post_only_target_falls_back_to_role_default(self, client, staff_user, target):
        """The same endpoint under another spelling is just as unreachable by navigation."""
        client.force_login(staff_user)
        assert _navigated_status(client, target) == 405
        data = client.post(f"{reverse('offline_key_salt')}?next={quote(target, safe='')}").json()
        assert data["home_url"] == "/"

    def test_navigable_target_without_end_slash_is_kept(self, client, staff_user):
        """A missing end slash on a navigable page is appended by the server, so the target stays."""
        client.force_login(staff_user)
        target = reverse("core:workitem_inbox").rstrip("/")
        assert _navigated_status(client, target) == 200
        data = client.post(f"{reverse('offline_key_salt')}?next={quote(target, safe='')}").json()
        assert data["home_url"] == target

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

    @pytest.mark.parametrize("target", NON_CANONICAL_SALT_TARGETS)
    def test_non_canonical_post_only_next_is_not_followed(self, client, staff_user, target):
        from core.views.auth import resolve_post_login_url

        request = client.get(reverse("login")).wsgi_request
        request.user = staff_user
        assert resolve_post_login_url(request, target) == "/"

    def test_navigable_next_is_followed(self, client, staff_user):
        from core.views.auth import resolve_post_login_url

        request = client.get(reverse("login")).wsgi_request
        request.user = staff_user
        assert resolve_post_login_url(request, reverse("core:workitem_inbox")) == reverse("core:workitem_inbox")
