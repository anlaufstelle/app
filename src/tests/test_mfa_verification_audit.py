"""Audit contracts at the public MFA verification and regeneration endpoints."""

from unittest.mock import patch

import pytest
from django_otp.oath import totp
from django_otp.plugins.otp_static.models import StaticToken
from django_otp_webauthn import exceptions
from django_otp_webauthn.models import WebAuthnCredential

from core.models import AuditLog, EncryptedTOTPDevice
from core.services.security import generate_backup_codes

pytestmark = pytest.mark.django_db


def _totp(user):
    return EncryptedTOTPDevice.objects.create(user=user, name="test", confirmed=True, key="a" * 40)


def test_totp_success_is_audited_without_token(client, staff_user):
    device = _totp(staff_user)
    token = f"{totp(device.bin_key):06d}"
    client.force_login(staff_user)
    response = client.post("/mfa/verify/", {"token": token})
    assert response.status_code == 302
    entry = AuditLog.objects.get(action="mfa_verified", user=staff_user)
    assert entry.facility == staff_user.facility
    assert entry.detail == {"mode": "totp"}
    assert client.session["mfa_verified"] is True


def test_totp_failure_uses_fixed_method_even_with_untrusted_mode(client, staff_user):
    _totp(staff_user)
    client.force_login(staff_user)
    client.post("/mfa/verify/", {"token": "bad-secret", "mode": "body-pii@example.invalid"})
    entry = AuditLog.objects.get(action=AuditLog.Action.MFA_FAILED, user=staff_user)
    assert entry.detail == {"event": "mfa_token_invalid", "mode": "totp"}


@pytest.mark.parametrize("success", [True, False])
def test_passkey_assertion_result_is_audited_once(client, staff_user, success):
    device = WebAuthnCredential.objects.create(
        user=staff_user,
        name="test",
        confirmed=True,
        credential_id=b"cred",
        public_key=b"public",
        aaguid="00000000-0000-0000-0000-000000000000",
        sign_count=0,
    )
    client.force_login(staff_user)
    session = client.session
    session["otp_webauthn_authentication_state"] = {"challenge": "test"}
    session.save()
    with patch("django_otp_webauthn.helpers.WebAuthnHelper.authenticate_complete") as verify:
        if success:
            verify.return_value = device
        else:
            verify.side_effect = exceptions.UnprocessableEntity()
        response = client.post("/webauthn/authentication/complete/", "{}", content_type="application/json")
    assert response.status_code == (200 if success else 422)
    action = "mfa_verified" if success else AuditLog.Action.WEBAUTHN_FAILED
    entry = AuditLog.objects.get(action=action, user=staff_user)
    assert entry.facility == staff_user.facility
    if success:
        assert entry.detail == {"mode": "passkey"}
        assert client.session["mfa_verified"] is True
    else:
        assert entry.detail["event"] == "webauthn_assertion_failed"
        assert client.session.get("mfa_verified") is not True


@pytest.mark.parametrize("token", ["", "invalid-secret"])
def test_rejected_regeneration_is_audited_without_replacing_codes(client, staff_user, token):
    _totp(staff_user)
    generate_backup_codes(staff_user)
    staff_user.refresh_from_db()
    before = list(StaticToken.objects.filter(device__user=staff_user).values_list("token", flat=True))
    client.force_login(staff_user)
    session = client.session
    session["mfa_verified"] = True
    session.save()
    response = client.post("/mfa/backup-codes/regenerate/", {"token": token})
    assert response.status_code == 302
    entry = AuditLog.objects.get(action="mfa_backup_regenerate_denied", user=staff_user)
    assert entry.detail == {"reason": "invalid_totp"}
    staff_user.refresh_from_db()
    assert list(StaticToken.objects.filter(device__user=staff_user).values_list("token", flat=True)) == before


def test_backup_consumption_keeps_single_existing_audit(client, staff_user):
    _totp(staff_user)
    code = generate_backup_codes(staff_user)[0]
    client.force_login(staff_user)
    response = client.post("/mfa/verify/", {"token": code, "mode": "backup"})
    assert response.status_code == 302
    assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.BACKUP_CODES_USED).count() == 1
    assert not AuditLog.objects.filter(user=staff_user, action="mfa_verified").exists()


@pytest.mark.parametrize("verified", [False, True])
def test_regeneration_without_verified_factor_is_audited(client, staff_user, verified):
    if not verified:
        _totp(staff_user)
    client.force_login(staff_user)
    session = client.session
    session["mfa_verified"] = verified
    session.save()
    response = client.post("/mfa/backup-codes/regenerate/", {"token": "not-a-code"})
    assert response.status_code == 302
    entry = AuditLog.objects.get(action="mfa_backup_regenerate_denied", user=staff_user)
    assert entry.detail == {"reason": "missing_totp" if verified else "unverified_session"}
