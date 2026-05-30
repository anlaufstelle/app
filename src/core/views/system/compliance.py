"""Compliance-Dashboard fuer super_admin (Refs #919)."""

from __future__ import annotations

from collections import OrderedDict, defaultdict

from django.views.generic import TemplateView

from core.services.compliance import ComplianceStatus, aggregate_checks
from core.views.system.mixins import SystemAuditMixin

# Render-Reihenfolge der Kategorien — Backup oben, weil DSGVO Art. 32
# Wiederherstellbarkeit zuerst priorisiert; Versionen ganz unten als Info.
_CATEGORY_ORDER = (
    "Datenbank",
    "Backup",
    "Virus-Scan",
    "Retention",
    "MFA",
    "Audit",
    "System",
)


class SystemComplianceView(SystemAuditMixin, TemplateView):
    """Read-Only Compliance-Dashboard fuer super_admin.

    Aggregiert die elf Compliance-Checks aus :func:`core.services.
    compliance.aggregate_checks` und gruppiert sie nach Kategorie.
    Liefert pro Render eine Summary-Statistik (n_ok, n_warning,
    n_critical, n_unknown) plus die kategorisierte Liste fuer das
    Template.

    Wer hier rein darf:

    - super_admin (via :class:`SystemAuditMixin`).
    - facility_admin **nicht** — die Checks sind installationsweit
      und nicht facility-scoped.

    Audit-Eintrag pro Aufruf: automatisch via SystemAuditMixin
    (``SYSTEM_VIEW`` mit ``target_type="SystemComplianceView"``).
    """

    template_name = "core/system/compliance.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        checks = aggregate_checks()

        summary = {status.value: 0 for status in ComplianceStatus}
        for check in checks:
            summary[check.status.value] += 1

        # Nach Kategorie gruppieren in stabiler Reihenfolge.
        grouped: dict[str, list] = defaultdict(list)
        for check in checks:
            grouped[check.category].append(check)

        ordered_groups: OrderedDict[str, list] = OrderedDict()
        # Erst die bekannten Kategorien in _CATEGORY_ORDER, dann der Rest
        # alphabetisch (falls jemand eine neue Kategorie ergaenzt und sie
        # noch nicht in der Reihenfolge gelistet ist).
        for category in _CATEGORY_ORDER:
            if category in grouped:
                ordered_groups[category] = grouped.pop(category)
        for category in sorted(grouped):
            ordered_groups[category] = grouped[category]

        worst_status = _worst_status(checks)

        context.update(
            {
                "groups": ordered_groups,
                "summary": summary,
                "total_checks": len(checks),
                "worst_status": worst_status,
            }
        )
        return context


def _worst_status(checks) -> str:
    """Liefert den schlechtesten Status (critical > warning > unknown > ok)."""
    priorities = {
        ComplianceStatus.CRITICAL.value: 4,
        ComplianceStatus.WARNING.value: 3,
        ComplianceStatus.UNKNOWN.value: 2,
        ComplianceStatus.OK.value: 1,
    }
    worst = ComplianceStatus.OK.value
    worst_prio = priorities[worst]
    for check in checks:
        prio = priorities.get(check.status.value, 0)
        if prio > worst_prio:
            worst = check.status.value
            worst_prio = prio
    return worst
