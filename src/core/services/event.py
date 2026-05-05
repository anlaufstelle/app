"""Service layer for Event CRUD with EventHistory and AuditLog."""

import logging
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import Activity, AuditLog, Client, DeletionRequest, Event, EventHistory
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
        if isinstance(value, dict) and (value.get("__file__") or value.get("__files__")):
            if value.get("__files__"):
                result[slug] = {
                    "__files__": True,
                    "entries": [{"id": e.get("id"), "sort": e.get("sort", 0)} for e in (value.get("entries") or [])],
                }
            else:
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


def _format_field_display_value(value, ft):
    """Format a ``data_json`` value for human-readable detail display.

    Wandelt SELECT/MULTI_SELECT-Slugs in Labels (``['beratung']`` →
    ``Beratung``) und BOOLEAN-Werte in ``Ja``/``Nein`` um. Refs #749.
    """
    if ft is None:
        return value

    if ft.field_type == ft.FieldType.BOOLEAN:
        return _("Ja") if value else _("Nein")

    if ft.field_type == ft.FieldType.SELECT and ft.options_json:
        label_map = {o["slug"]: o["label"] for o in ft.options_json if isinstance(o, dict) and "slug" in o}
        return label_map.get(value, value)

    if ft.field_type == ft.FieldType.MULTI_SELECT and ft.options_json and isinstance(value, list):
        label_map = {o["slug"]: o["label"] for o in ft.options_json if isinstance(o, dict) and "slug" in o}
        return ", ".join(label_map.get(v, str(v)) for v in value)

    return value


