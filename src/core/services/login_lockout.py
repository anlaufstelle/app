"""Account-Lockout nach gehäuften Fehlanmeldungen (Refs #612).

Tracking erfolgt über den bestehenden AuditLog (Option A aus dem Design):
kein zusätzliches Modell, dafür eine Composite-Query je Login. Der Audit-
Log wird ohnehin auf jeden `user_login_failed`-Signal geschrieben — wir
zählen einfach die relevanten Einträge.
"""

from datetime import timedelta

from django.utils import timezone

from core.models import AuditLog

# Schwelle und Zeitfenster (Issue #612): 10 Fehlversuche in 15 Minuten
# lösen die Sperre aus; sie wirkt, bis das Fenster leer ist — also 15 Min
# nach dem letzten Fehlversuch.  Einträge vor dem letzten LOGIN_UNLOCK zählen
# nicht mit, damit Admin-Unlock wirkt.
LOCKOUT_THRESHOLD = 10
LOCKOUT_WINDOW = timedelta(minutes=15)


def is_locked(user) -> bool:
    """Return True when the user has reached the lockout threshold."""
    if user is None or not getattr(user, "pk", None):
        return False

    cutoff = timezone.now() - LOCKOUT_WINDOW
    last_unlock = AuditLog.objects.filter(user=user, action=AuditLog.Action.LOGIN_UNLOCK).order_by("-timestamp").first()

    qs = AuditLog.objects.filter(
        user=user,
        action=AuditLog.Action.LOGIN_FAILED,
        timestamp__gte=cutoff,
    )
    if last_unlock is not None:
        qs = qs.filter(timestamp__gt=last_unlock.timestamp)
    return qs.count() >= LOCKOUT_THRESHOLD


def unlock(user, unlocked_by, ip_address=None) -> AuditLog:
    """Record a LOGIN_UNLOCK audit entry for the user.

    Subsequent `is_locked(user)` calls ignore LOGIN_FAILED entries with
    `timestamp <= this_entry.timestamp`.
    """
    return AuditLog.objects.create(
        facility=getattr(user, "facility", None),
        user=user,
        action=AuditLog.Action.LOGIN_UNLOCK,
        target_type="User",
        target_id=str(user.pk),
        detail={"unlocked_by": str(unlocked_by.pk) if unlocked_by else None},
        ip_address=ip_address,
    )
