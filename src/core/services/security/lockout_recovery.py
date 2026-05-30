"""Token-basierte Lockout-Recovery (Refs #869, Variante B2).

Eigener kurzlebiger Token (`TimestampSigner`, TTL 30 Minuten) — kein neues
DB-Modell. Der Token traegt nur die User-PK und ist signiert + zeitbeschraenkt.
Wird das Passwort des Users zwischen Versand und Klick geaendert, ist das
egal: der Token-Flow setzt ausschliesslich einen LOGIN_UNLOCK-AuditLog,
keine Passwort-Aenderung. Wenn der Account auch nach Klick weiterhin gesperrt
ist (z.B. weil zwischenzeitlich neue LOGIN_FAILED-Eintraege entstanden sind),
greifen sie ab dem Klick-Zeitpunkt wieder normal.
"""

from __future__ import annotations

from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

from core.models import User

_SALT = "lockout-recovery.v1"
_MAX_AGE_SECONDS = 30 * 60  # 30 Minuten


def build_recovery_token(user) -> str:
    """Erzeugt einen signierten Token, der den User identifiziert."""
    signer = TimestampSigner(salt=_SALT)
    return signer.sign(str(user.pk))


def verify_recovery_token(token: str) -> User | None:
    """Validiert den Token gegen Signatur + TTL.

    Returns:
        Den User-Objekt bei gueltigem Token, sonst ``None``.
    """
    if not token:
        return None
    signer = TimestampSigner(salt=_SALT)
    try:
        pk_str = signer.unsign(token, max_age=_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    try:
        return User.objects.get(pk=int(pk_str), is_active=True)
    except (User.DoesNotExist, ValueError):
        return None
