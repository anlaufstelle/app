"""Pins for facility-scoped account management and the second-factor reset (``core.services.user_access``).

Source: mutation delta of the release-0.99.6 checkpoint, 16 mutants.
Groups:

* ``reset_user_mfa`` -- the target row is locked under the facility the
  caller looked up: a ``super_admin`` acting on an account that moved to
  another facility in the meantime is refused, nothing is removed and nothing
  is audited. The refusal and the failed hash write name their reason.
* ``has_second_factor`` -- a TOTP device alone (even unconfirmed) and a
  passkey alone each count as a second factor.
* ``reset_managed_user_mfa`` -- the facility-scoped entry hands the client IP
  through to the ``MFA_RESET_BY_ADMIN`` audit row.
* ``_assert_can_reset_mfa`` -- an actor without any attributes (``None``) is
  refused with ``PermissionDenied``, not ``AttributeError``; every refusal
  carries its message.
* ``_assert_actor``, ``create_invited_user``, ``update_user_access`` -- the
  refusals for a foreign facility, the ``super_admin`` role and the own
  administration access carry their message.
"""

import pytest
from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken

from core.models import AuditLog, User
from core.models import EncryptedTOTPDevice as TOTPDevice
from core.models.user import SessionRevocationError
from core.services.security import generate_backup_codes
from core.services.user_access import (
    create_invited_user,
    has_second_factor,
    reset_managed_user_mfa,
    reset_user_mfa,
    update_user_access,
)

pytestmark = pytest.mark.django_db

INACTIVE_ACTOR_MESSAGE = "Second-factor reset requires an active, signed-in administrator."
OWN_ACCOUNT_MESSAGE = "The own second factor cannot be reset through account management."
NOT_ALLOWED_MESSAGE = "Second-factor reset is not allowed for this account."
HASH_NOT_STORED_MESSAGE = "The revoked password hash of the target was not stored."
FOREIGN_FACILITY_MESSAGE = "Benutzerverwaltung ist nur in der eigenen Einrichtung erlaubt."
SUPER_ADMIN_ROLE_MESSAGE = "Die Rolle Systemadministration kann hier nicht vergeben werden."
OWN_ADMIN_ACCESS_MESSAGE = "Der eigene Administrationszugang darf nicht entzogen werden."


def _passkey(user):
    from django_otp_webauthn.models import WebAuthnCredential

    return WebAuthnCredential.objects.create(
        user=user,
        name="key",
        confirmed=True,
        credential_id=f"cred-{user.pk}".encode(),
        public_key=b"pubkey-bytes",
        aaguid="00000000-0000-0000-0000-000000000000",
        sign_count=0,
    )


def _enroll(user) -> None:
    TOTPDevice.objects.create(user=user, name="phone", confirmed=True)
    _passkey(user)
    generate_backup_codes(user)


def _factor_rows(user) -> tuple[int, int, int, int]:
    from django_otp_webauthn.models import WebAuthnCredential

    return (
        TOTPDevice.objects.filter(user=user).count(),
        WebAuthnCredential.objects.filter(user=user).count(),
        StaticDevice.objects.filter(user=user).count(),
        StaticToken.objects.filter(device__user=user).count(),
    )


def _reset_rows(user):
    return AuditLog.objects.filter(user=user, action=AuditLog.Action.MFA_RESET_BY_ADMIN)


def _access_values(**overrides) -> dict:
    values = {
        "username": "testadmin",
        "email": "admin@example.org",
        "display_name": "Admin",
        "role": User.Role.FACILITY_ADMIN,
        "is_active": True,
        "mfa_required": False,
        "can_confirm_deletion": True,
    }
    values.update(overrides)
    return values


class TestResetUserMfaLocksUnderTheLookedUpFacility:
    def test_super_admin_does_not_reset_an_account_moved_after_lookup(
        self, super_admin_user, staff_user, second_facility
    ):
        """A super_admin may reach every facility, so only the facility filter of
        the locked read notices that the looked-up account has moved: the reset
        is refused, every factor stays and no audit row is written.

        Pins ``x_reset_user_mfa__mutmut_8``.
        """
        _enroll(staff_user)
        before = _factor_rows(staff_user)
        # ``staff_user`` is the row as the caller looked it up; the stored row
        # has moved to another facility before the lock is taken.
        User.objects.filter(pk=staff_user.pk).update(facility=second_facility)

        with pytest.raises(PermissionDenied):
            reset_user_mfa(staff_user, actor=super_admin_user)

        assert _factor_rows(staff_user) == before
        assert not _reset_rows(staff_user).exists()

    def test_refusal_for_a_moved_account_names_the_reason(self, admin_user, staff_user, second_facility):
        """The refusal for an account that moved after lookup carries the
        "not allowed for this account" message.

        Pins ``x_reset_user_mfa__mutmut_11``.
        """
        _enroll(staff_user)
        User.objects.filter(pk=staff_user.pk).update(facility=second_facility)

        with pytest.raises(PermissionDenied) as exc_info:
            reset_user_mfa(staff_user, actor=admin_user)

        assert exc_info.value.args == (NOT_ALLOWED_MESSAGE,)

    def test_failed_hash_write_names_the_reason(self, admin_user, staff_user, monkeypatch):
        """When storing the revoked password hash hits no row, the
        ``SessionRevocationError`` says so.

        Pins ``x_reset_user_mfa__mutmut_43``.
        """
        _enroll(staff_user)
        monkeypatch.setattr(QuerySet, "update", lambda queryset, **values: 0)

        with pytest.raises(SessionRevocationError) as exc_info:
            reset_user_mfa(staff_user, actor=admin_user)

        assert exc_info.value.args == (HASH_NOT_STORED_MESSAGE,)


