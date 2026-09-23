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

Backwards-Kompat (Refs #2294): Es gibt **keine** Migration, die bestehende
Token-Zeilen gehasht haette — Konten, die ihre Codes vor der Umstellung auf
Hash-Speicherung erhalten und seither nicht neu erzeugt haben, tragen also
weiterhin Klartext-Zeilen im ``xxxx-xxxx``-Format. Der Fallback dafuer bleibt,
greift aber nur nach **expliziter Formatpruefung** gegen ``_LEGACY_CODE_RE``
(genau das historische Erzeugungsformat: zweimal vier Kleinbuchstaben-Hex mit
Bindestrich). Zuvor wurde der Eingabewert unveraendert mitgesucht; damit galt
ein aus der Datenbank gelesener Token-Wert selbst als gueltiges Geheimnis und
der zugesagte Schutz der gehashten Speicherung war aufgehoben. Ein
hashfoermiger Eingabewert wird nie direkt verglichen. Beim naechsten
``Codes neu erzeugen``-Lauf werden Klartext-Zeilen durch gehashte
128-Bit-Codes ersetzt; Uebergangsfrist und Abschaltung stehen in
``docs/security-notes.md``.
"""

from __future__ import annotations

import hashlib
import re
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
# Refs #2294: EXAKT das historische Erzeugungsformat aus der Einfuehrung der
# Backup-Codes — ``f"{secrets.token_hex(2)}-{secrets.token_hex(2)}"``, also
# zweimal vier Kleinbuchstaben-Hex mit Bindestrich. Nur ein Eingabewert dieser
# Form darf ueberhaupt gegen die Klartext-Zeilen der Alt-Bestaende geprueft
# werden. Ein gespeicherter Hash (16 Hex-Zeichen, ohne Bindestrich) erfuellt
# diese Form nicht und ist damit kein Kandidat fuer den Direktvergleich.
_LEGACY_CODE_RE = re.compile(r"^[0-9a-f]{4}-[0-9a-f]{4}$")


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

    Refs #2294: Der Cleartext-Lookup gilt ausschliesslich fuer Eingaben im
    historischen ``xxxx-xxxx``-Format (``_LEGACY_CODE_RE``). Jeder andere
    Eingabewert — insbesondere ein aus der Datenbank gelesener Token-Wert —
    wird nur noch ueber seinen Hash gesucht und faellt damit durch.
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
    candidates = [hashed]
    if _LEGACY_CODE_RE.fullmatch(token):
        # Alt-Bestand ohne Hash-Migration (Refs #2294): nur eine Eingabe im
        # historischen Erzeugungsformat darf ueberhaupt direkt verglichen werden.
        candidates.append(token)
    # Atomarer Consume (Refs #2110): Der DELETE selbst entscheidet, welcher
    # parallele Aufruf gewinnt. ``generate_backup_codes`` ersetzt das Set
    # vollstaendig, daher mischt ein Device nie Hash- und Legacy-Cleartext-
    # Zeilen desselben Codes; der Filter kann hoechstens einen Treffer loeschen.
    deleted, _ = device.token_set.filter(token__in=candidates).delete()
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
