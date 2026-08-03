"""Field-Template- und Marker-Helfer fuer Events (Refs #777).

Reine Lese-Helfer ohne DB-Writes. Diese Funktionen werden von ``crud.py``
und ``context.py`` gemeinsam genutzt; um Zirkular-Imports zu vermeiden
liegen sie in einem eigenen Modul.

Aufgeteilt aus dem alten ``services/event.py`` (#777).

M2 (Refs #1058): Die entity-agnostischen Helfer (``remove_restricted_fields``,
``build_field_template_lookup``, ``validate_data_json`` — vormals
``_validate_data_json``) leben jetzt in :mod:`core.services.dynamic_data`, weil
Client/Case dieselbe Feldmechanik bekommen. Sie werden hier unveraendert
**re-exportiert**, damit alle Bestands-Importe und -Monkeypatches
(``from core.services.events.fields import ...``) stabil bleiben. Event-
spezifisches (Suchindex, File-Marker-Parsing, Delete-History, Form-Splitting)
bleibt in diesem Modul.
"""

from __future__ import annotations

from datetime import date, time

from django.core.files.uploadedfile import UploadedFile

from core.models import Client
from core.services.dynamic_data import (
    build_field_template_lookup,
    remove_restricted_fields,
    validate_data_json,
)

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


def file_marker_slugs(data_json):
    """Slugs in ``data_json``, deren Wert ein FILE-Marker ist (beide Formate).

    E17 (M1, Refs #1057): Der Update-Pfad reicht diese Menge über den Form-kwarg
    ``existing_file_slugs`` an :class:`~core.forms.events.DynamicEventDataForm`, damit
    ein bereits vorhandener Datei-Anhang beim Bearbeiten als „gefüllt" zählt. Die
    Klartext-Vorbelegung (:func:`decrypt_event_text_data`) strippt die Marker — sie
    erreichen die Form also nicht mehr über ``initial_data``. Reiner Lese-Helfer.
    """
    if not data_json:
        return set()
    return {slug for slug, value in data_json.items() if _is_file_marker(value)}


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
