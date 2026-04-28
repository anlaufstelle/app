"""Fernet encryption (AES-128) for sensitive data fields.

Supports MultiFernet for key rotation: multiple keys can be configured
(ENCRYPTION_KEYS, comma-separated). The first key is used for encryption,
all keys are tried for decryption.
"""

import logging

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings

logger = logging.getLogger(__name__)


class EncryptionError(Exception):
    """General encryption error."""


class EncryptionKeyMissing(EncryptionError):
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


def get_fernet():
    """Create a MultiFernet instance from all configured keys."""
    keys = _get_keys()
    if not keys:
        raise EncryptionKeyMissing(
            "ENCRYPTION_KEY / ENCRYPTION_KEYS ist nicht gesetzt. Generiere einen Key mit generate_key()."
        )
    fernets = [Fernet(k.encode() if isinstance(k, str) else k) for k in keys]
    return MultiFernet(fernets)


def encrypt_field(value):
    """Encrypt plaintext and return a dict with a marker."""
    if not value:
        return value
    try:
        f = get_fernet()
        token = f.encrypt(str(value).encode("utf-8")).decode("utf-8")
        return {"__encrypted__": True, "value": token}
    except EncryptionKeyMissing:
        raise EncryptionError("Cannot encrypt field: no encryption key configured. Set ENCRYPTION_KEY in environment.")
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
    except (InvalidToken, Exception) as exc:
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

FILE_FORMAT_VERSION = 1
CHUNK_SIZE = 64 * 1024  # 64 KB per chunk


def encrypt_file(input_stream, output_path):
    """Encrypt a file stream in chunks, writing to output_path.

    Format: [1B version][4B chunk_count][per chunk: 4B token_len + token_bytes]
    Each chunk is independently Fernet-encrypted for streaming decryption.
    """
    import struct
    from pathlib import Path

    f = get_fernet()
    chunks = []
    while True:
        data = input_stream.read(CHUNK_SIZE)
        if not data:
            break
        if isinstance(data, str):
            data = data.encode("utf-8")
        chunks.append(f.encrypt(data))

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "wb") as out:
        out.write(struct.pack(">B", FILE_FORMAT_VERSION))
        out.write(struct.pack(">I", len(chunks)))
        for token in chunks:
            token_bytes = token if isinstance(token, bytes) else token.encode("utf-8")
            out.write(struct.pack(">I", len(token_bytes)))
            out.write(token_bytes)


def decrypt_file_stream(input_path):
    """Generator that yields decrypted chunks from an encrypted file.

    Reads chunk-by-chunk to avoid loading the entire file into memory.
    """
    import struct

    f = get_fernet()

    with open(input_path, "rb") as inp:
        version_bytes = inp.read(1)
        if not version_bytes:
            raise EncryptionError("Empty encrypted file")
        (version,) = struct.unpack(">B", version_bytes)
        if version != FILE_FORMAT_VERSION:
            raise EncryptionError(f"Unsupported file format version: {version}")

        (chunk_count,) = struct.unpack(">I", inp.read(4))

        for _ in range(chunk_count):
            len_bytes = inp.read(4)
            if len(len_bytes) < 4:
                raise EncryptionError("Truncated encrypted file")
            (token_len,) = struct.unpack(">I", len_bytes)
            token = inp.read(token_len)
            if len(token) < token_len:
                raise EncryptionError("Truncated encrypted file")
            try:
                yield f.decrypt(token)
            except InvalidToken as exc:
                raise EncryptionError(f"Chunk decryption failed: {exc}") from exc
