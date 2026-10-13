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

from django.utils.translation import gettext as _

from core.models import AuditLog
from core.services import ops_thresholds
from core.services.compliance import _clock
from core.services.compliance._types import ComplianceCategory, ComplianceCheck, ComplianceStatus

_CATEGORY = ComplianceCategory.BACKGROUND_JOBS


def _cron_action_hint() -> str:
    return _("docker compose -f docker-compose.prod.yml logs cron prüfen (Ops-Runbook § 3.2).")


def _latest(action: str) -> AuditLog | None:
    entries = AuditLog.objects.filter(action=action, facility__isnull=True)
    if action in {AuditLog.Action.SNAPSHOT_RUN_COMPLETED, AuditLog.Action.BREACH_SCAN_COMPLETED}:
        entries = entries.filter(
            detail__job_run__scope="installation",
            detail__job_run__status="success",
            detail__job_run__skipped=0,
            detail__job_run__failed=0,
        )
    return entries.order_by("-timestamp").only("timestamp", "detail").first()


def _age_hours(entry: AuditLog) -> float:
    return (_clock.now() - entry.timestamp).total_seconds() / 3600.0


def _snapshot_checks() -> list[ComplianceCheck]:
    """Statistik-Snapshots — monatlicher Cron (1. des Monats)."""
    key, label = "snapshot_last_run", _("Statistik-Snapshots")
    entry = _latest(AuditLog.Action.SNAPSHOT_RUN_COMPLETED)
    if entry is None:
        return [
            ComplianceCheck(
                key=key,
                label=label,
                category=_CATEGORY,
                status=ComplianceStatus.UNKNOWN,
                message=_("Noch kein Snapshot-Lauf — Cron lief vielleicht nie."),  # pragma: no mutate
                action_hint=_cron_action_hint(),
            )
        ]
    age_days = (_clock.now() - entry.timestamp).days
    detail = _("Letzter Lauf: %(timestamp)s") % {"timestamp": f"{entry.timestamp:%Y-%m-%d %H:%M}"}
    if age_days <= 35:
        return [
            ComplianceCheck(
                key=key,
                label=label,
                category=_CATEGORY,
                status=ComplianceStatus.OK,
                message=_("Letzter Lauf vor %(age_days)s Tag(en).") % {"age_days": age_days},  # pragma: no mutate
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
                message=_("Letzter Lauf vor %(age_days)s Tagen — Monats-Cron prüfen.")
                % {"age_days": age_days},  # pragma: no mutate
                detail=detail,
                action_hint=_("Cron muss monatlich laufen (1. des Monats)."),  # pragma: no mutate
            )
        ]
    return [
        ComplianceCheck(
            key=key,
            label=label,
            category=_CATEGORY,
            status=ComplianceStatus.CRITICAL,
            message=_("Letzter Lauf vor %(age_days)s Tagen — Cron ausgefallen?")
            % {"age_days": age_days},  # pragma: no mutate
            detail=detail,
            action_hint=_cron_action_hint(),
        )
    ]


def _breach_scan_checks() -> list[ComplianceCheck]:
    """Breach-Detection — stündlicher Cron."""
    key, label = "breach_scan_last_run", _("Breach-Detection-Scan")
    entry = _latest(AuditLog.Action.BREACH_SCAN_COMPLETED)
    if entry is None:
        return [
            ComplianceCheck(
                key=key,
                label=label,
                category=_CATEGORY,
                status=ComplianceStatus.UNKNOWN,
                message=_("Noch kein Breach-Scan — Cron lief vielleicht nie."),  # pragma: no mutate
                action_hint=_cron_action_hint(),
            )
        ]
    age = _age_hours(entry)
    detail = _("Letzter Scan: %(timestamp)s") % {"timestamp": f"{entry.timestamp:%Y-%m-%d %H:%M}"}
    if age <= 3:
        return [
            ComplianceCheck(
                key=key,
                label=label,
                category=_CATEGORY,
                status=ComplianceStatus.OK,
                message=_("Letzter Scan vor %(age)sh.") % {"age": f"{age:.0f}"},  # pragma: no mutate
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
                message=_("Letzter Scan vor %(age)sh — Cron prüfen.") % {"age": f"{age:.0f}"},  # pragma: no mutate
                detail=detail,
                action_hint=_("Cron muss stündlich laufen."),  # pragma: no mutate
            )
        ]
    return [
        ComplianceCheck(
            key=key,
            label=label,
            category=_CATEGORY,
            status=ComplianceStatus.CRITICAL,
            message=_("Letzter Scan vor %(age)sh — Cron ausgefallen?") % {"age": f"{age:.0f}"},  # pragma: no mutate
            detail=detail,
            action_hint=_cron_action_hint(),
        )
    ]


