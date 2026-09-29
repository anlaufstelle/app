"""Guard: accounts with several confirmed TOTP devices keep signing in (Refs #2328).

Up to v0.99.1 every check point picked the account's TOTP device with
``.first()`` on an unordered queryset, i.e. the OLDEST confirmed device
(lowest primary key). Accounts that ended up with more than one confirmed
device have been using exactly that device ever since. A release must not
switch them to another device: whoever could sign in before must still be
able to.

Contract: for an account with several confirmed TOTP devices, the code of the
oldest device is accepted at every check point -- sign-in (``/mfa/verify/``,
including the device bound by a backup-code sign-in), sudo (service and
``/sudo/``), backup-code regeneration and the admin area (which is gated by
sudo) -- and the code of a younger device is refused, as in v0.99.1.
Exactly one device is checked, so a code of one device never counts against
another.

The two devices are created directly in the test: the application itself no
longer leaves two confirmed devices behind (the factor-change
acknowledgement removes the replaced one).
"""

from __future__ import annotations

import time

import pytest
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import totp as oath_totp

from core.models import EncryptedTOTPDevice as TOTPDevice
from core.services.security import generate_backup_codes, verify_totp_or_backup

VERIFY_URL = "/mfa/verify/"
SUDO_URL = "/sudo/"
REGENERATE_URL = "/mfa/backup-codes/regenerate/"
ADMIN_URL = "/admin-mgmt/"
PASSWORD = "testpass123"


def _code(device: TOTPDevice) -> str:
    token = oath_totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)
    return f"{token:0{device.digits}d}"


def _create_legacy_devices(user) -> tuple[TOTPDevice, TOTPDevice]:
    """Two confirmed devices; the younger one has the higher primary key."""
    oldest = TOTPDevice.objects.create(user=user, name="oldest", confirmed=True, key="a" * 40)
    younger = TOTPDevice.objects.create(user=user, name="younger", confirmed=True, key="b" * 40)
    assert oldest.pk < younger.pk
    assert _code(oldest) != _code(younger)
    generate_backup_codes(user)
    return oldest, younger


@pytest.fixture
def legacy_devices(staff_user):
    return _create_legacy_devices(staff_user)


@pytest.fixture
def sudo_enabled(settings):
    settings.SUDO_MODE_ENABLED = True


def _password_only(client, user) -> None:
    client.force_login(user)


def _verified(client, user) -> None:
    client.force_login(user)
    session = client.session
    session["mfa_verified"] = True
    session.save()


@pytest.mark.django_db
class TestSignIn:
    def test_code_of_the_oldest_device_signs_in(self, client, staff_user, legacy_devices):
        oldest, _younger = legacy_devices
        _password_only(client, staff_user)
        response = client.post(VERIFY_URL, {"token": _code(oldest)})
        assert response.status_code == 302
        assert client.session.get("mfa_verified") is True
        assert client.session[DEVICE_ID_SESSION_KEY] == oldest.persistent_id

    def test_code_of_the_younger_device_is_refused(self, client, staff_user, legacy_devices):
        _oldest, younger = legacy_devices
        _password_only(client, staff_user)
        response = client.post(VERIFY_URL, {"token": _code(younger)})
        assert response.status_code == 200
        assert client.session.get("mfa_verified") is not True

    def test_backup_code_sign_in_binds_the_oldest_device(self, client, staff_user, legacy_devices):
        oldest, _younger = legacy_devices
        code = generate_backup_codes(staff_user)[0]
        _password_only(client, staff_user)
        response = client.post(VERIFY_URL, {"token": code, "mode": "backup"})
        assert response.status_code == 302
        assert client.session[DEVICE_ID_SESSION_KEY] == oldest.persistent_id

    def test_the_oldest_code_leaves_the_younger_device_untouched(self, client, staff_user, legacy_devices):
        oldest, younger = legacy_devices
        _password_only(client, staff_user)
        client.post(VERIFY_URL, {"token": _code(oldest)})
        younger.refresh_from_db()
        assert younger.throttling_failure_count == 0


@pytest.mark.django_db
@pytest.mark.usefixtures("sudo_enabled")
class TestSudo:
    def test_service_accepts_the_oldest_device(self, staff_user, legacy_devices):
        oldest, _younger = legacy_devices
        assert verify_totp_or_backup(staff_user, _code(oldest)) is True

    def test_service_refuses_the_younger_device(self, staff_user, legacy_devices):
        _oldest, younger = legacy_devices
        assert verify_totp_or_backup(staff_user, _code(younger)) is False

    def test_sudo_page_grants_with_the_oldest_code(self, client, staff_user, legacy_devices):
        oldest, _younger = legacy_devices
        _verified(client, staff_user)
        response = client.post(SUDO_URL, {"password": PASSWORD, "otp_token": _code(oldest), "next": "/"})
        assert response.status_code == 302
        assert client.session["sudo_until"] > time.time()

    def test_sudo_page_refuses_the_younger_code(self, client, staff_user, legacy_devices):
        _oldest, younger = legacy_devices
        _verified(client, staff_user)
        response = client.post(SUDO_URL, {"password": PASSWORD, "otp_token": _code(younger), "next": "/"})
        assert response.status_code == 403
        assert "sudo_until" not in client.session


@pytest.mark.django_db
class TestBackupCodeRegeneration:
    def test_code_of_the_oldest_device_regenerates(self, client, staff_user, legacy_devices):
        oldest, _younger = legacy_devices
        _verified(client, staff_user)
        response = client.post(REGENERATE_URL, {"token": _code(oldest)})
        assert response.status_code == 302
        assert response.url == "/mfa/backup-codes/"

    def test_code_of_the_younger_device_is_refused(self, client, staff_user, legacy_devices):
        _oldest, younger = legacy_devices
        _verified(client, staff_user)
        response = client.post(REGENERATE_URL, {"token": _code(younger)})
        assert response.status_code == 302
        assert response.url == "/mfa/settings/"


@pytest.mark.django_db
@pytest.mark.usefixtures("sudo_enabled")
class TestAdminArea:
    def test_code_of_the_oldest_device_opens_the_admin_area(self, client, super_admin_user):
        oldest, _younger = _create_legacy_devices(super_admin_user)
        _verified(client, super_admin_user)
        assert client.get(ADMIN_URL).status_code == 302
        response = client.post(SUDO_URL, {"password": PASSWORD, "otp_token": _code(oldest), "next": ADMIN_URL})
        assert response.status_code == 302
        assert client.get(ADMIN_URL).status_code == 200

    def test_code_of_the_younger_device_keeps_the_admin_area_closed(self, client, super_admin_user):
        _oldest, younger = _create_legacy_devices(super_admin_user)
        _verified(client, super_admin_user)
        response = client.post(SUDO_URL, {"password": PASSWORD, "otp_token": _code(younger), "next": ADMIN_URL})
        assert response.status_code == 403
        assert client.get(ADMIN_URL).status_code == 302
