"""2FA-Recovery via Backup-Codes (Refs #588).

Nutzt django-otps Standard-`StaticDevice`/`StaticToken`:
`StaticDevice.verify_token(token)` löscht den Token nach erfolgreicher
Verifikation automatisch — dadurch ist jeder Code einmalig. Wir legen pro
User genau ein `StaticDevice` an (Name "backup"); Regenerieren = Device
neu befüllen, nicht mehrere Devices erzeugen.

Refs #790 (C-22): Backup-Codes haben jetzt 128-Bit Entropie
(``secrets.token_urlsafe(16)``) statt 32 Bit, und die DB speichert nur
SHA-256-Hashes der Codes. Damit ist ein DB-Leak nicht mehr aequivalent
zur Kompromittierung der Backup-Codes (Pre-Image-Angriffe gegen 128-Bit-
Eingaben sind infeasibel).

Backwards-Kompat: alte 32-Bit-Codes im ``xxxx-xxxx``-Format bleiben in der
DB und werden weiterhin verifiziert (Cleartext-Match-Fallback). Beim
naechsten ``Codes neu erzeugen``-Lauf werden sie durch gehashte 128-Bit-
Codes ersetzt.
"""

from __future__ import annotations

import hashlib
import secrets

from django.db import transaction
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken

BACKUP_CODES_COUNT = 10
BACKUP_DEVICE_NAME = "backup"
# Refs #790: token_urlsafe(16) liefert 22 Zeichen, 128 Bit Entropie. Format
# bleibt unsegmentiert (kein xxxx-xxxx) — neue Codes sind "Geheimnis-Strings"
# zum Copy-Paste. Wer Lesbarkeit will, kann beim Display-Layer formatieren.
_CODE_BYTES = 16


def _generate_code() -> str:
    """Liefert einen 128-Bit Backup-Code als URL-safe Base64-String (22 Zeichen)."""
    return secrets.token_urlsafe(_CODE_BYTES)


def _hash_code(code: str) -> str:
    """SHA-256-Hex-Digest des Codes (truncated auf 16 Hex-Zeichen).

    ``StaticToken.token`` aus django-otp ist ein CharField(max_length=16).
    Wir koennten die Spalte per Migration eines Drittanbieter-Modells
    vergroessern, das ist aber ein Wartungs-Risiko bei django-otp-Updates.
    Truncation auf 16 Hex-Zeichen liefert 64-Bit Image-Entropie — bei
    128-Bit-Eingabe ist das fuer Pre-Image-Angriffe weiterhin 2^64
    Versuche, also infeasibel. Kein Salt noetig (Input hat selbst 128 Bit).
    """
    return hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]


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
    else. In der Datenbank wird nur der SHA-256-Hash gespeichert (Refs #790).
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


def verify_backup_code(user, token: str) -> bool:
    """Consume a backup code. Returns True on success.

    Refs #790: prueft den Input gegen Hash- UND Cleartext-Lookup in EINER
    DB-Query — damit ein einzelner Throttle-Increment greift, statt zweier
    aufeinanderfolgender ``verify_token``-Aufrufe (die unter
    ThrottlingMixin nach 1 Miss bereits 1s Delay verlangen). Single-use
    bleibt erhalten, indem der Treffer direkt geloescht wird.
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

    hashed = _hash_code(token)
    # Match in EINER Query — gegen Hash (neue Codes ab #790) ODER Cleartext (Legacy).
    match = device.token_set.filter(token__in=[hashed, token]).first()
    if match is None:
        device.throttle_increment()
        return False

    match.delete()
    device.throttle_reset(commit=False)
    device.set_last_used_timestamp(commit=False)
    device.save()
    return True
