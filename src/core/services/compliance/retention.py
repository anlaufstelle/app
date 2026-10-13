"""Retention-Lauf-Check fuer Compliance-Dashboard (Refs #919, #958-M3).

Letzter ``RETENTION_RUN_COMPLETED``-Audit-Log-Eintrag, geschrieben vom
``enforce_retention``-Command nach jedem Lauf.

Refs #2413: on the public demo (``DEMO_MODE``) the retention card explains that
the job does not run there instead of pointing at the cron job.
"""

from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext as _

from core.models import AuditLog, Facility
from core.services import ops_thresholds
from core.services.compliance import _clock
from core.services.compliance._types import ComplianceCategory, ComplianceCheck, ComplianceStatus


def _retention_config_checks() -> list[ComplianceCheck]:
    """Warnt aggregiert vor unbegrenzter AuditLog-Aufbewahrung (Refs #2053)."""
    # Lokaler Import vermeidet den bestehenden Zyklus
    # compliance -> system.bans -> compliance beim Django-Start.
    from core.services.system import rls_bypass_for_read

    # Der Compliance-Service laeuft auch aus Cron-/Command-Kontexten ohne
    # Super-Admin-GUC. Facility und Settings sind RLS-geschuetzt; ohne den
    # expliziten lesenden Bypass waere die Installationssicht dort leer.
    with rls_bypass_for_read():
        facilities = list(
            Facility.objects.order_by("name").values_list(
                "name",
                "settings__auditlog_retention_months",
            )
        )

    unlimited = [name for name, months in facilities if months == 0]
    if not unlimited:
        return []

    count = len(unlimited)
    total = len(facilities)
    names = ", ".join(unlimited)
    message = _(
        "%(count)s von %(total)s Einrichtungen speichern das Audit-Log unbegrenzt (Aufbewahrung 0 Monate): %(names)s"
    ) % {"count": count, "total": total, "names": names}  # noqa: E501
    action_hint = _(
        "Verwaltung → Einstellungen öffnen und unter Datenschutz und Fristen "
        "eine begrenzte AuditLog-Aufbewahrung festlegen."
    )
    return [
        ComplianceCheck(
            key="auditlog_retention_unlimited",
            label=_("Audit-Log-Aufbewahrung"),  # pragma: no mutate
            category=ComplianceCategory.RETENTION,  # pragma: no mutate
            status=ComplianceStatus.WARNING,
            message=message,
            action_hint=action_hint,
        )
    ]


def _demo_retention_card() -> ComplianceCheck:
    """Card text on the demo (Refs #2413).

    The demo reset never runs ``enforce_retention``: on a fresh demo seed it
    would soft-delete the seeded anonymous events beyond the retention period
    and change the demo areas every hour. Status stays ``unknown`` whatever the
    log contains: without a real run there is no green, and there is no
    operator hint because there is nothing a visitor could set up.
    """
    return ComplianceCheck(
        key="retention_last_run",
        label=_("Retention-Lauf"),  # pragma: no mutate
        category=ComplianceCategory.RETENTION,  # pragma: no mutate
        status=ComplianceStatus.UNKNOWN,
        message=_(
            "In der Demo nicht eingerichtet: der Löschlauf würde die stündlich neu erzeugten Beispieldaten löschen."
        ),
    )


def _retention_checks() -> list[ComplianceCheck]:
    """Retention-Cron LastRun aus AuditLog ``RETENTION_RUN_COMPLETED``."""
    if getattr(settings, "DEMO_MODE", False):
        return [_demo_retention_card()]
    entry = (
        AuditLog.objects.filter(
            action=AuditLog.Action.RETENTION_RUN_COMPLETED,
            facility__isnull=True,
            detail__job_run__scope="installation",
            detail__job_run__status="success",
            detail__job_run__skipped=0,
            detail__job_run__failed=0,
        )
        .order_by("-timestamp")
        .only("timestamp", "detail")
        .first()
    )
    if entry is None:
        return [
            ComplianceCheck(
                key="retention_last_run",
                label=_("Retention-Lauf"),  # pragma: no mutate
                category=ComplianceCategory.RETENTION,  # pragma: no mutate
                status=ComplianceStatus.UNKNOWN,
                message=_("Noch kein RETENTION_RUN_COMPLETED-Eintrag — Cron lief vielleicht nie."),  # pragma: no mutate
                action_hint=_("enforce_retention-Cron prüfen (Ops-Runbook §6)."),  # pragma: no mutate
            )
        ]
    # Refs #1872: ``enforce_retention`` laeuft laut ``deploy/crontab`` taeglich
    # um 03:00. Die frueheren Tages-Schwellen (OK <= 7 d, WARNING <= 14 d)
    # zeigten einen seit knapp einer Woche toten Cron noch gruen — bei einem
    # Job, an dem die DSFA-Zusage zu Loeschfristen haengt. Jetzt dieselbe
    # 1,5x/2x-Intervall-Regel wie Backup und Audit-Ketten-Pruefung.
    age_hours = (_clock.now() - entry.timestamp).total_seconds() / 3600.0
    detail = _("Letzter Lauf: %(timestamp)s") % {"timestamp": f"{entry.timestamp:%Y-%m-%d %H:%M}"}
    if age_hours <= ops_thresholds.RETENTION_WARN_HOURS:
        status = ComplianceStatus.OK
        message = _("Letzter Lauf vor %(age_hours)sh.") % {"age_hours": f"{age_hours:.0f}"}
        hint = None
    elif age_hours <= ops_thresholds.RETENTION_CRITICAL_HOURS:
        status = ComplianceStatus.WARNING
        message = _("Letzter Lauf vor %(age_hours)sh — ein Tageslauf fehlt.") % {"age_hours": f"{age_hours:.0f}"}
        hint = _("Cron muss täglich laufen (deploy/crontab: 0 3 * * *).")
    else:
        status = ComplianceStatus.CRITICAL
        message = _("Letzter Lauf vor %(age_hours)sh — mindestens zwei Läufe ausgefallen.") % {
            "age_hours": f"{age_hours:.0f}"
        }
        hint = _("Cron-Status im Container prüfen + manuell ausführen.")
    return [
        ComplianceCheck(
            key="retention_last_run",
            label=_("Retention-Lauf"),  # pragma: no mutate
            category=ComplianceCategory.RETENTION,  # pragma: no mutate
            status=status,
            message=message,
            detail=detail,
            action_hint=hint,
        )
    ]
