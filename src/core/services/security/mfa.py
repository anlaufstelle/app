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

Klartext-Altbestand (Refs #2294, #2327): Codes aus der Zeit vor der
Hash-Speicherung lagen im historischen ``xxxx-xxxx``-Format im Klartext in der
Token-Spalte. Die Datenmigration ``0181_hash_cleartext_backup_codes`` hat diesen
Restbestand durch die Hashes ersetzt; ein alter Code verifiziert seither ueber
denselben Hash-Kandidaten wie ein neuer. Einen Klartext-Vergleich gibt es
nicht mehr — weder fuer den Eingabewert noch fuer einen aus der Datenbank
gelesenen Token-Wert. Speicher- und Migrationsvertrag stehen in
``docs/security-notes.md``.
"""

from __future__ import annotations

import hashlib
import secrets

from django.db import transaction
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken

# TOTP-Zugriff ueber das Proxy-Modell mit at-rest verschluesseltem Secret
# (Refs #1362, ADR-031) — ``verify_token`` entschluesselt transparent.
from core.models import EncryptedTOTPDevice as TOTPDevice

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

    Refs #790: der Eingabewert wird ausschliesslich ueber seinen Hash
    gesucht, in EINER DB-Query — ein Fehlversuch kostet genau einen
    Throttle-Increment. Single-use bleibt erhalten, indem der Treffer direkt
    geloescht wird.

    Refs #2294, #2327: Es gibt keinen Klartext-Vergleich. Der Altbestand im
    ``xxxx-xxxx``-Format ist durch die Datenmigration ``0181`` gehasht; eine
    Klartext-Zeile, die am Migrationsweg vorbei in der Tabelle liegt, ist kein
    gueltiger Code, und ein aus der Datenbank gelesener Token-Wert faellt als
    Eingabe ebenfalls durch (sein Hash steht nirgends).
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
    deleted, _ = device.token_set.filter(token=_hash_code(token)).delete()
    if deleted == 0:
        device.throttle_increment()
        return False

    device.throttle_reset(commit=False)
    device.set_last_used_timestamp(commit=False)
    # Refs #2355: nur die Spalten, die die beiden Helfer setzen. Die Instanz
    # ist nicht gesperrt gelesen; ein voller ``save()`` schriebe jede weitere
    # Spalte des Geraets mit dem Stand von vor dem Lesen zurueck.
    device.save(update_fields=["throttling_failure_timestamp", "throttling_failure_count", "last_used_at"])
    return True


def verify_totp_or_backup(user, token: str) -> bool:
    """Verifiziert einen frischen TOTP-Token ODER Backup-Code (A3.2, Refs #1024).

    Reihenfolge: erst das bestätigte ``TOTPDevice`` (``verify_token`` ist
    single-use je Zeitfenster + gedrosselt), dann Backup-Code. Genutzt vom
    Sudo-Mode-Re-Auth, wenn der User 2FA aktiv hat — ein über eine gestohlene
    Session erbeutetes Passwort allein soll sensible Aktionen nicht freischalten.
    """
    token = (token or "").strip().replace(" ", "")
    if not token:
        return False
    device = TOTPDevice.objects.filter(user=user, confirmed=True).first()
    if device is not None and device.verify_token(token):
        return True
    return verify_backup_code(user, token)
