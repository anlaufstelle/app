"""Cron-Last-Run-Checks fürs Compliance-Dashboard (Refs #794, #919).

Jedes per systemd-Timer laufende Command schreibt nach Erfolg einen
``*_COMPLETED``-AuditLog-Marker (``facility=None``). Diese Helper lesen
den jüngsten Marker je Job und melden OK/WARNING/CRITICAL nach Alter —
analog zu :func:`core.services.compliance.retention._retention_checks`.

Backup und Retention haben eigene Helper (``backup.py``/``retention.py``)
und bleiben in ihren Kategorien; hier nur die drei bisher unüberwachten
Jobs unter der Kategorie „Hintergrundjobs".
"""

from __future__ import annotations

from core.models import AuditLog
from core.services.compliance import _clock
from core.services.compliance._types import ComplianceCheck, ComplianceStatus

_CATEGORY = "Hintergrundjobs"


def _latest(action: str) -> AuditLog | None:
    return AuditLog.objects.filter(action=action).order_by("-timestamp").only("timestamp", "detail").first()


def _age_hours(entry: AuditLog) -> float:
    return (_clock.now() - entry.timestamp).total_seconds() / 3600.0


def _snapshot_checks() -> list[ComplianceCheck]:
    """Statistik-Snapshots — monatlicher Cron (1. des Monats)."""
    key, label = "snapshot_last_run", "Statistik-Snapshots"
    entry = _latest(AuditLog.Action.SNAPSHOT_RUN_COMPLETED)
    if entry is None:
        return [
            ComplianceCheck(
                key=key,
                label=label,
                category=_CATEGORY,
                status=ComplianceStatus.UNKNOWN,
                message="Noch kein Snapshot-Lauf — Cron lief vielleicht nie.",  # pragma: no mutate
                action_hint="systemd-Timer 'anlaufstelle-snapshots.timer' prüfen (Ops-Runbook §3).",  # pragma: no mutate # noqa: E501
            )
        ]
    age_days = (_clock.now() - entry.timestamp).days
    detail = f"Letzter Lauf: {entry.timestamp:%Y-%m-%d %H:%M}"
    if age_days <= 35:
        return [
            ComplianceCheck(
                key=key,
                label=label,
                category=_CATEGORY,
                status=ComplianceStatus.OK,
                message=f"Letzter Lauf vor {age_days} Tag(en).",  # pragma: no mutate
                detail=detail,
            )
        ]
    if age_days <= 65:
        return [
            ComplianceCheck(
                key=key,
                label=label,
                category=_CATEGORY,
                status=ComplianceStatus.WARNING,
                message=f"Letzter Lauf vor {age_days} Tagen — Monats-Cron prüfen.",  # pragma: no mutate
                detail=detail,
                action_hint="Cron muss monatlich laufen (1. des Monats).",  # pragma: no mutate
            )
        ]
    return [
        ComplianceCheck(
            key=key,
            label=label,
            category=_CATEGORY,
            status=ComplianceStatus.CRITICAL,
            message=f"Letzter Lauf vor {age_days} Tagen — Cron ausgefallen?",  # pragma: no mutate
            detail=detail,
            action_hint="systemd-Timer-Status prüfen + manuell ausführen.",  # pragma: no mutate
        )
    ]


def _breach_scan_checks() -> list[ComplianceCheck]:
    """Breach-Detection — stündlicher Cron."""
    key, label = "breach_scan_last_run", "Breach-Detection-Scan"
    entry = _latest(AuditLog.Action.BREACH_SCAN_COMPLETED)
    if entry is None:
        return [
            ComplianceCheck(
                key=key,
                label=label,
                category=_CATEGORY,
                status=ComplianceStatus.UNKNOWN,
                message="Noch kein Breach-Scan — Cron lief vielleicht nie.",  # pragma: no mutate
                action_hint="systemd-Timer 'anlaufstelle-breach.timer' prüfen (Ops-Runbook §3).",  # pragma: no mutate # noqa: E501
            )
        ]
    age = _age_hours(entry)
    detail = f"Letzter Scan: {entry.timestamp:%Y-%m-%d %H:%M}"
    if age <= 3:
        return [
            ComplianceCheck(
                key=key,
                label=label,
                category=_CATEGORY,
                status=ComplianceStatus.OK,
                message=f"Letzter Scan vor {age:.0f}h.",  # pragma: no mutate
                detail=detail,
            )
        ]
    if age <= 24:
        return [
            ComplianceCheck(
                key=key,
                label=label,
                category=_CATEGORY,
                status=ComplianceStatus.WARNING,
                message=f"Letzter Scan vor {age:.0f}h — Cron prüfen.",  # pragma: no mutate
                detail=detail,
                action_hint="Cron muss stündlich laufen.",  # pragma: no mutate
            )
        ]
    return [
        ComplianceCheck(
            key=key,
            label=label,
            category=_CATEGORY,
            status=ComplianceStatus.CRITICAL,
            message=f"Letzter Scan vor {age:.0f}h — Cron ausgefallen?",  # pragma: no mutate
            detail=detail,
            action_hint="systemd-Timer-Status prüfen + manuell ausführen.",  # pragma: no mutate
        )
    ]


def _mv_refresh_checks() -> list[ComplianceCheck]:
    """Statistik-Materialized-View — stündlicher Refresh."""
    key, label = "mv_refresh_last_run", "Statistik-View-Refresh"
    entry = _latest(AuditLog.Action.MV_REFRESH_COMPLETED)
    if entry is None:
        return [
            ComplianceCheck(
                key=key,
                label=label,
                category=_CATEGORY,
                status=ComplianceStatus.UNKNOWN,
                message="Noch kein View-Refresh — Cron lief vielleicht nie.",  # pragma: no mutate
                action_hint="systemd-Timer 'anlaufstelle-mv-refresh.timer' prüfen (Ops-Runbook §3).",  # pragma: no mutate # noqa: E501
            )
        ]
    age = _age_hours(entry)
    detail = f"Letzter Refresh: {entry.timestamp:%Y-%m-%d %H:%M}"
    if age <= 2:
        return [
            ComplianceCheck(
                key=key,
                label=label,
                category=_CATEGORY,
                status=ComplianceStatus.OK,
                message=f"Letzter Refresh vor {age:.0f}h.",  # pragma: no mutate
                detail=detail,
            )
        ]
    if age <= 6:
        return [
            ComplianceCheck(
                key=key,
                label=label,
                category=_CATEGORY,
                status=ComplianceStatus.WARNING,
                message=f"Letzter Refresh vor {age:.0f}h — Dashboard evtl. veraltet.",  # pragma: no mutate
                detail=detail,
                action_hint="Cron muss stündlich laufen.",  # pragma: no mutate
            )
        ]
    return [
        ComplianceCheck(
            key=key,
            label=label,
            category=_CATEGORY,
            status=ComplianceStatus.CRITICAL,
            message=f"Letzter Refresh vor {age:.0f}h — Cron ausgefallen?",  # pragma: no mutate
            detail=detail,
            action_hint="systemd-Timer-Status prüfen + manuell ausführen.",  # pragma: no mutate
        )
    ]
