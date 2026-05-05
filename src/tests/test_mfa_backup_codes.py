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

# Refs #790: token_urlsafe(16) -> 22 Zeichen aus Base64-URL-safe Alphabet.
CODE_FORMAT = re.compile(r"^[A-Za-z0-9_-]{22}$")


@pytest.mark.django_db
class TestBackupCodesService:
    def test_generate_returns_ten_codes_in_expected_format(self, staff_user):
        codes = generate_backup_codes(staff_user)
        assert len(codes) == BACKUP_CODES_COUNT
        assert all(CODE_FORMAT.match(c) for c in codes), (
            f"Erwartet 22-Zeichen URL-safe Base64 (128 Bit Entropie), gemessen: {codes}"
        )
        assert len(set(codes)) == BACKUP_CODES_COUNT, "Codes müssen alle unterschiedlich sein"

    def test_generated_codes_have_at_least_128_bits_of_entropy(self, staff_user):
        """Refs #790 (C-22): 22 Zeichen Base64URL = 132 Bit, also >= 128 Bit."""
        import math

        codes = generate_backup_codes(staff_user)
        for c in codes:
            entropy_bits = len(c) * math.log2(64)  # Base64-Alphabet
            assert entropy_bits >= 128, (
                f"Backup-Code '{c}' hat nur {entropy_bits:.0f} Bit Entropie, mind. 128 Bit erwartet."
            )

    def test_db_only_stores_hashes_not_clear_codes(self, staff_user):
        """Refs #790 (C-22): DB-Leak darf nicht aequivalent zur Code-Kompromittierung sein.
        StaticToken.token speichert SHA-256-Prefix (16 Hex-Zeichen, getruncated
        wegen django-otps CharField(max_length=16)) — kein Klartext.
        """
        codes = generate_backup_codes(staff_user)
        device = StaticDevice.objects.get(user=staff_user, name="backup")
        stored_tokens = list(device.token_set.values_list("token", flat=True))
        assert all(len(t) == 16 and all(c in "0123456789abcdef" for c in t) for t in stored_tokens), (
            f"DB-Tokens sollen SHA-256-Prefix sein (16 Hex-Zeichen), gemessen: {stored_tokens}"
        )
        # Keiner der Klartext-Codes darf in der DB stehen.
        for code in codes:
            assert code not in stored_tokens, (
                "Klartext-Backup-Code im DB-Token-Field gefunden — Hash-Storage greift nicht."
            )

    def test_legacy_cleartext_codes_still_verify(self, staff_user):
        """Refs #790: Pre-Migration Codes (xxxx-xxxx, Cleartext-Storage) muessen
        weiterhin verifizieren, damit User mit alten Codes nicht ausgesperrt
        sind, bis sie regenerieren."""
        from django_otp.plugins.otp_static.models import StaticToken

        device = StaticDevice.objects.create(user=staff_user, name="backup", confirmed=True)
        # Simuliere alten Cleartext-Eintrag.
        StaticToken.objects.create(device=device, token="abcd-1234")

        assert verify_backup_code(staff_user, "abcd-1234") is True
        # Single-use bleibt erhalten.
        assert verify_backup_code(staff_user, "abcd-1234") is False

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

    def test_legacy_8hex_without_dash_normalised(self, client, staff_user):
        """Legacy-Codes (8-Zeichen-Hex ohne Bindestrich) werden zu xxxx-xxxx
        normalisiert — Refs #790 erhaelt diese Convenience nur fuer Codes,
        die das alte Format zweifellos erfuellen.
        """
        from django_otp.plugins.otp_static.models import StaticToken

        self._setup_confirmed_totp(staff_user)
        # Legacy-Token direkt anlegen (Cleartext, Pre-#790-Aera).
        device = StaticDevice.objects.get_or_create(user=staff_user, name="backup", defaults={"confirmed": True})[0]
        device.confirmed = True
        device.save(update_fields=["confirmed"])
        StaticToken.objects.create(device=device, token="abcd-1234")
        client.login(username="teststaff", password="testpass123")

        response = client.post(
            "/mfa/verify/",
            {"mode": "backup", "token": "abcd1234"},  # ohne Dash, klein
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
        # Refs #790: DB speichert SHA-256-Prefixes, ``first`` enthaelt Klartext-Codes.
        from core.services.mfa import _hash_code

        still = StaticDevice.objects.get(user=staff_user, name="backup").token_set.values_list("token", flat=True)
        assert set(still) == {_hash_code(c) for c in first}

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
