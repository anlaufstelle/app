"""MFA completion survives stale admin writes and session or authentication faults."""

from importlib import import_module

import pytest
from django.contrib.auth import HASH_SESSION_KEY
from django.test import Client
from django_otp.oath import totp

from core.models import EncryptedTOTPDevice, User
from tests.test_mfa_session_revocation import _browser, _prepare

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["db", "cache", "cached_db"])
def session_backend(request, settings):
    settings.SESSION_ENGINE = f"django.contrib.sessions.backends.{request.param}"
    return import_module(settings.SESSION_ENGINE).SessionStore


@pytest.mark.parametrize("action", ["change", "disable"])
@pytest.mark.parametrize(
    "next_auth",
    ["other_login", "own_sudo", "wrong_otp_sudo", "other_login_upgrade", "own_sudo_upgrade", "wrong_otp_sudo_upgrade"],
)
def test_initiating_session_survives_next_authentication(action, next_auth, session_backend, staff_user, settings):
    EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=True)
    current, revoked = _browser(staff_user), _browser(staff_user)
    url, data, pending = _prepare(current, staff_user, action)
    assert current.post(url, data).status_code == 302
    assert current.get("/mfa/settings/").status_code == 200
    before = current.session[HASH_SESSION_KEY]

    if next_auth.endswith("_upgrade"):
        settings.PASSWORD_HASHERS = [
            "django.contrib.auth.hashers.PBKDF2PasswordHasher",
            "django.contrib.auth.hashers.MD5PasswordHasher",
        ]
        next_auth = next_auth.removesuffix("_upgrade")
    if next_auth == "other_login":
        assert Client().login(username=staff_user.username, password="testpass123")
    else:
        token = "invalid"
        if pending and next_auth == "own_sudo":
            # Setup already consumed the current time step; use the next valid step.
            token = str(totp(pending.bin_key, step=pending.step, t0=pending.t0, digits=pending.digits, drift=1)).zfill(
                pending.digits
            )
        response = current.post("/sudo/", {"password": "testpass123", "otp_token": token, "next": "/mfa/settings/"})
        assert response.status_code == (403 if pending and next_auth == "wrong_otp_sudo" else 302)

    assert revoked.get("/mfa/settings/").status_code == 302
    stored = User.objects.get(pk=staff_user.pk)
    assert stored.get_session_auth_hash() == before
    if "PBKDF2PasswordHasher" in settings.PASSWORD_HASHERS[0]:
        assert stored._revoked_password_hash().startswith("pbkdf2_sha256$")
    response = current.get("/mfa/settings/")
    assert response.status_code == 200, (
        next_auth,
        before == stored.get_session_auth_hash(),
        response.status_code,
        getattr(response, "url", None),
    )
