"""Security-Service Subpackage (Refs #959).

Buendelt Auth-/MFA-/Lockout-/Sudo-bezogene Helper, die vorher als sechs
flache Module unter ``services/`` lagen. Aufrufer importieren ueber den
Subpackage-Namen, z.B.::

    from core.services.security import is_locked, unlock, enter_sudo

Module:

- :mod:`core.services.security.login_lockout` — Login-Failure-Counting +
  Auto-Unlock (``is_locked``, ``unlock``).
- :mod:`core.services.security.lockout_recovery` — Signed-Token-basierte
  Self-Service-Entsperrung (``build_recovery_token``,
  ``verify_recovery_token``).
- :mod:`core.services.security.mfa` — TOTP-/Backup-Code-Verwaltung
  (``generate_backup_codes``, ``remaining_backup_codes``,
  ``verify_backup_code``).
- :mod:`core.services.security.sudo_mode` — Sudo-TTL-Logik fuer
  privilegierte Admin-Aktionen (``enter_sudo``, ``is_in_sudo``,
  ``clear_sudo``, ``RequireSudoModeMixin``).
- :mod:`core.services.security.locking` — Optimistic Locking
  (``check_version_conflict``).
- :mod:`core.services.security.invite` — Invite-Mail-Versand
  (``build_invite_url``, ``send_invite_email``).
"""

from core.services.security.invite import build_invite_url, invite_token_generator, send_invite_email
from core.services.security.locking import check_version_conflict
from core.services.security.lockout_recovery import (
    build_recovery_token,
    verify_recovery_token,
)
from core.services.security.login_lockout import (
    LOCKOUT_THRESHOLD,
    LOCKOUT_WINDOW,
    is_locked,
    unlock,
)
from core.services.security.mfa import (
    BACKUP_CODES_COUNT,
    _hash_code,
    generate_backup_codes,
    remaining_backup_codes,
    verify_backup_code,
    verify_totp_or_backup,
)
from core.services.security.sudo_mode import (
    SUDO_SESSION_KEY,
    RequireSudoModeMixin,
    clear_sudo,
    enter_sudo,
    is_in_sudo,
)
from core.services.security.totp import (
    decrypt_totp_key,
    encrypt_totp_key,
    is_encrypted_totp_key,
)

__all__ = [
    "BACKUP_CODES_COUNT",
    "LOCKOUT_THRESHOLD",
    "LOCKOUT_WINDOW",
    "RequireSudoModeMixin",
    "SUDO_SESSION_KEY",
    "_hash_code",
    "build_invite_url",
    "build_recovery_token",
    "check_version_conflict",
    "clear_sudo",
    "decrypt_totp_key",
    "encrypt_totp_key",
    "enter_sudo",
    "generate_backup_codes",
    "invite_token_generator",
    "is_encrypted_totp_key",
    "is_in_sudo",
    "is_locked",
    "remaining_backup_codes",
    "send_invite_email",
    "unlock",
    "verify_backup_code",
    "verify_recovery_token",
    "verify_totp_or_backup",
]
