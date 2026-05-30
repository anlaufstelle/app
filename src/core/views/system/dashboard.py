"""SystemDashboardView — Cross-Facility-Dashboard fuer super_admin."""

from django.views.generic import TemplateView

from core.models import AuditLog, Facility, Organization
from core.models.user import User
from core.services import system_health
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
            }
        )
        return context
