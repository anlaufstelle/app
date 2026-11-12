"""A pending TOTP secret belongs to the session that started the setup.

A second session of the same account must never see or confirm the secret
another session was shown; reloading in the same session keeps it so a
scanned code stays valid. Confirmed devices are never touched.
"""

import base64

import pytest
from django.test import Client
from django_otp.oath import totp

from core.models import EncryptedTOTPDevice

pytestmark = pytest.mark.django_db

SETUP_URL = "/mfa/setup/"
CHANGE_URL = "/mfa/change-device/"
BACKUP_CODES_URL = "/mfa/backup-codes/"


def _token_for_secret(secret: str) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    return f"{totp(key, step=30, t0=0, digits=6):06d}"


def _session(user, *, verified=False) -> Client:
    client = Client()
    client.force_login(user)
    if verified:
        session = client.session
        session["mfa_verified"] = True
        session.save()
    return client


def _secret(client: Client, url: str = SETUP_URL) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return response.context["secret"]


def test_second_session_gets_other_secret_and_cannot_confirm_the_first(staff_user):
    first = _session(staff_user)
    second = _session(staff_user)
    first_secret = _secret(first)

    second_secret = _secret(second)
    response = second.post(SETUP_URL, {"token": _token_for_secret(first_secret)})

    assert second_secret != first_secret
    assert response.status_code == 200
    assert response.context["token_error"] is True
    assert "mfa_setup_pending" not in second.session


def test_reload_in_same_session_keeps_secret_and_confirms(staff_user):
    client = _session(staff_user)
    first_secret = _secret(client)

    assert _secret(client) == first_secret
    response = client.post(SETUP_URL, {"token": _token_for_secret(first_secret)})

    assert response.status_code == 302
    assert response.url == BACKUP_CODES_URL
    assert EncryptedTOTPDevice.objects.filter(user=staff_user, confirmed=False).count() == 1


def test_first_session_is_refused_after_another_session_replaced_the_secret(staff_user):
    first = _session(staff_user)
    second = _session(staff_user)
    first_secret = _secret(first)
    second_secret = _secret(second)

    response = first.post(SETUP_URL, {"token": _token_for_secret(first_secret)})

    assert response.status_code == 200
    assert response.context["token_error"] is True
    assert response.context["secret"] not in (first_secret, second_secret)
    assert "mfa_setup_pending" not in first.session
    assert EncryptedTOTPDevice.objects.filter(user=staff_user, confirmed=False).count() == 1


def test_leftover_device_from_another_session_is_discarded(staff_user):
    leftover = EncryptedTOTPDevice.objects.create(user=staff_user, name="leftover", confirmed=False)
    leftover_secret = base64.b32encode(leftover.bin_key).decode("ascii").rstrip("=")
    client = _session(staff_user)

    secret = _secret(client)

    assert secret != leftover_secret
    assert not EncryptedTOTPDevice.objects.filter(pk=leftover.pk).exists()


def test_device_change_binds_secret_to_session_and_keeps_confirmed_device(staff_user, settings):
    settings.SUDO_MODE_ENABLED = False
    current = EncryptedTOTPDevice.objects.create(user=staff_user, name="current", confirmed=True)
    first = _session(staff_user, verified=True)
    second = _session(staff_user, verified=True)
    first_secret = _secret(first, CHANGE_URL)

    second_secret = _secret(second, CHANGE_URL)
    response = second.post(CHANGE_URL, {"token": _token_for_secret(first_secret)})

    assert second_secret != first_secret
    assert response.status_code == 200
    assert response.context["token_error"] is True
    assert EncryptedTOTPDevice.objects.filter(pk=current.pk, confirmed=True).exists()
