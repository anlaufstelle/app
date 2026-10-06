"""Guards for the Sudo re-authentication case ENT-SUDO-01 in matrix B (Refs #2476).

An empty second-factor field and a wrong non-empty code take different paths
through ``SudoModeView.post``; the manual case therefore sends a real POST
without ``otp_token`` in addition to the wrong-code probe. Each trigger route
the case offers (``/mfa/disable/`` without MFA enforcement, ``/mfa/setup/``
with it) carries its own ``next`` and audit value; the behaviour pins replay
both round trips and the password-only POST against the code.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest
from django_otp.oath import totp as oath_totp

from core.models import AuditLog
from core.models import EncryptedTOTPDevice as TOTPDevice
from core.services.security.sudo_mode import SUDO_SESSION_KEY
from tests._matrix_text import MATRIX_B, case_section, matrix_case, non_empty_lines

# Reads files under docs/ — not available inside the mutation sandbox.
pytestmark = pytest.mark.architecture

SUDO_CASE = "### TC-ID: ENT-SUDO-01 "


class TestSudoProbeSendsMissingOtp:
    def test_missing_otp_post_is_a_mandatory_step(self):
        steps = non_empty_lines(case_section(matrix_case(MATRIX_B, SUDO_CASE), "Schritte"))
        missing = [line for line in steps if "ohne `otp_token`" in line]
        assert missing, "ENT-SUDO-01 needs a step that POSTs without otp_token."
        assert any("Network" in line and "`required`" in line for line in missing)

    def test_wrong_code_is_an_additional_probe_not_a_substitute(self):
        steps = non_empty_lines(case_section(matrix_case(MATRIX_B, SUDO_CASE), "Schritte"))
        wrong_code = [line for line in steps if "`000000`" in line]
        assert wrong_code, "The non-empty wrong code stays a separate probe."
        assert not any("ohne `otp_token`" in line or "leer" in line for line in wrong_code), (
            "A wrong non-empty code must not stand in for the missing second factor."
        )

    def test_expected_audit_count_covers_three_failures(self):
        expected = case_section(matrix_case(MATRIX_B, SUDO_CASE), "Erwartetes Ergebnis")
        assert "drei neue `SUDO_MODE_FAILED`" in expected
        assert "nicht geprüft" in expected


class TestSudoVariantsCarryTheirOwnValues:
    @pytest.mark.parametrize("path", ["/mfa/disable/", "/mfa/setup/"])
    def test_each_offered_trigger_has_next_and_audit_value(self, path):
        block = matrix_case(MATRIX_B, SUDO_CASE)
        if path not in case_section(block, "Vorbereitung"):
            pytest.skip(f"{path} is not offered as a trigger")
        assert f"/sudo/?next={quote(path, safe='')}" in block
        assert f'detail.next="{path}"' in block


def _totp_token(device: TOTPDevice) -> str:
    token = oath_totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)
    return f"{token:0{device.digits}d}"


def _mfa_verified(client, user):
    client.force_login(user)
    session = client.session
    session["mfa_verified"] = True
    session.save()


def _failed_sudo(user) -> list[dict]:
    return list(
        AuditLog.objects.filter(user=user, action=AuditLog.Action.SUDO_MODE_FAILED)
        .order_by("timestamp")
        .values_list("detail", flat=True)
    )


@pytest.mark.django_db
class TestSudoMissingOtpBehaviour:
    @pytest.fixture(autouse=True)
    def _sudo_on(self, settings):
        settings.SUDO_MODE_ENABLED = True

    @pytest.mark.parametrize("payload", [{}, {"otp_token": ""}, {"otp_token": "   "}], ids=["absent", "empty", "blank"])
    def test_password_only_post_is_rejected_and_audited(self, client, staff_user, payload):
        TOTPDevice.objects.create(user=staff_user, name="default", confirmed=True)
        _mfa_verified(client, staff_user)

        response = client.post("/sudo/", {"password": "testpass123", "next": "/mfa/setup/", **payload})

        assert response.status_code == 403
        assert SUDO_SESSION_KEY not in client.session
        assert _failed_sudo(staff_user) == [{"factor": "otp"}]
        assert not AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.SUDO_MODE_ENTERED).exists()


@pytest.mark.django_db
class TestSudoSetupVariantFlow:
    def test_setup_route_round_trip(self, client, staff_user, settings):
        settings.SUDO_MODE_ENABLED = True
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
        device = TOTPDevice.objects.create(user=staff_user, name="default", confirmed=True)
        _mfa_verified(client, staff_user)
        assert staff_user.is_mfa_enforced

        first = client.get("/mfa/setup/")
        assert first.status_code == 302
        assert first["Location"] == "/sudo/?next=%2Fmfa%2Fsetup%2F"

        entered = client.post(
            "/sudo/",
            {"password": "testpass123", "otp_token": _totp_token(device), "next": "/mfa/setup/"},
        )
        assert entered.status_code == 302
        assert entered["Location"] == "/mfa/setup/"
        audit = AuditLog.objects.get(user=staff_user, action=AuditLog.Action.SUDO_MODE_ENTERED)
        assert audit.detail == {"next": "/mfa/setup/"}

        follow_up = client.get("/mfa/setup/")
        assert follow_up.status_code == 302
        assert follow_up["Location"] == "/mfa/settings/"

        refused = client.post("/mfa/disable/")
        assert refused.status_code == 302
        assert refused["Location"] == "/mfa/settings/"
        assert TOTPDevice.objects.filter(user=staff_user, confirmed=True).exists()


@pytest.mark.django_db
class TestSudoDisableVariantFlow:
    def test_disable_route_round_trip(self, client, staff_user, settings):
        settings.SUDO_MODE_ENABLED = True
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = False
        device = TOTPDevice.objects.create(user=staff_user, name="default", confirmed=True)
        _mfa_verified(client, staff_user)

        first = client.post("/mfa/disable/")
        assert first.status_code == 302
        assert first["Location"] == "/sudo/?next=%2Fmfa%2Fdisable%2F"

        entered = client.post(
            "/sudo/",
            {"password": "testpass123", "otp_token": _totp_token(device), "next": "/mfa/disable/"},
        )
        assert entered["Location"] == "/mfa/disable/"
        audit = AuditLog.objects.get(user=staff_user, action=AuditLog.Action.SUDO_MODE_ENTERED)
        assert audit.detail == {"next": "/mfa/disable/"}

        assert client.get("/mfa/disable/").status_code == 405

        disabled = client.post("/mfa/disable/")
        assert disabled["Location"] == "/mfa/settings/"
        assert not TOTPDevice.objects.filter(user=staff_user).exists()
