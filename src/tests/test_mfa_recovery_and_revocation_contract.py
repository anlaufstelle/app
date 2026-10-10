"""Behaviour behind the documented 2FA recovery path and session revocation.

The admin guide (section 2.7 and the ``OFFLINE_REVOCATION_SESSION_SCAN_LIMIT``
row), the FAQ (section 4) and the ADR-015 addendum describe these flows step
by step. The tests replay exactly those steps over HTTP so the documentation
can cite them as evidence:

* Recovery after losing the TOTP device (2FA not mandatory): sign in with a
  backup code, press "2FA deaktivieren" on ``/mfa/settings/``, confirm sudo
  with password plus another backup code. The sudo step redirects back to
  ``/mfa/disable/`` with GET, which shows a confirmation and keeps the factor.
  Submitting that confirmation inside the sudo window removes the factor;
  afterwards ``/mfa/setup/`` offers a fresh setup. An explicit device-change
  flow also reuses setup without disabling the old factor first.
* Sessions beyond ``OFFLINE_REVOCATION_SESSION_SCAN_LIMIT``: after a lockout
  or a role change a session the scan did not reach stays authenticated;
  after deactivation or an invitation resend that made the initial password
  unusable it is signed out on its next request (inactive user or changed
  password hash).

``settings/test.py`` disables sudo — the recovery tests switch it back on.
"""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.sessions.models import Session
from django.test import Client

from core.models import AuditLog, User
from core.models import EncryptedTOTPDevice as TOTPDevice
from core.services.security import RevocationReason, generate_backup_codes, revoke_credentials
from core.services.user_access import resend_user_invitation

SETTINGS_URL = "/mfa/settings/"
DISABLE_URL = "/mfa/disable/"
SETUP_URL = "/mfa/setup/"
SUDO_URL = "/sudo/"
PASSWORD = "testpass123"


@pytest.fixture
def lost_device_account(settings, staff_user):
    """Non-mandatory 2FA account whose TOTP device is gone; backup codes remain."""
    settings.SUDO_MODE_ENABLED = True
    settings.MFA_ENFORCE_PRIVILEGED_ROLES = False
    device = TOTPDevice.objects.create(user=staff_user, name="lost phone", confirmed=True)
    codes = generate_backup_codes(staff_user)
    assert not staff_user.is_mfa_enforced
    return staff_user, device, codes


def _sign_in_with_backup_code(client, user, code):
    response = client.post("/login/", {"username": user.username, "password": PASSWORD})
    assert response.status_code == 302
    response = client.post("/mfa/verify/", {"mode": "backup", "token": code})
    assert response.status_code == 302
    assert client.session.get("mfa_verified") is True


def _disabled_audits(user):
    return AuditLog.objects.filter(action=AuditLog.Action.MFA_DISABLED, target_id=str(user.pk)).count()


@pytest.mark.django_db
class TestLostDeviceRecoveryByDisableAndSetup:
    def test_after_sudo_confirmation_keeps_the_factor(self, client, lost_device_account):
        user, device, codes = lost_device_account
        _sign_in_with_backup_code(client, user, codes[0])

        page = client.get(SETTINGS_URL)
        assert page.status_code == 200
        assert b'data-testid="mfa-disable-button"' in page.content

        response = client.post(DISABLE_URL)
        assert response.status_code == 302
        target = urlsplit(response.url)
        assert target.path == SUDO_URL
        assert parse_qs(target.query)["next"] == [DISABLE_URL]

        response = client.post(SUDO_URL, {"password": PASSWORD, "otp_token": codes[1], "next": DISABLE_URL})
        assert response.status_code == 302
        assert response.url == DISABLE_URL
        assert client.session["sudo_until"] > time.time()

        response = client.get(DISABLE_URL)
        assert response.status_code == 200
        assert b'action="/mfa/disable/"' in response.content
        assert TOTPDevice.objects.filter(pk=device.pk).exists()
        assert _disabled_audits(user) == 0

    def test_confirmation_post_inside_sudo_window_disables_and_setup_follows(self, client, lost_device_account):
        user, device, codes = lost_device_account
        _sign_in_with_backup_code(client, user, codes[0])
        client.post(DISABLE_URL)
        client.post(SUDO_URL, {"password": PASSWORD, "otp_token": codes[1], "next": DISABLE_URL})
        client.get(DISABLE_URL)

        assert client.get(SETTINGS_URL).status_code == 200
        response = client.post(DISABLE_URL)
        assert response.status_code == 302
        assert response.url == SETTINGS_URL
        assert not TOTPDevice.objects.filter(user=user).exists()
        assert _disabled_audits(user) == 1

        page = client.get(SETTINGS_URL)
        assert page.status_code == 200
        assert b'data-testid="mfa-setup-button"' in page.content
        assert client.get(SETUP_URL).status_code == 200

    def test_single_press_suffices_while_sudo_window_runs(self, client, lost_device_account):
        user, device, codes = lost_device_account
        _sign_in_with_backup_code(client, user, codes[0])
        response = client.post(SUDO_URL, {"password": PASSWORD, "otp_token": codes[1], "next": SETTINGS_URL})
        assert response.status_code == 302
        assert response.url == SETTINGS_URL

        response = client.post(DISABLE_URL)
        assert response.status_code == 302
        assert response.url == SETTINGS_URL
        assert not TOTPDevice.objects.filter(pk=device.pk).exists()
        assert _disabled_audits(user) == 1

    def test_mandatory_2fa_offers_no_disable_button(self, client, lost_device_account):
        user, device, codes = lost_device_account
        user.mfa_required = True
        user.save(update_fields=["mfa_required"])
        _sign_in_with_backup_code(client, user, codes[0])
        page = client.get(SETTINGS_URL)
        assert page.status_code == 200
        assert b'data-testid="mfa-disable-button"' not in page.content


def _verified_browser(user):
    browser = Client()
    browser.force_login(user)
    session = browser.session
    session["mfa_verified"] = True
    session.save()
    return browser


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("trigger", "survivor_stays_signed_in"),
    [("lockout", True), ("role_change", True), ("deactivation", False), ("invitation_resend", False)],
)
def test_session_missed_by_scan_limit(trigger, survivor_stays_signed_in, settings, staff_user, admin_user):
    settings.OFFLINE_REVOCATION_SESSION_SCAN_LIMIT = 1
    if trigger == "invitation_resend":
        staff_user.must_change_password = True
        staff_user.email = "invite@example.test"
        staff_user.save(update_fields=["must_change_password", "email"])
    browsers = [_verified_browser(staff_user), _verified_browser(staff_user)]
    keys = [browser.session.session_key for browser in browsers]

    if trigger == "lockout":
        revoke_credentials(staff_user, reason=RevocationReason.ACCOUNT_LOCKOUT)
    elif trigger == "role_change":
        staff_user.role = User.Role.ASSISTANT
        staff_user.save(update_fields=["role"])
    elif trigger == "deactivation":
        staff_user.is_active = False
        staff_user.save(update_fields=["is_active"])
    else:
        resend_user_invitation(staff_user, facility=staff_user.facility, actor=admin_user)

    survivors = [b for b, key in zip(browsers, keys, strict=True) if Session.objects.filter(session_key=key).exists()]
    assert len(survivors) == 1
    response = survivors[0].get(SETTINGS_URL)
    if survivor_stays_signed_in:
        assert response.status_code == 200
        assert response.wsgi_request.user.is_authenticated
    else:
        assert response.status_code == 302
        assert response.url.startswith("/login/")
        assert not response.wsgi_request.user.is_authenticated
