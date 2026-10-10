"""Event field, search-index and history helpers (Refs #777, #2525).

Read helpers are shared by crud.py and context.py. The maintenance helper
rebuild_event_search_text writes only the derived index within a facility.

Entity-independent dynamic-data and file-marker helpers are re-exported
from core.services.dynamic_data to preserve existing imports and patches
(Refs #1058, #1738, ADR-039). Search indexes, delete-history snapshots,
form splitting and JSON serialization remain event-specific here.
"""

from __future__ import annotations

from datetime import date, time

from django.core.files.uploadedfile import UploadedFile
from django.db import transaction

from core.models import Client, Event, Facility
from core.services.dynamic_data import (
    _is_file_marker,
    build_field_template_lookup,
    file_marker_slugs,
    is_multi_file_marker,
    is_singleton_file_marker,
    normalize_file_marker,
    remove_restricted_fields,
    validate_data_json,
)
from core.services.events.history_diff import build_field_redaction_marker

# M2 (Refs #1058): Alt-Alias auf den jetzt oeffentlichen Namen. ``crud.py`` und
# ``services/events/__init__.py`` importieren weiterhin ``_validate_data_json``;
# der Alias haelt diese Pfade (und Test-Monkeypatches) unveraendert gueltig.
_validate_data_json = validate_data_json

__all__ = [
    "CONTACT_STAGE_ORDER",
    "_is_file_marker",
    "_serialize_json_value",
    "_snapshot_field_metadata",
    "_validate_data_json",
    "build_field_template_lookup",
    "build_redacted_delete_history",
    "compute_event_search_text",
    "file_marker_slugs",
    "is_multi_file_marker",
    "is_singleton_file_marker",
    "normalize_file_marker",
    "rebuild_event_search_text",
    "remove_restricted_fields",
    "split_file_and_text_data",
    "stage_index",
    "validate_data_json",
]


# Ordered contact stages (lowest -> highest).
CONTACT_STAGE_ORDER = [
    Client.ContactStage.IDENTIFIED,
    Client.ContactStage.QUALIFIED,
]


def compute_event_search_text(data_json, document_type):
    """Build the plaintext event index using effective sensitivity (ADR-016/043).

    Only unencrypted fields whose effective sensitivity is Normal are indexed:
    the higher of document-type and field levels must have rank zero. An empty
    field level inherits the document-type level. Elevated, High and unknown
    levels are excluded at write time, including inheriting fields in sensitive
    types (Refs #2525). Unknown levels fail closed through the central policy.

    Unassigned slugs, file markers and legacy encrypted marker dictionaries are
    excluded. Pseudonym search remains a separate, role-scoped read path.
    """
    if not data_json:
        return ""
    if document_type is None:
        return ""

    # Load field metadata once for this document type.
    from core.services.compliance.sensitivity import field_is_search_indexed

    field_meta = {
        dtf.field_template.slug: dtf.field_template
        for dtf in document_type.fields.select_related("field_template").all()
    }

    parts: list[str] = []
    for slug, value in data_json.items():
        ft = field_meta.get(slug)
        if ft is None:
            continue
        if not field_is_search_indexed(document_type.sensitivity, ft):
            continue
        if isinstance(value, dict):
            # Exclude file markers and legacy encrypted marker dictionaries.
            continue
        if isinstance(value, list):
            parts.extend(str(v) for v in value if v is not None and not isinstance(v, dict))
        elif value is not None and value != "":
            parts.append(str(value))
    return " ".join(parts)


@transaction.atomic
def rebuild_event_search_text(facility: Facility, *, dry_run: bool = False) -> tuple[int, int]:
    """Rebuild one facility's indexes, including deleted events (Refs #2525).

    Only the derived search column is written; source data, timestamps and
    history stay intact. Lock events on write runs so a concurrent event update
    cannot be overwritten by an index computed from an older payload.
    Return the checked and changed (or would-change) event counts.
    """
    events = Event.objects.for_facility(facility).select_related("document_type")
    if not dry_run:
        events = events.select_for_update(of=("self",))
    checked = changed = 0
    for event in events.iterator(chunk_size=500):
        checked += 1
        search_text = compute_event_search_text(event.data_json, event.document_type)
        if search_text == event.search_text:
            continue
        changed += 1
        if not dry_run:
            Event.objects.for_facility(facility).filter(pk=event.pk).update(search_text=search_text)
    return checked, changed


def stage_index(stage):
    """Return numeric index for a contact stage (higher = more qualified)."""
    try:
        return CONTACT_STAGE_ORDER.index(stage)
    except ValueError:
        return -1


def _serialize_json_value(value):
    """Normalisiert Form-``cleaned_data``-Werte auf das JSON-Wire-Format.

    Refs #1073: ``DynamicEventDataForm`` liefert fuer DATE/TIME-Felder
    ``datetime.date``/``datetime.time``-Objekte — ``Event.data_json`` ist
    aber ein JSONField ohne Custom-Encoder, der Save wirft sonst
    ``TypeError`` (Fehler 500). ISO-Strings sind das etablierte Format:
    der Seed schreibt sie, ``bans.py`` liest via ``date.fromisoformat``.
    ``datetime`` ist eine ``date``-Subklasse und damit mit abgedeckt.
    """
    if isinstance(value, (date, time)):
        return value.isoformat()
    return value


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
    redaktierten Payload schreiben.

    Format: ``{"_redacted": True, "fields": [...slugs...]}`` — nur die
    Feld-Namen bleiben erhalten, damit der Audit nachvollziehen kann
    *was* es gab, nicht *welche Werte*. Gebaut wird er ueber die gemeinsame
    Fabrik in ``history_diff``, damit Produzent und Erkenner nicht driften.
    """
    return build_field_redaction_marker((event.data_json or {}).keys())


def split_file_and_text_data(cleaned_data):
    """Split a form's ``cleaned_data`` into ``(file_fields, text_data)``.

    ``MultipleFileField`` liefert eine Liste von ``UploadedFile``, klassische
    ``FileField`` eine einzelne ``UploadedFile``. Beide werden auf eine Liste
    pro Slug normalisiert.
    """
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
