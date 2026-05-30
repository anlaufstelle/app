"""SystemDashboardView — Cross-Facility-Dashboard fuer super_admin."""

from django.views.generic import TemplateView

import core.services.system.health as system_health
from core.models import AuditLog, Facility, Organization
from core.models.user import User
from core.services.compliance import ComplianceStatus, cron_job_checks
from core.views.system.mixins import SystemAuditMixin


class SystemDashboardView(SystemAuditMixin, TemplateView):
    """Cross-facility-Dashboard fuer super_admin.

    Zeigt die Organisation, alle Einrichtungen sowie Cross-Facility-Counts
    (User pro Rolle, AuditLog-Total). Der View ist Read-Only — Mutationen
    laufen weiterhin ueber das Django-Admin (``/admin-mgmt/``).
    """

    template_name = "core/system/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Aktuelles Datenmodell geht von einer Organisation pro Installation
        # aus. ``.first()`` ist tolerant gegenueber leerer DB (z.B. fresh
        # Setup vor dem Seed) und zeigt dann ``None`` im Template.
        organization = Organization.objects.first()
        facilities = Facility.objects.select_related("organization").order_by("name")

        # User-Counts pro Rolle, in der Reihenfolge der Role-Choices.
        role_counts = []
        for value, label in User.Role.choices:
            role_counts.append(
                {
                    "value": value,
                    "label": label,
                    "count": User.objects.filter(role=value).count(),
                }
            )

        # Refs #871: Health-Card oben im Dashboard. Die Pruefungen sind
        # defensiv geschrieben — Fehler in einem Subcheck duerfen das
        # Dashboard-Render nicht kippen.
        pending = system_health.pending_migrations()
        health = {
            "db": system_health.check_database(),
            "migrations_pending": pending,
            "migrations_pending_count": len(pending),
            "disk": system_health.disk_usage(),
            "backup": system_health.last_backup_info(),
            "versions": system_health.app_versions(),
        }

        context.update(
            {
                "organization": organization,
                "facilities": facilities,
                "facilities_count": facilities.count(),
                "role_counts": role_counts,
                "total_users": User.objects.count(),
                "auditlog_total": AuditLog.objects.count(),
                "health": health,
                "cron_jobs": _cron_job_summary(),
            }
        )
        return context


def _cron_job_summary() -> dict:
    """Refs #977: kompakte Aggregat-Sicht der Hintergrundjob-Last-Run-Checks.

    Buendelt die fuenf Cron-Checks aus :func:`core.services.compliance.
    cron_job_checks` und liefert pro Status einen Zaehler plus den
    schlechtesten Gesamt-Status fuer die Ampel auf der /system/-Uebersicht.
    """
    checks = cron_job_checks()
    counts = {status.value: 0 for status in ComplianceStatus}
    for check in checks:
        counts[check.status.value] += 1

    # Worst-Status nach Prioritaet (critical > warning > unknown > ok) —
    # bestimmt die Farbe des Gesamt-Indikators.
    if counts[ComplianceStatus.CRITICAL.value]:
        worst = ComplianceStatus.CRITICAL.value
    elif counts[ComplianceStatus.WARNING.value]:
        worst = ComplianceStatus.WARNING.value
    elif counts[ComplianceStatus.UNKNOWN.value]:
        worst = ComplianceStatus.UNKNOWN.value
    else:
        worst = ComplianceStatus.OK.value

    return {
        "checks": checks,
        "total": len(checks),
        "ok": counts[ComplianceStatus.OK.value],
        # „ueberfaellig" = warning + critical (ein Job laeuft nicht wie erwartet).
        "overdue": counts[ComplianceStatus.WARNING.value] + counts[ComplianceStatus.CRITICAL.value],
        "unknown": counts[ComplianceStatus.UNKNOWN.value],
        "worst": worst,
    }
