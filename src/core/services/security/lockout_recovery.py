"""Token-basierte Lockout-Recovery (Refs #869, Variante B2; Single-Use Refs #1273).

Eigener kurzlebiger Token (`TimestampSigner`, TTL 30 Minuten) — kein neues
DB-Modell. Der Token traegt die User-PK **und** einen Anker auf den juengsten
LOGIN_FAILED-Eintrag zum Erstellungszeitpunkt. Damit ist er **einmalig**
nutzbar: Sobald die Entsperrung erfolgt ist (oder der Account anderweitig
entsperrt wurde), existiert ein LOGIN_UNLOCK, der neuer ist als der gebundene
Fehlversuch — ein Replay des Links im 30-Min-Fenster wird dann abgewiesen
(Refs #1273).

Der Token-Flow setzt ausschliesslich einen LOGIN_UNLOCK-AuditLog, keine
Passwort-Aenderung. Wird das Passwort zwischen Versand und Klick geaendert,
ist das egal. Bleibt der Account nach dem Entsperren weiterhin gesperrt (z.B.
weil zwischenzeitlich neue LOGIN_FAILED-Eintraege entstanden sind), greift die
Sperre ab dem Klick-Zeitpunkt wieder normal.
"""

from __future__ import annotations

from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

from core.models import AuditLog, User

_SALT = "lockout-recovery.v1"
_MAX_AGE_SECONDS = 30 * 60  # 30 Minuten


def _latest_failed_id(user) -> int:
    """PK des juengsten LOGIN_FAILED-Eintrags des Users (Anker), oder 0."""
    pk = (
        AuditLog.objects.filter(user=user, action=AuditLog.Action.LOGIN_FAILED)
        .order_by("-timestamp", "-id")
        .values_list("id", flat=True)
        .first()
    )
    return int(pk) if pk is not None else 0


def build_recovery_token(user) -> str:
    """Erzeugt einen signierten, einmalig nutzbaren Token.

    Payload ``<user.pk>:<anchor>``, wobei ``anchor`` den juengsten
    LOGIN_FAILED-Eintrag zum Erstellungszeitpunkt referenziert (0, wenn
    keiner existiert). Siehe :func:`verify_recovery_token` fuer die
    Einmal-Nutzungs-Pruefung.
    """
    signer = TimestampSigner(salt=_SALT)
    return signer.sign(f"{user.pk}:{_latest_failed_id(user)}")


def verify_recovery_token(token: str) -> User | None:
    """Validiert Signatur, TTL **und** Einmal-Nutzung.

    Der Token ist an den juengsten Fehlversuch zum Erstellungszeitpunkt
    gebunden. Existiert bereits ein LOGIN_UNLOCK, der neuer ist als dieser
    Fehlversuch (durch eine vorherige Nutzung desselben Links **oder** eine
    andere Entsperrung), gilt der Token als verbraucht und wird abgewiesen
    (Refs #1273).

    Returns:
        Den User bei gueltigem, noch nicht verbrauchtem Token, sonst ``None``.
    """
    if not token:
        return None
    signer = TimestampSigner(salt=_SALT)
    try:
        payload = signer.unsign(token, max_age=_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None

    pk_str, sep, anchor_str = payload.partition(":")
    if not sep:
        return None  # Alt-Token ohne Anker -> nicht (mehr) vertrauenswuerdig.
    try:
        user = User.objects.get(pk=int(pk_str), is_active=True)
        anchor_id = int(anchor_str)
    except (User.DoesNotExist, ValueError):
        return None

    anchor_ts = None
    if anchor_id > 0:
        anchor_ts = (
            AuditLog.objects.filter(
                pk=anchor_id, user=user, action=AuditLog.Action.LOGIN_FAILED
            )
            .values_list("timestamp", flat=True)
            .first()
        )
        if anchor_ts is None:
            return None  # Anker verschwunden/fremd -> Token nicht vertrauenswuerdig.

    last_unlock_ts = (
        AuditLog.objects.filter(user=user, action=AuditLog.Action.LOGIN_UNLOCK)
        .order_by("-timestamp")
        .values_list("timestamp", flat=True)
        .first()
    )
    # Verbraucht, sobald seit dem gebundenen Fehlversuch (bzw. ueberhaupt, wenn
    # keiner gebunden ist) eine Entsperrung stattgefunden hat.
    if last_unlock_ts is not None and (anchor_ts is None or last_unlock_ts > anchor_ts):
        return None
    return user
