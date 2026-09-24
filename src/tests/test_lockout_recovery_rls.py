"""RLS-Guards fuer anonyme Lockout-Recovery-Pfade (Refs #2111)."""

import pytest
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from core.models import AuditLog, User
from core.services.security import build_recovery_token, generate_backup_codes, invite_token_generator
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


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestPasswordSetupUnlockUnderProdRole:
    """Reset-Confirm und Invite-Setup (anonym) heben den Lockout unter NOSUPERUSER/NOBYPASSRLS auf.

    Beide Wege laufen ueber ``revoke_credentials`` (Refs #1965): ``lift_lockout``
    schreibt LOGIN_UNLOCK ueber den request-losen Helper ``audit_event``, waehrend
    die Session-GUCs fuer den anonymen Request noch auf ``''`` stehen
    (FacilityScopeMiddleware). Ohne Sync auf den Ziel-Kontext verletzt der
    INSERT die WITH-CHECK-Policy (Migration 0085) — NACHDEM das Passwort
    bereits gespeichert ist: 500er statt Weiterleitung, keine Unlock- und
    keine CREDENTIALS_REVOKED-Zeile (Refs #2391).
    """

    @pytest.mark.parametrize(
        ("route", "token_generator", "trigger", "usable_password"),
        [
            ("/invite/{uid}/{token}/", invite_token_generator, "invite_setup", False),
            ("/password-reset/{uid}/{token}/", default_token_generator, "password_reset", True),
        ],
        ids=["invite", "password_reset"],
    )
    def test_set_password_writes_unlock_and_revoked_with_target_facility_guc(
        self,
        client,
        rls_test_role,  # noqa: F811
        facility_a_with_data,  # noqa: F811
        route,
        token_generator,
        trigger,
        usable_password,
    ):
        user = User.objects.create_user(
            username=f"rls-setup-{trigger}",
            email=f"{trigger}@example.org",
            role=User.Role.STAFF,
            facility=facility_a_with_data,
        )
        if usable_password:
            user.set_password("Altes!Passw0rd")
        else:
            user.set_unusable_password()
        user.save(update_fields=["password"])
        url = route.format(uid=urlsafe_base64_encode(force_bytes(user.pk)), token=token_generator.make_token(user))
        new_password = "SetupChos3n!Pwd"

        with as_rls_role(rls_test_role, facility_id=""):
            response = client.get(url, follow=True)
            assert response.status_code == 200
            response = client.post(
                response.redirect_chain[-1][0],
                {"new_password1": new_password, "new_password2": new_password},
                REMOTE_ADDR="203.0.113.230",
            )

        assert response.status_code == 302
        unlock = AuditLog.objects.get(user=user, action=AuditLog.Action.LOGIN_UNLOCK)
        assert unlock.facility_id == facility_a_with_data.pk
        assert unlock.detail["trigger"] == trigger
        assert unlock.ip_address == "203.0.113.230"
        revoked = AuditLog.objects.get(user=user, action=AuditLog.Action.CREDENTIALS_REVOKED)
        assert revoked.facility_id == facility_a_with_data.pk
        assert revoked.detail["reason"] == trigger
