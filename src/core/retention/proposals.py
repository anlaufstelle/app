"""RetentionProposal-Lifecycle (#744).

API-Grenze fuer Vorschlags-Verwaltung: Erzeugen, Approve/Defer/Reject,
Bulk-Operationen, Dashboard-Aufbereitung, Reactivation,
Stale-Cleanup.

**Phase 1 (#744):** Re-Exports aus :mod:`core.services.retention`.
Physische Verschiebung des Codes in dieses Modul ist Phase 2 — die
API-Grenze besteht bereits, neue Aufrufer sollen ueber dieses Modul
importieren.
"""

from core.services.retention import (
    DASHBOARD_CATEGORY_LABELS,
    annotate_urgency,
    approve_proposal,
    build_proposal_details,
    build_retention_dashboard_context,
    bulk_approve_proposals,
    bulk_defer_proposals,
    bulk_reject_proposals,
    cleanup_stale_proposals,
    create_proposal,
    create_proposals_for_facility,
    defer_proposal,
    get_dashboard_proposals,
    reactivate_deferred_proposals,
    reject_proposal,
)

__all__ = [
    "DASHBOARD_CATEGORY_LABELS",
    "annotate_urgency",
    "approve_proposal",
    "build_proposal_details",
    "build_retention_dashboard_context",
    "bulk_approve_proposals",
    "bulk_defer_proposals",
    "bulk_reject_proposals",
    "cleanup_stale_proposals",
    "create_proposal",
    "create_proposals_for_facility",
    "defer_proposal",
    "get_dashboard_proposals",
    "reactivate_deferred_proposals",
    "reject_proposal",
]
