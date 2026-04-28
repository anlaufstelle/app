"""Service layer for Event CRUD with EventHistory and AuditLog."""

import logging

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import Activity, AuditLog, Client, DeletionRequest, Event, EventHistory
from core.services.activity import log_activity
from core.services.file_vault import delete_event_attachments
from core.services.sensitivity import user_can_see_document_type

logger = logging.getLogger(__name__)


def _is_file_marker(value):
    """Return True if value is a file attachment marker dict."""
    return isinstance(value, dict) and value.get("__file__") is True and "attachment_id" in value


def _validate_data_json(document_type, data_json):
    """Only accept fields defined in the DocumentType's field templates.

    FILE-typed fields use marker dicts ``{"__file__": True, "attachment_id": "..."}``
    instead of plain values.  These markers are passed through without modification.
    """
    if not data_json:
        return {}
    allowed_slugs = set(document_type.fields.values_list("field_template__slug", flat=True))
    file_slugs = set(
        document_type.fields.filter(
            field_template__field_type="file",
        ).values_list("field_template__slug", flat=True)
    )
    unknown = set(data_json.keys()) - allowed_slugs
    if unknown:
        logger.warning("Unknown fields in data_json removed: %s", unknown)
    cleaned = {}
    for k, v in data_json.items():
        if k not in allowed_slugs:
            continue
        # Allow file marker dicts for FILE-typed fields
        if k in file_slugs and _is_file_marker(v):
            cleaned[k] = v
        elif k in file_slugs:
            # Non-marker values for FILE fields are handled by the upload flow;
            # skip plain values (e.g. stale filenames) so they don't overwrite markers.
            continue
        else:
            cleaned[k] = v
    return cleaned


# Ordered contact stages (lowest → highest).
CONTACT_STAGE_ORDER = [
    Client.ContactStage.IDENTIFIED,
    Client.ContactStage.QUALIFIED,
]


def stage_index(stage):
    """Return numeric index for a contact stage (higher = more qualified)."""
    try:
        return CONTACT_STAGE_ORDER.index(stage)
    except ValueError:
        return -1


@transaction.atomic
def create_event(facility, user, document_type, occurred_at, data_json, client=None, is_anonymous=False, case=None):
    """Create an event + EventHistory(CREATE)."""
    if document_type.facility_id != facility.pk:
        raise ValueError("DocumentType gehört nicht zur Facility")
    if client and client.facility_id != facility.pk:
        raise ValueError("Client gehört nicht zur Facility")
    if case is not None:
        if case.facility_id != facility.pk:
            raise ValidationError(_("Fall gehört nicht zur selben Einrichtung wie das Ereignis."))
        if case.client_id is not None and client is not None and case.client_id != client.pk:
            raise ValidationError(_("Klientel des Ereignisses passt nicht zum Klientel des Falls."))
        if case.client_id is not None and (client is None or is_anonymous):
            raise ValidationError(_("Anonyme Ereignisse dürfen nicht an klientelbezogene Fälle gehängt werden."))

    # Sensitivity gate: user must be allowed to create events of this DocumentType.
    # The form queryset already hides restricted types in the UI; this is the
    # server-side guarantee against spoofed POSTs.
    if user is not None and not user_can_see_document_type(user, document_type):
        raise PermissionDenied(_("Diese Dokumentation darf von Ihrer Rolle nicht erstellt werden."))

    # Auto-normalize: no client → anonymous (when allowed)
    if client is None and not is_anonymous:
        if not document_type.min_contact_stage:
            is_anonymous = True

    # Gate: anonymous not allowed when min_contact_stage is set
    if document_type.min_contact_stage and is_anonymous:
        raise ValidationError(
            _(
                "Anonyme Kontakte sind bei diesem Dokumentationstyp nicht erlaubt, "
                "da eine Mindest-Kontaktstufe vorausgesetzt wird."
            )
        )

    # Gate: client required when min_contact_stage is set
    if document_type.min_contact_stage and client is None and not is_anonymous:
        raise ValidationError(
            _(
                "Für diesen Dokumentationstyp muss ein Klientel ausgewählt werden, "
                "da eine Mindest-Kontaktstufe vorausgesetzt wird."
            )
        )

    # Gate: check min_contact_stage
    if document_type.min_contact_stage and client is not None and not is_anonymous:
        required = stage_index(document_type.min_contact_stage)
        actual = stage_index(client.contact_stage)
        if actual < required:
            raise ValidationError(
                _(
                    "Klientel muss mindestens die Kontaktstufe "
                    "'%(required_stage)s' "
                    "haben, aktuelle Stufe ist "
                    "'%(actual_stage)s'."
                )
                % {
                    "required_stage": document_type.min_contact_stage,
                    "actual_stage": client.get_contact_stage_display(),
                }
            )

    data_json = _validate_data_json(document_type, data_json)

    event = Event(
        facility=facility,
        client=client,
        document_type=document_type,
        occurred_at=occurred_at,
        data_json=data_json,
        is_anonymous=is_anonymous,
        created_by=user,
        case=case,
    )
    event.save()
    EventHistory.objects.create(
        event=event,
        changed_by=user,
        action=EventHistory.Action.CREATE,
        data_after=data_json,
    )
    summary = f"{document_type.name}"
    if client:
        summary += f" für {client.pseudonym}"
    log_activity(
        facility=facility,
        actor=user,
        verb=Activity.Verb.CREATED,
        target=event,
        summary=summary,
    )
    return event


