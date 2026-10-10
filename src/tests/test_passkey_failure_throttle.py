"""Failed and interrupted passkey assertions cannot bypass the second factor."""

from unittest.mock import patch

import pytest
from django_otp_webauthn import exceptions
from django_otp_webauthn.models import WebAuthnCredential

from core.models import AuditLog

pytestmark = pytest.mark.django_db
COMPLETE_URL = "/webauthn/authentication/complete/"


def _pending(client):
    session = client.session
    session["otp_webauthn_authentication_state"] = {"challenge": "test"}
    session.save()


@pytest.mark.parametrize("body", ["{}", "{"])
def test_failed_or_interrupted_assertions_are_counted_and_throttled(client, staff_user, ratelimit_enabled, body):
    WebAuthnCredential.objects.create(
        user=staff_user,
        name="test",
        confirmed=True,
        credential_id=b"cred",
        public_key=b"public",
        aaguid="00000000-0000-0000-0000-000000000000",
        sign_count=0,
    )
    client.force_login(staff_user)
    with patch(
        "django_otp_webauthn.helpers.WebAuthnHelper.authenticate_complete", side_effect=exceptions.UnprocessableEntity()
    ):
        for attempt in range(60):
            _pending(client)
            response = client.post(COMPLETE_URL, body, content_type="application/json")
            assert response.status_code == (422 if body == "{}" else 400)
            assert client.session.get("mfa_verified") is not True
            assert client.session.get("otp_device_id") is None
            assert "otp_webauthn_authentication_state" not in client.session
            assert (
                AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.WEBAUTHN_FAILED).count() == attempt + 1
            )
        _pending(client)
        blocked = client.post(COMPLETE_URL, body, content_type="application/json")
    assert blocked.status_code == 429
    assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.WEBAUTHN_FAILED).count() == 60
    assert not AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.MFA_VERIFIED).exists()
    assert client.session.get("mfa_verified") is not True
    assert client.session.get("otp_device_id") is None
    protected = client.get("/mfa/settings/")
    assert protected.status_code == 302
    assert protected.url == "/mfa/verify/"
    for entry in AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.WEBAUTHN_FAILED):
        assert entry.facility_id == staff_user.facility_id
        assert set(entry.detail) == {"event", "code"}
        assert entry.detail["event"] == "webauthn_assertion_failed"


def test_malformed_request_without_started_ceremony_does_not_log_factor_failure(client, staff_user):
    client.force_login(staff_user)
    response = client.post(COMPLETE_URL, "{", content_type="application/json")
    assert response.status_code == 400
    assert not AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.WEBAUTHN_FAILED).exists()
    assert client.session.get("mfa_verified") is not True


def test_unsupported_method_does_not_consume_a_ceremony_or_log_factor_failure(client, staff_user):
    client.force_login(staff_user)
    _pending(client)
    response = client.generic("GET", COMPLETE_URL, "{", content_type="application/json")
    assert response.status_code == 400
    assert "otp_webauthn_authentication_state" in client.session
    assert not AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.WEBAUTHN_FAILED).exists()
    assert client.session.get("mfa_verified") is not True
