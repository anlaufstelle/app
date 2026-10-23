"""Single-use, server-keyed backup codes and second-factor verification."""

from __future__ import annotations

import base64
import hmac
import re
import secrets
from contextlib import contextmanager
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils.crypto import salted_hmac
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken

# TOTP access uses the proxy model with an encrypted secret (ADR-031).
from core.models import AuditLog, User
from core.models import EncryptedTOTPDevice as TOTPDevice
from core.models.user import SessionRevocationError
from core.services.audit import audit_event

BACKUP_CODES_COUNT = 10
BACKUP_DEVICE_NAME = "backup"
# The 128-bit input has 22 URL-safe characters; legacy 32-bit codes are rejected.
_CODE_BYTES = 16
_BACKUP_CODE_FORM = re.compile(r"[A-Za-z0-9_-]{22}")


def _generate_code() -> str:
    """Return a 128-bit backup code as an unpadded base64url string."""
    return secrets.token_urlsafe(_CODE_BYTES)


def _hash_code(code: str) -> str:
    """Return a purpose-separated, 96-bit HMAC fitting StaticToken's field.

    The key derives from SECRET_KEY. Changing it invalidates issued codes;
    a database digest alone is neither a credential nor an offline guessing oracle.
    """
    key = salted_hmac("core.security.backup_codes", "storage", algorithm="sha256").digest()
    return base64.urlsafe_b64encode(hmac.digest(key, code.encode("utf-8"), "sha256")).decode("ascii")[:16]


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
    else. Only server-keyed digests are stored in the database.
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


