"""Lazy generation and rotation of per-user salts for client-side PBKDF2.

The salt is the only server-side artefact of the offline encryption layer.
Combined with the user's password (provided client-side), it deterministically
derives the AES-GCM-256 session key in the browser. Salt is base64url-encoded
without padding, 16 bytes (128 bit) of entropy from ``secrets.token_bytes``.
"""

import base64
import secrets


def ensure_offline_key_salt(user) -> str:
    """Return the user's offline salt, generating one on first access.

    The salt is intentionally never rotated except on password change
    (handled by ``CustomPasswordChangeView.form_valid``), so that an active
    session can re-derive the same key after a tab reload.
    """
    if not user.offline_key_salt:
        salt_bytes = secrets.token_bytes(16)
        user.offline_key_salt = base64.urlsafe_b64encode(salt_bytes).decode().rstrip("=")
        user.save(update_fields=["offline_key_salt"])
    return user.offline_key_salt
