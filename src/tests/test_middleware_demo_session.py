"""Stale session keys must not wipe the new session cookie on the demo (Refs #2531).

Switching the demo scenario replaces the session key. Requests the old tab had
already sent (the service worker fetching the ``/offline/*`` shells right after
a page load) reach the server after the switch with the replaced key. Django's
``SessionMiddleware`` answers such a request with a deletion of the session
cookie because the session behind the key is empty, and the browser applies
that deletion to the cookie the switch has just set: the visitor lands on the
login page.

These tests drive the whole stack through the test client; the stale request is
a separate client that still holds the replaced key.
"""

import pytest
from django.conf import settings as django_settings
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db

SESSION_COOKIE = django_settings.SESSION_COOKIE_NAME


def _is_deleted(response):
    """True when the response expires the session cookie (``Max-Age=0``)."""
    morsel = response.cookies.get(SESSION_COOKIE)
    return morsel is not None and morsel.value == "" and morsel["max-age"] == 0


def _stale_client(session_key):
    client = Client()
    client.cookies[SESSION_COOKIE] = session_key
    return client


@pytest.fixture
def demo_mode(settings):
    settings.DEMO_MODE = True
    return settings


@pytest.fixture
def replaced_session_key(client, staff_user, lead_user):
    """Session key that a second login on the same client has replaced."""
    client.force_login(staff_user)
    old_key = client.session.session_key
    client.force_login(lead_user)
    assert client.session.session_key != old_key
    return old_key


class TestStaleSessionKeyOnDemo:
    def test_stale_request_does_not_delete_session_cookie(self, demo_mode, replaced_session_key):
        response = _stale_client(replaced_session_key).get(reverse("core:offline_workitem_list_shell"))

        assert response.status_code == 200
        assert not _is_deleted(response), "a response to a stale key must not touch the session cookie"

    def test_stale_request_stays_anonymous(self, demo_mode, replaced_session_key):
        """The replaced key never regains an authenticated session."""
        response = _stale_client(replaced_session_key).get(reverse("core:dashboard"))

        assert response.status_code == 302
        assert reverse("login") in response["Location"]

    def test_logout_still_deletes_session_cookie(self, demo_mode, client, staff_user):
        client.force_login(staff_user)

        response = client.post(reverse("logout"))

        assert _is_deleted(response)

    def test_unknown_key_cookie_is_deleted_outside_demo_mode(self, settings, replaced_session_key):
        settings.DEMO_MODE = False

        response = _stale_client(replaced_session_key).get(reverse("core:offline_workitem_list_shell"))

        assert _is_deleted(response)