def verify_backup_code(user, token: str, *, throttle_failures: bool = True) -> bool:
    """Consume one valid-format code atomically, returning whether it matched.

    Anonymous recovery uses endpoint limits instead of increasing the victim's
    device throttle. Authenticated verification keeps the device throttle.
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

    # Atomarer Consume (Refs #2110): Der DELETE selbst entscheidet, welcher
    # parallele Aufruf gewinnt. ``generate_backup_codes`` ersetzt das Set
    # vollstaendig und ein Satz enthaelt jeden Code nur einmal; der Filter
    # trifft also hoechstens eine Zeile.
    deleted = 0
    if _has_backup_code_form(token):
        deleted, _ = device.token_set.filter(token=_hash_code(token)).delete()
    if deleted == 0:
        if throttle_failures:
            device.throttle_increment()
        return False

    device.throttle_reset(commit=False)
    device.set_last_used_timestamp(commit=False)
    # Refs #2355: nur die Spalten, die die beiden Helfer setzen. Die Instanz
    # ist nicht gesperrt gelesen; ein voller ``save()`` schriebe jede weitere
    # Spalte des Geraets mit dem Stand von vor dem Lesen zurueck.
    device.save(update_fields=["throttling_failure_timestamp", "throttling_failure_count", "last_used_at"])
    return True


def current_totp_device(user) -> TOTPDevice | None:
    """The account's TOTP device that every check point verifies: the oldest confirmed one.

    Refs #2328: sign-in, sudo (and with it the admin area) and backup-code
    regeneration all call this helper. Up to v0.99.1 they took ``.first()`` on
    an unordered queryset, i.e. the lowest primary key; accounts that ended up
    with several confirmed devices have been signing in with that device ever
    since, so it stays the one that counts -- an update must not lock them
    out. A factor change does not depend on this order: its confirmation step
    checks the code against the new, still unconfirmed device, and the
    acknowledgement of the new backup codes (``MFABackupCodesView.post``)
    confirms it and removes every other confirmed TOTP device in the same
    transaction. After a completed change the account has exactly one.

    Exactly ONE device, not all of them: django-otp advises against checking
    every device (``match_token``), because each failure then throttles every
    device -- using one device would gradually lock the other.
    """
    return TOTPDevice.objects.filter(user=user, confirmed=True).order_by("pk").first()


def _has_totp_form(token: str, device: TOTPDevice) -> bool:
    """Genau so viele ASCII-Ziffern, wie das Geraet Stellen hat."""
    return len(token) == device.digits and token.isascii() and token.isdigit()


def _has_backup_code_form(token: str) -> bool:
    return _BACKUP_CODE_FORM.fullmatch(token) is not None


def verify_totp_or_backup(user, token: str) -> bool:
    """Verifiziert einen frischen TOTP-Token ODER Backup-Code (A3.2, Refs #1024).

    Genutzt vom Sudo-Mode-Re-Auth, wenn der User 2FA aktiv hat — ein über eine
    gestohlene Session erbeutetes Passwort allein soll sensible Aktionen nicht
    freischalten.

    Refs #2328: Die Form der Eingabe waehlt den EINEN Pruefweg, bevor ein Geraet
    beruehrt wird. Ein TOTP-Code geht nur an das geltende ``TOTPDevice``
    (``current_totp_device``; ``verify_token`` ist single-use je Zeitfenster +
    gedrosselt), ein Backup-Code nur an ``verify_backup_code`` (eigene Drossel).
    Frueher ging jede Eingabe zuerst an das TOTP-Geraet: jeder Backup-Code
    zaehlte dort als Fehlversuch, und wer Sudo mit Backup-Codes bestaetigte,
    sperrte sich schrittweise den naechsten gueltigen TOTP-Code. Eine Eingabe
    ohne eine der beiden Formen kann kein Weg annehmen; sie wird abgewiesen,
    ohne ein Geraet zu beruehren.
    """
    token = (token or "").strip().replace(" ", "")
    if not token:
        return False
    if _has_backup_code_form(token):
        return verify_backup_code(user, token)
    device = current_totp_device(user)
    if device is not None and _has_totp_form(token, device):
        return device.verify_token(token)
    return False


@contextmanager
def mfa_change_transaction(user: User, *, require_current_session: bool = False):
    """Lock the authorized account through factor changes and their audit write."""
    with transaction.atomic():
        locked = User.objects.select_for_update().filter(facility=user.facility_id, pk=user.pk).first()
        if locked is None:
            # Preserve the administrative reset's denial contract (Refs #2640).
            operation = "change" if require_current_session else "reset"
            raise PermissionDenied(f"Second-factor {operation} is not allowed for this account.")
        if require_current_session and (not locked.is_active or locked.password != user.password):
            raise PermissionDenied("The account authentication changed during the second-factor operation.")
        yield locked


def revoke_mfa_sessions(locked: User) -> None:
    """Persist a fresh session nonce inside ``mfa_change_transaction``.

    The row lock serializes changes against other factor changes and password
    writes. A signal-free update preserves the offline salt. Device changes
    and auditing must share the caller's transaction, so any failure restores
    the old factor and auth hash. No bounded session scan is involved.
    """
    locked.revoke_sessions()
    if User.objects.filter(facility=locked.facility_id, pk=locked.pk).update(password=locked.password) != 1:
        raise SessionRevocationError("The revoked password hash of the target was not stored.")


@dataclass(frozen=True)
class MFASetupResult:
    user: User
    device: TOTPDevice
    sessions_revoked: bool


def confirm_totp_setup(user: User, device_pk, *, ip_address: str | None = None) -> MFASetupResult | None:
    """Confirm only the authorized device, atomically replacing the old factor."""
    with mfa_change_transaction(user, require_current_session=True) as locked:
        device = TOTPDevice.objects.filter(user=locked, pk=device_pk, confirmed=False).first()
        if device is None:
            return None
        sessions_revoked = locked.has_confirmed_mfa_device
        if sessions_revoked:
            revoke_mfa_sessions(locked)
        device.confirmed = True
        device.save(update_fields=["confirmed"])
        replaced, _info = TOTPDevice.objects.filter(user=locked, confirmed=True).exclude(pk=device.pk).delete()
        audit_event(
            AuditLog.Action.MFA_ENABLED,
            user=locked,
            facility=locked.facility,
            target_obj=locked,
            detail={"event": "mfa_setup_confirmed", "count": replaced, "sessions_revoked": sessions_revoked},
            ip_address=ip_address,
        )
    return MFASetupResult(user=locked, device=device, sessions_revoked=sessions_revoked)


def disable_mfa(user: User, *, ip_address: str | None = None) -> User | None:
    """Remove TOTP and passkeys only together with session revocation and audit."""
    from django_otp_webauthn.models import WebAuthnCredential

    with mfa_change_transaction(user, require_current_session=True) as locked:
        # Recheck enforcement under the lock, including a concurrent account change.
        if locked.is_mfa_enforced:
            raise PermissionDenied("Two-factor authentication is mandatory for this account.")
        devices = TOTPDevice.objects.filter(user=locked)
        passkeys = WebAuthnCredential.objects.filter(user=locked)
        if not devices.exists() and not passkeys.exists():
            return None
        revoke_mfa_sessions(locked)
        devices.delete()
        passkeys_deleted, _info = passkeys.delete()
        audit_event(
            AuditLog.Action.MFA_DISABLED,
            user=locked,
            facility=locked.facility,
            target_obj=locked,
            detail={"event": "mfa_disabled", "passkeys_removed": passkeys_deleted, "sessions_revoked": True},
            ip_address=ip_address,
        )
    return locked
