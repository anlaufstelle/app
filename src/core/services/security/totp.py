"""At-rest-Verschluesselung des TOTP-Secrets (Refs #1362).

django-otp legt das TOTP-Secret (``TOTPDevice.key``) als **Klartext**-Hex ab
(40 Hex-Zeichen fuer den Default-20-Byte-Key). Damit ist ein DB-/Backup-Leser
in der Lage, jeden Authenticator zu rekonstruieren und gueltige TOTP-Codes zu
erzeugen — auch fuer die MFA-pflichtigen Rollen ``super_admin``/
``facility_admin``. Das widerspricht der #790-Haertung (Backup-Codes nur
gehasht, PII Fernet-verschluesselt).

Dieses Modul kapselt die Ver-/Entschluesselung des Secrets mit dem
**vorhandenen** MultiFernet-Setup (:func:`core.services.file_vault.get_fernet`,
Rotation ueber ``ENCRYPTION_KEYS``). Es ist die **einzige** Quelle der
Format-Erkennung — Modell (:class:`core.models.mfa.EncryptedTOTPDevice`) und
Datenmigration teilen sich diese Helfer, damit ein von der Migration erzeugtes
Token vom Modell exakt gleich erkannt wird.

Format-Unterscheidung (idempotent):

* **Klartext** — reiner Hex-String gerader Laenge (django-otp ``key_validator``
  ist ein ``hex_validator``; ``default_key`` = ``random_hex(20)``).
* **Verschluesselt** — Fernet-Token (URL-safe Base64, beginnt mit ``gA``);
  enthaelt zwangslaeufig Nicht-Hex-Zeichen und ist damit nie ``_is_plain_hex``.
"""

from __future__ import annotations

import re

from cryptography.fernet import InvalidToken

from core.services.file_vault import EncryptionError, get_fernet

_HEX_RE = re.compile(r"\A[0-9a-fA-F]+\Z")


def _is_plain_hex(value: str) -> bool:
    """True, wenn ``value`` ein reiner Hex-String gerader Laenge ist (Klartext-Key)."""
    return bool(value) and len(value) % 2 == 0 and bool(_HEX_RE.match(value))


def is_encrypted_totp_key(value: str) -> bool:
    """True, wenn ``value`` bereits ein Fernet-Token ist (nicht Klartext-Hex).

    Idempotenz-Anker fuer Migration und Modell: ein reiner Hex-String ist
    Klartext (``False``), alles andere gilt als bereits verschluesselt
    (``True``). Ein leerer Wert ist kein Token → ``False``.
    """
    if not value:
        return False
    return not _is_plain_hex(value)


def encrypt_totp_key(plain_hex: str) -> str:
    """Verschluesselt einen Klartext-Hex-Key zu einem Fernet-Token (String)."""
    return get_fernet().encrypt(plain_hex.encode("ascii")).decode("ascii")


def decrypt_totp_key(value: str) -> str:
    """Liefert den Klartext-Hex-Key zurueck.

    Akzeptiert bewusst **auch** einen bereits im Klartext vorliegenden
    (unmigrierten/Legacy-)Key und gibt ihn unveraendert zurueck — so
    funktioniert der Verify-Pfad sowohl vor als auch nach der Datenmigration.
    Nur ein tatsaechliches Fernet-Token wird entschluesselt; MultiFernet
    probiert dabei alle konfigurierten Keys (Rotation).
    """
    if not value:
        return value
    if _is_plain_hex(value):
        return value
    try:
        return get_fernet().decrypt(value.encode("ascii")).decode("ascii")
    except InvalidToken as exc:
        raise EncryptionError(f"TOTP-Key-Entschluesselung fehlgeschlagen: {exc}") from exc
