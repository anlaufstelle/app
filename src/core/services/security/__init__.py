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
- :mod:`core.services.security.origin` — feste Basis-Origin fuer
  sicherheitsrelevante Links (``build_external_url``,
  ``external_link_context``, ``get_external_origin``).
- :mod:`core.services.security.credentials` — zentraler Credential-Lifecycle
  fuer Passwortwechsel, Reset, Invite-Setup und Rechteentzug
  (``revoke_credentials``, ``RevocationReason``).
"""

from core.services.security.credentials import (
    REVOCATION_EFFECTS,
    RevocationEffects,
    RevocationReason,
    revoke_credentials,
)
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
from core.services.security.origin import (
    build_external_url,
    external_link_context,
    get_external_origin,
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
    "REVOCATION_EFFECTS",
    "RequireSudoModeMixin",
    "RevocationEffects",
    "RevocationReason",
    "SUDO_SESSION_KEY",
    "_hash_code",
    "build_external_url",
    "build_invite_url",
    "build_recovery_token",
    "check_version_conflict",
    "clear_sudo",
    "decrypt_totp_key",
    "encrypt_totp_key",
    "enter_sudo",
    "external_link_context",
    "generate_backup_codes",
    "get_external_origin",
    "invite_token_generator",
    "is_encrypted_totp_key",
    "is_in_sudo",
    "is_locked",
    "remaining_backup_codes",
    "revoke_credentials",
    "send_invite_email",
    "unlock",
    "verify_backup_code",
    "verify_recovery_token",
    "verify_totp_or_backup",
]
