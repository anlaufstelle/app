"""Event-CRUD-Pfade (Refs #777).

``create_event``, ``update_event``, ``soft_delete_event`` plus die
Attachment-Schreiber ``attach_files_to_new_event`` und
``apply_attachment_changes``. Alles, was ein Event oder seine Anhaenge
mutiert, lebt hier.

Aufgeteilt aus dem alten ``services/event.py`` (#777).
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from core.models import Activity, AuditLog, Event, EventHistory, FieldTemplate
from core.services.audit import audit_event
from core.services.compliance import user_can_see_document_type
from core.services.dashboard import log_activity
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


def _apply_remove(slug, current_entries, attachments_by_pk, request_post, event, user):
    """REMOVE-Schritt: hidden CSV ``<slug>__remove`` listet zu loeschende
    ``entry_id``-Werte -> ``soft_delete_attachment_chain``. Liefert die um die
    geloeschten Entries bereinigte Entry-Liste."""
    from core.services.file_vault import soft_delete_attachment_chain

    remove_raw = request_post.get(f"{slug}__remove", "")
    remove_ids = {x.strip() for x in remove_raw.split(",") if x.strip()}
    if not remove_ids:
        return current_entries
    filtered = []
    for entry in current_entries:
        att = attachments_by_pk.get(str(entry["id"]))
        if att and str(att.entry_id) in remove_ids:
            soft_delete_attachment_chain(event, att.entry_id, user)
            continue
        filtered.append(entry)
    return filtered


def _apply_replace(slug, current_entries, attachments_by_pk, request_files, facility, ft, event, user):
    """REPLACE-Schritt: pro bestehendem Entry ein File-Input
    ``<slug>__replace__<entry_id>`` -> ``store_encrypted_file(supersedes=...)``.
    Liefert die (ggf. ersetzte) Entry-Liste."""
    from core.services.file_vault import store_encrypted_file

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
    return updated_entries


def _apply_add(slug, updated_entries, file_fields, facility, ft, event, user):
    """ADD-Schritt: alle Files aus dem Multi-Upload-Feld werden mit frischen
    ``entry_id`` angehaengt. Liefert die um die neuen Entries erweiterte Liste."""
    from core.services.file_vault import store_encrypted_file

    add_files = file_fields.get(slug) or []
    base_sort = (max((e.get("sort", 0) for e in updated_entries), default=-1)) + 1
    for idx, uploaded_file in enumerate(add_files):
        new_att = store_encrypted_file(facility, uploaded_file, ft, event, user, sort_order=base_sort + idx)
        updated_entries.append({"id": str(new_att.pk), "sort": base_sort + idx})
    return updated_entries


def _write_file_marker(event, slug, updated_entries):
    """Schreibt bzw. entfernt den Stufe-B-Marker im ``event.data_json``."""
    if updated_entries:
        event.data_json[slug] = {"__files__": True, "entries": updated_entries}
    elif slug in event.data_json:
        del event.data_json[slug]


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

        current_entries = _apply_remove(slug, current_entries, attachments_by_pk, request_post, event, user)
        updated_entries = _apply_replace(
            slug, current_entries, attachments_by_pk, request_files, facility, ft, event, user
        )
        updated_entries = _apply_add(slug, updated_entries, file_fields, facility, ft, event, user)

        _write_file_marker(event, slug, updated_entries)

    event.save(update_fields=["data_json"])


def _validate_case_assignment(facility, case, client, is_anonymous):
    """Refs #1160: Fall-Zuordnung pruefen (Facility-Scope, Person-Match,
    Anonymitaets-Ausschluss). Wirkungs-identisch zum frueheren Inline-Block."""
    if case is None:
        return
    if case.facility_id != facility.pk:
        raise ValidationError(_("Fall gehört nicht zur selben Einrichtung wie das Ereignis."))
    if case.client_id is not None and client is not None and case.client_id != client.pk:
        raise ValidationError(_("Person des Ereignisses passt nicht zur Person des Falls."))
    if case.client_id is not None and (client is None or is_anonymous):
        raise ValidationError(_("Anonyme Ereignisse dürfen nicht an klientelbezogene Fälle gehängt werden."))


def _validate_contact_stage(document_type, client, is_anonymous):
    """Refs #1160: Mindest-Kontaktstufen-Regeln des DocumentType pruefen.

    Wirkungs-identisch zum frueheren Inline-Block — die Reihenfolge der
    Checks (anonym -> fehlende Person -> Stufe zu niedrig) bleibt erhalten.
    """
    if not document_type.min_contact_stage:
        return
    if is_anonymous:
        raise ValidationError(
            _(
                "Anonyme Kontakte sind bei diesem Dokumentationstyp nicht erlaubt, "
                "da eine Mindest-Kontaktstufe vorausgesetzt wird."
            )
        )
    if client is None:
        raise ValidationError(
            _(
                "Für diesen Dokumentationstyp muss eine Person ausgewählt werden, "
                "da eine Mindest-Kontaktstufe vorausgesetzt wird."
            )
        )
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


@transaction.atomic
def create_event(
    facility,
    user,
    document_type,
    occurred_at,
    data_json,
    client=None,
    is_anonymous=False,
    case=None,
    idempotency_key=None,
):
    """Create an event + EventHistory(CREATE).

    ``idempotency_key`` (Review R5/R6): der normalisierte X-Idempotency-Key des
    Offline-Replays wird als persistenter DB-Backstop mitgeschrieben. Der
    partielle Unique-Constraint (``event_idem_key_per_user_uniq``, je
    ``created_by``) fängt Duplikate ab, wenn der Cache-Dedup ausfällt.
    """
    if document_type.facility_id != facility.pk:
        raise ValueError("DocumentType gehört nicht zur Facility")
    if client and client.facility_id != facility.pk:
        raise ValueError("Client gehört nicht zur Facility")
    _validate_case_assignment(facility, case, client, is_anonymous)

    if user is not None and not user_can_see_document_type(user, document_type):
        raise PermissionDenied(_("Diese Dokumentation darf von Ihrer Rolle nicht erstellt werden."))

    if client is None and not is_anonymous and not document_type.min_contact_stage:
        is_anonymous = True

    _validate_contact_stage(document_type, client, is_anonymous)

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
        idempotency_key=idempotency_key,
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
    audit_event(
        AuditLog.Action.EVENT_CREATE,
        user=user,
        facility=facility,
        target_obj=event,
        detail={"document_type": document_type.name, "is_anonymous": is_anonymous},
    )
    return event


@transaction.atomic
def update_event(event, user, data_json, expected_updated_at=None, require_version_token=False, **kwargs):
    """Update an event + EventHistory(UPDATE).

    Refs #734: nutzt zentrale ``check_version_conflict`` statt eigenem
    ``str(updated_at)``-Vergleich.

    ``require_version_token`` (Refs #1338): wird 1:1 an
    ``check_version_conflict`` durchgereicht. ``EventUpdateView`` setzt es
    auf das Ergebnis von ``_wants_json_response`` — JSON-/Offline-Replay-
    Clients müssen dadurch einen Token mitschicken, während der klassische
    HTML-Formular-Pfad beim Default ``False`` bleibt (kein Verhaltensbruch).
    """
    from core.services.security import check_version_conflict

    check_version_conflict(event, expected_updated_at, require_token=require_version_token)
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


def decrypt_event_text_data(event):
    """Refs #1160: Klartext-``data_json`` eines Events fuer Form-Vorbelegung.

    Liefert ein ``{slug: wert}``-Dict, in dem File-Marker (legacy ``__file__``
    und Stufe-B ``__files__``) uebersprungen und alle uebrigen Werte via
    :func:`safe_decrypt` (Default ``""``) entschluesselt sind. Aus
    ``EventUpdateView.get``/``post`` herausgezogen — beide Stellen bauten
    bisher dieselbe Schleife (Duplikat, Refs #1160 R1b).
    """
    from core.services.file_vault import safe_decrypt

    result = {}
    for key, value in (event.data_json or {}).items():
        if isinstance(value, dict) and (value.get("__file__") or value.get("__files__")):
            continue
        result[key] = safe_decrypt(value, default="")
    return result


def merge_update_payload(event, merged, restricted_keys, document_type):
    """Refs #1160: Update-Payload um Felder ergaenzen, die das Formular nicht traegt.

    Aus ``EventUpdateView.post`` herausgezogen (R1b). Mutiert ``merged`` in-place
    und liefert es zurueck:

    - **Restricted-Felder** (vom User nicht sichtbar, vorher per
      ``remove_restricted_fields`` aus dem Form entfernt) werden mit ihrem
      Original-Wert aus ``event.data_json`` wieder eingesetzt — sonst wuerde der
      Update sie loeschen.
    - **FILE-Felder** behalten ihren bestehenden Marker (beide Formate), damit
      ``apply_attachment_changes`` ihn anschliessend anpassen kann.
    """
    event_data = event.data_json or {}

    # Re-insert restricted fields with original values
    for key in restricted_keys:
        if key in event_data:
            merged[key] = event_data[key]

    # Für FILE-Felder: bestehenden Marker (beide Formate) erstmal beibehalten —
    # apply_attachment_changes passt ihn anschliessend an.
    field_templates = build_field_template_lookup(document_type)
    for slug, ft in field_templates.items():
        if ft.field_type == FieldTemplate.FieldType.FILE:
            existing_marker = event_data.get(slug)
            if isinstance(existing_marker, dict) and (
                existing_marker.get("__file__") or existing_marker.get("__files__")
            ):
                merged[slug] = existing_marker
    return merged


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
    audit_event(
        AuditLog.Action.DELETE,
        user=user,
        facility=event.facility,
        target_obj=event,
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
