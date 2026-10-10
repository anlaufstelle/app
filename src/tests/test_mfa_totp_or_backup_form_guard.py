"""Fresh TOTP and strong backup codes follow separate verification paths."""

from __future__ import annotations

import secrets
import time
from datetime import timedelta

import pytest
from django.utils import timezone
from django_otp.oath import totp as oath_totp
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken

from core.models import EncryptedTOTPDevice as TOTPDevice
from core.services.security import generate_backup_codes, verify_totp_or_backup
from core.services.security.mfa import BACKUP_DEVICE_NAME, _hash_code

SUDO_URL = "/sudo/"
PASSWORD = "testpass123"
HISTORIC_CODE = "abcd-1234"


def _code(device: TOTPDevice, *, steps_ahead: int = 0) -> str:
    token = oath_totp(
        device.bin_key,
        step=device.step,
        t0=device.t0 - steps_ahead * device.step,
        digits=device.digits,
    )
    return f"{token:0{device.digits}d}"


def _wrong_totp_code(device: TOTPDevice) -> str:
    """Sechs Ziffern, die in keinem Fenster der Toleranz gueltig sind."""
    valid = {_code(device, steps_ahead=offset) for offset in (-1, 0, 1)}
    candidate = 0
    while f"{candidate:06d}" in valid:
        candidate += 1
    return f"{candidate:06d}"


@pytest.fixture
def konto(staff_user):
    totp = TOTPDevice.objects.create(user=staff_user, name="totp", confirmed=True, key="a" * 40)
    codes = generate_backup_codes(staff_user)
    backup = StaticDevice.objects.get(user=staff_user, name=BACKUP_DEVICE_NAME)
    return totp, backup, codes


def _failures(device) -> int:
    device.refresh_from_db()
    return device.throttling_failure_count


def _throttle(device) -> None:
    device.throttling_failure_count = 3
    device.throttling_failure_timestamp = timezone.now()
    device.save(update_fields=["throttling_failure_count", "throttling_failure_timestamp"])


@pytest.mark.django_db
class TestBackupCodeFormNeverTouchesTheTotpDevice:
    def test_valid_backup_code_leaves_the_totp_throttle_untouched(self, staff_user, konto):
        totp, _backup, codes = konto
        assert verify_totp_or_backup(staff_user, codes[0]) is True
        assert _failures(totp) == 0
        assert totp.throttling_failure_timestamp is None

    def test_every_generated_code_takes_the_backup_path(self, staff_user, konto):
        totp, _backup, codes = konto
        for code in codes:
            assert verify_totp_or_backup(staff_user, code) is True
        assert _failures(totp) == 0

    def test_wrong_backup_code_counts_only_at_the_backup_codes(self, staff_user, konto):
        totp, backup, codes = konto
        wrong = secrets.token_urlsafe(16)
        assert wrong not in codes
        assert verify_totp_or_backup(staff_user, wrong) is False
        assert _failures(totp) == 0
        assert _failures(backup) == 1

    def test_historic_backup_code_is_rejected_without_throttling_totp(self, staff_user, konto):
        totp, backup, _codes = konto
        StaticToken.objects.create(device=backup, token=_hash_code(HISTORIC_CODE))
        assert verify_totp_or_backup(staff_user, HISTORIC_CODE) is False
        assert backup.token_set.filter(token=_hash_code(HISTORIC_CODE)).exists()
        assert _failures(totp) == 0

    def test_backup_code_still_works_while_the_totp_device_is_throttled(self, staff_user, konto):
        totp, _backup, codes = konto
        _throttle(totp)
        assert verify_totp_or_backup(staff_user, codes[0]) is True

    def test_sudo_with_backup_code_leaves_the_totp_throttle_untouched(self, client, staff_user, konto, settings):
        settings.SUDO_MODE_ENABLED = True
        totp, _backup, codes = konto
        client.force_login(staff_user)
        session = client.session
        session["mfa_verified"] = True
        session.save()

        response = client.post(SUDO_URL, {"password": PASSWORD, "otp_token": codes[0], "next": "/"})

        assert response.status_code == 302
        assert client.session["sudo_until"] > time.time()
        assert _failures(totp) == 0


@pytest.mark.django_db
class TestTotpFormNeverTouchesTheBackupCodes:
    def test_valid_totp_code_verifies(self, staff_user, konto):
        totp, backup, _codes = konto
        assert verify_totp_or_backup(staff_user, _code(totp)) is True
        assert _failures(backup) == 0

    def test_wrong_totp_code_counts_only_at_the_totp_device(self, staff_user, konto):
        totp, backup, _codes = konto
        assert verify_totp_or_backup(staff_user, _wrong_totp_code(totp)) is False
        assert _failures(totp) == 1
        assert _failures(backup) == 0


@pytest.mark.django_db
class TestBothThrottlesStillHold:
    def test_throttled_totp_device_refuses_its_valid_code(self, staff_user, konto):
        totp, _backup, _codes = konto
        _throttle(totp)
        assert verify_totp_or_backup(staff_user, _code(totp)) is False

    def test_throttled_backup_codes_refuse_a_valid_code(self, staff_user, konto):
        _totp, backup, codes = konto
        _throttle(backup)
        assert verify_totp_or_backup(staff_user, codes[0]) is False
        assert backup.token_set.filter(token=_hash_code(codes[0])).exists()

    def test_repeated_wrong_totp_codes_keep_counting(self, staff_user, konto):
        totp, _backup, _codes = konto
        wrong = _wrong_totp_code(totp)
        verify_totp_or_backup(staff_user, wrong)
        totp.refresh_from_db()
        # Die Sperre nach dem ersten Fehlversuch zaehlt nicht weiter; erst nach
        # ihrem Ablauf zaehlt der naechste Fehlversuch wieder.
        totp.throttling_failure_timestamp = timezone.now() - timedelta(seconds=5)
        totp.save(update_fields=["throttling_failure_timestamp"])
        verify_totp_or_backup(staff_user, wrong)
        assert _failures(totp) == 2


@pytest.mark.django_db
class TestInputOfNeitherFormIsRejected:
    @pytest.mark.parametrize(
        "eingabe",
        [
            "12345",  # zu kurz fuer das Geraet
            "1234567",  # zu lang fuer das Geraet
            "12_3456",  # int() naehme das an, ist aber keine Ziffernfolge
            "+123456",
            "abc",
            "!" * 22,  # Laenge eines Backup-Codes, fremdes Alphabet
            "a" * 21,
            "a" * 23,
            "ABCD-1234",  # historisches Format nur in Kleinbuchstaben gespeichert
        ],
    )
    def test_rejected_without_touching_any_device(self, staff_user, konto, eingabe):
        totp, backup, _codes = konto
        assert verify_totp_or_backup(staff_user, eingabe) is False
        assert _failures(totp) == 0
        assert _failures(backup) == 0
        assert backup.token_set.count() == 10
