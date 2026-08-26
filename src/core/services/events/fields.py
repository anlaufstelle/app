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
(``from core.services.events.fields import ...``) stabil bleiben.

M24/AP2 (Refs #1738, ADR-039): Dasselbe gilt jetzt fuer das
**File-Marker-Parsing** (``_is_file_marker``, ``is_singleton_file_marker``,
``is_multi_file_marker``, ``file_marker_slugs``, ``normalize_file_marker``) —
Anhaenge haengen nicht mehr nur am Ereignis. Event-spezifisches (Suchindex,
Delete-History, Form-Splitting, JSON-Wire-Normalisierung) bleibt in diesem
Modul.
"""

from __future__ import annotations

from datetime import date, time

from django.core.files.uploadedfile import UploadedFile

from core.models import Client
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

    Refs #1991 (Fix-Runde 1): Der Sensitivity-Test laeuft ueber die zentrale
    ``sensitivity_rank`` statt ueber eine Mengen-Zugehoerigkeit. Die alte
    Formulierung (``ft.sensitivity in {elevated, high}``) fragte, ob der Wert
    zu den bekannten HOHEN Stufen gehoert — ein Wert ausserhalb von
    ``DocumentType.Sensitivity`` fiel durch beide Zweige und landete im
    Klartext-Index, obwohl ``user_can_see_field`` ihn fuer JEDE Rolle sperrt.
    ``Event.search_text`` wird von ``event_search_queryset`` per ``icontains``
    durchsucht; dessen Doc-Type-Gate greift auf EREIGNIS-Ebene und schuetzt
    kein einzelnes Feld — der Index waere damit ein Substring-Orakel samt
    Trefferkarte auf einen fuer niemanden sichtbaren Inhalt gewesen. Der Index
    ist die dritte Sichtbarkeits-Wahrheit neben Listen-Query und Detail-Gate
    und zieht deshalb dieselbe fail-closed-Linie.

    Indexiert wird genau in zwei Faellen:

    * ``sensitivity == ""`` — „erbt vom Dokumentationstyp". Der Typ-Schutz
      greift auf Ereignis-Ebene, das Feld braucht keinen eigenen.
    * ``sensitivity_rank(...) == 0`` — ausdrueckliches ``normal``.

    Alles andere (ELEVATED, HIGH und jeder unbekannte Wert) bleibt draussen.

    File-Marker (``__file__``/``__files__``) werden uebersprungen; ihr
    Klartext-Dateiname kommt nicht in den Suchindex (Refs #622).
    """
    if not data_json:
        return ""
    if document_type is None:
        return ""

    # Field-Templates des DocumentType einmalig laden.
    from core.services.compliance.sensitivity import sensitivity_rank

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
        if ft.sensitivity != "" and sensitivity_rank(ft.sensitivity) != 0:
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
