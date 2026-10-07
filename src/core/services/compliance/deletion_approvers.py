"""Approvers for deletion requests per facility.

A deletion request is completed by a second person with ``can_confirm_deletion``;
nobody approves or rejects their own request. Without an active approver no request
of the facility can be completed, with exactly one that person's own requests stay
open. The demo is evaluated like any installation: its seed gives every facility
two approvers, so a red or yellow card there points at a real seed gap.
"""

from __future__ import annotations

from django.db.models import Count, Q
from django.utils.translation import gettext as _

from core.models import Facility
from core.services.compliance._types import ComplianceCheck, ComplianceStatus

MIN_APPROVERS = 2


def _deletion_approver_checks() -> list[ComplianceCheck]:
    """One card: the facility with the fewest active approvers decides the status."""
    # Local import avoids the compliance -> system -> compliance cycle at start-up.
    from core.services.system import rls_bypass_for_read

    with rls_bypass_for_read():
        rows = list(
            Facility.objects.order_by("name")
            .annotate(
                approvers=Count("users", filter=Q(users__is_active=True, users__can_confirm_deletion=True)),
            )
            .values_list("name", "approvers")
        )
    if not rows:
        return []

    without = [name for name, count in rows if count == 0]
    single = [name for name, count in rows if count == 1]
    detail = "; ".join(f"{name}: {count}" for name, count in rows)
    parts = []
    if without:
        parts.append(_("Ohne Genehmiger: %(names)s.") % {"names": ", ".join(without)})
    if single:
        parts.append(
            _("Nur ein Genehmiger, eigene Anträge nicht genehmigbar: %(names)s.") % {"names": ", ".join(single)}
        )
    if without:
        status = ComplianceStatus.CRITICAL
    elif single:
        status = ComplianceStatus.WARNING
    else:
        status = ComplianceStatus.OK
        parts.append(_("Jede Einrichtung hat mindestens zwei aktive Genehmiger für Löschanträge."))
    return [
        ComplianceCheck(
            key="deletion_approvers",
            label="Genehmiger für Löschanträge",  # pragma: no mutate
            category="Berechtigungen",  # pragma: no mutate
            status=status,
            message=" ".join(parts),
            detail=detail,
            action_hint=None
            if status == ComplianceStatus.OK
            else _(
                "Verwaltung → Zugänge öffnen und mindestens zwei aktiven Konten das Recht „Löschbestätigung“ geben."
            ),
        )
    ]
