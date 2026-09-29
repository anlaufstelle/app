"""Guard: a factor change works regardless of which confirmed device is checked (Refs #2328).

Every check point verifies exactly one confirmed TOTP device: the oldest one
(``current_totp_device``). A factor change must not rely on that order. The
guard drives the whole change through the test client -- sudo, setup page,
confirmation of the new device, acknowledgement of the new backup codes --
for an account with one confirmed device and for a legacy account with two:

* The confirmation step accepts only the code of the NEW device; codes of the
  devices in use are refused and change nothing.
* Until the acknowledgement the device in use keeps signing in (for the
  legacy account: the oldest), the new device does not.
* After the acknowledgement exactly one confirmed TOTP device is left, the new
  one. The code of every replaced device is refused at every check point:
  sign-in, sudo page, sudo service, backup-code regeneration, admin area.

The account is a facility admin so the admin area (gated by sudo) is one of
the check points.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest
from django.test import Client
from django_otp.oath import totp as oath_totp

from core.models import EncryptedTOTPDevice as TOTPDevice
from core.services.security import generate_backup_codes, remaining_backup_codes, verify_totp_or_backup

VERIFY_URL = "/mfa/verify/"
SUDO_URL = "/sudo/"
SETUP_URL = "/mfa/setup/"
BACKUP_CODES_URL = "/mfa/backup-codes/"
REGENERATE_URL = "/mfa/backup-codes/regenerate/"
ADMIN_URL = "/admin-mgmt/"
PASSWORD = "testpass123"


def _code(device: TOTPDevice, *, steps_ahead: int = 0) -> str:
    """The device's TOTP code; ``steps_ahead=1`` is the next window (within the tolerance of 1)."""
    token = oath_totp(
        device.bin_key,
        step=device.step,
        t0=device.t0 - steps_ahead * device.step,
        digits=device.digits,
    )
    return f"{token:0{device.digits}d}"


def _confirmed_pks(user) -> list[int]:
    return list(TOTPDevice.objects.filter(user=user, confirmed=True).order_by("pk").values_list("pk", flat=True))


def _verified(client, user) -> None:
    client.force_login(user)
    session = client.session
    session["mfa_verified"] = True
    session.save()


def _verified_with_sudo(client, user) -> None:
    _verified(client, user)
    session = client.session
    session["sudo_until"] = int(time.time()) + 600
    session.save()


@pytest.fixture(autouse=True)
def sudo_enabled(settings):
    settings.SUDO_MODE_ENABLED = True


@pytest.fixture
def no_backoff(settings):
    """Let one flow make several attempts in a row without waiting out django-otp's backoff."""
    settings.OTP_TOTP_THROTTLE_FACTOR = 0


@pytest.fixture(params=[1, 2], ids=["one_device", "legacy_two_devices"])
def old_devices(request, admin_user) -> list[TOTPDevice]:
    devices = [
        TOTPDevice.objects.create(user=admin_user, name=f"old-{index}", confirmed=True, key=f"{index + 1}" * 40)
        for index in range(request.param)
    ]
    generate_backup_codes(admin_user)
    return devices


def _open_setup(client, user) -> TOTPDevice:
    """Start the change from a verified session with sudo; returns the new, unconfirmed device."""
    _verified_with_sudo(client, user)
    client.post(SETUP_URL, {"token": ""})
    return TOTPDevice.objects.get(user=user, confirmed=False)


def _confirm_new_device(client, new: TOTPDevice) -> None:
    response = client.post(SETUP_URL, {"token": _code(new)})
    assert response.status_code == 302
    assert response.url == BACKUP_CODES_URL


def _acknowledge(client) -> None:
    response = client.post(BACKUP_CODES_URL, {"confirm_saved": "1"})
    assert response.status_code == 302


# Check points: each returns True when the code is accepted.


def _signs_in(user, code: str) -> bool:
    client = Client()
    client.force_login(user)
    response = client.post(VERIFY_URL, {"token": code})
    return response.status_code == 302 and client.session.get("mfa_verified") is True


