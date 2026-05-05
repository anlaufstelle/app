"""AuditLog-Pruning fuer den Retention-Pfad (#744).

Aus ``services/retention.py`` ausgekoppelt — semantisch eigenes Thema
(append-only-Trigger, separate Aufbewahrungsfrist).
"""

from datetime import timedelta

from django.db import connection, transaction
from django.utils import timezone

from core.models import AuditLog
from core.services._db_admin import bypass_replication_triggers


def prune_auditlog(facility, settings_obj, now=None, dry_run=False):
    """Loescht AuditLog-Eintraege aelter als ``settings_obj.auditlog_retention_months``.

    AuditLog ist append-only (siehe ``AuditLog.delete()``-Override + DB-
    Trigger ``auditlog_immutable`` in Migration 0024). Refs #781 (C-13):
    statt ``ALTER TABLE ... DISABLE TRIGGER`` / ``ENABLE TRIGGER`` wird
    der Trigger jetzt transaktional ueber ``bypass_replication_triggers``
    (``SET LOCAL session_replication_role = replica``) umgangen.

    Vorteil: ``pg_trigger.tgenabled`` bleibt durchgaengig ``'O'`` — bei
    SIGKILL zwischen den ALTER-Statements bleibt vorher der Trigger
    disabled, jetzt nicht mehr (``SET LOCAL`` wird beim Connection-Drop
    automatisch verworfen).

    ``settings_obj.auditlog_retention_months == 0`` -> No-op (deaktiviert).

    Refs #129 Teil B, Refs #733 (Tier-2-Sprint, Audit-Massnahme #14),
    Refs #781 (C-13).
    """
    months = getattr(settings_obj, "auditlog_retention_months", 0) or 0
    if months <= 0:
        return {"count": 0}
    now = now or timezone.now()
    cutoff = now - timedelta(days=months * 30)
    qs = AuditLog.objects.filter(facility=facility, timestamp__lt=cutoff)
    count = qs.count()
    if count == 0 or dry_run:
        return {"count": count}
    if connection.vendor == "postgresql":
        with transaction.atomic(), bypass_replication_triggers():
            qs.delete()
    else:
        # SQLite/Tests ohne Trigger — direkt loeschen.
        qs.delete()
    return {"count": count}