class TestHasSecondFactorSingleFactor:
    def test_unconfirmed_totp_device_alone_counts(self, staff_user):
        """An account whose only factor is an unconfirmed TOTP device has a
        second factor (the reset would remove it).

        Pins ``x_has_second_factor__mutmut_2`` and ``x_has_second_factor__mutmut_3``.
        """
        TOTPDevice.objects.create(user=staff_user, name="pending", confirmed=False)

        assert has_second_factor(staff_user) is True

    def test_passkey_alone_counts(self, staff_user):
        """An account whose only factor is a passkey has a second factor.

        Pins ``x_has_second_factor__mutmut_2`` and ``x_has_second_factor__mutmut_4``.
        """
        _passkey(staff_user)

        assert has_second_factor(staff_user) is True


class TestResetManagedUserMfaAuditIp:
    def test_audit_row_stores_the_client_ip(self, facility, admin_user, staff_user):
        """The facility-scoped reset writes the given client IP into the
        ``MFA_RESET_BY_ADMIN`` audit row.

        Pins ``x_reset_managed_user_mfa__mutmut_12`` and ``x_reset_managed_user_mfa__mutmut_15``.
        """
        _enroll(staff_user)

        reset_managed_user_mfa(staff_user, facility=facility, actor=admin_user, ip_address="203.0.113.7")

        assert _reset_rows(staff_user).get().ip_address == "203.0.113.7"


class TestAssertCanResetMfaRefusals:
    def test_actor_without_attributes_is_refused_fail_closed(self, staff_user):
        """An actor without ``is_authenticated`` (here ``None``) is refused with
        ``PermissionDenied`` -- not ``AttributeError`` -- and nothing is removed.

        Pins ``x__assert_can_reset_mfa__mutmut_8``.
        """
        _enroll(staff_user)
        before = _factor_rows(staff_user)

        with pytest.raises(PermissionDenied) as exc_info:
            reset_user_mfa(staff_user, actor=None)

        assert exc_info.value.args == (INACTIVE_ACTOR_MESSAGE,)
        assert _factor_rows(staff_user) == before

    def test_inactive_actor_refusal_names_the_reason(self, admin_user, staff_user):
        """An inactive administrator is refused with the "active, signed-in
        administrator" message.

        Pins ``x__assert_can_reset_mfa__mutmut_22``.
        """
        admin_user.is_active = False

        with pytest.raises(PermissionDenied) as exc_info:
            reset_user_mfa(staff_user, actor=admin_user)

        assert exc_info.value.args == (INACTIVE_ACTOR_MESSAGE,)

    def test_own_account_refusal_names_the_reason(self, admin_user):
        """Resetting the own account is refused with the own-second-factor message.

        Pins ``x__assert_can_reset_mfa__mutmut_27``.
        """
        with pytest.raises(PermissionDenied) as exc_info:
            reset_user_mfa(admin_user, actor=admin_user)

        assert exc_info.value.args == (OWN_ACCOUNT_MESSAGE,)

    def test_foreign_facility_target_refusal_names_the_reason(self, admin_user, foreign_staff):
        """A facility_admin is refused for a foreign-facility account with the
        "not allowed for this account" message.

        Pins ``x__assert_can_reset_mfa__mutmut_55``.
        """
        with pytest.raises(PermissionDenied) as exc_info:
            reset_user_mfa(foreign_staff, actor=admin_user)

        assert exc_info.value.args == (NOT_ALLOWED_MESSAGE,)


class TestAccountManagementRefusalMessages:
    def test_foreign_facility_refusal_names_the_reason(self, second_facility, admin_user, foreign_staff):
        """A facility_admin acting on a foreign facility is refused with the
        own-facility message.

        Pins ``x__assert_actor__mutmut_31``.
        """
        with pytest.raises(PermissionDenied) as exc_info:
            reset_managed_user_mfa(foreign_staff, facility=second_facility, actor=admin_user)

        assert exc_info.value.args == (FOREIGN_FACILITY_MESSAGE,)

    def test_inviting_a_super_admin_names_the_reason(self, facility, admin_user):
        """Inviting an account with the super_admin role is refused with the
        role message.

        Pins ``x_create_invited_user__mutmut_6``.
        """
        with pytest.raises(PermissionDenied) as exc_info:
            create_invited_user(
                facility=facility,
                actor=admin_user,
                username="new-super",
                email="new-super@example.org",
                display_name="New",
                role=User.Role.SUPER_ADMIN,
                mfa_required=False,
                can_confirm_deletion=False,
            )

        assert exc_info.value.args == (SUPER_ADMIN_ROLE_MESSAGE,)
        assert not User.objects.filter(username="new-super").exists()

    def test_granting_super_admin_names_the_reason(self, facility, admin_user, staff_user):
        """Raising an account to the super_admin role is refused with the role message.

        Pins ``x_update_user_access__mutmut_9``.
        """
        values = _access_values(username=staff_user.username, role=User.Role.SUPER_ADMIN)

        with pytest.raises(PermissionDenied) as exc_info:
            update_user_access(staff_user, facility=facility, actor=admin_user, **values)

        assert exc_info.value.args == (SUPER_ADMIN_ROLE_MESSAGE,)

    def test_removing_the_own_admin_access_names_the_reason(self, facility, admin_user):
        """Downgrading the own facility_admin access is refused with the
        own-administration-access message.

        Pins ``x_update_user_access__mutmut_30``.
        """
        values = _access_values(role=User.Role.STAFF)

        with pytest.raises(PermissionDenied) as exc_info:
            update_user_access(admin_user, facility=facility, actor=admin_user, **values)

        assert exc_info.value.args == (OWN_ADMIN_ACCESS_MESSAGE,)
        admin_user.refresh_from_db()
        assert admin_user.role == User.Role.FACILITY_ADMIN
