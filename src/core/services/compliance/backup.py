"""Backup- und Restore-Checks fuer Compliance-Dashboard (Refs #919, #958-M3).

- ``_backup_checks``: Letzter Backup-Zeitpunkt aus ``settings.BACKUP_DIR``.
- ``_restore_checks``: Letzter dokumentierter Restore-Test (AuditLog
  ``RESTORE_VERIFIED``, gesetzt via ``manage.py mark_restore_verified``).

Refs #2414: on the public demo (``DEMO_MODE``) both cards explain that backup
and restore are not set up there instead of pointing at operator steps.
"""

from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext as _

from core.models import AuditLog
from core.services import ops_thresholds
from core.services.compliance import _clock
from core.services.compliance._types import ComplianceCategory, ComplianceCheck, ComplianceStatus


def _demo_mode() -> bool:
    return bool(getattr(settings, "DEMO_MODE", False))


def _demo_card(key: str, label: str) -> ComplianceCheck:
    """Card text on the demo (Refs #2414).

    The demo holds only synthetic data, regenerates it every hour and has no
    backup timer; the hourly ``seed --flush`` would also delete any restore
    proof. Status stays ``unknown`` whatever the backup directory or the log
    contain: without real evidence there is no green, and there is no
    operator hint because there is nothing a visitor could set up.
    """
    return ComplianceCheck(
        key=key,
        label=label,
        category=ComplianceCategory.BACKUP,  # pragma: no mutate
        status=ComplianceStatus.UNKNOWN,
        message=_("In der Demo nicht eingerichtet: die Daten werden stündlich neu erzeugt."),
    )


def _backup_checks() -> list[ComplianceCheck]:
    """Letzter Backup-Zeitpunkt aus settings.BACKUP_DIR."""
    if _demo_mode():
        return [_demo_card("backup_age", "Letztes Backup")]
    # Lazy import (Refs #959): siehe compliance/system_info.py.
    from core.services.system.health import last_backup_info

    info = last_backup_info()
    if info is None:
        return [
            ComplianceCheck(
                key="backup_age",
                label="Letztes Backup",  # pragma: no mutate
                category=ComplianceCategory.BACKUP,  # pragma: no mutate
                status=ComplianceStatus.UNKNOWN,
                message="Kein Backup gefunden oder BACKUP_DIR nicht konfiguriert.",  # pragma: no mutate
                action_hint="settings.BACKUP_DIR pruefen, Cron-Job 'backup.sh' aktivieren.",  # pragma: no mutate
            )
        ]
    if info.get("in_future"):
        # Refs #2342: Ein Zeitstempel in der Zukunft ist kein Messwert. Frueher
        # ergab er ein negatives Alter, das unter jeder Schwelle blieb — die
        # Karte stand auf gruen und nannte ein negatives Alter.
        return [
            ComplianceCheck(
                key="backup_age",
                label="Letztes Backup",  # pragma: no mutate
                category=ComplianceCategory.BACKUP,  # pragma: no mutate
                status=ComplianceStatus.WARNING,
                message="Zeitstempel der letzten Sicherung liegt in der Zukunft — das Alter ist nicht messbar.",  # pragma: no mutate  # noqa: E501
                detail=f"{info['path']} (Zeitstempel: {info['mtime']:%Y-%m-%d %H:%M UTC})",
                action_hint="Systemuhr/Zeitsynchronisation des Hosts und den Dateizeitstempel der Sicherung pruefen.",  # pragma: no mutate  # noqa: E501
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
                category=ComplianceCategory.BACKUP,  # pragma: no mutate
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
                category=ComplianceCategory.BACKUP,  # pragma: no mutate
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
            category=ComplianceCategory.BACKUP,  # pragma: no mutate
            status=ComplianceStatus.CRITICAL,
            message=f"Backup ist {age_hours:.0f}h alt — mindestens zwei Laeufe ausgefallen.",  # pragma: no mutate
            detail=detail,
            action_hint="Backup-Cron + Disk-Speicher pruefen, Restore-Test nachholen.",  # pragma: no mutate
        )
    ]


def _restore_checks() -> list[ComplianceCheck]:
    """Letzter dokumentierter Restore-Test (AuditLog ``RESTORE_VERIFIED``)."""
    if _demo_mode():
        return [_demo_card("restore_verified", "Restore-Test")]
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
                category=ComplianceCategory.BACKUP,  # pragma: no mutate
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
                category=ComplianceCategory.BACKUP,  # pragma: no mutate
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
                category=ComplianceCategory.BACKUP,  # pragma: no mutate
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
            category=ComplianceCategory.BACKUP,  # pragma: no mutate
            status=ComplianceStatus.CRITICAL,
            message=f"Letzter Test vor {age_days} Tagen — DSGVO Art. 32 verletzt.",  # pragma: no mutate
            detail=detail,
            action_hint="Restore-Test sofort nachholen und dokumentieren.",  # pragma: no mutate
        )
    ]