def _mv_refresh_checks() -> list[ComplianceCheck]:
    """Statistik-Materialized-View — stündlicher Refresh."""
    key, label = "mv_refresh_last_run", _("Statistik-View-Refresh")
    entry = _latest(AuditLog.Action.MV_REFRESH_COMPLETED)
    if entry is None:
        return [
            ComplianceCheck(
                key=key,
                label=label,
                category=_CATEGORY,
                status=ComplianceStatus.UNKNOWN,
                message=_("Noch kein View-Refresh — Cron lief vielleicht nie."),  # pragma: no mutate
                action_hint=_cron_action_hint(),
            )
        ]
    age = _age_hours(entry)
    detail = _("Letzter Refresh: %(timestamp)s") % {"timestamp": f"{entry.timestamp:%Y-%m-%d %H:%M}"}
    if age <= 2:
        return [
            ComplianceCheck(
                key=key,
                label=label,
                category=_CATEGORY,
                status=ComplianceStatus.OK,
                message=_("Letzter Refresh vor %(age)sh.") % {"age": f"{age:.0f}"},  # pragma: no mutate
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
                message=_("Letzter Refresh vor %(age)sh — Dashboard evtl. veraltet.")
                % {"age": f"{age:.0f}"},  # pragma: no mutate
                detail=detail,
                action_hint=_("Cron muss stündlich laufen."),  # pragma: no mutate
            )
        ]
    return [
        ComplianceCheck(
            key=key,
            label=label,
            category=_CATEGORY,
            status=ComplianceStatus.CRITICAL,
            message=_("Letzter Refresh vor %(age)sh — Cron ausgefallen?") % {"age": f"{age:.0f}"},  # pragma: no mutate
            detail=detail,
            action_hint=_cron_action_hint(),
        )
    ]


def _audit_chain_checks() -> list[ComplianceCheck]:
    """Audit-Ketten-Verifikation — taeglicher Cron 03:30 (Refs #1070, #1872).

    ``verify_audit_chain`` meldete einen gruenen Lauf bisher ausschliesslich
    ueber den Exit-Code an supercronic. Ob die HMAC-Kette des AuditLog
    ueberhaupt je geprueft wurde, war damit **nirgends** im System sichtbar —
    genau die Luecke aus #1872. Der Marker ``AUDIT_CHAIN_VERIFIED`` wird nur
    nach vollstaendig gruenem Lauf geschrieben; ein Tamper-Fund beendet den
    Command vorher. Ein alter Marker heisst also entweder „Cron aus" oder
    „Lauf bricht seit Tagen rot ab" — beides gehoert eskaliert.

    Bewusst nur Anzeige, **kein** Pruef-Button im Request: ein voller
    Kettendurchlauf skaliert mit der Zahl der AuditLog-Zeilen und liefe mit
    wachsender Kette in den Gateway-Timeout (siehe ``services/hubs.py``); eine
    Job-Queue gibt es im Projekt nicht.
    """
    key, label = "audit_chain_last_run", _("Audit-Ketten-Prüfung")
    entry = _latest(AuditLog.Action.AUDIT_CHAIN_VERIFIED)
    if entry is None:
        return [
            ComplianceCheck(
                key=key,
                label=label,
                category=_CATEGORY,
                status=ComplianceStatus.UNKNOWN,
                message=_("Noch keine Ketten-Prüfung — Cron lief vielleicht nie."),  # pragma: no mutate
                action_hint=_cron_action_hint(),
            )
        ]
    age = _age_hours(entry)
    detail = _("Letzte Prüfung: %(timestamp)s") % {"timestamp": f"{entry.timestamp:%Y-%m-%d %H:%M}"}
    if age <= ops_thresholds.AUDIT_CHAIN_WARN_HOURS:
        return [
            ComplianceCheck(
                key=key,
                label=label,
                category=_CATEGORY,
                status=ComplianceStatus.OK,
                message=_("Letzte Prüfung vor %(age)sh.") % {"age": f"{age:.0f}"},  # pragma: no mutate
                detail=detail,
            )
        ]
    if age <= ops_thresholds.AUDIT_CHAIN_CRITICAL_HOURS:
        return [
            ComplianceCheck(
                key=key,
                label=label,
                category=_CATEGORY,
                status=ComplianceStatus.WARNING,
                message=_("Letzte Prüfung vor %(age)sh — ein Tageslauf fehlt.")
                % {"age": f"{age:.0f}"},  # pragma: no mutate
                detail=detail,
                action_hint=_cron_action_hint(),
            )
        ]
    return [
        ComplianceCheck(
            key=key,
            label=label,
            category=_CATEGORY,
            status=ComplianceStatus.CRITICAL,
            message=_("Letzte Prüfung vor %(age)sh — Cron aus oder Lauf bricht rot ab.")
            % {"age": f"{age:.0f}"},  # pragma: no mutate
            detail=detail,
            action_hint=_cron_action_hint(),
        )
    ]
