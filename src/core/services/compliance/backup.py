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
        return [_demo_card("backup_age", _("Letztes Backup"))]
    # Lazy import (Refs #959): siehe compliance/system_info.py.
    from core.services.system.health import last_backup_info

    info = last_backup_info()
    if info is None:
        return [
            ComplianceCheck(
                key="backup_age",
                label=_("Letztes Backup"),  # pragma: no mutate
                category=ComplianceCategory.BACKUP,  # pragma: no mutate
                status=ComplianceStatus.UNKNOWN,
                message=_("Kein Backup gefunden oder BACKUP_DIR nicht konfiguriert."),  # pragma: no mutate
                action_hint=_("settings.BACKUP_DIR prüfen, Cron-Job 'backup.sh' aktivieren."),  # pragma: no mutate
            )
        ]
    if info.get("in_future"):
        # Refs #2342: Ein Zeitstempel in der Zukunft ist kein Messwert. Frueher
        # ergab er ein negatives Alter, das unter jeder Schwelle blieb — die
        # Karte stand auf gruen und nannte ein negatives Alter.
        return [
            ComplianceCheck(
                key="backup_age",
                label=_("Letztes Backup"),  # pragma: no mutate
                category=ComplianceCategory.BACKUP,  # pragma: no mutate
                status=ComplianceStatus.WARNING,
                message=_(
                    "Zeitstempel der letzten Sicherung liegt in der Zukunft — das Alter ist nicht messbar."
                ),  # pragma: no mutate  # noqa: E501
                detail=_("%(path)s (Zeitstempel: %(timestamp)s)")
                % {"path": info["path"], "timestamp": f"{info['mtime']:%Y-%m-%d %H:%M UTC}"},
                action_hint=_(
                    "Systemuhr/Zeitsynchronisation des Hosts und den Dateizeitstempel der Sicherung prüfen."
                ),  # pragma: no mutate  # noqa: E501
            )
        ]
    age_hours = info["age_hours"]
    detail = _("%(path)s (Alter: %(age_hours)sh)") % {"path": info["path"], "age_hours": f"{age_hours:.1f}"}
    # Refs #1872: Schwellen aus ``ops_thresholds`` (36 h / 48 h = 1,5 x / 2 x
    # Tagesintervall des Host-Cron) statt der frueheren, hier lokal gesetzten
    # 24 h / 72 h — dieselbe Backup-Zeile meldete sonst je nach Ansicht anderes.
    if age_hours <= ops_thresholds.BACKUP_WARN_HOURS:
        return [
            ComplianceCheck(
                key="backup_age",
                label=_("Letztes Backup"),  # pragma: no mutate
                category=ComplianceCategory.BACKUP,  # pragma: no mutate
                status=ComplianceStatus.OK,
                message=_("Backup ist %(age_hours)sh alt (Schwelle: %(hours)sh).")
                % {
                    "age_hours": f"{age_hours:.0f}",
                    "hours": ops_thresholds.BACKUP_WARN_HOURS,
                },  # pragma: no mutate  # noqa: E501
                detail=detail,
            )
        ]
    if age_hours <= ops_thresholds.BACKUP_CRITICAL_HOURS:
        return [
            ComplianceCheck(
                key="backup_age",
                label=_("Letztes Backup"),  # pragma: no mutate
                category=ComplianceCategory.BACKUP,  # pragma: no mutate
                status=ComplianceStatus.WARNING,
                message=_("Backup ist %(age_hours)sh alt — ein Tageslauf fehlt.")
                % {"age_hours": f"{age_hours:.0f}"},  # pragma: no mutate
                detail=detail,
                action_hint=_("Backup-Cron prüfen, evtl. manuell auslösen."),  # pragma: no mutate
            )
        ]
    return [
        ComplianceCheck(
            key="backup_age",
            label=_("Letztes Backup"),  # pragma: no mutate
            category=ComplianceCategory.BACKUP,  # pragma: no mutate
            status=ComplianceStatus.CRITICAL,
            message=_("Backup ist %(age_hours)sh alt — mindestens zwei Läufe ausgefallen.")
            % {"age_hours": f"{age_hours:.0f}"},  # pragma: no mutate
            detail=detail,
            action_hint=_("Backup-Cron + Disk-Speicher prüfen, Restore-Test nachholen."),  # pragma: no mutate
        )
    ]


def _restore_checks() -> list[ComplianceCheck]:
    """Letzter dokumentierter Restore-Test (AuditLog ``RESTORE_VERIFIED``)."""
    if _demo_mode():
        return [_demo_card("restore_verified", _("Restore-Test"))]
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
                label=_("Restore-Test"),  # pragma: no mutate
                category=ComplianceCategory.BACKUP,  # pragma: no mutate
                status=ComplianceStatus.UNKNOWN,
                message=_("Noch nie ein Restore-Test dokumentiert."),  # pragma: no mutate
                action_hint=_(
                    "manage.py mark_restore_verified --note '...' nach jedem Test ausführen."
                ),  # pragma: no mutate # noqa: E501
            )
        ]
    age_days = (_clock.now() - entry.timestamp).days
    detail = _("Letzter Restore-Test: %(timestamp)s; Notiz: %(note)s") % {
        "timestamp": f"{entry.timestamp:%Y-%m-%d}",
        "note": entry.detail.get("note") or "—",
    }
    if age_days <= 90:
        return [
            ComplianceCheck(
                key="restore_verified",
                label=_("Restore-Test"),  # pragma: no mutate
                category=ComplianceCategory.BACKUP,  # pragma: no mutate
                status=ComplianceStatus.OK,
                message=_("Letzter Test vor %(age_days)s Tagen.") % {"age_days": age_days},  # pragma: no mutate
                detail=detail,
            )
        ]
    if age_days <= 180:
        return [
            ComplianceCheck(
                key="restore_verified",
                label=_("Restore-Test"),  # pragma: no mutate
                category=ComplianceCategory.BACKUP,  # pragma: no mutate
                status=ComplianceStatus.WARNING,
                message=_("Letzter Test vor %(age_days)s Tagen — Auffrischung empfohlen.")
                % {"age_days": age_days},  # pragma: no mutate
                detail=detail,
                action_hint=_(
                    "Restore gegen frische DB testen, dann mark_restore_verified ausführen."
                ),  # pragma: no mutate # noqa: E501
            )
        ]
    return [
        ComplianceCheck(
            key="restore_verified",
            label=_("Restore-Test"),  # pragma: no mutate
            category=ComplianceCategory.BACKUP,  # pragma: no mutate
            status=ComplianceStatus.CRITICAL,
            message=_("Letzter Test vor %(age_days)s Tagen — DSGVO Art. 32 verletzt.")
            % {"age_days": age_days},  # pragma: no mutate
            detail=detail,
            action_hint=_("Restore-Test sofort nachholen und dokumentieren."),  # pragma: no mutate
        )
    ]
