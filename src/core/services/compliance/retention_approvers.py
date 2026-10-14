"""Installation-wide staffing for the two-person manual retention approval."""

from django.db.models import Count, Q
from django.utils.translation import gettext as _

from core.models import Facility, User
from core.services.compliance._types import ComplianceCategory, ComplianceCheck, ComplianceStatus


def _retention_approver_checks() -> list[ComplianceCheck]:
    """Report the least staffed facility; manual request approvers stay separate."""
    from core.services.system import rls_bypass_for_read

    with rls_bypass_for_read():
        rows = list(
            Facility.objects.order_by("name")
            .annotate(
                approvers=Count(
                    "users",
                    filter=Q(
                        users__is_active=True,
                        users__can_confirm_deletion=True,
                        users__role__in=[User.Role.LEAD, User.Role.FACILITY_ADMIN],
                    ),
                )
            )
            .values_list("name", "approvers")
        )
    if not rows:
        return []
    without = [name for name, count in rows if count == 0]
    single = [name for name, count in rows if count == 1]
    parts = []
    if without:
        parts.append(_("Ohne Genehmiger für den Aufbewahrungslauf: %(names)s.") % {"names": ", ".join(without)})
    if single:
        parts.append(
            _("Nur ein Genehmiger für den Aufbewahrungslauf, keine manuelle Löschfreigabe möglich: %(names)s.")
            % {"names": ", ".join(single)}
        )
    status = ComplianceStatus.CRITICAL if without else ComplianceStatus.WARNING if single else ComplianceStatus.OK
    if status == ComplianceStatus.OK:
        parts.append(_("Jede Einrichtung hat mindestens zwei aktive Genehmiger für den Aufbewahrungslauf."))
    return [
        ComplianceCheck(
            key="retention_approvers",
            label=_("Genehmiger für den Aufbewahrungslauf"),
            category=ComplianceCategory.PERMISSIONS,
            status=status,
            message=" ".join(parts),
            detail="; ".join(f"{name}: {count}" for name, count in rows),
            action_hint=None
            if status == ComplianceStatus.OK
            else _(
                "Verwaltung → Zugänge öffnen und je Einrichtung mindestens zwei aktive Konten mit Rolle Leitung "
                "oder Anwendungsbetreuung und dem Recht „Löschbestätigung“ benennen."
            ),
        )
    ]
