"""MFA completion survives stale admin writes and session or authentication faults."""

from importlib import import_module

import pytest
from django.contrib.auth import HASH_SESSION_KEY
from django.db import OperationalError
from django_otp.oath import totp

from core.models import EncryptedTOTPDevice, User
from core.services.security import verify_totp_or_backup
from tests.test_mfa_session_revocation import _audits, _browser, _prepare
from tests.test_mfa_session_revocation import test_only_other_account_sessions_end as check_normal_revocation

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["db", "cache", "cached_db"])
def session_backend(request, settings):
    settings.SESSION_ENGINE = f"django.contrib.sessions.backends.{request.param}"
    return import_module(settings.SESSION_ENGINE).SessionStore


@pytest.mark.parametrize("action", ["change", "disable"])
def test_normal_revocation_across_backends(action, session_backend, settings, staff_user, lead_user):
    check_normal_revocation(action, settings, staff_user, lead_user)


@pytest.mark.parametrize("action", ["change", "disable"])
@pytest.mark.parametrize("failure_stage", ["create", "final_save", "final_save_after_write"])
def test_session_storage_failure_preserves_ceremony(action, failure_stage, session_backend, staff_user, monkeypatch):
    old = EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=True)
    current, other = _browser(staff_user), _browser(staff_user)
    url, data, pending = _prepare(current, staff_user, action)
    before = User.objects.get(pk=staff_user.pk).password
    old_session_hash = current.session[HASH_SESSION_KEY]
    old_session_key = current.session.session_key
    ceremony = dict(current.session)

    def unavailable(self):
        raise OperationalError("Session storage unavailable")

    original_save = session_backend.save

    def fail_final_save(self, must_create=False):
        if not must_create and self.get(HASH_SESSION_KEY) != old_session_hash:
            if failure_stage == "final_save_after_write":
                original_save(self, must_create=must_create)
            raise OperationalError("Final session save unavailable")
        return original_save(self, must_create=must_create)

    # Fail precisely when cycle_key tries to create the successor session;
    # authentication and the MFA transaction have normal database access.
    with monkeypatch.context() as fault:
        if failure_stage == "create":
            fault.setattr(session_backend, "create", unavailable)
        else:
            fault.setattr(session_backend, "save", fail_final_save)
        current.raise_request_exception = False
        response = current.post(url, data)
    current.raise_request_exception = True
    stored = User.objects.get(pk=staff_user.pk)
    still_confirmed = EncryptedTOTPDevice.objects.filter(pk=old.pk, confirmed=True).exists()
    still_pending = bool(pending and EncryptedTOTPDevice.objects.filter(pk=pending.pk, confirmed=False).exists())
    own = current.get("/mfa/settings/")
    foreign = other.get("/mfa/settings/")
    observed = (
        response.status_code,
        still_confirmed,
        still_pending,
        _audits(staff_user, action).count(),
        own.status_code,
        foreign.status_code,
    )
    assert stored.password == before and still_confirmed and own.status_code == 200, observed
    assert response.status_code == 302
    assert foreign.status_code == 200
    assert _audits(staff_user, action).count() == 0
    assert current.session.session_key == old_session_key
    assert current.session["local_work_marker"] == "keep"
    token = totp(old.bin_key, step=old.step, t0=old.t0, digits=old.digits)
    assert verify_totp_or_backup(staff_user, str(token).zfill(old.digits))
    if pending:
        assert still_pending
        for key in ("mfa_setup_pending", "mfa_setup_device_pk", "mfa_backup_codes"):
            assert current.session[key] == ceremony[key]
    assert current.post(url, data).status_code == 302
    assert current.get("/mfa/settings/").status_code == 200
    assert other.get("/mfa/settings/").status_code == 302
    assert _audits(staff_user, action).count() == 1


@pytest.mark.parametrize("session_backend", ["cache", "cached_db"], indirect=True)
@pytest.mark.parametrize("action", ["change", "disable"])
@pytest.mark.parametrize("cache_fault", ["silent", "exception"])
def test_successor_cache_write_must_be_visible(action, cache_fault, session_backend, staff_user, monkeypatch):
    old = EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=True)
    current, other = _browser(staff_user), _browser(staff_user)
    url, data, pending = _prepare(current, staff_user, action)
    before = current.session[HASH_SESSION_KEY]
    backend_cache = session_backend()._cache
    original_set = backend_cache.set

    def fail_successor_write(key, value, *args, **kwargs):
        if isinstance(value, dict) and value.get(HASH_SESSION_KEY, before) != before:
            if cache_fault == "exception":
                raise OperationalError("Successor cache write unavailable")
            return False
        return original_set(key, value, *args, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(backend_cache, "set", fail_successor_write)
        response = current.post(url, data)

    assert response.status_code == 302
    assert EncryptedTOTPDevice.objects.filter(pk=old.pk, confirmed=True).exists()
    assert not _audits(staff_user, action).exists()
    assert current.get("/mfa/settings/").status_code == 200
    assert other.get("/mfa/settings/").status_code == 200
    if pending:
        assert current.session["mfa_setup_device_pk"] == pending.pk
    assert current.post(url, data).status_code == 302
    assert _audits(staff_user, action).count() == 1
    assert other.get("/mfa/settings/").status_code == 302


@pytest.mark.parametrize("action", ["change", "disable"])
def test_completed_session_needs_no_second_write(action, session_backend, staff_user, settings, monkeypatch):
    settings.SESSION_SAVE_EVERY_REQUEST = True
    settings.MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"
    EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=True)
    current, other = _browser(staff_user), _browser(staff_user)
    url, data, _pending = _prepare(current, staff_user, action)
    old_hash = current.session[HASH_SESSION_KEY]
    original_save = session_backend.save
    writes = []

    def save_once(self, must_create=False):
        if not must_create and self.get(HASH_SESSION_KEY) != old_hash:
            writes.append(dict(self))
            if len(writes) > 1:
                raise OperationalError("A second session write would fail after commit")
        return original_save(self, must_create=must_create)

    with monkeypatch.context() as fault:
        fault.setattr(session_backend, "save", save_once)
        response = current.post(url, data)
    assert response.status_code == 302
    assert len(writes) == 1
    assert "_messages" in writes[0]
    assert current.get("/mfa/settings/").status_code == 200
    assert other.get("/mfa/settings/").status_code == 302
