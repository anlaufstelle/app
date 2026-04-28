"""Service layer for Case CRUD and event assignment."""

import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import AuditLog, Case
from core.services.sensitivity import user_can_see_event

logger = logging.getLogger(__name__)


@transaction.atomic
def create_case(facility, user, client, title, description="", lead_user=None):
    """Create a new Case with status OPEN."""
    if client and client.facility_id != facility.pk:
        raise ValueError(_("Klientel gehört nicht zur Einrichtung."))
    if lead_user and lead_user.facility_id != facility.pk:
        raise ValueError(_("Fallverantwortlicher gehört nicht zur Einrichtung."))

    case = Case(
        facility=facility,
        client=client,
        title=title,
        description=description,
        status=Case.Status.OPEN,
        created_by=user,
        lead_user=lead_user,
    )
    case.save()
    return case


@transaction.atomic
def update_case(case, user, **fields):
    """Update mutable fields on a case (title, description, lead_user, client).

    Writes an AuditLog entry with the names of changed fields (no PII values).
    """
    allowed = {"title", "description", "lead_user", "client"}
    changed_fields = []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"Feld '{key}' darf nicht aktualisiert werden.")
        if getattr(case, key) != value:
            changed_fields.append(key)
        setattr(case, key, value)
    case.save()

    if changed_fields:
        AuditLog.objects.create(
            facility=case.facility,
            user=user,
            action=AuditLog.Action.CASE_UPDATE,
            target_type="Case",
            target_id=str(case.pk),
            detail={"changed_fields": changed_fields},
        )
    return case


@transaction.atomic
def close_case(case, user):  # user reserved for future audit trail
    """Close a case."""
    case.status = Case.Status.CLOSED
    case.closed_at = timezone.now()
    case.save()
    return case


@transaction.atomic
def reopen_case(case, user):  # user reserved for future audit trail
    """Reopen a previously closed case."""
    case.status = Case.Status.OPEN
    case.closed_at = None
    case.save()
    return case


@transaction.atomic
def assign_event_to_case(case, event, user):  # user reserved for future audit trail
    """Assign an event to a case. Both must belong to the same facility.

    Additional invariants:
    - Anonymous events may only be attached to cases without a client.
    - Clientel of the event must match the case's clientel when both are set.
    - The user must be permitted to see the event (service-layer fallback for
      callers that bypass the view-level :func:`get_visible_event_or_404`).
    """
    if event.facility_id != case.facility_id:
        raise ValueError(_("Ereignis gehört nicht zur selben Einrichtung wie der Fall."))
    if user is not None and not user_can_see_event(user, event):
        raise ValidationError(_("Ereignis ist für diese Rolle nicht sichtbar."))
    if case.client_id is not None:
        if event.is_anonymous or event.client_id is None:
            raise ValidationError(_("Anonyme Ereignisse können nicht an einen klientelbezogenen Fall gehängt werden."))
        if event.client_id != case.client_id:
            raise ValidationError(_("Klientel des Ereignisses passt nicht zum Klientel des Falls."))
    event.case = case
    event.save()
    return event


@transaction.atomic
def remove_event_from_case(event, user):  # user reserved for future audit trail
    """Remove an event from its case."""
    event.case = None
    event.save()
    return event
