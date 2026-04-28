"""Service layer for WorkItem-related business logic."""

import logging

from django.db import transaction
from django.utils import timezone

from core.models import Activity, WorkItem
from core.services.activity import log_activity

logger = logging.getLogger(__name__)


@transaction.atomic
def create_workitem(facility, user, *, client=None, **data):
    """Create a work item with activity logging."""
    workitem = WorkItem(facility=facility, created_by=user, client=client, **data)
    workitem.save()
    log_activity(
        facility=facility,
        actor=user,
        verb=Activity.Verb.CREATED,
        target=workitem,
        summary=f"Aufgabe: {workitem.title}",
    )
    return workitem


@transaction.atomic
def update_workitem(workitem, user, **fields):
    """Update a work item with activity logging.

    Accepts allowed fields (item_type, title, description, priority, due_date,
    assigned_to, client) and persists them.
    """
    for key, value in fields.items():
        setattr(workitem, key, value)
    workitem.save()

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
    elif new_status == WorkItem.Status.OPEN and old_status == WorkItem.Status.DONE:
        log_activity(
            facility=workitem.facility,
            actor=user,
            verb=Activity.Verb.REOPENED,
            target=workitem,
            summary=f"Aufgabe wiedereröffnet: {workitem.title}",
        )

    return workitem
