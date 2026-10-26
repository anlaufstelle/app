"""Validate configured key rings before any encrypted data is accessed."""

import warnings

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured


def validate_encryption_key_ring(value: str) -> None:
    """Reject malformed entries and warn about redundant keys without disclosure."""
    seen: set[str] = set()
    entries = [entry.strip() for entry in value.split(",") if entry.strip()]
    for position, entry in enumerate(entries, start=1):
        try:
            Fernet(entry)
        except (ValueError, TypeError):
            raise ImproperlyConfigured(f"ENCRYPTION_KEYS entry {position} is not a valid Fernet key.") from None
        if entry in seen:
            warnings.warn(
                f"ENCRYPTION_KEYS entry {position} duplicates an earlier entry.",
                RuntimeWarning,
                stacklevel=2,
            )
        seen.add(entry)