def _opens_sudo(user, code: str) -> bool:
    client = Client()
    _verified(client, user)
    response = client.post(SUDO_URL, {"password": PASSWORD, "otp_token": code, "next": "/"})
    return response.status_code == 302 and "sudo_until" in client.session


def _passes_sudo_service(user, code: str) -> bool:
    return verify_totp_or_backup(user, code)


def _regenerates_backup_codes(user, code: str) -> bool:
    client = Client()
    _verified(client, user)
    response = client.post(REGENERATE_URL, {"token": code})
    return response.status_code == 302 and response.url == BACKUP_CODES_URL


def _opens_admin_area(user, code: str) -> bool:
    client = Client()
    _verified(client, user)
    client.post(SUDO_URL, {"password": PASSWORD, "otp_token": code, "next": ADMIN_URL})
    return client.get(ADMIN_URL).status_code == 200


CHECK_POINTS: dict[str, Callable[..., bool]] = {
    "sign_in": _signs_in,
    "sudo_page": _opens_sudo,
    "sudo_service": _passes_sudo_service,
    "backup_code_regeneration": _regenerates_backup_codes,
    "admin_area": _opens_admin_area,
}


@pytest.mark.django_db
class TestConfirmationChecksOnlyTheNewDevice:
    def test_codes_of_the_devices_in_use_are_refused(self, client, admin_user, old_devices):
        new = _open_setup(client, admin_user)
        for old in old_devices:
            response = client.post(SETUP_URL, {"token": _code(old)})
            assert response.status_code == 200
            assert "mfa_setup_pending" not in client.session
        new.refresh_from_db()
        assert new.confirmed is False
        assert _confirmed_pks(admin_user) == [old.pk for old in old_devices]
        assert remaining_backup_codes(admin_user) == 10
        for old in old_devices:
            old.refresh_from_db()
            assert old.throttling_failure_count == 0

    def test_code_of_the_new_device_is_accepted(self, client, admin_user, old_devices):
        new = _open_setup(client, admin_user)
        _confirm_new_device(client, new)
        assert client.session.get("mfa_setup_pending") is True


@pytest.mark.django_db
@pytest.mark.usefixtures("no_backoff")
class TestBeforeTheAcknowledgement:
    def test_the_device_in_use_still_signs_in_and_the_new_one_does_not(self, client, admin_user, old_devices):
        new = _open_setup(client, admin_user)
        _confirm_new_device(client, new)

        assert _signs_in(admin_user, _code(new, steps_ahead=1)) is False
        assert _signs_in(admin_user, _code(old_devices[0])) is True
        assert _confirmed_pks(admin_user) == [old.pk for old in old_devices]


@pytest.mark.django_db
@pytest.mark.usefixtures("no_backoff")
class TestAfterTheAcknowledgement:
    @pytest.fixture
    def new_device(self, client, admin_user, old_devices) -> TOTPDevice:
        new = _open_setup(client, admin_user)
        for old in old_devices:
            client.post(SETUP_URL, {"token": _code(old)})
        _confirm_new_device(client, new)
        _acknowledge(client)
        return new

    def test_exactly_the_new_device_remains(self, admin_user, old_devices, new_device):
        assert _confirmed_pks(admin_user) == [new_device.pk]
        assert not TOTPDevice.objects.filter(pk__in=[old.pk for old in old_devices]).exists()

    @pytest.mark.parametrize("check_point", list(CHECK_POINTS))
    def test_codes_of_the_replaced_devices_are_refused(self, admin_user, old_devices, new_device, check_point):
        for old in old_devices:
            # The next window: not yet used by any device, within the tolerance.
            assert CHECK_POINTS[check_point](admin_user, _code(old, steps_ahead=1)) is False

    def test_the_new_device_signs_in(self, admin_user, new_device):
        # The confirmation used the current window; the next one is within the tolerance.
        assert _signs_in(admin_user, _code(new_device, steps_ahead=1)) is True
