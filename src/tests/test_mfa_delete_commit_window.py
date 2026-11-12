"""Probe a stale browser request at the MFA transaction's commit boundary."""

from concurrent.futures import ThreadPoolExecutor
from importlib import import_module

import pytest
from django.db import connections
from django.test import Client

from core.models import EncryptedTOTPDevice
from tests.test_mfa_passkey_session_revocation import _passkey
from tests.test_mfa_session_revocation import _browser


@pytest.mark.django_db(transaction=True)
def test_stale_request_at_predecessor_deletion_keeps_successor_cookie(staff_user, settings, monkeypatch):
    settings.SESSION_ENGINE = "django.contrib.sessions.backends.db"
    EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=True)
    credential = _passkey(staff_user)
    current = _browser(staff_user)
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
    response = current.post(f"/mfa/passkey/{credential.pk}/delete/")
    assert response.status_code == 302
    assert entered and len(stale_responses) == 1
    assert current.session.session_key != old_key
    assert current.get("/mfa/settings/").status_code == 200
    current.cookies.update(stale_responses[0].cookies)
    assert current.get("/mfa/settings/").status_code == 200, "a late stale response must preserve the acting session"
