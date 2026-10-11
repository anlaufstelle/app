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
from django.contrib.sessions.models import Session
from django.http import HttpResponse
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from core.middleware.demo_session import DemoSessionMiddleware
from core.middleware.session_idle import ABSOLUTE_DEADLINE_KEY

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

    def test_unknown_key_cookie_is_preserved_outside_demo_mode(self, settings, replaced_session_key):
        settings.DEMO_MODE = False

        response = _stale_client(replaced_session_key).get(reverse("core:offline_workitem_list_shell"))

        assert SESSION_COOKIE not in response.cookies


@pytest.mark.parametrize("demo_enabled", [False, True])
class TestSessionCookieInvariants:
    @pytest.fixture(autouse=True)
    def session_mode(self, settings, demo_enabled):
        settings.DEMO_MODE = demo_enabled

    def test_logout_deletes_cookie_and_revokes_session(self, client, staff_user):
        client.force_login(staff_user)
        key = client.session.session_key

        response = client.post(reverse("logout"))

        assert _is_deleted(response)
        assert not Session.objects.filter(session_key=key).exists()
        assert response.wsgi_request.session.modified
        assert response.wsgi_request.user.is_anonymous

    @pytest.mark.parametrize("operation", ["flush", "clear"])
    def test_explicit_empty_session_clear_deletes_cookie(self, replaced_session_key, operation):
        request = RequestFactory().get("/", HTTP_COOKIE=f"{SESSION_COOKIE}={replaced_session_key}")
        middleware = DemoSessionMiddleware(lambda request: HttpResponse())
        middleware.process_request(request)

        assert not request.session.items()
        getattr(request.session, operation)()
        response = middleware.process_response(request, HttpResponse())

        assert request.session.modified
        assert _is_deleted(response)

    def test_unknown_key_session_write_sets_fresh_cookie(self, replaced_session_key):
        request = RequestFactory().get("/", HTTP_COOKIE=f"{SESSION_COOKIE}={replaced_session_key}")
        middleware = DemoSessionMiddleware(lambda request: HttpResponse())
        middleware.process_request(request)
        assert not request.session.items()
        request.session["value"] = "created"

        response = middleware.process_response(request, HttpResponse())

        key = response.cookies[SESSION_COOKIE].value
        assert key and key != replaced_session_key
        assert Session.objects.get(session_key=key).get_decoded()["value"] == "created"
        assert not Session.objects.filter(session_key=replaced_session_key).exists()

    def test_absolute_expiry_flushes_session_and_deletes_cookie(self, client, staff_user):
        client.force_login(staff_user)
        session = client.session
        session[ABSOLUTE_DEADLINE_KEY] = timezone.now().timestamp() - 1
        session.save()
        key = session.session_key

        response = client.get("/clients/")

        assert response.status_code == 302
        assert response.url.startswith("/login/")
        assert response.wsgi_request.user.is_anonymous
        assert response.wsgi_request.session.modified
        assert _is_deleted(response)
        assert not Session.objects.filter(session_key=key).exists()

    def test_idle_expiry_keeps_request_anonymous(self, client, staff_user):
        client.force_login(staff_user)
        key = client.session.session_key
        Session.objects.filter(session_key=key).update(expire_date=timezone.now() - timezone.timedelta(seconds=1))

        response = client.get("/clients/")

        assert response.status_code == 302
        assert response.url.startswith("/login/")
        assert response.wsgi_request.user.is_anonymous
        assert response.wsgi_request.session.is_empty()
        assert not response.wsgi_request.session.modified
        assert SESSION_COOKIE not in response.cookies

    def test_clear_of_existing_session_keeps_django_save_behavior(self, client, staff_user):
        client.force_login(staff_user)
        key = client.session.session_key
        request = RequestFactory().get("/", HTTP_COOKIE=f"{SESSION_COOKIE}={key}")
        middleware = DemoSessionMiddleware(lambda request: HttpResponse())
        middleware.process_request(request)

        request.session.clear()
        response = middleware.process_response(request, HttpResponse())

        assert request.session.modified
        assert response.cookies[SESSION_COOKIE].value == key
        assert Session.objects.get(session_key=key).get_decoded() == {}
        next_response = _stale_client(key).get("/clients/")
        assert next_response.status_code == 302
        assert next_response.wsgi_request.user.is_anonymous
