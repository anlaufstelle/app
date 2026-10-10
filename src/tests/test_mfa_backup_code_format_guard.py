"""Stored 96-bit keyed digests are not usable as backup-code credentials."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken

from core.models import AuditLog
from core.models import EncryptedTOTPDevice as TOTPDevice
from core.services.security import (
    BACKUP_CODES_COUNT,
    generate_backup_codes,
    is_locked,
    remaining_backup_codes,
    verify_backup_code,
)

# Stored tokens are 16 base64url characters of a server-keyed digest.
STORED_TOKEN_FORMAT = re.compile(r"^[A-Za-z0-9_-]{16}$")


def _confirmed_totp(user):
    return TOTPDevice.objects.create(user=user, name="totp", confirmed=True)


def _stored_tokens(user) -> list[str]:
    device = StaticDevice.objects.get(user=user, name="backup")
    return list(device.token_set.values_list("token", flat=True))


def _lock_account(user, count: int = 10):
    for _ in range(count):
        AuditLog.objects.create(
            user=user,
            facility=user.facility,
            action=AuditLog.Action.LOGIN_FAILED,
            detail={"username": user.username},
        )


@pytest.mark.django_db
class TestStoredDigestIsNotACredential:
    """Service-Ebene: der DB-Wert darf nie direkt verglichen werden."""

    def test_stored_token_has_digest_shape(self, staff_user):
        generate_backup_codes(staff_user)
        stored = _stored_tokens(staff_user)
        assert len(stored) == BACKUP_CODES_COUNT
        assert all(STORED_TOKEN_FORMAT.fullmatch(t) for t in stored), stored

    def test_stored_digest_is_rejected_and_consumes_nothing(self, staff_user):
        generate_backup_codes(staff_user)
        stored = _stored_tokens(staff_user)

        assert verify_backup_code(staff_user, stored[0]) is False
        assert remaining_backup_codes(staff_user) == BACKUP_CODES_COUNT
        assert stored[0] in _stored_tokens(staff_user)

    def test_every_stored_digest_is_rejected(self, staff_user):
        generate_backup_codes(staff_user)
        for token in _stored_tokens(staff_user):
            assert verify_backup_code(staff_user, token) is False
        assert remaining_backup_codes(staff_user) == BACKUP_CODES_COUNT

    def test_original_code_still_works_exactly_once(self, staff_user):
        """Gegenprobe: der ausgegebene Code bleibt genau einmal gueltig (Refs #2110)."""
        codes = generate_backup_codes(staff_user)
        assert verify_backup_code(staff_user, codes[0]) is True
        assert verify_backup_code(staff_user, codes[0]) is False
        assert remaining_backup_codes(staff_user) == BACKUP_CODES_COUNT - 1

    def test_legacy_cleartext_device_does_not_accept_its_own_digest(self, staff_user):
        """Auch neben Legacy-Klartext-Zeilen bleibt ein hashfoermiger Wert wertlos."""
        from core.services.security.mfa import _hash_code

        device = StaticDevice.objects.create(user=staff_user, name="backup", confirmed=True)
        StaticToken.objects.create(device=device, token="abcd-1234")
        StaticToken.objects.create(device=device, token=_hash_code("Ccccccccccccccccccccc1"))

        assert verify_backup_code(staff_user, _hash_code("Ccccccccccccccccccccc1")) is False
        assert device.token_set.count() == 2


@pytest.mark.django_db
class TestDigestRejectedInMfaVerifyView:
    """Einstieg 1: ``/mfa/verify/`` im Backup-Modus."""

    def test_stored_digest_does_not_verify_session(self, client, staff_user):
        _confirmed_totp(staff_user)
        generate_backup_codes(staff_user)
        stored = _stored_tokens(staff_user)
        assert client.login(username="teststaff", password="testpass123")

        response = client.post("/mfa/verify/", {"mode": "backup", "token": stored[0]})

        assert response.status_code == 200
        assert client.session.get("mfa_verified") is not True
        assert remaining_backup_codes(staff_user) == BACKUP_CODES_COUNT
        assert not AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.BACKUP_CODES_USED).exists()
        assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.MFA_FAILED).exists()

    def test_original_code_still_verifies_session(self, client, staff_user):
        _confirmed_totp(staff_user)
        codes = generate_backup_codes(staff_user)
        assert client.login(username="teststaff", password="testpass123")

        response = client.post("/mfa/verify/", {"mode": "backup", "token": codes[0]})

        assert response.status_code == 302
        assert client.session.get("mfa_verified") is True
        assert remaining_backup_codes(staff_user) == BACKUP_CODES_COUNT - 1


@pytest.mark.django_db
class TestDigestRejectedInSudoMode:
    """Einstieg 2: Sudo-Reauthentifizierung ueber ``verify_totp_or_backup``."""

    def _verified_session(self, client):
        assert client.login(username="teststaff", password="testpass123")
        session = client.session
        session["mfa_verified"] = True
        session.save()

    def test_stored_digest_does_not_grant_sudo(self, client, staff_user):
        _confirmed_totp(staff_user)
        generate_backup_codes(staff_user)
        stored = _stored_tokens(staff_user)
        self._verified_session(client)

        response = client.post(
            reverse("sudo_mode"),
            {"password": "testpass123", "otp_token": stored[0], "next": "/"},
        )

        assert response.status_code == 403
        assert client.session.get("sudo_until") is None
        assert remaining_backup_codes(staff_user) == BACKUP_CODES_COUNT

    def test_original_code_grants_sudo(self, client, staff_user):
        _confirmed_totp(staff_user)
        codes = generate_backup_codes(staff_user)
        self._verified_session(client)

        response = client.post(
            reverse("sudo_mode"),
            {"password": "testpass123", "otp_token": codes[0], "next": "/"},
        )

        assert response.status_code == 302
        assert client.session.get("sudo_until") is not None
        assert remaining_backup_codes(staff_user) == BACKUP_CODES_COUNT - 1


@pytest.mark.django_db
class TestDigestRejectedInLockoutRecovery:
    """Einstieg 3: Lockout-Recovery per Backup-Code (Refs #869, Var. C)."""

    def test_stored_digest_does_not_unlock(self, client, staff_user):
        generate_backup_codes(staff_user)
        stored = _stored_tokens(staff_user)
        _lock_account(staff_user)
        assert is_locked(staff_user) is True

        response = client.post(
            reverse("core:lockout_recovery_backup_code"),
            {"username": staff_user.username, "backup_code": stored[0]},
        )

        assert response.status_code == 200
        assert is_locked(staff_user) is True
        assert remaining_backup_codes(staff_user) == BACKUP_CODES_COUNT
        assert not AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.LOGIN_UNLOCK).exists()

    def test_original_code_unlocks(self, client, staff_user):
        codes = generate_backup_codes(staff_user)
        _lock_account(staff_user)

        response = client.post(
            reverse("core:lockout_recovery_backup_code"),
            {"username": staff_user.username, "backup_code": codes[0]},
        )

        assert response.status_code == 302
        assert is_locked(staff_user) is False
        assert remaining_backup_codes(staff_user) == BACKUP_CODES_COUNT - 1