def _build_prior_versions(attachment, predecessor_index):
    """Walk the ``superseded_by`` chain backwards for an attachment.

    ``predecessor_index`` ist ``{successor_pk: predecessor}`` für alle
    Attachments des Events, vorab in einem einzigen Query erstellt
    (#662 FND-05). Damit ist die Auflösung der Versionskette O(N) statt
    Query-pro-Schritt.

    Returns a list of dicts (newest-first) describing each predecessor —
    used by the detail view to show a „Vorversionen"-list (Refs #587).
    """
    prior_versions = []
    current_attachment = attachment
    seen = {current_attachment.pk}
    predecessor = predecessor_index.get(current_attachment.pk)
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
        predecessor = predecessor_index.get(current_attachment.pk)
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

    # Alle Attachments des Events vorab in 1 Query laden + Indizes bauen
    # (#662 FND-05): bisher loeste jedes File-Entry und jeder Schritt einer
    # Versionskette eine eigene DB-Query aus.
    all_attachments = list(event.attachments.all())
    attachments_by_pk = {att.pk: att for att in all_attachments}
    # Vorgaenger-Index: zu jedem ``superseded_by`` (Nachfolger-PK) das
    # Vorgaenger-Attachment.
    predecessor_index = {att.superseded_by_id: att for att in all_attachments if att.superseded_by_id is not None}

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

        # File attachment marker — beide Formate (Stufe A + B) transparent.
        if _is_file_marker(value):
            entries_meta = normalize_file_marker(value)
            file_entries = []
            for entry in entries_meta:
                # Entry-ID ist die Attachment-PK; aus dem vorab geladenen
                # Index lesen statt erneut DB zu fragen.
                try:
                    entry_pk = uuid.UUID(entry["id"]) if not isinstance(entry["id"], uuid.UUID) else entry["id"]
                except (ValueError, TypeError):
                    continue
                att = attachments_by_pk.get(entry_pk)
                if not att:
                    continue
                if att.deleted_at is not None:
                    continue
                prior = _build_prior_versions(att, predecessor_index)
                file_entries.append(
                    {
                        "attachment_id": str(att.pk),
                        "original_filename": get_original_filename(att),
                        "file_size_display": format_file_size(att.file_size),
                        "prior_versions": prior,
                    }
                )
            if file_entries:
                # Für Rückwärtskompatibilität mit Templates (Stufe A): wir
                # setzen attachment_id/original_filename/file_size_display/
                # prior_versions auf das **erste** Entry (singleton-Verhalten)
                # UND liefern zusätzlich ``entries`` mit der vollen Liste.
                first = file_entries[0]
                fields_display.append(
                    {
                        "label": ft.name if ft else key,
                        "is_file": True,
                        "attachment_id": first["attachment_id"],
                        "original_filename": first["original_filename"],
                        "file_size_display": first["file_size_display"],
                        "prior_versions": first["prior_versions"],
                        "entries": file_entries,
                        "is_sensitive": bool(field_sensitivity),
                    }
                )
                continue

        decrypted = safe_decrypt(value, default=_("[verschlüsselt]"))
        fields_display.append(
            {
                "label": ft.name if ft else key.replace("-", " ").title(),
                "value": _format_field_display_value(decrypted, ft),
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


def build_redacted_delete_history(event):
    """Return ``data_before``-Payload fuer EventHistory(DELETE) — redaktiert.

    Refs #714: Beide Loesch-Pfade (manuelles ``soft_delete_event`` und
    automatisches ``retention._soft_delete_events``) muessen denselben
    redaktierten Payload schreiben — sonst leben Klartext-Werte in der
    append-only EventHistory weiter und unterlaufen DSGVO Art. 17 + 5
    Abs. 1 lit. e + § 67 SGB X.

    Format: ``{"_redacted": True, "fields": [...slugs...]}`` — nur die
    Feld-Namen (Schluessel von ``data_json``) bleiben erhalten, damit
    der Audit nachvollziehen kann *was* es gab, nicht *welche Werte*.
    """
    field_names = list((event.data_json or {}).keys())
    return {"_redacted": True, "fields": field_names}


def _is_file_marker(value):
    """Return True if value is a Stufe-A (__file__) or Stufe-B (__files__) marker."""
    if not isinstance(value, dict):
        return False
    if value.get("__file__") is True and "attachment_id" in value:
        return True
    if value.get("__files__") is True and isinstance(value.get("entries"), list):
        return True
    return False


def is_singleton_file_marker(value):
    """Alt-Format (Stufe A): ``{"__file__": True, "attachment_id": "<uuid>"}``."""
    return isinstance(value, dict) and value.get("__file__") is True and "attachment_id" in value


def is_multi_file_marker(value):
    """Neu-Format (Stufe B): ``{"__files__": True, "entries": [{"id": ..., "sort": ...}]}``."""
    return isinstance(value, dict) and value.get("__files__") is True and isinstance(value.get("entries"), list)


def normalize_file_marker(value):
    """Liefert eine Liste von ``{"id": <attachment_id>, "sort": <int>}``.

    Akzeptiert sowohl das Stufe-A-Singleton als auch das Stufe-B-List-Format.
    Nicht-Marker-Werte liefern eine leere Liste.
    """
    if is_multi_file_marker(value):
        entries = value["entries"] or []
        return [
            {"id": str(e.get("id")), "sort": int(e.get("sort", i))} for i, e in enumerate(entries) if e and e.get("id")
        ]
    if is_singleton_file_marker(value):
        return [{"id": str(value.get("attachment_id")), "sort": 0}]
    return []


def _validate_data_json(document_type, data_json):
    """Only accept fields defined in the DocumentType's field templates.

    FILE-typed fields use marker dicts — entweder das Stufe-A-Singleton
    (``{"__file__": True, "attachment_id": "..."}``) oder das Stufe-B-List-
    Format (``{"__files__": True, "entries": [...]}``). Beide werden
    unmodifiziert durchgereicht.
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


def split_file_and_text_data(cleaned_data):
    """Split a form's ``cleaned_data`` into ``(file_fields, text_data)``.

    ``MultipleFileField`` liefert eine Liste von ``UploadedFile``, klassische
    ``FileField`` eine einzelne ``UploadedFile``. Beide werden auf eine Liste
    pro Slug normalisiert. Text-/Auswahl-Werte landen unverändert in
    ``text_data`` (Refs FND-A001 — DRY zwischen ``EventCreateView`` und
    ``EventUpdateView``).
    """
    from django.core.files.uploadedfile import UploadedFile

    file_fields = {}
    text_data = {}
    for key, value in cleaned_data.items():
        if isinstance(value, list) and value and all(isinstance(v, UploadedFile) for v in value):
            file_fields[key] = value
        elif isinstance(value, UploadedFile):
            file_fields[key] = [value]
        else:
            text_data[key] = value
    return file_fields, text_data


def attach_files_to_new_event(event, user, file_fields, document_type):
    """Speichere File-Uploads für ein frisch erzeugtes Event.

    Erwartet die UploadedFile-Listen aus :func:`split_file_and_text_data` und
    erzeugt pro Slug einen Stufe-B-Marker (``__files__``). Der Aufrufer ist
    dafür zuständig, den umliegenden ``transaction.atomic``-Block zu setzen,
    damit das Event und die Anhänge gemeinsam roll-back-fähig bleiben
    (Refs #584).
    """
    from core.services.file_vault import store_encrypted_file

    if not file_fields:
        return
    field_templates = build_field_template_lookup(document_type)
    for slug, uploaded_list in file_fields.items():
        ft = field_templates.get(slug)
        if not ft or not uploaded_list:
            continue
        entries = []
        for idx, uploaded_file in enumerate(uploaded_list):
            attachment = store_encrypted_file(event.facility, uploaded_file, ft, event, user, sort_order=idx)
            entries.append({"id": str(attachment.pk), "sort": idx})
        # Stufe B (#622): Neue Events nutzen immer ``__files__``, auch bei
        # einem einzigen Eintrag — das spart einen zweiten Code-Pfad beim
        # Auslesen.
        event.data_json[slug] = {"__files__": True, "entries": entries}
    event.save(update_fields=["data_json"])


def apply_attachment_changes(event, user, request_post, request_files, file_fields, document_type):
    """Applizieren von REMOVE/REPLACE/ADD auf die FILE-Felder eines Events.

    - **REMOVE**: hidden CSV ``<slug>__remove`` listet zu löschende
      ``entry_id``-Werte → ``soft_delete_attachment_chain``.
    - **REPLACE**: pro bestehendem Entry ein File-Input
      ``<slug>__replace__<entry_id>`` → ``store_encrypted_file(supersedes=…)``.
    - **ADD**: alle Files aus dem Multi-Upload-Feld werden mit frischen
      ``entry_id`` angehängt.

    Aktualisiert anschließend den Marker im ``event.data_json`` (Stufe-B-
    Format) bzw. entfernt ihn, wenn keine Entries mehr übrig sind. Aufrufer
    müssen den umgebenden ``transaction.atomic``-Block setzen — siehe Doku
    zu :func:`attach_files_to_new_event`.
    """
    from core.services.file_vault import soft_delete_attachment_chain, store_encrypted_file

    field_templates = build_field_template_lookup(document_type)
    facility = event.facility
    for slug, ft in field_templates.items():
        if ft.field_type != "file":
            continue

        existing_marker = (event.data_json or {}).get(slug)
        current_entries = normalize_file_marker(existing_marker)

        # 1) REMOVE
        remove_raw = request_post.get(f"{slug}__remove", "")
        remove_ids = {x.strip() for x in remove_raw.split(",") if x.strip()}
        if remove_ids:
            filtered = []
            for entry in current_entries:
                att = event.attachments.filter(pk=entry["id"]).first()
                if att and str(att.entry_id) in remove_ids:
                    soft_delete_attachment_chain(event, att.entry_id, user)
                    continue
                filtered.append(entry)
            current_entries = filtered

        # 2) REPLACE
        updated_entries = []
        for entry in current_entries:
            att = event.attachments.filter(pk=entry["id"]).first()
            if not att:
                continue
            replace_file = request_files.get(f"{slug}__replace__{att.entry_id}")
            if replace_file is not None:
                new_att = store_encrypted_file(facility, replace_file, ft, event, user, supersedes=att)
                updated_entries.append({"id": str(new_att.pk), "sort": att.sort_order})
            else:
                updated_entries.append(entry)

        # 3) ADD
        add_files = file_fields.get(slug) or []
        base_sort = (max((e.get("sort", 0) for e in updated_entries), default=-1)) + 1
        for idx, uploaded_file in enumerate(add_files):
            new_att = store_encrypted_file(facility, uploaded_file, ft, event, user, sort_order=base_sort + idx)
            updated_entries.append({"id": str(new_att.pk), "sort": base_sort + idx})

        if updated_entries:
            event.data_json[slug] = {"__files__": True, "entries": updated_entries}
        elif slug in event.data_json:
            del event.data_json[slug]

    event.save(update_fields=["data_json"])


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
    """Update an event + EventHistory(UPDATE).

    Refs #734: nutzt zentrale ``check_version_conflict`` statt eigenem
    ``str(updated_at)``-Vergleich. Der eigene Vergleich war offset-
    sensitiv (gleicher Instant mit anderem Timezone-Offset wuerde als
    Konflikt gewertet) und drift-anfaellig.
    """
    from core.services.locking import check_version_conflict

    check_version_conflict(event, expected_updated_at)
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
    history_payload = build_redacted_delete_history(event)
    event.is_deleted = True
    event.data_json = {}
    delete_event_attachments(event)
    event.save()
    EventHistory.objects.create(
        event=event,
        changed_by=user,
        action=EventHistory.Action.DELETE,
        data_before=history_payload,
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
