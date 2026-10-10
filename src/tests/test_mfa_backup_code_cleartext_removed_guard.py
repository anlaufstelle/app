"""Cleartext rows are not credentials; modern keyed digests verify under RLS."""

from __future__ import annotations

import pytest
from django.urls import reverse
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken

from core.models import AuditLog
from core.models import EncryptedTOTPDevice as TOTPDevice
from core.services.security import is_locked, verify_backup_code
from core.services.security.mfa import _hash_code
from tests.test_rls_functional import (  # noqa: F401
    as_rls_role,
    rls_test_role,
)

ALT_CODE = "abcd-1234"
MODERN_CODE = "Abcdefghijklmnopqrstu1"


def _backup_device(user, token: str) -> StaticDevice:
    device = StaticDevice.objects.create(user=user, name="backup", confirmed=True)
    StaticToken.objects.create(device=device, token=token)
    return device


def _confirmed_totp(user):
    return TOTPDevice.objects.create(user=user, name="totp", confirmed=True)


def _lock_account(user, count: int = 10):
    for _ in range(count):
        AuditLog.objects.create(
            user=user,
            facility=user.facility,
            action=AuditLog.Action.LOGIN_FAILED,
            detail={"username": user.username},
        )


@pytest.mark.django_db
class TestCleartextRowIsNoCredential:
    """Service-Ebene: eine Klartext-Zeile im Altformat wird nie direkt verglichen."""

    def test_cleartext_row_is_rejected(self, staff_user):
        _backup_device(staff_user, ALT_CODE)
        assert verify_backup_code(staff_user, ALT_CODE) is False

    def test_cleartext_row_stays_in_place(self, staff_user):
        device = _backup_device(staff_user, ALT_CODE)
        verify_backup_code(staff_user, ALT_CODE)
        assert list(device.token_set.values_list("token", flat=True)) == [ALT_CODE]

    def test_rejection_counts_as_one_throttle_failure(self, staff_user):
        device = _backup_device(staff_user, ALT_CODE)
        verify_backup_code(staff_user, ALT_CODE)
        device.refresh_from_db()
        assert device.throttling_failure_count == 1
        assert device.last_used_at is None

    def test_modern_digest_verifies_exactly_once(self, staff_user):
        """A modern code verifies once against its keyed digest."""
        device = _backup_device(staff_user, _hash_code(MODERN_CODE))
        assert verify_backup_code(staff_user, MODERN_CODE) is True
        assert verify_backup_code(staff_user, MODERN_CODE) is False
        assert device.token_set.count() == 0


@pytest.mark.django_db
class TestCleartextRowRejectedInMfaVerifyView:
    """Einstieg 1: ``/mfa/verify/`` im Backup-Modus."""

    def test_cleartext_row_does_not_verify_session(self, client, staff_user):
        _confirmed_totp(staff_user)
        device = _backup_device(staff_user, ALT_CODE)
        assert client.login(username="teststaff", password="testpass123")

        response = client.post("/mfa/verify/", {"mode": "backup", "token": ALT_CODE})

        assert response.status_code == 200
        assert client.session.get("mfa_verified") is not True
        assert device.token_set.count() == 1
        assert not AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.BACKUP_CODES_USED).exists()
        assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.MFA_FAILED).exists()

    @pytest.mark.parametrize("token", [MODERN_CODE, f" {MODERN_CODE} "])
    def test_modern_digest_verifies_session(self, client, staff_user, token):
        """Modern input remains case-sensitive and permits surrounding whitespace."""
        _confirmed_totp(staff_user)
        device = _backup_device(staff_user, _hash_code(MODERN_CODE))
        assert client.login(username="teststaff", password="testpass123")

        response = client.post("/mfa/verify/", {"mode": "backup", "token": token})

        assert response.status_code == 302
        assert client.session.get("mfa_verified") is True
        assert device.token_set.count() == 0


@pytest.mark.django_db
class TestCleartextRowRejectedInSudoMode:
    """Einstieg 2: Sudo-Reauthentifizierung ueber ``verify_totp_or_backup``."""

    def test_cleartext_row_does_not_grant_sudo(self, client, staff_user):
        _confirmed_totp(staff_user)
        device = _backup_device(staff_user, ALT_CODE)
        assert client.login(username="teststaff", password="testpass123")
        session = client.session
        session["mfa_verified"] = True
        session.save()

        response = client.post(
            reverse("sudo_mode"),
            {"password": "testpass123", "otp_token": ALT_CODE, "next": "/"},
        )

        assert response.status_code == 403
        assert client.session.get("sudo_until") is None
        assert device.token_set.count() == 1


