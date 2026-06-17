"""Case-Service Subpackage (Refs #959).

Buendelt alle Case-/Goal-/WorkItem-/Handover-bezogenen Services, die
vorher als vier flache Module unter ``services/`` lagen.

Module:

- :mod:`.cases`     — Case-CRUD, Event-Zuordnung (vorher services/cases.py).
- :mod:`.goals`     — Goal/Milestone-CRUD (vorher services/goals.py).
- :mod:`.workitems` — WorkItem-CRUD, Status-Workflow, Wiederkehrlogik,
  Bulk-Operationen (vorher services/workitems.py).
- :mod:`.handover`  — Schicht-/Tages-Uebergabe-Summary (vorher
  services/handover.py).
"""

from core.services.case.cases import (
    assign_event_to_case,
    close_case,
    create_case,
    remove_event_from_case,
    reopen_case,
    update_case,
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
    "assign_event_to_case",
    "build_handover_summary",
    "bulk_assign_workitems",
    "bulk_update_workitem_priority",
    "bulk_update_workitem_status",
    "close_case",
    "create_case",
    "create_goal",
    "create_milestone",
    "create_workitem",
    "delete_milestone",
    "duplicate_recurring_workitem",
    "remove_event_from_case",
    "reopen_case",
    "toggle_milestone",
    "unachieve_goal",
    "update_case",
    "update_goal",
    "update_workitem",
    "update_workitem_status",
]
