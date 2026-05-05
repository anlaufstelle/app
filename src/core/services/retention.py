"""Re-Export-Stub fuer Bestands-Aufrufer (#744 Phase 2).

Die Retention-Logik wurde in das Submodul :mod:`core.retention`
verschoben:

- :mod:`core.retention.audit_pruning` — AuditLog-Pruning.
- :mod:`core.retention.anonymization` — Klient-Anonymisierungs-Trigger.
- :mod:`core.retention.legal_holds` — LegalHold-Lifecycle.
- :mod:`core.retention.proposals` — RetentionProposal-Lifecycle
  (CRUD, Bulk, Dashboard, Reactivate, Cleanup, Vorschlags-Pipeline).
- :mod:`core.retention.enforcement` — Soft-Delete-Strategien
  (anonymous, identified, qualified, document_type) + Pipeline.

Diese Datei re-exportiert alle public Symbole, damit Bestands-Aufrufer
(``management/commands/enforce_retention.py``, Tests, andere Services)
ohne Aenderungen weiterlaufen. Neue Aufrufer sollen direkt aus
``core.retention.<modul>`` importieren.
"""

from core.retention.anonymization import anonymize_clients
from core.retention.audit_pruning import prune_auditlog
from core.retention.enforcement import (
    _soft_delete_events,
    collect_doomed_events,
    enforce_activities,
    enforce_anonymous,
    enforce_document_type_retention,
    enforce_identified,
    enforce_qualified,
    process_facility_retention,
)
from core.retention.legal_holds import (
    create_legal_hold,
    dismiss_legal_hold,
    get_active_hold_target_ids,
    has_active_hold,
)
from core.retention.proposals import (
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
    "anonymize_clients",
    "approve_proposal",
    "build_proposal_details",
    "build_retention_dashboard_context",
    "bulk_approve_proposals",
    "bulk_defer_proposals",
    "bulk_reject_proposals",
    "cleanup_stale_proposals",
    "collect_doomed_events",
    "create_legal_hold",
    "create_proposal",
    "create_proposals_for_facility",
    "defer_proposal",
    "dismiss_legal_hold",
    "enforce_activities",
    "enforce_anonymous",
    "enforce_document_type_retention",
    "enforce_identified",
    "enforce_qualified",
    "get_active_hold_target_ids",
    "get_dashboard_proposals",
    "has_active_hold",
    "process_facility_retention",
    "prune_auditlog",
    "reactivate_deferred_proposals",
    "reject_proposal",
]