@pytest.mark.django_db
class TestCleartextRowRejectedInLockoutRecovery:
    """Einstieg 3: Lockout-Recovery per Backup-Code."""

    def test_cleartext_row_does_not_unlock(self, client, staff_user):
        device = _backup_device(staff_user, ALT_CODE)
        _lock_account(staff_user)
        assert is_locked(staff_user) is True

        response = client.post(
            reverse("core:lockout_recovery_backup_code"),
            {"username": staff_user.username, "backup_code": ALT_CODE},
        )

        assert response.status_code == 200
        assert is_locked(staff_user) is True
        assert device.token_set.count() == 1
        assert not AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.LOGIN_UNLOCK).exists()


@pytest.mark.django_db
class TestBackupCodeWritePathsUnderRlsRole:
    """Modern backup-code verification writes account-scoped audit rows under RLS."""

    def test_verify_with_modern_code_writes_audit_with_account_facility(
        self,
        client,
        staff_user,
        rls_test_role,  # noqa: F811
    ):
        _confirmed_totp(staff_user)
        device = _backup_device(staff_user, _hash_code(MODERN_CODE))
        client.force_login(staff_user)

        with as_rls_role(rls_test_role, facility_id=staff_user.facility_id):
            response = client.post("/mfa/verify/", {"mode": "backup", "token": MODERN_CODE})

        assert response.status_code == 302
        assert client.session.get("mfa_verified") is True
        assert device.token_set.count() == 0
        entry = AuditLog.objects.get(user=staff_user, action=AuditLog.Action.BACKUP_CODES_USED)
        assert entry.facility_id == staff_user.facility_id

    def test_verify_with_modern_code_writes_audit_for_account_without_facility(
        self,
        client,
        super_admin_user,
        rls_test_role,  # noqa: F811
    ):
        _confirmed_totp(super_admin_user)
        _backup_device(super_admin_user, _hash_code(MODERN_CODE))
        client.force_login(super_admin_user)

        with as_rls_role(rls_test_role, facility_id=""):
            response = client.post("/mfa/verify/", {"mode": "backup", "token": MODERN_CODE})

        assert response.status_code == 302
        assert client.session.get("mfa_verified") is True
        entry = AuditLog.objects.get(user=super_admin_user, action=AuditLog.Action.BACKUP_CODES_USED)
        assert entry.facility_id is None

    def test_rejected_cleartext_row_writes_failure_audit(
        self,
        client,
        staff_user,
        rls_test_role,  # noqa: F811
    ):
        _confirmed_totp(staff_user)
        device = _backup_device(staff_user, ALT_CODE)
        client.force_login(staff_user)

        with as_rls_role(rls_test_role, facility_id=staff_user.facility_id):
            response = client.post("/mfa/verify/", {"mode": "backup", "token": ALT_CODE})

        assert response.status_code == 200
        assert client.session.get("mfa_verified") is not True
        assert device.token_set.count() == 1
        entry = AuditLog.objects.get(user=staff_user, action=AuditLog.Action.MFA_FAILED)
        assert entry.facility_id == staff_user.facility_id

    def test_lockout_recovery_with_modern_code_writes_unlock_anonymously(
        self,
        client,
        staff_user,
        rls_test_role,  # noqa: F811
    ):
        device = _backup_device(staff_user, _hash_code(MODERN_CODE))
        _lock_account(staff_user)

        with as_rls_role(rls_test_role, facility_id=""):
            response = client.post(
                reverse("core:lockout_recovery_backup_code"),
                {"username": staff_user.username, "backup_code": MODERN_CODE},
            )

        assert response.status_code == 302
        assert is_locked(staff_user) is False
        assert device.token_set.count() == 0
        unlock = AuditLog.objects.get(user=staff_user, action=AuditLog.Action.LOGIN_UNLOCK)
        assert unlock.facility_id == staff_user.facility_id
        used = AuditLog.objects.get(user=staff_user, action=AuditLog.Action.BACKUP_CODES_USED)
        assert used.facility_id == staff_user.facility_id
