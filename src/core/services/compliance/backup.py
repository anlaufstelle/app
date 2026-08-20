"""Backup- und Restore-Checks fuer Compliance-Dashboard (Refs #919, #958-M3).

- ``_backup_checks``: Letzter Backup-Zeitpunkt aus ``settings.BACKUP_DIR``.
- ``_restore_checks``: Letzter dokumentierter Restore-Test (AuditLog
  ``RESTORE_VERIFIED``, gesetzt via ``manage.py mark_restore_verified``).
"""

from __future__ import annotations

from core.models import AuditLog
from core.services import ops_thresholds
from core.services.compliance import _clock
from core.services.compliance._types import ComplianceCheck, ComplianceStatus


def _backup_checks() -> list[ComplianceCheck]:
    """Letzter Backup-Zeitpunkt aus settings.BACKUP_DIR."""
    # Lazy import (Refs #959): siehe compliance/system_info.py.
    from core.services.system.health import last_backup_info

    info = last_backup_info()
    if info is None:
        return [
            ComplianceCheck(
                key="backup_age",
                label="Letztes Backup",  # pragma: no mutate
                category="Backup",  # pragma: no mutate
                status=ComplianceStatus.UNKNOWN,
                message="Kein Backup gefunden oder BACKUP_DIR nicht konfiguriert.",  # pragma: no mutate
                action_hint="settings.BACKUP_DIR pruefen, Cron-Job 'backup.sh' aktivieren.",  # pragma: no mutate
            )
        ]
    age_hours = info["age_hours"]
    detail = f"{info['path']} (Alter: {age_hours:.1f}h)"
    # Refs #1872: Schwellen aus ``ops_thresholds`` (36 h / 48 h = 1,5 x / 2 x
    # Tagesintervall des Host-Cron) statt der frueheren, hier lokal gesetzten
    # 24 h / 72 h — dieselbe Backup-Zeile meldete sonst je nach Ansicht anderes.
    if age_hours <= ops_thresholds.BACKUP_WARN_HOURS:
        return [
            ComplianceCheck(
                key="backup_age",
                label="Letztes Backup",  # pragma: no mutate
                category="Backup",  # pragma: no mutate
                status=ComplianceStatus.OK,
                message=f"Backup ist {age_hours:.0f}h alt (Schwelle: {ops_thresholds.BACKUP_WARN_HOURS}h).",  # pragma: no mutate  # noqa: E501
                detail=detail,
            )
        ]
    if age_hours <= ops_thresholds.BACKUP_CRITICAL_HOURS:
        return [
            ComplianceCheck(
                key="backup_age",
                label="Letztes Backup",  # pragma: no mutate
                category="Backup",  # pragma: no mutate
                status=ComplianceStatus.WARNING,
                message=f"Backup ist {age_hours:.0f}h alt — ein Tageslauf fehlt.",  # pragma: no mutate
                detail=detail,
                action_hint="Backup-Cron pruefen, evtl. manuell ausloesen.",  # pragma: no mutate
            )
        ]
    return [
        ComplianceCheck(
            key="backup_age",
            label="Letztes Backup",  # pragma: no mutate
            category="Backup",  # pragma: no mutate
            status=ComplianceStatus.CRITICAL,
            message=f"Backup ist {age_hours:.0f}h alt — mindestens zwei Laeufe ausgefallen.",  # pragma: no mutate
            detail=detail,
            action_hint="Backup-Cron + Disk-Speicher pruefen, Restore-Test nachholen.",  # pragma: no mutate
        )
    ]


def _restore_checks() -> list[ComplianceCheck]:
    """Letzter dokumentierter Restore-Test (AuditLog ``RESTORE_VERIFIED``)."""
    entry = (
        AuditLog.objects.filter(action=AuditLog.Action.RESTORE_VERIFIED)
        .order_by("-timestamp")
        .only("timestamp", "detail")
        .first()
    )
    if entry is None:
        return [
            ComplianceCheck(
                key="restore_verified",
                label="Restore-Test",  # pragma: no mutate
                category="Backup",  # pragma: no mutate
                status=ComplianceStatus.UNKNOWN,
                message="Noch nie ein Restore-Test dokumentiert.",  # pragma: no mutate
                action_hint="manage.py mark_restore_verified --note '...' nach jedem Test ausfuehren.",  # pragma: no mutate # noqa: E501
            )
        ]
    age_days = (_clock.now() - entry.timestamp).days
    detail = f"Letzter Restore-Test: {entry.timestamp:%Y-%m-%d}; Notiz: {entry.detail.get('note') or '—'}"
    if age_days <= 90:
        return [
            ComplianceCheck(
                key="restore_verified",
                label="Restore-Test",  # pragma: no mutate
                category="Backup",  # pragma: no mutate
                status=ComplianceStatus.OK,
                message=f"Letzter Test vor {age_days} Tagen.",  # pragma: no mutate
                detail=detail,
            )
        ]
    if age_days <= 180:
        return [
            ComplianceCheck(
                key="restore_verified",
                label="Restore-Test",  # pragma: no mutate
                category="Backup",  # pragma: no mutate
                status=ComplianceStatus.WARNING,
                message=f"Letzter Test vor {age_days} Tagen — Auffrischung empfohlen.",  # pragma: no mutate
                detail=detail,
                action_hint="Restore gegen frische DB testen, dann mark_restore_verified ausfuehren.",  # pragma: no mutate # noqa: E501
            )
        ]
    return [
        ComplianceCheck(
            key="restore_verified",
            label="Restore-Test",  # pragma: no mutate
            category="Backup",  # pragma: no mutate
            status=ComplianceStatus.CRITICAL,
            message=f"Letzter Test vor {age_days} Tagen — DSGVO Art. 32 verletzt.",  # pragma: no mutate
            detail=detail,
            action_hint="Restore-Test sofort nachholen und dokumentieren.",  # pragma: no mutate
        )
    ]
