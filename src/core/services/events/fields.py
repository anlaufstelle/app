"""Field-Template- und Marker-Helfer fuer Events (Refs #777).

Reine Lese-Helfer ohne DB-Writes. Diese Funktionen werden von ``crud.py``
und ``context.py`` gemeinsam genutzt; um Zirkular-Imports zu vermeiden
liegen sie in einem eigenen Modul.

Aufgeteilt aus dem alten ``services/event.py`` (Phase 1 von [#777](https://github.com/tobiasnix/anlaufstelle/issues/777)).
"""

from __future__ import annotations

import logging

from django.core.files.uploadedfile import UploadedFile

from core.models import Client
from core.services.sensitivity import user_can_see_field

logger = logging.getLogger(__name__)


# Ordered contact stages (lowest -> highest).
CONTACT_STAGE_ORDER = [
    Client.ContactStage.IDENTIFIED,
    Client.ContactStage.QUALIFIED,
]


def compute_event_search_text(data_json, document_type):
    """Refs #827 (C-60): Plain-text-Suchindex fuer ``Event.search_text``.

    Sammelt alle ``data_json``-Werte, deren ``FieldTemplate``
    **unverschluesselt** ist und keine erhoehte Feld-Sensitivity hat
    (Sensitivity == NORMAL bzw. erbt vom DocumentType). Verschluesselte
    und ELEVATED/HIGH-Felder werden absichtlich nicht in den Index
    aufgenommen — der Index soll keine Daten exponieren, die ein User
    ueber das normale Detail-View nicht sehen darf.

    File-Marker (``__file__``/``__files__``) werden uebersprungen; ihr
    Klartext-Dateiname kommt nicht in den Suchindex (Refs #622).
    """
    if not data_json:
        return ""
    if document_type is None:
        return ""

    # Field-Templates des DocumentType einmalig laden.
    from core.models import DocumentType

    elevated = {DocumentType.Sensitivity.ELEVATED, DocumentType.Sensitivity.HIGH}
    field_meta = {
        dtf.field_template.slug: dtf.field_template
        for dtf in document_type.fields.select_related("field_template").all()
    }

    parts: list[str] = []
    for slug, value in data_json.items():
        ft = field_meta.get(slug)
        if ft is None:
            continue
        if ft.is_encrypted:
            continue
        if ft.sensitivity in elevated:
            continue
        if isinstance(value, dict):
            # File-Marker oder altes encrypted-Marker-Dict — nicht aufnehmen.
            continue
        if isinstance(value, list):
            parts.extend(str(v) for v in value if v is not None and not isinstance(v, dict))
        elif value is not None and value != "":
            parts.append(str(value))
    return " ".join(parts)


def stage_index(stage):
    """Return numeric index for a contact stage (higher = more qualified)."""
    try:
        return CONTACT_STAGE_ORDER.index(stage)
    except ValueError:
        return -1


def build_field_template_lookup(document_type, *, ordered=False):
    """Return ``{slug: FieldTemplate}`` for a :class:`DocumentType`.

    Prefetches ``field_template`` with ``select_related`` so callers can
    iterate over the lookup without triggering N+1 queries. Pass
    ``ordered=True`` when the caller relies on ``sort_order``.
    """
    qs = document_type.fields.select_related("field_template")
    if ordered:
        qs = qs.order_by("sort_order")
    return {dtf.field_template.slug: dtf.field_template for dtf in qs}


def remove_restricted_fields(user, document_type, data_form):
    """Remove fields from *data_form* that *user* may not see.

    Returns a list of removed field names so callers can re-inject the
    original values on update.
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


def _is_file_marker(value):
    """Return True if value is a Stufe-A (__file__) or Stufe-B (__files__) marker."""
    if not isinstance(value, dict):
        return False
    if value.get("__file__") is True and "attachment_id" in value:
        return True
    return value.get("__files__") is True and isinstance(value.get("entries"), list)


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
    # Refs #819 (R-007): Choice-Konstante statt Magic-String.
    from core.models import FieldTemplate

    file_slugs = set(
        document_type.fields.filter(
            field_template__field_type=FieldTemplate.FieldType.FILE,
        ).values_list("field_template__slug", flat=True)
    )
    unknown = set(data_json.keys()) - allowed_slugs
    if unknown:
        logger.warning("Unknown fields in data_json removed: %s", unknown)
    cleaned = {}
    for k, v in data_json.items():
        if k not in allowed_slugs:
            continue
        if k in file_slugs and _is_file_marker(v):
            cleaned[k] = v
        elif k in file_slugs:
            continue
        else:
            cleaned[k] = v
    return cleaned


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
    *was* es gab, nicht *welche Werte*.
    """
    field_names = list((event.data_json or {}).keys())
    return {"_redacted": True, "fields": field_names}


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
