"""Guard: after a factor change the new TOTP device's code signs in (Refs #2328).

A factor change (``/mfa/setup/`` again while a factor exists) sets up a new
TOTP device; the acknowledgement of the new backup codes confirms it and
removes the replaced one. The test drives the whole way through the test
client (sudo, setup, acknowledgement, fresh sign-in): the new device's code
signs in, the replaced device's code no longer does. Which device counts for
accounts that already have several confirmed devices is covered by
``test_mfa_legacy_multiple_totp_devices_guard.py``.
"""

from __future__ import annotations

import time

import pytest
from django_otp.oath import TOTP
from django_otp.oath import totp as oath_totp

from core.models import EncryptedTOTPDevice as TOTPDevice
from core.services.security import generate_backup_codes

VERIFY_URL = "/mfa/verify/"


def _code(device: TOTPDevice, *, steps_ahead: int = 0) -> str:
    """TOTP-Code des Geraets; ``steps_ahead`` fuer das naechste Zeitfenster (Toleranz 1)."""
    token = oath_totp(
        device.bin_key,
        step=device.step,
        t0=device.t0 - steps_ahead * device.step,
        digits=device.digits,
    )
    return f"{token:0{device.digits}d}"


def _next_window_code(device: TOTPDevice) -> str:
    """Code for the window right after the device's last accepted one.

    Relative to the device, not the clock: a confirmation posted across a window
    boundary stores a drift of -1, and "now + 1" would then fall outside the tolerance.
    """
    device = TOTPDevice.objects.get(pk=device.pk)
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits)
    totp.time = device.t0 + (device.last_t + 1) * device.step
    return f"{totp.token():0{device.digits}d}"


def _password_only(client, user):
    client.force_login(user)


def _verified(client, user):
    client.force_login(user)
    session = client.session
    session["mfa_verified"] = True
    session.save()


@pytest.mark.django_db
class TestFactorChangeFlowEndToEnd:
    """Der ganze Weg im Test-Client: Wechsel mit Sudo, Quittung, neue Anmeldung."""

    def test_after_the_change_the_new_device_signs_in(self, client, staff_user, settings):
        settings.SUDO_MODE_ENABLED = True
        alt = TOTPDevice.objects.create(user=staff_user, name="alt", confirmed=True, key="a" * 40)
        generate_backup_codes(staff_user)
        _verified(client, staff_user)
        session = client.session
        session["sudo_until"] = int(time.time()) + 600
        session.save()

        client.post("/mfa/setup/", {"token": ""})
        neu = TOTPDevice.objects.get(user=staff_user, confirmed=False)
        assert client.post("/mfa/setup/", {"token": _code(neu)}).url == "/mfa/backup-codes/"
        assert client.post("/mfa/backup-codes/", {"confirm_saved": "1"}).status_code == 302
        neu.refresh_from_db()
        assert neu.confirmed is True

        client.logout()
        _password_only(client, staff_user)
        # Der Setup-Code hat das aktuelle Zeitfenster verbraucht; das naechste liegt in der Toleranz.
        response = client.post(VERIFY_URL, {"token": _next_window_code(neu)})
        assert response.status_code == 302
        assert client.session.get("mfa_verified") is True

        client.logout()
        _password_only(client, staff_user)
        response = client.post(VERIFY_URL, {"token": _code(alt)})
        assert response.status_code == 200
        assert client.session.get("mfa_verified") is not True
