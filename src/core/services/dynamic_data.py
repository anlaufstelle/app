"""Entity-agnostische Helfer für dynamische Feld-Daten (M2, Refs #1058).

Bis M2 lagen diese Funktionen in :mod:`core.services.events.fields`, obwohl sie
faktisch nie event-spezifisch waren: sie arbeiten ausschliesslich auf einem
``DocumentType``, seinen ``FieldTemplate``-Zuordnungen und einem
``data_json``-Dict. M2 macht das dynamische Feldsystem entity-übergreifend
(Client/Case bekommen ebenfalls ``document_type`` + ``data_json``), darum ziehen
sie in ein eigenes, entity-neutrales Modul um.

:mod:`core.services.events.fields` re-exportiert alle hier umgezogenen Namen
(inklusive des Alt-Alias ``_validate_data_json``), damit Bestands-Importe und
-Monkeypatches unverändert funktionieren. Event-spezifisches (Suchindex,
File-Marker-Parsing, Delete-History) bleibt dort.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from core.services.compliance import user_can_see_field
from core.services.config import block_ordered_dtf_queryset

if TYPE_CHECKING:  # pragma: no cover — nur für Typprüfung, kein Laufzeit-Import
    from django import forms

    from core.models import DocumentType, FieldTemplate

logger = logging.getLogger(__name__)


def build_field_template_lookup(document_type: DocumentType, *, ordered: bool = False) -> dict[str, FieldTemplate]:
    """Return ``{slug: FieldTemplate}`` for a :class:`DocumentType`.

    Prefetches ``field_template`` with ``select_related`` so callers can
    iterate over the lookup without triggering N+1 queries. Pass
    ``ordered=True`` when the caller relies on ``sort_order``.
    """
    # #1713-Folge: geordnete Lookups folgen der globalen Block-Reihenfolge
    # (Dict-Insertion-Order == Anzeige-Reihenfolge, z. B. Event-Detail).
    qs = block_ordered_dtf_queryset(document_type) if ordered else document_type.fields.select_related("field_template")
    return {dtf.field_template.slug: dtf.field_template for dtf in qs}


def remove_restricted_fields(user: Any, document_type: DocumentType, data_form: forms.Form) -> list[str]:
    """Remove fields from *data_form* that *user* may not see.

    Returns a list of removed field names so callers can re-inject the
    original values on update.
    """
    doc_sensitivity = document_type.sensitivity
    field_templates = build_field_template_lookup(document_type)
    restricted: list[str] = []
    for name in list(data_form.fields.keys()):
        ft = field_templates.get(name)
        field_sensitivity = ft.sensitivity if ft else ""
        if not user_can_see_field(user, doc_sensitivity, field_sensitivity):
            del data_form.fields[name]
            restricted.append(name)
    return restricted


def validate_data_json(document_type: DocumentType, data_json: Mapping[str, Any] | None) -> dict[str, Any]:
    """Only accept fields defined in the DocumentType's field templates.

    FILE-typed fields use marker dicts — entweder das Stufe-A-Singleton
    (``{"__file__": True, "attachment_id": "..."}``) oder das Stufe-B-List-
    Format (``{"__files__": True, "entries": [...]}``). Beide werden
    unmodifiziert durchgereicht. Datums-/Zeitwerte werden auf ISO-Strings
    normalisiert (Refs #1073).

    M2 (Refs #1058): vormals ``_validate_data_json`` in
    ``services/events/fields.py``; der private Alt-Name bleibt dort als Alias
    bestehen.
    """
    # Funktionslokal: die Marker-/Serialisierungs-Helfer bleiben laut M2-Schnitt
    # im Event-Modul (File-Marker sind event-gebunden, E8) — ein Modul-Level-
    # Import wäre ein Zyklus, weil ``events/fields.py`` dieses Modul re-exportiert.
    from core.services.events.fields import _is_file_marker, _serialize_json_value

    if not data_json:
        return {}
    allowed_slugs = set(document_type.fields.values_list("field_template__slug", flat=True))
    # Refs #819: Choice-Konstante statt Magic-String.
    from core.models import FieldTemplate as FieldTemplateModel

    file_slugs = set(
        document_type.fields.filter(
            field_template__field_type=FieldTemplateModel.FieldType.FILE,
        ).values_list("field_template__slug", flat=True)
    )
    unknown = set(data_json.keys()) - allowed_slugs
    if unknown:
        logger.warning("Unknown fields in data_json removed: %s", unknown)
    cleaned: dict[str, Any] = {}
    for k, v in data_json.items():
        if k not in allowed_slugs:
            continue
        if k in file_slugs and _is_file_marker(v):
            cleaned[k] = v
        elif k in file_slugs:
            continue
        else:
            cleaned[k] = _serialize_json_value(v)
    return cleaned


def merge_dynamic_data(
    document_type: DocumentType,
    cleaned_text_data: Mapping[str, Any],
    *,
    old_data: Mapping[str, Any] | None,
    restricted_slugs: Iterable[str],
) -> dict[str, Any]:
    """E11-Merge-Policy für ``data_json`` — **ein** Punkt für alle Entitäten.

    Semantik (bewusst eng, damit ein Typwechsel keine Geister-Keys hinterlässt):

    1. Das Ergebnis wird **komplett** aus ``cleaned_text_data`` aufgebaut, also
       aus dem validierten Formular des **aktuellen** ``document_type``. Alte
       Werte werden NICHT als Basis übernommen.
    2. Für Slugs in ``restricted_slugs`` — Felder, die der Benutzer wegen seiner
       Sensitivity-Rolle gar nicht zu sehen bekam und die deshalb per
       :func:`remove_restricted_fields` aus dem Formular entfernt wurden — wird
       der Alt-Wert aus ``old_data`` nachgezogen. Sonst würde der Speichern-
       Vorgang Daten löschen, die der Benutzer nie sehen (und daher auch nie
       absichtlich leeren) konnte.
    3. Der Nachzug greift **nur innerhalb desselben Typs**: das Ergebnis läuft
       abschliessend durch :func:`validate_data_json`, dessen Whitelist die
       Slugs des AKTUELLEN ``document_type`` sind. Wechselt der Typ, sind die
       Alt-Slugs nicht mehr in der Whitelist und fallen weg — inklusive
       restricted Alt-Werte. Das ist gewollt: ein Wert des alten Typs hat im
       Feldset des neuen Typs keine Bedeutung mehr und wäre unsichtbarer
       Ballast im ``data_json``.

    Nebenwirkungsfrei: weder ``cleaned_text_data`` noch ``old_data`` werden
    mutiert. Datums-/Zeitwerte werden wie überall auf ISO-Strings normalisiert.

    :param document_type: der **aktuelle** Typ (nach einem etwaigen Wechsel).
    :param cleaned_text_data: ``cleaned_data`` des Formulars ohne Datei-Uploads.
    :param old_data: bisheriges ``data_json`` der Entität (``None`` = neu).
    :param restricted_slugs: Slugs, die für diesen Benutzer aus dem Formular
        entfernt wurden (Rückgabe von :func:`remove_restricted_fields`).
    """
    merged: dict[str, Any] = dict(cleaned_text_data)
    existing = old_data or {}
    for slug in restricted_slugs:
        if slug in existing:
            merged[slug] = existing[slug]
    return validate_data_json(document_type, merged)
