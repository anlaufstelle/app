"""Event-CRUD-Pfade (Refs #777).

``create_event``, ``update_event``, ``soft_delete_event`` plus die
Attachment-Schreiber ``attach_files_to_new_event`` und
``apply_attachment_changes``. Alles, was ein Event oder seine Anhaenge
mutiert, lebt hier.

Aufgeteilt aus dem alten ``services/event.py`` (Phase 1 von [#777](https://github.com/tobiasnix/anlaufstelle/issues/777)).
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from core.models import Activity, AuditLog, Event, EventHistory, FieldTemplate
from core.services.activity import log_activity
from core.services.events.fields import (
    _snapshot_field_metadata,
    _validate_data_json,
    build_field_template_lookup,
    build_redacted_delete_history,
    compute_event_search_text,
    normalize_file_marker,
    stage_index,
)
from core.services.file_vault import delete_event_attachments
from core.services.sensitivity import user_can_see_document_type


def attach_files_to_new_event(event, user, file_fields, document_type):
    """Speichere File-Uploads fuer ein frisch erzeugtes Event.

    Erwartet die UploadedFile-Listen aus :func:`split_file_and_text_data` und
    erzeugt pro Slug einen Stufe-B-Marker (``__files__``). Der Aufrufer ist
    fuer den umliegenden ``transaction.atomic``-Block zustaendig (Refs #584).
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
        event.data_json[slug] = {"__files__": True, "entries": entries}
    event.save(update_fields=["data_json"])


def apply_attachment_changes(event, user, request_post, request_files, file_fields, document_type):
    """Applizieren von REMOVE/REPLACE/ADD auf die FILE-Felder eines Events.

    - **REMOVE**: hidden CSV ``<slug>__remove`` listet zu loeschende
      ``entry_id``-Werte -> ``soft_delete_attachment_chain``.
    - **REPLACE**: pro bestehendem Entry ein File-Input
      ``<slug>__replace__<entry_id>`` -> ``store_encrypted_file(supersedes=...)``.
    - **ADD**: alle Files aus dem Multi-Upload-Feld werden mit frischen
      ``entry_id`` angehaengt.

    Aktualisiert anschliessend den Marker im ``event.data_json``.
    """
    from core.services.file_vault import soft_delete_attachment_chain, store_encrypted_file

    field_templates = build_field_template_lookup(document_type)
    facility = event.facility
    for slug, ft in field_templates.items():
        if ft.field_type != FieldTemplate.FieldType.FILE:
            continue

        existing_marker = (event.data_json or {}).get(slug)
        current_entries = normalize_file_marker(existing_marker)

        # Refs #782 (C-17): Bulk-Load aller Attachments des aktuellen Slugs
        # in einer Query — vorher lud der REMOVE/REPLACE-Pfad jeden Entry
        # einzeln (``filter(pk=entry["id"]).first()``) und produzierte N+1.
        entry_ids = [entry["id"] for entry in current_entries]
        attachments_by_pk = (
            {str(att.pk): att for att in event.attachments.filter(pk__in=entry_ids)} if entry_ids else {}
        )

        # 1) REMOVE
        remove_raw = request_post.get(f"{slug}__remove", "")
        remove_ids = {x.strip() for x in remove_raw.split(",") if x.strip()}
        if remove_ids:
            filtered = []
            for entry in current_entries:
                att = attachments_by_pk.get(str(entry["id"]))
                if att and str(att.entry_id) in remove_ids:
                    soft_delete_attachment_chain(event, att.entry_id, user)
                    continue
                filtered.append(entry)
            current_entries = filtered

        # 2) REPLACE
        updated_entries = []
        for entry in current_entries:
            att = attachments_by_pk.get(str(entry["id"]))
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
            raise ValidationError(_("Person des Ereignisses passt nicht zur Person des Falls."))
        if case.client_id is not None and (client is None or is_anonymous):
            raise ValidationError(_("Anonyme Ereignisse dürfen nicht an klientelbezogene Fälle gehängt werden."))

    if user is not None and not user_can_see_document_type(user, document_type):
        raise PermissionDenied(_("Diese Dokumentation darf von Ihrer Rolle nicht erstellt werden."))

    if client is None and not is_anonymous and not document_type.min_contact_stage:
        is_anonymous = True

    if document_type.min_contact_stage and is_anonymous:
        raise ValidationError(
            _(
                "Anonyme Kontakte sind bei diesem Dokumentationstyp nicht erlaubt, "
                "da eine Mindest-Kontaktstufe vorausgesetzt wird."
            )
        )

    if document_type.min_contact_stage and client is None and not is_anonymous:
        raise ValidationError(
            _(
                "Für diesen Dokumentationstyp muss eine Person ausgewählt werden, "
                "da eine Mindest-Kontaktstufe vorausgesetzt wird."
            )
        )

    if document_type.min_contact_stage and client is not None and not is_anonymous:
        required = stage_index(document_type.min_contact_stage)
        actual = stage_index(client.contact_stage)
        if actual < required:
            raise ValidationError(
                _(
                    "Person muss mindestens die Kontaktstufe "
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
        search_text=compute_event_search_text(data_json, document_type),
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
    ``str(updated_at)``-Vergleich.
    """
    from core.services.locking import check_version_conflict

    check_version_conflict(event, expected_updated_at)
    data_json = _validate_data_json(event.document_type, data_json)
    data_before = event.data_json.copy() if event.data_json else {}
    event.data_json = data_json
    event.search_text = compute_event_search_text(data_json, event.document_type)
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
