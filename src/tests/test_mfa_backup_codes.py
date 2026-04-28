"""Tests für 2FA-Backup-Codes (Refs #588)."""

import re

import pytest
from django_otp.oath import totp as oath_totp
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice

from core.models import AuditLog
from core.services.mfa import (
    BACKUP_CODES_COUNT,
    generate_backup_codes,
    remaining_backup_codes,
    verify_backup_code,
)

CODE_FORMAT = re.compile(r"^[0-9a-f]{4}-[0-9a-f]{4}$")


@pytest.mark.django_db
class TestBackupCodesService:
    def test_generate_returns_ten_codes_in_expected_format(self, staff_user):
        codes = generate_backup_codes(staff_user)
        assert len(codes) == BACKUP_CODES_COUNT
        assert all(CODE_FORMAT.match(c) for c in codes)
        assert len(set(codes)) == BACKUP_CODES_COUNT, "Codes müssen alle unterschiedlich sein"

    def test_generate_replaces_existing_codes(self, staff_user):
        first = generate_backup_codes(staff_user)
        second = generate_backup_codes(staff_user)
        assert set(first).isdisjoint(set(second))
        # Genau ein StaticDevice mit frischem Satz.
        devices = StaticDevice.objects.filter(user=staff_user, name="backup")
        assert devices.count() == 1
        assert devices.first().token_set.count() == BACKUP_CODES_COUNT

    def test_remaining_backup_codes(self, staff_user):
        assert remaining_backup_codes(staff_user) == 0
        generate_backup_codes(staff_user)
        assert remaining_backup_codes(staff_user) == BACKUP_CODES_COUNT

    def test_verify_backup_code_is_single_use(self, staff_user):
        codes = generate_backup_codes(staff_user)
        one = codes[0]
        assert verify_backup_code(staff_user, one) is True
        assert verify_backup_code(staff_user, one) is False
        assert remaining_backup_codes(staff_user) == BACKUP_CODES_COUNT - 1

    def test_verify_unknown_code_rejected(self, staff_user):
        generate_backup_codes(staff_user)
        assert verify_backup_code(staff_user, "0000-0000") is False
        assert verify_backup_code(staff_user, "") is False
        assert verify_backup_code(staff_user, None) is False

    def test_verify_without_device_returns_false(self, staff_user):
        assert verify_backup_code(staff_user, "abcd-ef01") is False


@pytest.mark.django_db
class TestMFAVerifyWithBackupCode:
    """Der Login-Prompt akzeptiert Backup-Codes, wenn der Nutzer `mode=backup` sendet."""

    def _setup_confirmed_totp(self, user):
        device = TOTPDevice.objects.create(user=user, name="default", confirmed=True)
        return device

    def test_backup_code_login_marks_session_verified(self, client, staff_user):
        self._setup_confirmed_totp(staff_user)
        codes = generate_backup_codes(staff_user)
        client.login(username="teststaff", password="testpass123")

        response = client.post(
            "/mfa/verify/",
            {"mode": "backup", "token": codes[0]},
        )
        assert response.status_code == 302
        assert response.url == "/"
        # Session als verifiziert markiert; Code verbraucht.
        assert client.session.get("mfa_verified") is True
        assert remaining_backup_codes(staff_user) == BACKUP_CODES_COUNT - 1
        assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.BACKUP_CODES_USED).exists()

    def test_backup_code_without_dash_accepted(self, client, staff_user):
        """Ein- und Abtippen ohne Bindestrich muss vom Format her toleriert werden."""
        self._setup_confirmed_totp(staff_user)
        codes = generate_backup_codes(staff_user)
        raw = codes[0].replace("-", "")
        client.login(username="teststaff", password="testpass123")

        response = client.post(
            "/mfa/verify/",
            {"mode": "backup", "token": raw},
        )
        assert response.status_code == 302

    def test_totp_path_still_works_when_not_in_backup_mode(self, client, staff_user):
        device = self._setup_confirmed_totp(staff_user)
        generate_backup_codes(staff_user)
        token = oath_totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)

        client.login(username="teststaff", password="testpass123")
        response = client.post(
            "/mfa/verify/",
            {"token": f"{token:0{device.digits}d}"},
        )
        assert response.status_code == 302
        # Backup-Codes unverändert.
        assert remaining_backup_codes(staff_user) == BACKUP_CODES_COUNT

    def test_invalid_backup_code_logs_mfa_failed(self, client, staff_user):
        self._setup_confirmed_totp(staff_user)
        generate_backup_codes(staff_user)
        client.login(username="teststaff", password="testpass123")

        response = client.post(
            "/mfa/verify/",
            {"mode": "backup", "token": "1234-5678"},
        )
        assert response.status_code == 200  # Form mit Fehler
        failed = AuditLog.objects.filter(
            user=staff_user,
            action=AuditLog.Action.MFA_FAILED,
            detail__mode="backup",
        ).first()
        assert failed is not None


@pytest.mark.django_db
class TestMFABackupCodesView:
    def test_view_shows_codes_once(self, client, staff_user):
        client.login(username="teststaff", password="testpass123")
        session = client.session
        session["mfa_backup_codes"] = ["aaaa-bbbb", "cccc-dddd"]
        session.save()

        first = client.get("/mfa/backup-codes/")
        assert first.status_code == 200
        assert b"aaaa-bbbb" in first.content

        # Zweiter GET zeigt nichts mehr — Codes sind aus der Session entfernt.
        second = client.get("/mfa/backup-codes/")
        assert second.status_code == 302
        assert second.url == "/mfa/settings/"

    def test_view_redirects_when_no_codes_in_session(self, client, staff_user):
        client.login(username="teststaff", password="testpass123")
        response = client.get("/mfa/backup-codes/")
        assert response.status_code == 302
        assert response.url == "/mfa/settings/"


@pytest.mark.django_db
class TestMFARegenerate:
    def test_regenerate_rejected_on_invalid_totp(self, client, staff_user):
        TOTPDevice.objects.create(user=staff_user, name="default", confirmed=True)
        first = generate_backup_codes(staff_user)
        client.login(username="teststaff", password="testpass123")

        response = client.post(
            "/mfa/backup-codes/regenerate/",
            {"token": "000000"},
        )
        # Ungültiger Token → Redirect zurück in die Settings ohne Neugenerierung.
        assert response.status_code == 302
        assert response.url == "/mfa/settings/"
        still = StaticDevice.objects.get(user=staff_user, name="backup").token_set.values_list("token", flat=True)
        assert set(still) == set(first)

    def test_regenerate_rotates_codes_on_valid_totp(self, client, staff_user):
        device = TOTPDevice.objects.create(user=staff_user, name="default", confirmed=True)
        first = generate_backup_codes(staff_user)
        client.login(username="teststaff", password="testpass123")

        token = oath_totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)
        response = client.post(
            "/mfa/backup-codes/regenerate/",
            {"token": f"{token:0{device.digits}d}"},
        )
        assert response.status_code == 302
        assert response.url == "/mfa/backup-codes/"
        new_codes = client.session.get("mfa_backup_codes")
        assert new_codes is not None
        assert set(first).isdisjoint(set(new_codes))
        assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.BACKUP_CODES_REGENERATED).exists()
