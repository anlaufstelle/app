"""Retention-Lauf-Check fuer Compliance-Dashboard (Refs #919, #958-M3).

Letzter ``RETENTION_RUN_COMPLETED``-Audit-Log-Eintrag, geschrieben vom
``enforce_retention``-Command nach jedem Lauf.
"""

from __future__ import annotations

from core.models import AuditLog
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
    age_days = (_clock.now() - entry.timestamp).days
    detail = f"Letzter Lauf: {entry.timestamp:%Y-%m-%d %H:%M}"
    if age_days <= 7:
        status = ComplianceStatus.OK
        message = f"Letzter Lauf vor {age_days} Tag(en)."
        hint = None
    elif age_days <= 14:
        status = ComplianceStatus.WARNING
        message = f"Letzter Lauf vor {age_days} Tagen — Cron-Schedule pruefen."
        hint = "Cron muss alle 1-7 Tage laufen (Default: taeglich)."
    else:
        status = ComplianceStatus.CRITICAL
        message = f"Letzter Lauf vor {age_days} Tagen — Cron ausgefallen?"
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
