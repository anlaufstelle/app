"""Fernet encryption (AES-128) for sensitive data fields.

Supports MultiFernet for key rotation: multiple keys can be configured
(ENCRYPTION_KEYS, comma-separated). The first key is used for encryption,
all keys are tried for decryption.
"""

import hashlib
import logging
import struct
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.test.signals import setting_changed

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
    keys_str = getattr(settings, "ENCRYPTION_KEYS", "")
    if keys_str:
        return [k.strip() for k in keys_str.split(",") if k.strip()]

    single_key = getattr(settings, "ENCRYPTION_KEY", "")
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


def encrypt_field(value):
    """Encrypt plaintext and return a dict with a marker."""
    if not value:
        return value
    try:
        f = get_fernet()
        token = f.encrypt(str(value).encode("utf-8")).decode("utf-8")
        return {"__encrypted__": True, "value": token}
    except EncryptionKeyMissing as exc:
        raise EncryptionError(
            "Cannot encrypt field: no encryption key configured. Set ENCRYPTION_KEY in environment."
        ) from exc
    except Exception as exc:
        raise EncryptionError(f"Encryption failed: {exc}") from exc


def decrypt_field(value):
    """Decrypt an encrypted dict back to plaintext."""
    if not is_encrypted_value(value):
        return value
    try:
        f = get_fernet()
        return f.decrypt(value["value"].encode("utf-8")).decode("utf-8")
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
    """Decrypt with fallback on error."""
    if not is_encrypted_value(value):
        return value
    try:
        return decrypt_field(value)
    except EncryptionError:
        logger.warning("Decryption failed — using fallback.")
        return default


def encrypt_event_data(document_type, data_json):
    """Encrypt sensitive fields for bulk_create scenarios where save() is bypassed."""
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
        if value and not is_encrypted_value(value):
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
_CTX_LEN = 24  # 16B Datei-Bindung + 4B chunk_index + 4B total


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
    """
    from pathlib import Path

    f = get_fernet()
    raw_chunks = []
    while True:
        data = input_stream.read(CHUNK_SIZE)
        if not data:
            break
        if isinstance(data, str):
            data = data.encode("utf-8")
        raw_chunks.append(data)

    total = len(raw_chunks)
    if file_id is None:
        version = _FILE_FORMAT_V1
        tokens = [f.encrypt(chunk) for chunk in raw_chunks]
    else:
        version = _FILE_FORMAT_V2
        fid16 = _file_binding(file_id)
        tokens = [f.encrypt(_chunk_context(fid16, i, total) + chunk) for i, chunk in enumerate(raw_chunks)]

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "wb") as out:
        out.write(struct.pack(">B", version))
        out.write(struct.pack(">I", total))
        for token in tokens:
            token_bytes = token if isinstance(token, bytes) else token.encode("utf-8")
            out.write(struct.pack(">I", len(token_bytes)))
            out.write(token_bytes)


def decrypt_file_stream(input_path, file_id=None):
    """Generator that yields decrypted chunks from an encrypted file.

    Reads chunk-by-chunk to avoid loading the entire file into memory. Bei
    v2-Dateien (A4.5) ist ``file_id`` erforderlich; jeder Chunk wird gegen
    seine erwartete Position + Datei-Bindung geprueft (Reorder/Truncation/
    Splicing werfen ``EncryptionError``).
    """
    f = get_fernet()

    with open(input_path, "rb") as inp:
        version_bytes = inp.read(1)
        if not version_bytes:
            raise EncryptionError("Empty encrypted file")
        (version,) = struct.unpack(">B", version_bytes)
        if version not in (_FILE_FORMAT_V1, _FILE_FORMAT_V2):
            raise EncryptionError(f"Unsupported file format version: {version}")

        (chunk_count,) = struct.unpack(">I", inp.read(4))

        fid16 = None
        if version == _FILE_FORMAT_V2:
            if file_id is None:
                raise EncryptionError("file_id required to decrypt a v2 (bound) file")
            fid16 = _file_binding(file_id)

        for index in range(chunk_count):
            len_bytes = inp.read(4)
            if len(len_bytes) < 4:
                raise EncryptionError("Truncated encrypted file")
            (token_len,) = struct.unpack(">I", len_bytes)
            token = inp.read(token_len)
            if len(token) < token_len:
                raise EncryptionError("Truncated encrypted file")
            try:
                plaintext = f.decrypt(token)
            except InvalidToken as exc:
                raise EncryptionError(f"Chunk decryption failed: {exc}") from exc

            if version == _FILE_FORMAT_V1:
                yield plaintext
                continue

            # v2: der authentifizierte Kontext bindet den Chunk an (Datei, index, total).
            ctx, chunk_data = plaintext[:_CTX_LEN], plaintext[_CTX_LEN:]
            if ctx != _chunk_context(fid16, index, chunk_count):
                raise EncryptionError("Chunk binding mismatch — possible reorder/truncation/splicing")
            yield chunk_data
