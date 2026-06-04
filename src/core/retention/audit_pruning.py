"""AuditLog-Pruning fuer den Retention-Pfad (#744).

Aus ``services/retention.py`` ausgekoppelt — semantisch eigenes Thema
(append-only-Trigger, separate Aufbewahrungsfrist).
"""

import calendar
from datetime import datetime

from django.db import connection, transaction
from django.utils import timezone

from core.models import AuditLog
from core.services.system import bypass_replication_triggers

# A6.3 (Refs #1024 / #1016): Aktionen, die NICHT der Routine-Retention
# unterliegen. SECURITY_VIOLATION sind Breach-Detection-Forensiknachweise;
# RETENTION_RUN_COMPLETED ist der Marker, aus dem das Compliance-Dashboard den
# letzten erfolgreichen Lauf liest (sonst „UNKNOWN"). Beide tragen
# System-Metadaten, keine Klient-PII — die längere Aufbewahrung ist gerechtfertigt.
PRUNE_EXEMPT_ACTIONS = (
    AuditLog.Action.SECURITY_VIOLATION,
    AuditLog.Action.RETENTION_RUN_COMPLETED,
)


def _months_before(moment: datetime, months: int) -> datetime:
    """Kalendergenaue Monats-Subtraktion (A6.4, Refs #1024 / #1016).

    Zieht ``months`` Kalendermonate von ``moment`` ab und erhaelt Uhrzeit +
    tzinfo. Der Tag wird auf den letzten gueltigen Tag des Zielmonats geklemmt
    (z.B. 31.03. - 1 Monat = 28./29.02.). Vermeidet die fruehere 30-Tage-
    Naeherung (``months * 30``), die bei 24 Monaten ~10 Tage zu frueh schnitt.
    Analog zu ``_add_months`` in ``services/case/workitems.py``, aber
    datetime-/tz-erhaltend fuer den Retention-Cutoff.
    """
    month_index = moment.month - 1 - months
    year = moment.year + month_index // 12
    month = month_index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


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

    Ausgenommen (``PRUNE_EXEMPT_ACTIONS``, A6.3): SECURITY_VIOLATION und
    RETENTION_RUN_COMPLETED bleiben unabhaengig vom Alter erhalten.

    Refs #129 Teil B, Refs #733 (#14),
    Refs #781 (C-13), Refs #1024 (A6.3).
    """
    months = getattr(settings_obj, "auditlog_retention_months", 0) or 0
    if months <= 0:
        return {"count": 0}
    now = now or timezone.now()
    cutoff = _months_before(now, months)
    qs = AuditLog.objects.filter(facility=facility, timestamp__lt=cutoff).exclude(action__in=PRUNE_EXEMPT_ACTIONS)
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
