"""Reachable device replacement and safe post-sudo confirmation."""

from urllib.parse import parse_qs, urlsplit

import pytest
from django_otp.oath import totp

from core.models import AuditLog, EncryptedTOTPDevice
from core.services.security import generate_backup_codes, verify_backup_code

pytestmark = pytest.mark.django_db
CHANGE_URL = "/mfa/change-device/"


def _token(device):
    return f"{totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits):0{device.digits}d}"


def _verified(client, user):
    client.force_login(user)
    session = client.session
    session["mfa_verified"] = True
    session.save()


def test_settings_offer_device_replacement(client, staff_user):
    EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=True)
    _verified(client, staff_user)
    response = client.get("/mfa/settings/")
    assert response.status_code == 200
    assert b'href="/mfa/change-device/"' in response.content
    assert "Neues Gerät einrichten" in response.content.decode()


@pytest.mark.parametrize("factor", ["totp", "backup"])
def test_device_change_requires_sudo_and_replaces_only_after_ack(client, staff_user, settings, factor):
    settings.SUDO_MODE_ENABLED = True
    old = EncryptedTOTPDevice.objects.create(user=staff_user, name="old", confirmed=True)
    codes = generate_backup_codes(staff_user)
    _verified(client, staff_user)
    response = client.get(CHANGE_URL)
    assert response.status_code == 302
    assert parse_qs(urlsplit(response.url).query)["next"] == [CHANGE_URL]
    assert not EncryptedTOTPDevice.objects.filter(user=staff_user, confirmed=False).exists()
    sudo = client.post(
        "/sudo/",
        {"password": "testpass123", "otp_token": codes[0] if factor == "backup" else _token(old), "next": CHANGE_URL},
    )
    assert sudo.status_code == 302
    page = client.get(sudo.url)
    assert page.status_code == 200
    pending = EncryptedTOTPDevice.objects.get(user=staff_user, confirmed=False)
    assert pending.pk != old.pk
    assert EncryptedTOTPDevice.objects.filter(pk=old.pk, confirmed=True).exists()
    other = EncryptedTOTPDevice.objects.create(user=staff_user, name="other-old", confirmed=True)
    response = client.post(CHANGE_URL, {"token": _token(pending)})
    assert response.status_code == 302
    assert response.url == "/mfa/backup-codes/"
    assert EncryptedTOTPDevice.objects.filter(pk__in=[old.pk, other.pk], confirmed=True).count() == 2
    rejected = client.post(response.url)
    assert rejected.status_code == 200
    assert EncryptedTOTPDevice.objects.filter(pk=old.pk).exists()
    finished = client.post(response.url, {"confirm_saved": "on"})
    assert finished.status_code == 302
    assert list(EncryptedTOTPDevice.objects.filter(user=staff_user, confirmed=True).values_list("pk", flat=True)) == [
        pending.pk
    ]
    assert client.session["mfa_verified"] is True
    assert client.session["otp_device_id"] == pending.persistent_id
    entry = AuditLog.objects.get(user=staff_user, action=AuditLog.Action.MFA_ENABLED)
    assert entry.facility_id == staff_user.facility_id
    assert entry.detail == {"event": "mfa_setup_confirmed", "count": 2}
    assert verify_backup_code(staff_user, codes[1]) is False


@pytest.mark.parametrize("method", ["get", "post"])
def test_device_change_rejects_unverified_session(client, staff_user, method, settings):
    settings.SUDO_MODE_ENABLED = True
    old = EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=True)
    client.force_login(staff_user)
    response = getattr(client, method)(CHANGE_URL)
    assert response.status_code == 302
    assert response.url == "/mfa/verify/"
    assert list(EncryptedTOTPDevice.objects.filter(user=staff_user).values_list("pk", flat=True)) == [old.pk]


@pytest.mark.parametrize("method", ["get", "post"])
def test_disable_returns_to_confirmation_after_sudo(client, staff_user, settings, method):
    settings.SUDO_MODE_ENABLED = True
    old = EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=True)
    code = generate_backup_codes(staff_user)[0]
    _verified(client, staff_user)
    start = getattr(client, method)("/mfa/disable/")
    assert start.status_code == 302
    sudo = client.post("/sudo/", {"password": "testpass123", "otp_token": code, "next": "/mfa/disable/"})
    assert sudo.status_code == 302
    confirmation = client.get(sudo.url)
    assert confirmation.status_code == 200
    assert b'action="/mfa/disable/"' in confirmation.content
    assert EncryptedTOTPDevice.objects.filter(pk=old.pk).exists()
    result = client.post("/mfa/disable/")
    assert result.status_code == 302
    assert not EncryptedTOTPDevice.objects.filter(user=staff_user).exists()
    assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.MFA_DISABLED).count() == 1


@pytest.mark.parametrize("token", ["wrong", ""])
def test_wrong_or_missing_existing_factor_never_grants_device_change(client, staff_user, settings, token):
    settings.SUDO_MODE_ENABLED = True
    old = EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=True)
    _verified(client, staff_user)
    response = client.post("/sudo/", {"password": "testpass123", "otp_token": token, "next": CHANGE_URL})
    assert response.status_code == 403
    assert client.session.get("sudo_until") is None
    assert EncryptedTOTPDevice.objects.filter(user=staff_user).count() == 1
    assert EncryptedTOTPDevice.objects.filter(pk=old.pk).exists()


@pytest.mark.parametrize("method", ["get", "post"])
def test_device_change_requires_password_authentication(client, method):
    response = getattr(client, method)(CHANGE_URL)
    assert response.status_code == 302
    assert urlsplit(response.url).path == "/login/"


def test_device_change_inherits_post_rate_limit(client, staff_user, ratelimit_enabled):
    EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=True)
    _verified(client, staff_user)
    for _ in range(10):
        response = client.post(CHANGE_URL, {"token": "invalid"})
        assert response.status_code == 200
    response = client.post(CHANGE_URL, {"token": "invalid"})
    assert response.status_code == 429
    assert EncryptedTOTPDevice.objects.filter(user=staff_user, confirmed=True).count() == 1
    assert not AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.MFA_ENABLED).exists()
