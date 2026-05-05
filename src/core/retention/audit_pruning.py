"""AuditLog-Pruning fuer den Retention-Pfad (#744).

Aus ``services/retention.py`` ausgekoppelt — semantisch eigenes Thema
(append-only-Trigger, separate Aufbewahrungsfrist).
"""

from datetime import timedelta

from django.db import connection, transaction
from django.utils import timezone

from core.models import AuditLog


def prune_auditlog(facility, settings_obj, now=None, dry_run=False):
    """Loescht AuditLog-Eintraege aelter als ``settings_obj.auditlog_retention_months``.

    AuditLog ist append-only (siehe ``AuditLog.delete()``-Override + DB-
    Trigger ``auditlog_immutable`` in Migration 0024). Fuer das Retention-
    Pruning deaktivieren wir den Trigger transaktional und nutzen
    ``QuerySet.delete()`` (umgeht den Python-``delete()``-Override). Das
    DISABLE/ENABLE-Paar laeuft in derselben ``transaction.atomic()`` —
    bricht der COMMIT, wird auch das DISABLE TRIGGER zurueckgerollt.

    ``settings_obj.auditlog_retention_months == 0`` -> No-op (deaktiviert).

    Refs #129 Teil B, Refs #733 (Tier-2-Sprint, Audit-Massnahme #14).
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
        # Trigger temporaer deaktivieren — sonst blockt der DB-Trigger
        # die DELETE-Statements. ENABLE im finally, damit ein Fehler
        # waehrend QuerySet.delete() den Trigger nicht abgeschaltet laesst.
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute("ALTER TABLE core_auditlog DISABLE TRIGGER auditlog_immutable")
            try:
                qs.delete()
            finally:
                cur.execute("ALTER TABLE core_auditlog ENABLE TRIGGER auditlog_immutable")
    else:
        # SQLite/Tests ohne Trigger — direkt loeschen.
        qs.delete()
    return {"count": count}