@transaction.atomic
def update_event(event, user, data_json, expected_updated_at=None, **kwargs):
    """Update an event + EventHistory(UPDATE)."""
    if expected_updated_at is not None:
        current = Event.objects.filter(pk=event.pk).values_list("updated_at", flat=True).first()
        if current and str(current.isoformat()) != str(expected_updated_at):
            raise ValidationError(_("Das Ereignis wurde zwischenzeitlich bearbeitet. Bitte laden Sie die Seite neu."))
    data_json = _validate_data_json(event.document_type, data_json)
    data_before = event.data_json.copy() if event.data_json else {}
    event.data_json = data_json
    for k, v in kwargs.items():
        setattr(event, k, v)
    event.save()
    EventHistory.objects.create(
        event=event,
        changed_by=user,
        action=EventHistory.Action.UPDATE,
        data_before=data_before,
        data_after=data_json,
    )
    return event


@transaction.atomic
def soft_delete_event(event, user):
    """Soft-Delete + EventHistory(DELETE) + AuditLog."""
    field_names = list((event.data_json or {}).keys())
    event.is_deleted = True
    event.data_json = {}
    delete_event_attachments(event)
    event.save()
    EventHistory.objects.create(
        event=event,
        changed_by=user,
        action=EventHistory.Action.DELETE,
        data_before={"_redacted": True, "fields": field_names},
    )
    AuditLog.objects.create(
        facility=event.facility,
        user=user,
        action=AuditLog.Action.DELETE,
        target_type="Event",
        target_id=str(event.pk),
        detail={
            "document_type": event.document_type.name,
            "client_pseudonym": (event.client.pseudonym if event.client else None),
            "occurred_at": str(event.occurred_at),
        },
    )
    log_activity(
        facility=event.facility,
        actor=user,
        verb=Activity.Verb.DELETED,
        target=event,
        summary=f"{event.document_type.name} gelöscht",
    )


def request_deletion(event, user, reason):
    """Create a deletion request for qualified data (four-eyes principle).

    Idempotent: if a PENDING DeletionRequest already exists for the same
    event, the existing record is returned instead of creating a duplicate.
    Without this guard, double-clicks or parallel requests by multiple
    fachkräfte would clutter the four-eyes review queue with duplicate
    entries that all need to be reviewed individually (#530).
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
