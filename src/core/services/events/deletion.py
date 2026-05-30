"""4-Augen-Loeschungs-Workflow fuer Events (Refs #777, #932).

``request_deletion`` legt einen ``DeletionRequest`` an (idempotent fuer
PENDING-Antraege), ``approve_deletion`` und ``reject_deletion`` schliessen
ihn.

DSGVO Art. 5 (2) Rechenschaftspflicht (Refs #932): jeder Workflow-Schritt
schreibt einen dedizierten ``AuditLog``-Eintrag mit ``DELETION_*``-Action
und ``target_type="DeletionRequest"``. Bei Approve kommt zusaetzlich der
``Action.DELETE``-Eintrag aus :func:`soft_delete_event` (target=Event).

Aufgeteilt aus dem alten ``services/event.py`` (#777).
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from core.models import AuditLog, DeletionRequest, Event
from core.services.audit import audit_event
from core.services.events.crud import soft_delete_event


def request_deletion(event, user, reason):
    """Create a deletion request for qualified data (four-eyes principle).

    Idempotent: if a PENDING DeletionRequest already exists for the same
    event, the existing record is returned instead of creating a duplicate
    (#530). Im Idempotenz-Fall wird KEIN neues Audit-Event geschrieben
    (Refs #932) — der DELETION_REQUESTED-Eintrag existiert bereits vom
    ersten Aufruf.
    """
    existing = DeletionRequest.objects.filter(
        facility=event.facility,
        target_type="Event",
        target_id=event.pk,
        status=DeletionRequest.Status.PENDING,
    ).first()
    if existing is not None:
        return existing
    dr = DeletionRequest.objects.create(
        facility=event.facility,
        target_type="Event",
        target_id=event.pk,
        reason=reason,
        requested_by=user,
    )
    audit_event(
        action=AuditLog.Action.DELETION_REQUESTED,
        user=user,
        facility=event.facility,
        target_type="DeletionRequest",
        target_id=str(dr.pk),
        detail={"reason": reason, "target_event": str(event.pk)},
    )
    return dr


@transaction.atomic
def approve_deletion(deletion_request, reviewer):
    """Approve a deletion request and soft-delete the event.

    Refs #932: schreibt zusaetzlich zum DELETE-Audit (via
    :func:`soft_delete_event`) einen DELETION_APPROVED-Eintrag mit Bezug
    zum DeletionRequest. So sind die drei Workflow-Schritte (Request,
    Approve, Reject) in der Auditspur klar unterscheidbar.
    """
    event = Event.objects.get(pk=deletion_request.target_id, facility=deletion_request.facility)
    soft_delete_event(event, reviewer)
    deletion_request.status = DeletionRequest.Status.APPROVED
    deletion_request.reviewed_by = reviewer
    deletion_request.reviewed_at = timezone.now()
    deletion_request.save()
    audit_event(
        action=AuditLog.Action.DELETION_APPROVED,
        user=reviewer,
        facility=deletion_request.facility,
        target_type="DeletionRequest",
        target_id=str(deletion_request.pk),
        detail={"target_event": str(deletion_request.target_id)},
    )


@transaction.atomic
def reject_deletion(deletion_request, reviewer):
    """Reject a deletion request.

    Refs #932: schreibt einen DELETION_REJECTED-Eintrag (DSGVO Art. 5(2)
    Rechenschaftspflicht ueber abgelehnte Loesch-Versuche). KEIN
    ``Action.DELETE``-Eintrag — nur Approve loescht das Event.
    """
    deletion_request.status = DeletionRequest.Status.REJECTED
    deletion_request.reviewed_by = reviewer
    deletion_request.reviewed_at = timezone.now()
    deletion_request.save()
    audit_event(
        action=AuditLog.Action.DELETION_REJECTED,
        user=reviewer,
        facility=deletion_request.facility,
        target_type="DeletionRequest",
        target_id=str(deletion_request.pk),
        detail={"target_event": str(deletion_request.target_id)},
    )
