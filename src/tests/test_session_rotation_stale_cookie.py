"""Stale-cookie requests after a session-ID rotation (Refs #2034).

``rotate_session_after_privilege_gain`` deletes the old ``django_session`` row.
A request that is still on its way with the OLD ``sessionid`` cookie — for
example a service-worker precache fetch started before the rotating POST —
reaches the app after the row is gone. These tests pin down what such a
request gets back through the real middleware stack.

Current behaviour (documented here, not endorsed): Django's
``SessionMiddleware.process_response`` sees a session cookie in the request
and an empty session, so it answers with ``Set-Cookie: sessionid=""`` and an
expiry in 1970. A browser shares one cookie jar between the page and the
service worker; if that answer is applied AFTER the rotation response, it
deletes the NEW cookie and the user is signed out although the new session
row still exists.

The security side holds either way: the old cookie never inherits the raised
privilege (A9) — the stale request is anonymous.
"""

from __future__ import annotations

import pytest
from django.conf import settings as django_settings
from django.contrib.sessions.models import Session
from django.test import Client
from django.urls import reverse

from core.models import EncryptedTOTPDevice as TOTPDevice
from core.services.security import SUDO_SESSION_KEY
from tests.test_session_rotation import ADMIN_PASSWORD, STAFF_PASSWORD, _login, _valid_token

COOKIE = django_settings.SESSION_COOKIE_NAME

# Django-rendered URLs a stale request can plausibly hit right after the
# rotation: the service worker's install precache (``sw.js`` APP_SHELL holds
# ``/manifest.json`` and the ``/offline/…`` shells), a plain page and an HTMX
# partial request (``HtmxSessionMiddleware`` rewrites its login redirect).
STALE_REQUESTS = [
    pytest.param("/manifest.json", {}, id="sw-precache-manifest"),
    pytest.param("/offline/", {}, id="sw-precache-offline-home"),
    pytest.param("/", {}, id="page"),
    pytest.param("/", {"HTTP_HX_REQUEST": "true"}, id="htmx"),
]


def _stale_client(old_key: str) -> Client:
    stale = Client()
    stale.cookies[COOKIE] = old_key
    return stale


def _assert_deletes_session_cookie(response) -> None:
    morsel = response.cookies.get(COOKIE)
    assert morsel is not None, "stale response carries no session cookie header"
    assert morsel.value == ""
    assert str(morsel["max-age"]) == "0"
    assert morsel["expires"] == "Thu, 01 Jan 1970 00:00:00 GMT"


def _session_exists(key: str) -> bool:
    return Session.objects.filter(session_key=key).exists()


def _grant_sudo(client, admin_user):
    admin_user.set_password(ADMIN_PASSWORD)
    admin_user.save()
    old_key = _login(client, admin_user.username, ADMIN_PASSWORD)
    response = client.post(reverse("sudo_mode"), {"password": ADMIN_PASSWORD, "next": "/"})
    assert response.status_code == 302
    new_key = client.session.session_key
    assert new_key != old_key
    assert response.cookies[COOKIE].value == new_key
    return old_key, new_key, response


def _confirm_mfa_setup(client, staff_user):
    _login(client, staff_user.username, STAFF_PASSWORD)
    client.get("/mfa/setup/")
    device = TOTPDevice.objects.get(user=staff_user, confirmed=False)
    response = client.post("/mfa/setup/", {"token": _valid_token(device)})
    assert response.url == "/mfa/backup-codes/"
    old_key = client.session.session_key
    response = client.post("/mfa/backup-codes/", {"confirm_saved": "1"})
    assert response.status_code == 302
    new_key = client.session.session_key
    assert new_key != old_key
    assert response.cookies[COOKIE].value == new_key
    return old_key, new_key, response


@pytest.mark.django_db
class TestStaleCookieAfterSudoGrant:
    @pytest.mark.parametrize(("path", "headers"), STALE_REQUESTS)
    def test_stale_request_answers_with_session_cookie_deletion(self, client, admin_user, path, headers):
        old_key, new_key, _ = _grant_sudo(client, admin_user)

        stale_response = _stale_client(old_key).get(path, **headers)

        _assert_deletes_session_cookie(stale_response)
        # The rotated session is untouched on the server side.
        assert _session_exists(new_key)
        assert not _session_exists(old_key)

    def test_stale_request_stays_anonymous(self, client, admin_user):
        old_key, _, _ = _grant_sudo(client, admin_user)
        stale = _stale_client(old_key)

        response = stale.get("/")

        assert response.status_code == 302
        assert response.url.startswith("/login/")
        assert SUDO_SESSION_KEY not in stale.session

    def test_deletion_applied_after_rotation_signs_browser_out(self, client, admin_user):
        old_key, new_key, _ = _grant_sudo(client, admin_user)
        stale_response = _stale_client(old_key).get("/manifest.json")

        # One browser cookie jar: the stale answer arrives last.
        client.cookies.update(stale_response.cookies)
        response = client.get("/clients/")

        assert response.status_code == 302
        assert response.url.startswith("/login/")
        assert _session_exists(new_key), "the server never ended the rotated session"

    def test_deletion_applied_before_rotation_keeps_browser_signed_in(self, client, admin_user):
        old_key, _, rotation_response = _grant_sudo(client, admin_user)
        stale_response = _stale_client(old_key).get("/manifest.json")

        client.cookies.update(stale_response.cookies)
        client.cookies.update(rotation_response.cookies)
        response = client.get("/clients/")

        assert response.status_code == 200


@pytest.mark.django_db
class TestStaleCookieAfterMfaSetupCompletion:
    @pytest.mark.parametrize(("path", "headers"), STALE_REQUESTS)
    def test_stale_request_answers_with_session_cookie_deletion(self, client, staff_user, path, headers):
        old_key, new_key, _ = _confirm_mfa_setup(client, staff_user)

        stale_response = _stale_client(old_key).get(path, **headers)

        _assert_deletes_session_cookie(stale_response)
        assert _session_exists(new_key)

    def test_deletion_applied_after_rotation_signs_browser_out(self, client, staff_user):
        old_key, new_key, _ = _confirm_mfa_setup(client, staff_user)
        stale_response = _stale_client(old_key).get("/offline/")

        client.cookies.update(stale_response.cookies)
        response = client.get("/")

        assert response.status_code == 302
        assert response.url.startswith("/login/")
        assert _session_exists(new_key)
