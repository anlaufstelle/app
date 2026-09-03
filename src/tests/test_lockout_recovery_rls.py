"""RLS-Guards fuer anonyme Lockout-Recovery-Pfade (Refs #2111)."""

import pytest
from django.urls import reverse

from core.models import AuditLog, User
from core.services.security import build_recovery_token, generate_backup_codes
from core.services.security.lockout_recovery import consume_recovery_token, verify_recovery_token
from tests.test_rls_functional import (  # noqa: F401
    as_rls_role,
    facility_a_with_data,
    rls_test_role,
)


def _user_with_anchor(facility, username):
    user = User.objects.create_user(
        username=username,
        role=User.Role.STAFF,
        facility=facility,
    )
    AuditLog.objects.create(
        user=user,
        facility=facility,
        action=AuditLog.Action.LOGIN_FAILED,
        detail={"username": user.username},
    )
    return user, build_recovery_token(user)


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestLockoutRecoveryUnderProdRole:
    """Anonyme Requests funktionieren unter NOSUPERUSER/NOBYPASSRLS."""

    def test_verify_reads_anchor_with_target_facility_guc(
        self,
        rls_test_role,  # noqa: F811
        facility_a_with_data,  # noqa: F811
    ):
        user, token = _user_with_anchor(facility_a_with_data, "rls-recovery-verify")

        with as_rls_role(rls_test_role, facility_id=""):
            verified = verify_recovery_token(token)

        assert verified == user

    def test_consume_writes_unlock_with_target_facility_guc(
        self,
        rls_test_role,  # noqa: F811
        facility_a_with_data,  # noqa: F811
    ):
        user, token = _user_with_anchor(facility_a_with_data, "rls-recovery-consume")

        with as_rls_role(rls_test_role, facility_id=""):
            consumed = consume_recovery_token(token, ip_address="203.0.113.221")

        assert consumed == user
        unlock = AuditLog.objects.get(user=user, action=AuditLog.Action.LOGIN_UNLOCK)
        assert unlock.facility_id == facility_a_with_data.pk
        assert unlock.ip_address == "203.0.113.221"

    def test_backup_code_view_writes_unlock_with_target_facility_guc(
        self,
        client,
        rls_test_role,  # noqa: F811
        facility_a_with_data,  # noqa: F811
    ):
        user, _token = _user_with_anchor(facility_a_with_data, "rls-recovery-backup")
        code = generate_backup_codes(user)[0]

        with as_rls_role(rls_test_role, facility_id=""):
            response = client.post(
                reverse("core:lockout_recovery_backup_code"),
                {"username": user.username, "backup_code": code},
                REMOTE_ADDR="203.0.113.222",
            )

        assert response.status_code == 302
        unlock = AuditLog.objects.get(user=user, action=AuditLog.Action.LOGIN_UNLOCK)
        assert unlock.facility_id == facility_a_with_data.pk
        assert unlock.ip_address == "203.0.113.222"
