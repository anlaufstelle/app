"""Single-use, server-keyed backup codes and second-factor verification."""

from __future__ import annotations

import base64
import hmac
import re
import secrets

from django.db import transaction
from django.utils.crypto import salted_hmac
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken

# TOTP-Zugriff ueber das Proxy-Modell mit at-rest verschluesseltem Secret
# (Refs #1362, ADR-031) — ``verify_token`` entschluesselt transparent.
from core.models import EncryptedTOTPDevice as TOTPDevice

BACKUP_CODES_COUNT = 10
BACKUP_DEVICE_NAME = "backup"
# The 128-bit input has 22 URL-safe characters; legacy 32-bit codes are rejected.
_CODE_BYTES = 16
_BACKUP_CODE_FORM = re.compile(r"[A-Za-z0-9_-]{22}")


def _generate_code() -> str:
    """Return a 128-bit backup code as an unpadded base64url string."""
    return secrets.token_urlsafe(_CODE_BYTES)


def _hash_code(code: str) -> str:
    """Return a purpose-separated, 96-bit HMAC fitting StaticToken's field.

    The key derives from SECRET_KEY. Changing it invalidates issued codes;
    a database digest alone is neither a credential nor an offline guessing oracle.
    """
    key = salted_hmac("core.security.backup_codes", "storage", algorithm="sha256").digest()
    return base64.urlsafe_b64encode(hmac.digest(key, code.encode("utf-8"), "sha256")).decode("ascii")[:16]


def _get_or_create_device(user) -> StaticDevice:
    device, _created = StaticDevice.objects.get_or_create(
        user=user,
        name=BACKUP_DEVICE_NAME,
        defaults={"confirmed": True},
    )
    # Legacy-Datensätze könnten unconfirmed sein — in dem Fall reparieren,
    # damit verify_token() den Device überhaupt als gültig zählt.
    if not device.confirmed:
        device.confirmed = True
        device.save(update_fields=["confirmed"])
    return device


@transaction.atomic
def generate_backup_codes(user) -> list[str]:
    """Generate a fresh set of backup codes for a user.

    Replaces any previously issued codes. Returns the codes in clear text —
    the caller must display them exactly once and not persist them anywhere
    else. Only server-keyed digests are stored in the database.
    """
    device = _get_or_create_device(user)
    device.token_set.all().delete()
    codes = [_generate_code() for _ in range(BACKUP_CODES_COUNT)]
    StaticToken.objects.bulk_create([StaticToken(device=device, token=_hash_code(code)) for code in codes])
    return codes


def remaining_backup_codes(user) -> int:
    """Number of unused backup codes for the given user (0 if none)."""
    device = StaticDevice.objects.filter(user=user, name=BACKUP_DEVICE_NAME).first()
    if device is None:
        return 0
    return device.token_set.count()


def verify_backup_code(user, token: str, *, throttle_failures: bool = True) -> bool:
    """Consume one valid-format code atomically, returning whether it matched.

    Anonymous recovery uses endpoint limits instead of increasing the victim's
    device throttle. Authenticated verification keeps the device throttle.
    """
    if not token:
        return False
    device = StaticDevice.objects.filter(user=user, name=BACKUP_DEVICE_NAME, confirmed=True).first()
    if device is None:
        return False
    # Throttle-Pruefung wie in StaticDevice.verify_token.
    verify_allowed, _ = device.verify_is_allowed()
    if not verify_allowed:
        return False

    # Atomarer Consume (Refs #2110): Der DELETE selbst entscheidet, welcher
    # parallele Aufruf gewinnt. ``generate_backup_codes`` ersetzt das Set
    # vollstaendig und ein Satz enthaelt jeden Code nur einmal; der Filter
    # trifft also hoechstens eine Zeile.
    deleted = 0
    if _has_backup_code_form(token):
        deleted, _ = device.token_set.filter(token=_hash_code(token)).delete()
    if deleted == 0:
        if throttle_failures:
            device.throttle_increment()
        return False

    device.throttle_reset(commit=False)
    device.set_last_used_timestamp(commit=False)
    # Refs #2355: nur die Spalten, die die beiden Helfer setzen. Die Instanz
    # ist nicht gesperrt gelesen; ein voller ``save()`` schriebe jede weitere
    # Spalte des Geraets mit dem Stand von vor dem Lesen zurueck.
    device.save(update_fields=["throttling_failure_timestamp", "throttling_failure_count", "last_used_at"])
    return True


def current_totp_device(user) -> TOTPDevice | None:
    """The account's TOTP device that every check point verifies: the oldest confirmed one.

    Refs #2328: sign-in, sudo (and with it the admin area) and backup-code
    regeneration all call this helper. Up to v0.99.1 they took ``.first()`` on
    an unordered queryset, i.e. the lowest primary key; accounts that ended up
    with several confirmed devices have been signing in with that device ever
    since, so it stays the one that counts -- an update must not lock them
    out. A factor change does not depend on this order: its confirmation step
    checks the code against the new, still unconfirmed device, and the
    acknowledgement of the new backup codes (``MFABackupCodesView.post``)
    confirms it and removes every other confirmed TOTP device in the same
    transaction. After a completed change the account has exactly one.

    Exactly ONE device, not all of them: django-otp advises against checking
    every device (``match_token``), because each failure then throttles every
    device -- using one device would gradually lock the other.
    """
    return TOTPDevice.objects.filter(user=user, confirmed=True).order_by("pk").first()


def _has_totp_form(token: str, device: TOTPDevice) -> bool:
    """Genau so viele ASCII-Ziffern, wie das Geraet Stellen hat."""
    return len(token) == device.digits and token.isascii() and token.isdigit()


def _has_backup_code_form(token: str) -> bool:
    return _BACKUP_CODE_FORM.fullmatch(token) is not None


def verify_totp_or_backup(user, token: str) -> bool:
    """Verifiziert einen frischen TOTP-Token ODER Backup-Code (A3.2, Refs #1024).

    Genutzt vom Sudo-Mode-Re-Auth, wenn der User 2FA aktiv hat — ein über eine
    gestohlene Session erbeutetes Passwort allein soll sensible Aktionen nicht
    freischalten.

    Refs #2328: Die Form der Eingabe waehlt den EINEN Pruefweg, bevor ein Geraet
    beruehrt wird. Ein TOTP-Code geht nur an das geltende ``TOTPDevice``
    (``current_totp_device``; ``verify_token`` ist single-use je Zeitfenster +
    gedrosselt), ein Backup-Code nur an ``verify_backup_code`` (eigene Drossel).
    Frueher ging jede Eingabe zuerst an das TOTP-Geraet: jeder Backup-Code
    zaehlte dort als Fehlversuch, und wer Sudo mit Backup-Codes bestaetigte,
    sperrte sich schrittweise den naechsten gueltigen TOTP-Code. Eine Eingabe
    ohne eine der beiden Formen kann kein Weg annehmen; sie wird abgewiesen,
    ohne ein Geraet zu beruehren.
    """
    token = (token or "").strip().replace(" ", "")
    if not token:
        return False
    if _has_backup_code_form(token):
        return verify_backup_code(user, token)
    device = current_totp_device(user)
    if device is not None and _has_totp_form(token, device):
        return device.verify_token(token)
    return False
