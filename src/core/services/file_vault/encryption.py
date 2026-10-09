"""Fernet encryption (AES-128) for sensitive data fields.

Supports MultiFernet for key rotation: multiple keys can be configured
(ENCRYPTION_KEYS, comma-separated). The first key is used for encryption,
all keys are tried for decryption.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import struct
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.test.signals import setting_changed

from core.services.file_vault.keyring import split_key_ring

if TYPE_CHECKING:
    # Nur fuer Typannotationen (PEP 563 macht sie ohnehin lazy) — kein
    # Laufzeit-Import noetig. Analog ``core/services/conditions.py``: hier
    # zusaetzlich, um keinen harten Modul-Level-Import von ``core.models``
    # in dieses tief verlinkte Krypto-Modul zu ziehen.
    from core.models import DocumentType

logger = logging.getLogger(__name__)


class EncryptionError(Exception):
    """General encryption error."""


class EncryptionKeyMissing(EncryptionError):  # noqa: N818 — historischer Name, oeffentlich exportiert
    """ENCRYPTION_KEY is not configured."""


def _get_keys():
    """Return all configured Fernet keys as a list.

    Checks ENCRYPTION_KEYS (comma-separated) first, then falls back to
    ENCRYPTION_KEY (single key) for backward compatibility.
    """
    keys = split_key_ring(getattr(settings, "ENCRYPTION_KEYS", ""))
    if keys:
        return keys

    single_key = str(getattr(settings, "ENCRYPTION_KEY", "") or "").strip()
    if single_key:
        return [single_key]

    return []


@lru_cache(maxsize=1)
def get_fernet():
    """Create a MultiFernet instance from all configured keys.

    ``lru_cache(maxsize=1)`` hält eine Instanz pro Prozess — die Keys
    stammen aus Django-Settings und ändern sich nicht zur Laufzeit.
    Bei 200-Element-Loops (z.B. AttachmentListView mit ``safe_decrypt``
    pro Row, Refs #644) spart das die wiederholte Fernet-Instantiierung
    und Key-Normalisierung.

    Cache wird bei Test-``override_settings`` auf ``ENCRYPTION_KEY(S)``
    über den ``setting_changed``-Signal-Receiver invalidiert.
    """
    keys = _get_keys()
    if not keys:
        raise EncryptionKeyMissing(
            "ENCRYPTION_KEY / ENCRYPTION_KEYS ist nicht gesetzt. Generiere einen Key mit generate_key()."
        )
    fernets = [Fernet(k.encode() if isinstance(k, str) else k) for k in keys]
    return MultiFernet(fernets)


def _reset_fernet_cache(**kwargs):
    """Invalidate get_fernet()-Cache wenn Keys per override_settings geändert werden."""
    if kwargs.get("setting") in ("ENCRYPTION_KEY", "ENCRYPTION_KEYS"):
        get_fernet.cache_clear()


setting_changed.connect(_reset_fernet_cache)


# The non-UTF-8 magic cannot collide with legacy UTF-8 text tokens (Refs #2520).
# Version and type are inside the Fernet payload and therefore authenticated.
_FIELD_FORMAT_MAGIC = b"\x89ANLAUFSTELLE-FIELD\x00"
_LIST_FIELD_V1_PREFIX = _FIELD_FORMAT_MAGIC + b"1:list\x00"


def encrypt_field(value):
    """Encrypt text as before and non-empty lists as a versioned typed payload."""
    if not value:
        return value
    try:
        f = get_fernet()
        if isinstance(value, list):
            plaintext = _LIST_FIELD_V1_PREFIX + json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        else:
            plaintext = str(value).encode("utf-8")
        token = f.encrypt(plaintext).decode("utf-8")
        return {"__encrypted__": True, "value": token}
    except EncryptionKeyMissing as exc:
        raise EncryptionError(
            "Cannot encrypt field: no encryption key configured. Set ENCRYPTION_KEY in environment."
        ) from exc
    except Exception as exc:
        raise EncryptionError(f"Encryption failed: {exc}") from exc


def decrypt_field(value):
    """Restore typed lists; legacy UTF-8 tokens always remain text."""
    if not is_encrypted_value(value):
        return value
    try:
        f = get_fernet()
        plaintext = f.decrypt(value["value"].encode("utf-8"))
        if not plaintext.startswith(_FIELD_FORMAT_MAGIC):
            return plaintext.decode("utf-8")
        if not plaintext.startswith(_LIST_FIELD_V1_PREFIX):
            raise EncryptionError("Unsupported encrypted field format")
        try:
            decoded = json.loads(plaintext[len(_LIST_FIELD_V1_PREFIX) :].decode("utf-8"))
        except ValueError as exc:
            raise EncryptionError("Invalid encrypted list payload") from exc
        if not isinstance(decoded, list):
            raise EncryptionError("Encrypted list payload must contain a list")
        return decoded
    except EncryptionKeyMissing:
        raise
    except InvalidToken as exc:
        # A4.6 (Refs #1024 / #1016): nur InvalidToken (kaputtes/fremdes
        # Ciphertext) als EncryptionError verpacken. EncryptionKeyMissing
        # propagiert oben; alle anderen (unerwarteten) Fehler bewusst NICHT
        # fangen, damit echte Bugs nicht im safe_decrypt-Fallback verschwinden.
        raise EncryptionError(f"Decryption failed: {exc}") from exc


def is_encrypted_value(value):
    """Check whether a value has the encryption marker format."""
    return isinstance(value, dict) and value.get("__encrypted__") is True and "value" in value


def safe_decrypt(value, default="[verschlüsselt]"):
    """Decrypt with fallback on error.

    Refs #1269 (T4): ein fehlender/fehlkonfigurierter Schlüssel
    (``EncryptionKeyMissing``) wird NICHT als ``default``-Platzhalter maskiert,
    sondern laut gemeldet (Error-Log) und durchgereicht — sonst bleibt eine
    Key-Fehlkonfiguration auf Lesepfaden unsichtbar (der Boot-Time-Hard-Fail
    greift nur bei komplett fehlendem Key, nicht z.B. bei einem aus der Rotation
    gefallenen Key). Nur echte Token-Korruption (``InvalidToken`` → generischer
    ``EncryptionError``) fällt weiterhin graceful auf den Platzhalter zurück.
    Da ``EncryptionKeyMissing`` von ``EncryptionError`` erbt, muss es ZUERST
    gefangen werden.
    """
    if not is_encrypted_value(value):
        return value
    try:
        return decrypt_field(value)
    except EncryptionKeyMissing:
        logger.error(
            "safe_decrypt: Entschlüsselung unmöglich — ENCRYPTION_KEY/ENCRYPTION_KEYS "
            "fehlt oder ist fehlkonfiguriert. Fehler wird durchgereicht statt maskiert."
        )
        raise
    except EncryptionError:
        logger.warning("Decryption failed (corrupt token) — using fallback placeholder.")
        return default


def encrypt_dynamic_data(document_type: DocumentType, data_json: dict[str, Any] | None) -> dict[str, Any] | None:
    """Encrypt sensitive fields for bulk_create scenarios where save() is bypassed.

    Entity-agnostisch trotz des Parameternamens ``document_type``: die
    Funktion braucht nur ein Objekt mit ``.fields.filter(field_template__is_encrypted=True)``
    (aktuell ``DocumentType``, kuenftig z. B. ``Client``/``Case``-Aequivalente).

    File-Marker (``__file__``/``__files__``) werden uebersprungen — FILE-Felder
    sind immer ``is_encrypted=True`` (``FieldTemplate.save()``), ihr Marker-Dict
    ist aber Metadaten, kein Klartext (analog
    ``DynamicDataEncryptionMixin._encrypt_sensitive_fields``, die diesen Skip
    im Save-Pfad schon immer hatte — der Bulk-Pfad hier holt das nach).
    """
    from core.services.events.fields import _is_file_marker

    if not data_json:
        return data_json
    encrypted_slugs = set(
        document_type.fields.filter(
            field_template__is_encrypted=True,
        ).values_list("field_template__slug", flat=True)
    )
    result = data_json.copy()
    for key in encrypted_slugs:
        value = result.get(key)
        if value and not is_encrypted_value(value) and not _is_file_marker(value):
            result[key] = encrypt_field(value)
    return result


def generate_key():
    """Generate a new Fernet key (Base64-encoded)."""
    return Fernet.generate_key().decode("utf-8")


# --- Binary file encryption (chunk-based) ---

# v1: [1B ver=1][4B chunk_count][per chunk: 4B len + Fernet(data)] — keine
#     Positions-/Datei-Bindung (Bestandsformat, weiter lesbar).
# v2 (A4.5, Refs #1016): wie v1, aber jeder Chunk-Klartext ist mit einem
#     24-Byte-Kontext praefixiert (16B Datei-Bindung + 4B index + 4B total),
#     den Fernet mit-authentifiziert. Da Fernet kein AEAD-AAD bietet, ist das
#     der Weg, Reorder/Truncation/Cross-File-Splicing durch einen Angreifer mit
#     Disk-Schreibzugriff erkennbar zu machen. Aktiviert via ``file_id``.
_FILE_FORMAT_V1 = 1
_FILE_FORMAT_V2 = 2
FILE_FORMAT_VERSION = _FILE_FORMAT_V1  # Default-Schreibversion ohne file_id (Legacy)
CHUNK_SIZE = 64 * 1024  # 64 KB per chunk
_FID_LEN = 16  # 16B Datei-Bindung (SHA256(file_id)[:16])
_CTX_LEN = 24  # 16B Datei-Bindung + 4B chunk_index + 4B total

# --- Grenzen fuer die unauthentifizierten Header-Laengenfelder (Refs #1968) ---
# Der Datei-Header ([1B Version][4B chunk_count]) und die Chunk-Laengenfelder
# ([4B token_len]) liegen ausserhalb jedes Fernet-Tokens; wer Schreibzugriff auf
# das Storage hat (manipulierter Restore, kompromittiertes Volume), setzt sie
# frei. Ungeprueft bricht das die im Modul zugesagte Peak-RAM-Invariante
# O(CHUNK) (siehe ``encrypt_file`` und ``system.facility_export``): ein
# ``token_len`` von 2**32-1 laesst ``inp.read()`` 4 GiB anfordern.
_HEADER_LEN = 5  # 1B Version + 4B chunk_count
_CHUNK_RECORD_MIN_BYTES = 4  # jedes Chunk-Record traegt mindestens sein Laengenfeld
# Fernet-Token-Layout: [1B Version][8B Timestamp][16B IV][AES-CBC-Ciphertext]
# [32B HMAC-SHA256], base64url-kodiert. Der Ciphertext ist PKCS7-gepolstert,
# also hoechstens Klartextlaenge + 16.
_FERNET_RAW_OVERHEAD = 1 + 8 + 16 + 32 + 16

#: Obergrenze fuer ein ``token_len``-Feld. Groesster legitim geschriebener
#: Klartext pro Chunk ist ``CHUNK_SIZE`` (v1) bzw. ``CHUNK_SIZE + _CTX_LEN``
#: (v2, inklusive Bindungskontext). Bewusst aus ``CHUNK_SIZE`` abgeleitet und
#: NICHT aus ``FILE_VAULT_MAX_UPLOAD_BYTES``: das Upload-Limit ist
#: betreiberkonfigurierbar und kann historisch groesser gewesen sein — ein daran
#: gekoppelter Deckel sperrte Bestandsanhaenge aus. ``CHUNK_SIZE`` ist seit dem
#: Einfuehrungscommit des File-Vaults unveraendert.
MAX_CHUNK_TOKEN_BYTES = 4 * ((CHUNK_SIZE + _CTX_LEN + _FERNET_RAW_OVERHEAD + 2) // 3)


def _file_binding(file_id: str) -> bytes:
    """Stabile 16-Byte-Bindung aus der Storage-ID der Datei."""
    return hashlib.sha256(file_id.encode("utf-8")).digest()[:16]


def _chunk_context(fid16: bytes, index: int, total: int) -> bytes:
    return fid16 + struct.pack(">II", index, total)


def encrypt_file(input_stream, output_path, file_id=None):
    """Encrypt a file stream in chunks, writing to output_path.

    ``file_id is None`` → Legacy-v1 (keine Positions-/Datei-Bindung).
    ``file_id`` gesetzt → v2: bindet jeden Chunk an ``file_id`` + Index + Anzahl
    (A4.5). Produktion (``storage.py``) reicht die Storage-ID durch.

    Refs #1268: Seekbare Streams (Uploads, ``BytesIO``) werden in ZWEI Pässen
    verarbeitet — Pass 1 zählt nur die Chunks (hält keinen Klartext), Pass 2
    verschlüsselt + schreibt chunkweise auf die Disk. Peak-RAM ist damit
    O(CHUNK) statt O(Dateigröße). ``total`` muss vor den Chunks in den Header
    (und in den v2-Kontext), daher der Zähl-Pass. Nicht-seekbare Streams fallen
    auf die bisherige Voll-Pufferung zurück (Peak ~1x, in-place).
    """
    from pathlib import Path

    f = get_fernet()
    version = _FILE_FORMAT_V1 if file_id is None else _FILE_FORMAT_V2
    fid16 = _file_binding(file_id) if file_id is not None else None

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def _encrypt_chunk(index, total, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        if file_id is None:
            return f.encrypt(data)
        return f.encrypt(_chunk_context(fid16, index, total) + data)

    def _write_token(out, token):
        token_bytes = token if isinstance(token, bytes) else token.encode("utf-8")
        out.write(struct.pack(">I", len(token_bytes)))
        out.write(token_bytes)

    seekable = hasattr(input_stream, "seek") and (not hasattr(input_stream, "seekable") or input_stream.seekable())

    if seekable:
        try:
            start = input_stream.tell()
        except (OSError, ValueError):
            start = 0
        # Pass 1: Chunks zaehlen, ohne Klartext zu halten (Reads deterministisch
        # auf seekbaren File-/BytesIO-Streams → identische Chunk-Grenzen in Pass 2).
        total = 0
        while input_stream.read(CHUNK_SIZE):
            total += 1
        input_stream.seek(start)
        # Pass 2: chunkweise verschluesseln + schreiben (Peak-RAM O(CHUNK)).
        with open(output_path, "wb") as out:
            out.write(struct.pack(">B", version))
            out.write(struct.pack(">I", total))
            for index in range(total):
                data = input_stream.read(CHUNK_SIZE)
                _write_token(out, _encrypt_chunk(index, total, data))
        return

    # Fallback (nicht-seekbarer Stream): puffern wie bisher. In-place ersetzt
    # jeden Klartext-Slot sofort durch sein Token (Peak ~1x statt ~2x; PR-Review
    # bug_001).
    raw_chunks = []
    while True:
        data = input_stream.read(CHUNK_SIZE)
        if not data:
            break
        if isinstance(data, str):
            data = data.encode("utf-8")
        raw_chunks.append(data)
    total = len(raw_chunks)
    for i in range(total):
        raw_chunks[i] = _encrypt_chunk(i, total, raw_chunks[i])
    with open(output_path, "wb") as out:
        out.write(struct.pack(">B", version))
        out.write(struct.pack(">I", total))
        for token in raw_chunks:
            _write_token(out, token)


def decrypt_file_stream(input_path, file_id=None):
    """Generator that yields decrypted chunks from an encrypted file.

    Reads chunk-by-chunk to avoid loading the entire file into memory. Bei
    v2-Dateien (A4.5) ist ``file_id`` erforderlich; jeder Chunk wird gegen
    seine erwartete Position + Datei-Bindung geprueft (Reorder/Truncation/
    Splicing werfen ``EncryptionError``).

    Alle Header-Laengenfelder werden plausibilisiert (Refs #1968), bevor sie
    eine Leseanforderung oder eine Schleifengrenze werden — sie sind nicht
    Teil eines Fernet-Tokens und damit angreifer-kontrolliert. Referenz ist
    die tatsaechliche Dateigroesse (``os.fstat`` auf dem offenen Deskriptor,
    also TOCTOU-frei) plus ``MAX_CHUNK_TOKEN_BYTES``. Jeder Parserfehler
    verlaesst die Funktion als ``EncryptionError`` — insbesondere ein hinter
    dem Versionsbyte abgeschnittener Chunk-Zaehler, der frueher ``struct.error``
    an den Aufrufern vorbeischleuste.
    """
    f = get_fernet()

    with open(input_path, "rb") as inp:
        version_bytes = inp.read(1)
        if not version_bytes:
            raise EncryptionError("Empty encrypted file")
        (version,) = struct.unpack(">B", version_bytes)
        if version not in (_FILE_FORMAT_V1, _FILE_FORMAT_V2):
            raise EncryptionError(f"Unsupported file format version: {version}")

        count_bytes = inp.read(4)
        if len(count_bytes) < 4:
            raise EncryptionError("Truncated encrypted file — incomplete chunk count field")
        (chunk_count,) = struct.unpack(">I", count_bytes)

        # ``chunk_count`` gegen die ECHTE Dateigroesse plausibilisieren, nicht
        # gegen ``FILE_VAULT_MAX_UPLOAD_BYTES``: das Upload-Limit ist
        # betreiberkonfigurierbar und kann historisch groesser gewesen sein —
        # ein daran gekoppelter Deckel machte Bestandsanhaenge (Art.-9-Daten)
        # unlesbar. Jedes Chunk-Record belegt mindestens sein 4B-Laengenfeld,
        # also ist ``(Dateigroesse - Header) // 4`` eine Schranke, die eine
        # echte Datei niemals reissen kann.
        file_size = os.fstat(inp.fileno()).st_size
        max_chunks = max(0, (file_size - _HEADER_LEN) // _CHUNK_RECORD_MIN_BYTES)
        if chunk_count > max_chunks:
            raise EncryptionError(
                f"Implausible chunk count in header: {chunk_count} exceeds "
                f"{max_chunks} possible chunks in {file_size} bytes"
            )

        # ``file_id`` (sofern uebergeben) erlaubt die v2-Bindungspruefung UND die
        # Erkennung eines auf v1 herabgestuften, eigentlich gebundenen Headers.
        # Der 1B-Header (Version + Anzahl) liegt ausserhalb jedes Fernet-Tokens
        # und ist daher selbst nicht authentifiziert — PR-Review bug_002.
        fid16 = _file_binding(file_id) if file_id is not None else None
        if version == _FILE_FORMAT_V2 and file_id is None:
            raise EncryptionError("file_id required to decrypt a v2 (bound) file")
        if file_id is not None and chunk_count == 0:
            # Produktion schreibt nie ein leeres gebundenes File (enforce_magic_bytes
            # lehnt leere Uploads ab) → [count=0] ist ein Blanking-/Truncation-Versuch.
            # Versionsunabhaengig, weil der Header nicht authentifiziert ist: ein auf
            # [v1][count=0] gefaelschter Header wuerde sonst die per-Chunk-Downgrade-
            # Erkennung umgehen (Schleife laeuft 0x). Refs #1069.
            raise EncryptionError("Chunk binding mismatch — empty bound file (possible truncation/blanking)")

        for index in range(chunk_count):
            len_bytes = inp.read(4)
            if len(len_bytes) < 4:
                raise EncryptionError("Truncated encrypted file")
            (token_len,) = struct.unpack(">I", len_bytes)
            if token_len > MAX_CHUNK_TOKEN_BYTES:
                # Vor dem ``read``: sonst haengt die im Modul zugesagte
                # Peak-RAM-Invariante O(CHUNK) an einem angreifer-gesetzten
                # 32-Bit-Feld.
                raise EncryptionError(
                    f"Implausible chunk token length in header: {token_len} exceeds {MAX_CHUNK_TOKEN_BYTES}"
                )
            token = inp.read(token_len)
            if len(token) < token_len:
                raise EncryptionError("Truncated encrypted file")
            try:
                plaintext = f.decrypt(token)
            except InvalidToken as exc:
                raise EncryptionError(f"Chunk decryption failed: {exc}") from exc

            if version == _FILE_FORMAT_V1:
                # Downgrade-Erkennung: ein als v2 gebundener Chunk, dessen
                # Header-Byte auf v1 gefaelscht wurde, beginnt mit genau seiner
                # 16-Byte-Datei-Bindung. Echte v1-Daten treffen das nicht (und ein
                # Angreifer kann es ohne Schluessel nicht erzeugen).
                #
                # R12 (Refs #1476/#1666): NUR gegen die count-/index-invariante
                # Datei-Bindung ``fid16`` pruefen — NICHT gegen den vollen
                # 24-Byte-Kontext ``_chunk_context(fid16, index, chunk_count)``.
                # Der Header (Version + ``chunk_count``) liegt ausserhalb jedes
                # Fernet-Tokens und ist damit angreifer-kontrolliert. Wer v2->v1
                # herabstuft UND den ``chunk_count`` umschreibt, liesse den
                # rekonstruierten Kontext (mit gefaelschtem ``total``) vom im
                # Token gebundenen (originales ``total``) abweichen und umginge
                # so die Downgrade-/Truncation-Erkennung fail-open. Die
                # 16-Byte-Bindung ist von ``index``/``chunk_count`` unabhaengig
                # und schlaegt daher auch bei umgeschriebenem Header/Reorder an.
                if fid16 is not None and plaintext[:_FID_LEN] == fid16:
                    raise EncryptionError("Chunk binding mismatch — v1 header on bound chunk (possible downgrade)")
                yield plaintext
                continue

            # v2: der authentifizierte Kontext bindet den Chunk an (Datei, index, total).
            ctx, chunk_data = plaintext[:_CTX_LEN], plaintext[_CTX_LEN:]
            if ctx != _chunk_context(fid16, index, chunk_count):
                raise EncryptionError("Chunk binding mismatch — possible reorder/truncation/splicing")
            yield chunk_data
