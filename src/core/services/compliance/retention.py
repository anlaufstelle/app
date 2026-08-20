"""Retention-Lauf-Check fuer Compliance-Dashboard (Refs #919, #958-M3).

Letzter ``RETENTION_RUN_COMPLETED``-Audit-Log-Eintrag, geschrieben vom
``enforce_retention``-Command nach jedem Lauf.
"""

from __future__ import annotations

from core.models import AuditLog
from core.services import ops_thresholds
from core.services.compliance import _clock
from core.services.compliance._types import ComplianceCheck, ComplianceStatus


def _retention_checks() -> list[ComplianceCheck]:
    """Retention-Cron LastRun aus AuditLog ``RETENTION_RUN_COMPLETED``."""
    entry = (
        AuditLog.objects.filter(action=AuditLog.Action.RETENTION_RUN_COMPLETED)
        .order_by("-timestamp")
        .only("timestamp", "detail")
        .first()
    )
    if entry is None:
        return [
            ComplianceCheck(
                key="retention_last_run",
                label="Retention-Lauf",  # pragma: no mutate
                category="Retention",  # pragma: no mutate
                status=ComplianceStatus.UNKNOWN,
                message="Noch kein RETENTION_RUN_COMPLETED-Eintrag — Cron lief vielleicht nie.",  # pragma: no mutate
                action_hint="enforce_retention-Cron pruefen (Ops-Runbook §6).",  # pragma: no mutate
            )
        ]
    # Refs #1872: ``enforce_retention`` laeuft laut ``deploy/crontab`` taeglich
    # um 03:00. Die frueheren Tages-Schwellen (OK <= 7 d, WARNING <= 14 d)
    # zeigten einen seit knapp einer Woche toten Cron noch gruen — bei einem
    # Job, an dem die DSFA-Zusage zu Loeschfristen haengt. Jetzt dieselbe
    # 1,5x/2x-Intervall-Regel wie Backup und Audit-Ketten-Pruefung.
    age_hours = (_clock.now() - entry.timestamp).total_seconds() / 3600.0
    detail = f"Letzter Lauf: {entry.timestamp:%Y-%m-%d %H:%M}"
    if age_hours <= ops_thresholds.RETENTION_WARN_HOURS:
        status = ComplianceStatus.OK
        message = f"Letzter Lauf vor {age_hours:.0f}h."
        hint = None
    elif age_hours <= ops_thresholds.RETENTION_CRITICAL_HOURS:
        status = ComplianceStatus.WARNING
        message = f"Letzter Lauf vor {age_hours:.0f}h — ein Tageslauf fehlt."
        hint = "Cron muss taeglich laufen (deploy/crontab: 0 3 * * *)."
    else:
        status = ComplianceStatus.CRITICAL
        message = f"Letzter Lauf vor {age_hours:.0f}h — mindestens zwei Laeufe ausgefallen."
        hint = "Cron-Status im Container pruefen + manuell ausfuehren."
    return [
        ComplianceCheck(
            key="retention_last_run",
            label="Retention-Lauf",  # pragma: no mutate
            category="Retention",  # pragma: no mutate
            status=status,
            message=message,
            detail=detail,
            action_hint=hint,
        )
    ]
