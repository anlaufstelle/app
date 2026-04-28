"""Service layer for WorkItem-related business logic."""

import calendar
import logging
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from core.models import Activity, AuditLog, WorkItem
from core.services.activity import log_activity

logger = logging.getLogger(__name__)


def _add_months(source: date, months: int) -> date:
    """Add ``months`` to ``source`` using calendar-aware month arithmetic.

    Behaviour matches ``dateutil.relativedelta(months=+n)``:
    the day is clamped to the last valid day of the target month
    (e.g. 31.01. + 1 month = 28./29.02.).
    """
    month_index = source.month - 1 + months
    target_year = source.year + month_index // 12
    target_month = month_index % 12 + 1
    last_day = calendar.monthrange(target_year, target_month)[1]
    target_day = min(source.day, last_day)
    return date(target_year, target_month, target_day)


def _next_due_date(current: date, recurrence: str) -> date | None:
    """Compute the next due date for a recurrence pattern.

    Returns ``None`` when no duplicate should be scheduled.
    """
    if recurrence == WorkItem.Recurrence.WEEKLY:
        return current + timedelta(days=7)
    if recurrence == WorkItem.Recurrence.MONTHLY:
        return _add_months(current, 1)
    if recurrence == WorkItem.Recurrence.QUARTERLY:
        return _add_months(current, 3)
    if recurrence == WorkItem.Recurrence.YEARLY:
        return _add_months(current, 12)
    return None


def _log_workitem_update(workitem, user, changed_fields):
    """Write a single AuditLog entry for a WorkItem bulk field update."""
    AuditLog.objects.create(
        facility=workitem.facility,
        user=user,
        action=AuditLog.Action.WORKITEM_UPDATE,
        target_type="WorkItem",
        target_id=str(workitem.pk),
        detail={"changed_fields": list(changed_fields), "bulk": True},
    )


@transaction.atomic
def create_workitem(facility, user, *, client=None, **data):
    """Create a work item with activity and audit logging."""
    workitem = WorkItem(facility=facility, created_by=user, client=client, **data)
    workitem.save()
    log_activity(
        facility=facility,
        actor=user,
        verb=Activity.Verb.CREATED,
        target=workitem,
        summary=f"Aufgabe: {workitem.title}",
    )
    AuditLog.objects.create(
        facility=facility,
        user=user,
        action=AuditLog.Action.WORKITEM_CREATE,
        target_type="WorkItem",
        target_id=str(workitem.pk),
    )
    return workitem


@transaction.atomic
def update_workitem(workitem, user, *, expected_updated_at=None, **fields):
    """Update a work item with activity logging and audit logging.

    Accepts allowed fields (item_type, title, description, priority, due_date,
    assigned_to, client) and persists them. Writes an AuditLog entry with the
    names of changed fields (no PII values).

    ``expected_updated_at`` enables optimistic locking (Refs #531) — when the
    DB-side ``updated_at`` differs, a ``ValidationError`` is raised.
    """
    from core.services.locking import check_version_conflict

    check_version_conflict(workitem, expected_updated_at)
    changed_fields = []
    for key, value in fields.items():
        if getattr(workitem, key) != value:
            changed_fields.append(key)
        setattr(workitem, key, value)
    workitem.save()

    if changed_fields:
        AuditLog.objects.create(
            facility=workitem.facility,
            user=user,
            action=AuditLog.Action.WORKITEM_UPDATE,
            target_type="WorkItem",
            target_id=str(workitem.pk),
            detail={"changed_fields": changed_fields},
        )

    log_activity(
        facility=workitem.facility,
        actor=user,
        verb=Activity.Verb.UPDATED,
        target=workitem,
        summary=f"Aufgabe aktualisiert: {workitem.title}",
    )

    return workitem


def update_workitem_status(workitem, new_status, user):
    """Perform a status transition including auto-assign and completed_at management.

    - On IN_PROGRESS: auto-assign to the user if not yet assigned.
    - On DONE/DISMISSED: set completed_at.
    - On back-transition: reset completed_at.
    - On DONE with ``recurrence != NONE``: auto-duplicate the work item
      with the next due date (Refs #266).
    """
    old_status = workitem.status
    workitem.status = new_status

    if new_status == WorkItem.Status.IN_PROGRESS and not workitem.assigned_to:
        workitem.assigned_to = user

    if new_status in (WorkItem.Status.DONE, WorkItem.Status.DISMISSED):
        workitem.completed_at = timezone.now()
    elif workitem.completed_at:
        workitem.completed_at = None

    workitem.save()

    if new_status == WorkItem.Status.DONE:
        log_activity(
            facility=workitem.facility,
            actor=user,
            verb=Activity.Verb.COMPLETED,
            target=workitem,
            summary=f"Aufgabe erledigt: {workitem.title}",
        )
        if workitem.recurrence and workitem.recurrence != WorkItem.Recurrence.NONE:
            duplicate_recurring_workitem(workitem, user)
    elif new_status == WorkItem.Status.OPEN and old_status == WorkItem.Status.DONE:
        log_activity(
            facility=workitem.facility,
            actor=user,
            verb=Activity.Verb.REOPENED,
            target=workitem,
            summary=f"Aufgabe wiedereröffnet: {workitem.title}",
        )

    return workitem


