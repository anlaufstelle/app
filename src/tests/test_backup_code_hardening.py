"""Backup-code storage and anonymous recovery contracts."""

import base64
import hashlib
import hmac

import pytest
from django.utils.crypto import salted_hmac
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken

from core.models import EncryptedTOTPDevice
from core.services.security import (
    generate_backup_codes,
    remaining_backup_codes,
    verify_backup_code,
    verify_totp_or_backup,
)

pytestmark = pytest.mark.django_db


def test_generated_codes_use_keyed_96_bit_digests(staff_user, settings):
    codes = generate_backup_codes(staff_user)
    key = salted_hmac("core.security.backup_codes", "storage", algorithm="sha256").digest()
    expected = {base64.urlsafe_b64encode(hmac.digest(key, code.encode(), "sha256")).decode()[:16] for code in codes}
    stored = set(StaticToken.objects.filter(device__user=staff_user).values_list("token", flat=True))
    assert stored == expected
    assert all(len(value) == 16 for value in stored)
    assert verify_backup_code(staff_user, codes[0]) is True
    assert remaining_backup_codes(staff_user) == 9
    settings.SECRET_KEY = "a-different-server-secret"
    assert verify_backup_code(staff_user, codes[1]) is False
    assert verify_backup_code(staff_user, codes[0]) is False
    assert remaining_backup_codes(staff_user) == 9


@pytest.mark.parametrize("path", ["service", "sudo", "login", "recovery"])
def test_legacy_format_is_rejected_even_with_matching_digest(client, staff_user, path):
    from core.services.security.mfa import _hash_code

    device = StaticDevice.objects.create(user=staff_user, name="backup", confirmed=True)
    StaticToken.objects.create(device=device, token=_hash_code("abcd-1234"))
    if path == "service":
        assert verify_backup_code(staff_user, "abcd-1234") is False
    elif path == "sudo":
        assert verify_totp_or_backup(staff_user, "abcd-1234") is False
    elif path == "login":
        EncryptedTOTPDevice.objects.create(user=staff_user, name="test", confirmed=True)
        client.force_login(staff_user)
        response = client.post("/mfa/verify/", {"mode": "backup", "token": "abcd-1234"})
        assert response.status_code == 200
        assert client.session.get("mfa_verified") is not True
    else:
        response = client.post(
            "/account/recovery/backup-code/", {"username": staff_user.username, "backup_code": "abcd-1234"}
        )
        assert response.status_code == 200
    assert device.token_set.count() == 1


def test_previous_sha_digest_cannot_verify(staff_user):
    code = "Abcdefghijklmnopqrstu1"
    device = StaticDevice.objects.create(user=staff_user, name="backup", confirmed=True)
    StaticToken.objects.create(device=device, token=hashlib.sha256(code.encode()).hexdigest()[:16])
    assert verify_backup_code(staff_user, code) is False


def test_anonymous_failures_leave_device_throttle_unchanged(client, staff_user):
    generate_backup_codes(staff_user)
    device = StaticDevice.objects.get(user=staff_user, name="backup")
    for _ in range(3):
        response = client.post(
            "/account/recovery/backup-code/",
            {"username": staff_user.username, "backup_code": "A" * 22},
        )
        assert response.status_code == 200
    device.refresh_from_db()
    assert device.throttling_failure_count == 0
    assert device.throttling_failure_timestamp is None
    assert device.last_used_at is None
    assert remaining_backup_codes(staff_user) == 10


def test_anonymous_username_limit_survives_ip_rotation(client, staff_user, ratelimit_enabled):
    code = generate_backup_codes(staff_user)[0]
    for attempt in range(10):
        response = client.post(
            "/account/recovery/backup-code/",
            {"username": staff_user.username, "backup_code": "A" * 22},
            REMOTE_ADDR=f"192.0.2.{attempt + 1}",
        )
        assert response.status_code == 200
    response = client.post(
        "/account/recovery/backup-code/",
        {"username": f" {staff_user.username.upper()} ", "backup_code": code},
        REMOTE_ADDR="192.0.2.99",
    )
    assert response.status_code == 429
    assert remaining_backup_codes(staff_user) == 10
