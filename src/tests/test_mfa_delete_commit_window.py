"""Probe a stale browser request at the MFA transaction's commit boundary."""

from concurrent.futures import ThreadPoolExecutor
from importlib import import_module

import pytest
from django.contrib.auth import HASH_SESSION_KEY
from django.contrib.auth.middleware import AuthenticationMiddleware
from django.db import connections
from django.http import HttpResponse
from django.test import Client, RequestFactory

from core.middleware.demo_session import DemoSessionMiddleware
from core.models import EncryptedTOTPDevice, User
from tests.test_mfa_passkey_session_revocation import _passkey
from tests.test_mfa_session_revocation import _browser, _prepare


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("backend", ["db", "cache", "cached_db"])
@pytest.mark.parametrize("action", ["passkey-remove", "totp-remove", "totp-replace", "password-change"])
def test_stale_request_at_predecessor_deletion_keeps_successor_cookie(
    action, backend, staff_user, settings, monkeypatch
):
    settings.SESSION_ENGINE = f"django.contrib.sessions.backends.{backend}"
    EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=True)
    credential = _passkey(staff_user)
    current = _browser(staff_user)
    if action == "passkey-remove":
        url, data = f"/mfa/passkey/{credential.pk}/delete/", {}
    elif action == "password-change":
        url = "/password-change/"
        data = {
            "old_password": "testpass123",
            "new_password1": "New-secure-password-2736!",
            "new_password2": "New-secure-password-2736!",
        }
    else:
        # TOTP removal also removes passkeys; replacement completes the ceremony.
        url, data, _pending = _prepare(current, staff_user, "disable" if action == "totp-remove" else "change")
    assert current.get("/mfa/settings/").status_code == 200
    old_key = current.session.session_key
    session_store = import_module(settings.SESSION_ENGINE).SessionStore
    delete = session_store.delete
    stale_responses = []
    entered = False

    def background_request():
        try:
            stale = Client()
            stale.cookies[settings.SESSION_COOKIE_NAME] = old_key
            return stale.get("/manifest.json")
        finally:
            connections.close_all()

    def delete_after_parallel_request(session, session_key=None):
        nonlocal entered
        if (session_key or session.session_key) == old_key and not entered:
            entered = True
            with ThreadPoolExecutor(max_workers=1) as pool:
                stale_responses.append(pool.submit(background_request).result(timeout=10))
        return delete(session, session_key)

    monkeypatch.setattr(session_store, "delete", delete_after_parallel_request)
    response = current.post(url, data)
    assert response.status_code == 302
    assert entered and len(stale_responses) == 1
    assert current.session.session_key != old_key
    assert current.get("/mfa/settings/").status_code == 200
    current.cookies.update(stale_responses[0].cookies)
    assert current.get("/mfa/settings/").status_code == 200, "a late stale response must preserve the acting session"

    stale_response = stale_responses[0]
    assert stale_response.wsgi_request.user.is_anonymous
    assert settings.SESSION_COOKIE_NAME not in stale_response.cookies
    assert "Cookie" in stale_response.get("Vary", "")
    assert not session_store().exists(old_key)
    revoked = Client()
    revoked.cookies[settings.SESSION_COOKIE_NAME] = old_key
    denied = revoked.get("/mfa/settings/")
    assert denied.status_code == 302
    assert denied.url.startswith("/login/")
    assert denied.wsgi_request.user.is_anonymous
    assert settings.SESSION_COOKIE_NAME not in denied.cookies
    assert HASH_SESSION_KEY not in denied.wsgi_request.session


