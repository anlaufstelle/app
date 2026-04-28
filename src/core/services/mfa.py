"""2FA-Recovery via Backup-Codes (Refs #588).

Nutzt django-otps Standard-`StaticDevice`/`StaticToken`:
`StaticDevice.verify_token(token)` löscht den Token nach erfolgreicher
Verifikation automatisch — dadurch ist jeder Code einmalig. Wir legen pro
User genau ein `StaticDevice` an (Name "backup"); Regenerieren = Device
neu befüllen, nicht mehrere Devices erzeugen.
"""

from __future__ import annotations

import secrets

from django.db import transaction
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken

BACKUP_CODES_COUNT = 10
BACKUP_DEVICE_NAME = "backup"


def _generate_code() -> str:
    # Format „xxxx-xxxx": 8 Hex-Zeichen (32 Bit Entropie) mit Separator.
    # Pro Hälfte 2 Bytes = 4 Hex-Zeichen.
    return f"{secrets.token_hex(2)}-{secrets.token_hex(2)}"


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
    else.
    """
    device = _get_or_create_device(user)
    device.token_set.all().delete()
    codes = [_generate_code() for _ in range(BACKUP_CODES_COUNT)]
    StaticToken.objects.bulk_create([StaticToken(device=device, token=code) for code in codes])
    return codes


def remaining_backup_codes(user) -> int:
    """Number of unused backup codes for the given user (0 if none)."""
    device = StaticDevice.objects.filter(user=user, name=BACKUP_DEVICE_NAME).first()
    if device is None:
        return 0
    return device.token_set.count()


def verify_backup_code(user, token: str) -> bool:
    """Consume a backup code. Returns True on success.

    django-otp removes the matching `StaticToken` inside
    `StaticDevice.verify_token`, which guarantees single-use.
    """
    if not token:
        return False
    device = StaticDevice.objects.filter(user=user, name=BACKUP_DEVICE_NAME, confirmed=True).first()
    if device is None:
        return False
    return bool(device.verify_token(token))
