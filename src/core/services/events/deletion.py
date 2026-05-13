"""4-Augen-Loeschungs-Workflow fuer Events (Refs #777).

``request_deletion`` legt einen ``DeletionRequest`` an (idempotent fuer
PENDING-Antraege), ``approve_deletion`` und ``reject_deletion`` schliessen
ihn.

Aufgeteilt aus dem alten ``services/event.py`` (Phase 1 von #777).
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from core.models import DeletionRequest, Event
from core.services.events.crud import soft_delete_event


def request_deletion(event, user, reason):
    """Create a deletion request for qualified data (four-eyes principle).

    Idempotent: if a PENDING DeletionRequest already exists for the same
    event, the existing record is returned instead of creating a duplicate
    (#530).
    """
    existing = DeletionRequest.objects.filter(
        facility=event.facility,
        target_type="Event",
        target_id=event.pk,
        status=DeletionRequest.Status.PENDING,
    ).first()
    if existing is not None:
        return existing
    return DeletionRequest.objects.create(
        facility=event.facility,
        target_type="Event",
        target_id=event.pk,
        reason=reason,
        requested_by=user,
    )


@transaction.atomic
def approve_deletion(deletion_request, reviewer):
    """Approve a deletion request and soft-delete the event."""
    event = Event.objects.get(pk=deletion_request.target_id, facility=deletion_request.facility)
    soft_delete_event(event, reviewer)
    deletion_request.status = DeletionRequest.Status.APPROVED
    deletion_request.reviewed_by = reviewer
    deletion_request.reviewed_at = timezone.now()
    deletion_request.save()


@transaction.atomic
def reject_deletion(deletion_request, reviewer):
    """Reject a deletion request."""
    deletion_request.status = DeletionRequest.Status.REJECTED
    deletion_request.reviewed_by = reviewer
    deletion_request.reviewed_at = timezone.now()
    deletion_request.save()