@pytest.mark.django_db
@pytest.mark.parametrize("backend", ["db", "cache", "cached_db"])
def test_rejected_auth_hash_cannot_publish_anonymous_session_writes(backend, settings, staff_user):
    settings.SESSION_ENGINE = f"django.contrib.sessions.backends.{backend}"
    browser = _browser(staff_user)
    old_key = browser.session.session_key
    staff_user.set_password("Changed-password-2736!")
    staff_user.save(update_fields=["password"])
    request = RequestFactory().get("/", HTTP_COOKIE=f"{settings.SESSION_COOKIE_NAME}={old_key}")
    middleware = DemoSessionMiddleware(lambda request: HttpResponse())
    middleware.process_request(request)
    AuthenticationMiddleware(lambda request: HttpResponse()).process_request(request)
    assert request.user.is_anonymous
    request.session["notice"] = "Anonymous session-backed message"

    response = middleware.process_response(request, HttpResponse())

    assert settings.SESSION_COOKIE_NAME not in response.cookies
    assert "Cookie" in response.get("Vary", "")
    assert not import_module(settings.SESSION_ENGINE).SessionStore().exists(old_key)


@pytest.mark.django_db
def test_successful_login_with_rejected_auth_hash_publishes_new_session(settings, staff_user):
    browser = _browser(staff_user)
    old_key = browser.session.session_key
    staff_user.set_password("Changed-password-2736!")
    staff_user.save(update_fields=["password"])

    response = browser.post("/login/", {"username": staff_user.username, "password": "Changed-password-2736!"})

    assert response.status_code == 302
    assert response.wsgi_request.user.is_authenticated
    assert response.cookies[settings.SESSION_COOKIE_NAME].value != old_key
    assert browser.session[HASH_SESSION_KEY] == User.objects.get(pk=staff_user.pk).get_session_auth_hash()


@pytest.mark.django_db
@pytest.mark.parametrize("backend", ["db", "cache", "cached_db"])
@pytest.mark.parametrize("predecessor_retained", [False, True])
@pytest.mark.parametrize("save_every_request", [False, True])
def test_request_authenticated_before_rotation_cannot_publish_predecessor_cookie(
    backend, predecessor_retained, save_every_request, settings, staff_user, monkeypatch
):
    settings.SESSION_ENGINE = f"django.contrib.sessions.backends.{backend}"
    settings.SESSION_SAVE_EVERY_REQUEST = save_every_request
    EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=True)
    credential = _passkey(staff_user)
    current = _browser(staff_user)
    old_key = current.session.session_key
    request = RequestFactory().get("/manifest.json", HTTP_COOKIE=f"{settings.SESSION_COOKIE_NAME}={old_key}")
    middleware = DemoSessionMiddleware(lambda request: HttpResponse())
    middleware.process_request(request)
    AuthenticationMiddleware(lambda request: HttpResponse()).process_request(request)
    assert request.user.is_authenticated
    if not save_every_request:
        request.session["notice"] = "A write from an already running request"
    session_store = import_module(settings.SESSION_ENGINE).SessionStore
    original_delete = session_store.delete

    def retain_predecessor(session, session_key=None):
        if (session_key or session.session_key) != old_key:
            return original_delete(session, session_key)
        return None

    with monkeypatch.context() as delay:
        if predecessor_retained:
            delay.setattr(session_store, "delete", retain_predecessor)
        rotation = current.post(f"/mfa/passkey/{credential.pk}/delete/")
    assert rotation.status_code == 302
    assert current.session.session_key != old_key
    assert session_store().exists(old_key) is predecessor_retained

    response = middleware.process_response(request, HttpResponse())

    assert settings.SESSION_COOKIE_NAME not in response.cookies
    assert "Cookie" in response.get("Vary", "")
    assert not session_store().exists(old_key)
    current.cookies.update(response.cookies)
    assert current.get("/mfa/settings/").status_code == 200


@pytest.mark.django_db
def test_secret_key_fallback_still_rotates_authenticated_session(settings, staff_user):
    current = _browser(staff_user)
    old_key = current.session.session_key
    old_secret = settings.SECRET_KEY
    settings.SECRET_KEY = "New-secret-key-for-session-fallback-2736"
    settings.SECRET_KEY_FALLBACKS = [old_secret]

    response = current.get("/mfa/settings/")

    assert response.status_code == 200
    assert response.wsgi_request.user.is_authenticated
    assert response.cookies[settings.SESSION_COOKIE_NAME].value != old_key
    assert current.session[HASH_SESSION_KEY] == staff_user.get_session_auth_hash()
