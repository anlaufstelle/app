"""Passkey removal revokes only the account's other sessions, atomically."""

from importlib import import_module

import pytest
from django.contrib.auth import HASH_SESSION_KEY
from django.db import OperationalError
from django_otp_webauthn.models import WebAuthnCredential

from core.models import AuditLog, EncryptedTOTPDevice, User
from tests.test_mfa_session_revocation import _browser

pytestmark = pytest.mark.django_db


def _passkey(user, credential_id=b"removable"):
    return WebAuthnCredential.objects.create(
        user=user,
        name="Security key",
        confirmed=True,
        credential_id=credential_id,
        public_key=b"public-key",
        aaguid="00000000-0000-0000-0000-000000000000",
        sign_count=0,
    )


def _audits(user):
    return AuditLog.objects.filter(user=user, action=AuditLog.Action.WEBAUTHN_REMOVED)


@pytest.mark.parametrize("backend", ["db", "cache", "cached_db"])
def test_removal_revokes_other_sessions_and_preserves_current_and_foreign(
    backend, settings, staff_user, lead_user, second_facility_user
):
    settings.SESSION_ENGINE = f"django.contrib.sessions.backends.{backend}"
    settings.OFFLINE_REVOCATION_SESSION_SCAN_LIMIT = 0
    totp = EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=True)
    credential = _passkey(staff_user)
    salt = staff_user.ensure_offline_key_salt()
    current, other, offline = _browser(staff_user), _browser(staff_user), _browser(staff_user)
    same_facility, foreign_facility = _browser(lead_user), _browser(second_facility_user)
    session = current.session
    session["otp_device_id"] = credential.persistent_id
    session.save()
    old_key = current.session.session_key
    assert offline.get("/api/v1/offline/revocations/").status_code == 200

    response = current.post(f"/mfa/passkey/{credential.pk}/delete/")

    assert response.status_code == 302
    signed_out = other.get("/mfa/settings/")
    assert signed_out.status_code == 302
    assert signed_out.url.startswith("/login/")
    assert offline.get("/api/v1/offline/revocations/").status_code == 302
    assert current.get("/mfa/settings/").status_code == 200
    assert same_facility.get("/mfa/settings/").status_code == 200
    assert foreign_facility.get("/mfa/settings/").status_code == 200
    assert not WebAuthnCredential.objects.filter(pk=credential.pk).exists()
    assert EncryptedTOTPDevice.objects.filter(pk=totp.pk, confirmed=True).exists()
    stored = User.objects.get(pk=staff_user.pk)
    assert stored.offline_key_salt == salt
    assert current.session.session_key != old_key
    assert current.session[HASH_SESSION_KEY] == stored.get_session_auth_hash()
    assert current.session["local_work_marker"] == "keep"
    assert current.session["mfa_verified"] is True
    assert _audits(staff_user).get().detail == {
        "event": "webauthn_removed",
        "credential_pk": credential.pk,
        "sessions_revoked": True,
    }


@pytest.mark.parametrize("failure", ["foreign_account", "foreign_facility", "last_factor"])
def test_rejected_removal_preserves_credentials_and_all_sessions(failure, staff_user, lead_user, second_facility_user):
    owner = {"foreign_account": lead_user, "foreign_facility": second_facility_user, "last_factor": staff_user}[failure]
    credential = _passkey(owner)
    current, other, foreign = _browser(staff_user), _browser(staff_user), _browser(owner)
    before = User.objects.get(pk=staff_user.pk).password
    old_key = current.session.session_key

    response = current.post(f"/mfa/passkey/{credential.pk}/delete/")

    assert response.status_code == 302
    assert WebAuthnCredential.objects.filter(pk=credential.pk, user=owner).exists()
    assert User.objects.get(pk=staff_user.pk).password == before
    assert current.session.session_key == old_key
    assert current.get("/mfa/settings/").status_code == 200
    assert other.get("/mfa/settings/").status_code == 200
    assert foreign.get("/mfa/settings/").status_code == 200
    assert not _audits(staff_user).exists()


@pytest.mark.parametrize("failure", ["audit", "revocation", "session_create", "session_final_save"])
def test_failure_rolls_back_removal_and_revocation_and_allows_retry(failure, staff_user, settings, monkeypatch):
    EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=True)
    credential = _passkey(staff_user)
    current, other = _browser(staff_user), _browser(staff_user)
    before = User.objects.get(pk=staff_user.pk).password
    old_key = current.session.session_key
    old_hash = current.session[HASH_SESSION_KEY]
    session_store = import_module(settings.SESSION_ENGINE).SessionStore
    original_save = session_store.save

    def unavailable(*args, **kwargs):
        raise OperationalError("Private storage failure")

    def fail_final_save(session, must_create=False):
        if not must_create and session.get(HASH_SESSION_KEY) != old_hash:
            original_save(session, must_create=must_create)
            unavailable()
        return original_save(session, must_create=must_create)

    with monkeypatch.context() as fault:
        if failure == "audit":
            fault.setattr(AuditLog, "save", unavailable)
        elif failure == "revocation":
            fault.setattr(User, "revoke_sessions", unavailable)
        elif failure == "session_create":
            fault.setattr(session_store, "create", unavailable)
        else:
            fault.setattr(session_store, "save", fail_final_save)
        current.raise_request_exception = False
        response = current.post(f"/mfa/passkey/{credential.pk}/delete/")
    current.raise_request_exception = True

    assert response.status_code == 302
    assert WebAuthnCredential.objects.filter(pk=credential.pk).exists()
    assert User.objects.get(pk=staff_user.pk).password == before
    assert current.session.session_key == old_key
    assert not _audits(staff_user).exists()
    assert current.get("/mfa/settings/").status_code == 200
    assert other.get("/mfa/settings/").status_code == 200
    assert current.post(f"/mfa/passkey/{credential.pk}/delete/").status_code == 302
    assert not WebAuthnCredential.objects.filter(pk=credential.pk).exists()
    assert _audits(staff_user).count() == 1
    assert current.get("/mfa/settings/").status_code == 200
    signed_out = other.get("/mfa/settings/")
    assert signed_out.status_code == 302
    assert signed_out.url.startswith("/login/")
