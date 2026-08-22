"""Case-Service Subpackage (Refs #959).

Buendelt alle Case-/Goal-/WorkItem-/Handover-bezogenen Services, die
vorher als vier flache Module unter ``services/`` lagen.

Module:

- :mod:`.cases`     — Case-CRUD, Event-Zuordnung (vorher services/cases.py).
- :mod:`.deletion`  — Papierkorb, Vier-Augen-Antrag, Fristablauf-Tilgung
  (Refs #1818, ADR-044).
- :mod:`.goals`     — Goal/Milestone-CRUD (vorher services/goals.py).
- :mod:`.workitems` — WorkItem-CRUD, Status-Workflow, Wiederkehrlogik,
  Bulk-Operationen (vorher services/workitems.py).
- :mod:`.handover`  — Schicht-/Tages-Uebergabe-Summary (vorher
  services/handover.py).
"""

from core.services.case.cases import (
    assign_event_to_case,
    cases_with_live_client,
    close_case,
    create_case,
    get_case_or_none,
    remove_event_from_case,
    reopen_case,
    update_case,
)
from core.services.case.deletion import (
    anonymize_case,
    anonymize_case_now,
    anonymize_eligible_soft_deleted_cases,
    approve_case_deletion,
    case_requires_four_eyes,
    get_pending_case_deletion_request,
    pending_deletion_case_ids,
    redact_case_content,
    redact_case_goals,
    reject_case_deletion,
    request_case_deletion,
    restore_case,
    soft_delete_case,
)
from core.services.case.goals import (
    achieve_goal,
    create_goal,
    create_milestone,
    delete_milestone,
    toggle_milestone,
    unachieve_goal,
    update_goal,
)
from core.services.case.handover import (
    _collect_highlights,
    _collect_stats,
    build_handover_summary,
)
from core.services.case.workitems import (
    _add_months,
    _apply_status_transition,
    _log_workitem_update,
    _maybe_duplicate_recurring,
    _next_due_date,
    bulk_assign_workitems,
    bulk_update_workitem_priority,
    bulk_update_workitem_status,
    create_workitem,
    duplicate_recurring_workitem,
    update_workitem,
    update_workitem_status,
)

__all__ = [
    "_add_months",
    "_apply_status_transition",
    "_collect_highlights",
    "_collect_stats",
    "_log_workitem_update",
    "_maybe_duplicate_recurring",
    "_next_due_date",
    "achieve_goal",
    "anonymize_case",
    "anonymize_case_now",
    "anonymize_eligible_soft_deleted_cases",
    "approve_case_deletion",
    "assign_event_to_case",
    "build_handover_summary",
    "bulk_assign_workitems",
    "bulk_update_workitem_priority",
    "bulk_update_workitem_status",
    "case_requires_four_eyes",
    "cases_with_live_client",
    "close_case",
    "create_case",
    "create_goal",
    "create_milestone",
    "create_workitem",
    "delete_milestone",
    "duplicate_recurring_workitem",
    "get_case_or_none",
    "get_pending_case_deletion_request",
    "pending_deletion_case_ids",
    "redact_case_content",
    "redact_case_goals",
    "reject_case_deletion",
    "remove_event_from_case",
    "reopen_case",
    "request_case_deletion",
    "restore_case",
    "soft_delete_case",
    "toggle_milestone",
    "unachieve_goal",
    "update_case",
    "update_goal",
    "update_workitem",
    "update_workitem_status",
]