@transaction.atomic
def duplicate_recurring_workitem(workitem, user):
    """Create a follow-up WorkItem for a recurring task (Refs #266, #596).

    Copies relevant fields (title, description, priority, assigned_to, client,
    item_type, facility, recurrence, remind_at) and bumps ``due_date`` by the
    recurrence interval. If the source has no ``due_date`` the interval is
    applied to today so the new item still has a valid deadline.

    Idempotency (Refs #596): If the source WorkItem already carries a
    ``recurrence_duplicated_at`` marker, the follow-up has already been
    generated during an earlier DONE transition — the call is a no-op and
    returns ``None``. This prevents duplicate follow-ups on Done→Open→Done
    toggles and on bulk DONE paths that re-process items.

    Returns the newly created WorkItem, or ``None`` if ``recurrence`` is NONE
    or unknown, or if the source is already marked as duplicated.
    """
    if not workitem.recurrence or workitem.recurrence == WorkItem.Recurrence.NONE:
        return None

    # Refs #596: Idempotency guard — skip if a follow-up was already produced.
    if workitem.recurrence_duplicated_at is not None:
        return None

    reference_date = workitem.due_date or timezone.localdate()
    new_due_date = _next_due_date(reference_date, workitem.recurrence)
    if new_due_date is None:
        return None

    new_remind_at = None
    if workitem.remind_at and workitem.due_date:
        # Preserve the reminder offset relative to the due date.
        offset = workitem.due_date - workitem.remind_at
        new_remind_at = new_due_date - offset

    new_workitem = WorkItem.objects.create(
        facility=workitem.facility,
        created_by=user,
        client=workitem.client,
        assigned_to=workitem.assigned_to,
        item_type=workitem.item_type,
        title=workitem.title,
        description=workitem.description,
        priority=workitem.priority,
        due_date=new_due_date,
        remind_at=new_remind_at,
        recurrence=workitem.recurrence,
        status=WorkItem.Status.OPEN,
    )
    log_activity(
        facility=workitem.facility,
        actor=user,
        verb=Activity.Verb.CREATED,
        target=new_workitem,
        summary=f"Wiederkehrende Folgeaufgabe: {new_workitem.title}",
    )
    # Refs #596: Mark the source so a subsequent DONE transition does not
    # duplicate again. Persist within the same atomic block.
    workitem.recurrence_duplicated_at = timezone.now()
    workitem.save(update_fields=["recurrence_duplicated_at"])
    return new_workitem


@transaction.atomic
def bulk_update_workitem_status(workitems, user, status):
    """Bulk-update the status of multiple WorkItems (Refs #267, #593).

    Writes one AuditLog entry per changed WorkItem and respects completed_at
    semantics (set on DONE/DISMISSED, cleared otherwise).

    On bulk DONE transitions (Refs #593): for items with a non-NONE recurrence,
    a follow-up WorkItem is generated via ``duplicate_recurring_workitem`` —
    analogous to the single-update path in ``update_workitem_status``. The
    duplicate call is guarded by the ``recurrence_duplicated_at`` marker
    (Refs #596), so re-processing the same batch stays idempotent.

    Returns the number of processed items.
    """
    valid_statuses = {s.value for s in WorkItem.Status}
    if status not in valid_statuses:
        raise ValueError(f"Invalid status: {status}")

    count = 0
    for workitem in workitems:
        if workitem.status == status:
            continue
        workitem.status = status
        if status in (WorkItem.Status.DONE, WorkItem.Status.DISMISSED):
            workitem.completed_at = timezone.now()
        elif workitem.completed_at:
            workitem.completed_at = None
        workitem.save(update_fields=["status", "completed_at", "updated_at"])
        _log_workitem_update(workitem, user, ["status"])
        # Refs #593: Align bulk DONE with single-update path — trigger
        # recurrence duplication for recurring items. Idempotency (Refs #596)
        # is enforced inside duplicate_recurring_workitem via the
        # ``recurrence_duplicated_at`` marker.
        if status == WorkItem.Status.DONE and workitem.recurrence and workitem.recurrence != WorkItem.Recurrence.NONE:
            duplicate_recurring_workitem(workitem, user)
        count += 1
    return count


@transaction.atomic
def bulk_update_workitem_priority(workitems, user, priority):
    """Bulk-update the priority of multiple WorkItems (Refs #267).

    Returns the number of processed items.
    """
    valid_priorities = {p.value for p in WorkItem.Priority}
    if priority not in valid_priorities:
        raise ValueError(f"Invalid priority: {priority}")

    count = 0
    for workitem in workitems:
        if workitem.priority == priority:
            continue
        workitem.priority = priority
        workitem.save(update_fields=["priority", "updated_at"])
        _log_workitem_update(workitem, user, ["priority"])
        count += 1
    return count


@transaction.atomic
def bulk_assign_workitems(workitems, user, assignee_or_none):
    """Bulk-assign WorkItems to a user (or clear the assignment).

    ``assignee_or_none=None`` removes the assignment. Returns the processed count.
    """
    count = 0
    for workitem in workitems:
        if workitem.assigned_to_id == (assignee_or_none.pk if assignee_or_none else None):
            continue
        workitem.assigned_to = assignee_or_none
        workitem.save(update_fields=["assigned_to", "updated_at"])
        _log_workitem_update(workitem, user, ["assigned_to"])
        count += 1
    return count
