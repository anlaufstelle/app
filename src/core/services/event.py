"""Service layer for Event CRUD with EventHistory and AuditLog."""

import logging

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import Activity, AuditLog, Client, DeletionRequest, Event, EventHistory
from core.models.attachment import EventAttachment
from core.services.activity import log_activity
from core.services.encryption import safe_decrypt
from core.services.file_vault import (
    delete_event_attachments,
    get_original_filename,
)
from core.services.sensitivity import user_can_see_document_type, user_can_see_field
from core.utils.formatting import format_file_size

logger = logging.getLogger(__name__)


def build_field_template_lookup(document_type, *, ordered=False):
    """Return ``{slug: FieldTemplate}`` for a :class:`DocumentType`.

    Prefetches ``field_template`` with ``select_related`` so callers can
    iterate over the lookup without triggering N+1 queries. Pass
    ``ordered=True`` when the caller relies on ``sort_order`` — e.g. the
    detail view builds its display list in the configured field order.

    Extracted from five near-identical inline loops across ``views/events.py``
    (Refs FND-A001). Keeping this in ``services/event.py`` means the views
    only import a single helper instead of repeating the comprehension.
    """
    qs = document_type.fields.select_related("field_template")
    if ordered:
        qs = qs.order_by("sort_order")
    return {dtf.field_template.slug: dtf.field_template for dtf in qs}


def filtered_server_data_json(user, event):
    """Return ``event.data_json`` with fields *user* cannot see removed.

    Keeps the conflict-diff honest: the user only ever sees values they could
    also read via the normal detail view, so the merge UI cannot become a
    side-channel for restricted content. Encrypted values stay as their
    marker dicts — the client-side UI shows ``[verschlüsselt]`` for those
    rather than trying to decrypt.

    Moved from ``views/events.py`` into the service layer (Refs FND-A001)
    because the logic is pure data filtering with no HTTP concerns.
    """
    if not event.data_json:
        return {}
    doc_sensitivity = event.document_type.sensitivity
    field_templates = build_field_template_lookup(event.document_type)
    result = {}
    for slug, value in event.data_json.items():
        ft = field_templates.get(slug)
        field_sensitivity = ft.sensitivity if ft else ""
        if not user_can_see_field(user, doc_sensitivity, field_sensitivity):
            continue
        # File markers — keep metadata but not the file bytes.
        if isinstance(value, dict) and value.get("__file__"):
            result[slug] = {"__file__": True, "name": value.get("name", "")}
            continue
        result[slug] = safe_decrypt(value, default="") if isinstance(value, dict) else value
    return result


def remove_restricted_fields(user, document_type, data_form):
    """Remove fields from *data_form* that *user* may not see.

    Returns a list of removed field names so callers can re-inject the
    original values on update (see :class:`EventUpdateView`).

    Moved from ``views/events.py`` into the service layer (Refs FND-A001) —
    the function mutates a Django form object but the decision about what
    to remove is pure sensitivity business logic.
    """
    doc_sensitivity = document_type.sensitivity
    field_templates = build_field_template_lookup(document_type)
    restricted = []
    for name in list(data_form.fields.keys()):
        ft = field_templates.get(name)
        field_sensitivity = ft.sensitivity if ft else ""
        if not user_can_see_field(user, doc_sensitivity, field_sensitivity):
            del data_form.fields[name]
            restricted.append(name)
    return restricted


def _build_prior_versions(event, attachment):
    """Walk the ``superseded_by`` chain backwards for an attachment.

    Returns a list of dicts (newest-first) describing each predecessor —
    used by the detail view to show a „Vorversionen"-list (Refs #587).
    """
    prior_versions = []
    current_attachment = attachment
    seen = {current_attachment.pk}
    predecessor = EventAttachment.objects.filter(event=event, superseded_by=current_attachment).first()
    while predecessor is not None and predecessor.pk not in seen:
        seen.add(predecessor.pk)
        prior_versions.append(
            {
                "attachment_id": str(predecessor.pk),
                "original_filename": get_original_filename(predecessor),
                "file_size_display": format_file_size(predecessor.file_size),
                "superseded_at": predecessor.superseded_at,
            }
        )
        current_attachment = predecessor
        predecessor = EventAttachment.objects.filter(event=event, superseded_by=current_attachment).first()
    return prior_versions


def build_event_detail_context(event, user):
    """Build the template context dict for :class:`EventDetailView`.

    Returns a dict with ``event``, ``fields_display`` (list of per-field dicts
    with decrypted values / file markers / sensitivity flags) and ``history``
    (reverse-chronological :class:`EventHistory` queryset).

    Extracted from the view body (Refs FND-A003) so the 90-LOC building of
    the display list no longer lives in a GET handler and can be unit-tested
    without going through the HTTP layer.
    """
    field_templates = build_field_template_lookup(event.document_type, ordered=True)
    doc_sensitivity = event.document_type.sensitivity

    fields_display = []
    for key, value in (event.data_json or {}).items():
        ft = field_templates.get(key)
        field_sensitivity = ft.sensitivity if ft else ""
        is_encrypted = ft.is_encrypted if ft else False

        if not user_can_see_field(user, doc_sensitivity, field_sensitivity):
            fields_display.append(
                {
                    "label": ft.name if ft else key.replace("-", " ").title(),
                    "value": _("[Eingeschränkt]"),
                    "is_sensitive": bool(field_sensitivity),
                    "restricted": True,
                }
            )
            continue

        # File attachment marker
        if isinstance(value, dict) and value.get("__file__"):
            attachment = EventAttachment.objects.filter(pk=value.get("attachment_id"), event=event).first()
            if attachment:
                prior_versions = _build_prior_versions(event, attachment)
                fields_display.append(
                    {
                        "label": ft.name if ft else key,
                        "is_file": True,
                        "attachment_id": str(attachment.pk),
                        "original_filename": get_original_filename(attachment),
                        "file_size_display": format_file_size(attachment.file_size),
                        "is_sensitive": bool(field_sensitivity),
                        "prior_versions": prior_versions,
                    }
                )
                continue

        fields_display.append(
            {
                "label": ft.name if ft else key.replace("-", " ").title(),
                "value": safe_decrypt(value, default=_("[verschlüsselt]")),
                "is_encrypted": is_encrypted,
                "is_sensitive": bool(field_sensitivity),
            }
        )

    history = event.history.select_related("changed_by").order_by("-changed_at")

    return {
        "event": event,
        "fields_display": fields_display,
        "history": history,
    }


def _snapshot_field_metadata(document_type):
    """Return a frozen snapshot of field labels, sensitivity and encryption status.

    Format: ``{ "slug": {"name": "...", "sensitivity": "...", "is_encrypted": bool}, ... }``
    """
    result = {}
    for dtf in document_type.fields.select_related("field_template"):
        ft = dtf.field_template
        result[ft.slug] = {
            "name": ft.name,
            "sensitivity": ft.sensitivity,
            "is_encrypted": ft.is_encrypted,
        }
    return result


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
        field_metadata=_snapshot_field_metadata(document_type),
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
    AuditLog.objects.create(
        facility=facility,
        user=user,
        action=AuditLog.Action.EVENT_CREATE,
        target_type="Event",
        target_id=str(event.pk),
        detail={"document_type": document_type.name, "is_anonymous": is_anonymous},
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
        field_metadata=_snapshot_field_metadata(event.document_type),
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
        field_metadata=_snapshot_field_metadata(event.document_type),
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
