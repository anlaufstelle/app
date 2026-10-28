"""Pins for factor ownership, authentication freshness and audit evidence.

Source: mutation delta of the release-0.99.9 checkpoint, 23 mutants.
Groups: confirmation (6), disabling (5), passkey removal (12).
"""

import pytest
from django.core.exceptions import PermissionDenied
from django_otp_webauthn.models import WebAuthnCredential

from core.models import AuditLog, EncryptedTOTPDevice, User
from core.services.security.mfa import confirm_totp_setup, disable_mfa, remove_passkey

pytestmark = pytest.mark.django_db
IP_ADDRESS = "192.0.2.41"


@pytest.fixture
def credential(staff_user):
    return WebAuthnCredential.objects.create(
        user=staff_user,
        name="Security key",
        confirmed=True,
        credential_id=b"audit-key",
        public_key=b"public-key",
        aaguid="00000000-0000-0000-0000-000000000000",
        sign_count=0,
    )


def test_confirmation_preserves_pending_devices_and_audit(staff_user):
    """Replace confirmed factors only and retain the audit target and client IP.

    x_confirm_totp_setup__mutmut_27, x_confirm_totp_setup__mutmut_32,
    x_confirm_totp_setup__mutmut_34, x_confirm_totp_setup__mutmut_38,
    x_confirm_totp_setup__mutmut_40.
    """
    old = EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=True)
    other = EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=False)
    pending = EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=False)

    result = confirm_totp_setup(staff_user, pending.pk, ip_address=IP_ADDRESS)

    assert result.device.pk == pending.pk
    pending.refresh_from_db()
    other.refresh_from_db()
    assert pending.confirmed is True
    assert other.confirmed is False
    assert not EncryptedTOTPDevice.objects.filter(pk=old.pk).exists()
    audit = AuditLog.objects.for_facility(staff_user.facility).get(user=staff_user, action=AuditLog.Action.MFA_ENABLED)
    assert audit.detail["count"] == 1
    assert (audit.target_type, audit.target_id, audit.ip_address) == ("User", str(staff_user.pk), IP_ADDRESS)


def test_confirmation_preserves_newer_device_state(staff_user, monkeypatch):
    """Confirmation retains intervening throttle changes: x_confirm_totp_setup__mutmut_19."""
    pending = EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=False)
    original_save = EncryptedTOTPDevice.save

    def save_with_intervening_throttle(device, *args, **kwargs):
        EncryptedTOTPDevice.objects.filter(pk=device.pk).update(throttling_failure_count=9)
        return original_save(device, *args, **kwargs)

    monkeypatch.setattr(EncryptedTOTPDevice, "save", save_with_intervening_throttle)
    assert confirm_totp_setup(staff_user, pending.pk) is not None
    pending.refresh_from_db()
    assert pending.confirmed is True
    assert pending.throttling_failure_count == 9


def test_disable_passkey_only_account_revokes_sessions_and_audits(staff_user, credential):
    """Disabling passkey-only MFA revokes authentication and retains scoped audit IP.

    x_disable_mfa__mutmut_16, x_disable_mfa__mutmut_21,
    x_disable_mfa__mutmut_24, x_disable_mfa__mutmut_30.
    """
    previous_hash = staff_user.get_session_auth_hash()
    assert disable_mfa(staff_user, ip_address=IP_ADDRESS) is not None
    assert not WebAuthnCredential.objects.filter(pk=credential.pk).exists()
    staff_user.refresh_from_db()
    assert staff_user.get_session_auth_hash() != previous_hash
    audit = AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.MFA_DISABLED).get()
    assert audit.facility_id == staff_user.facility_id
    assert audit in AuditLog.objects.for_facility(staff_user.facility)
    assert audit.ip_address == IP_ADDRESS
    assert audit.detail["sessions_revoked"] is True


@pytest.mark.parametrize("change", ["password", "inactive"])
def test_removal_rejects_stale_authentication(staff_user, credential, change):
    """Reject stale authorization without deleting factors or revoking sessions.

    x_remove_passkey__mutmut_2, x_remove_passkey__mutmut_4, x_remove_passkey__mutmut_5.
    """
    EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=True)
    changes = {"password": "changed-password"} if change == "password" else {"is_active": False}
    User.objects.filter(pk=staff_user.pk).update(**changes)
    before = User.objects.get(pk=staff_user.pk).get_session_auth_hash()
    with pytest.raises(PermissionDenied):
        remove_passkey(staff_user, credential.pk)
    assert WebAuthnCredential.objects.filter(pk=credential.pk).exists()
    assert User.objects.get(pk=staff_user.pk).get_session_auth_hash() == before
    assert not AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.WEBAUTHN_REMOVED).exists()


@pytest.mark.parametrize("replacement", ["foreign", "pending"])
def test_last_factor_requires_confirmed_owned_replacement(staff_user, lead_user, credential, replacement):
    """Foreign or pending TOTP cannot replace the last factor.

    x_remove_passkey__mutmut_20, x_remove_passkey__mutmut_21.
    """
    EncryptedTOTPDevice.objects.create(
        user=lead_user if replacement == "foreign" else staff_user,
        confirmed=replacement == "foreign",
    )
    before = staff_user.get_session_auth_hash()
    with pytest.raises(PermissionDenied):
        remove_passkey(staff_user, credential.pk)
    assert WebAuthnCredential.objects.filter(pk=credential.pk).exists()
    assert User.objects.get(pk=staff_user.pk).get_session_auth_hash() == before
    assert not AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.WEBAUTHN_REMOVED).exists()


def test_removal_retains_scoped_audit_target_and_ip(staff_user, credential):
    """Successful removal retains facility, account target and client IP.

    x_remove_passkey__mutmut_34, x_remove_passkey__mutmut_35,
    x_remove_passkey__mutmut_37, x_remove_passkey__mutmut_41, x_remove_passkey__mutmut_43.
    """
    EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=True)
    remove_passkey(staff_user, credential.pk, ip_address=IP_ADDRESS)
    audit = AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.WEBAUTHN_REMOVED).get()
    assert audit.facility_id == staff_user.facility_id
    assert audit in AuditLog.objects.for_facility(staff_user.facility)
    assert (audit.target_type, audit.target_id, audit.ip_address) == ("User", str(staff_user.pk), IP_ADDRESS)
