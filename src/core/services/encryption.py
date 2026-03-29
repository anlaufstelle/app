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
