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
Delete-History, Form-Splitting) bleibt dort.

M24/AP2 (Refs #1738, ADR-039): Auch das **File-Marker-Parsing** ist hierher
gezogen. Bis M24 galten Datei-Felder als event-gebunden (E8-Sperre), seit
ADR-039 hängen Anhänge an Ereignis, Person **oder** Fall — die Marker sind
damit ebenso entity-neutral wie der Rest dieses Moduls. Nebeneffekt: der
funktionslokale Import-Zyklus in :func:`validate_data_json` entfällt für
``is_file_marker`` (der Helfer ist jetzt modul-lokal).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.services.compliance import user_can_see_document_type, user_can_see_field
from core.services.config import block_ordered_dtf_queryset

if TYPE_CHECKING:  # pragma: no cover — nur für Typprüfung, kein Laufzeit-Import
    from django import forms

    from core.models import DocumentType, FieldTemplate

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# File-Marker (M24/AP2, Refs #1738) — Single Source of Truth
# ---------------------------------------------------------------------------
#
# Ein FILE-Feld speichert in ``data_json`` keinen Wert, sondern einen Marker auf
# die verschlüsselte ``Attachment``-Zeile. Zwei Formate sind im Umlauf:
#
# * **Stufe A** (Alt, ein Anhang je Feld): ``{"__file__": True, "attachment_id": "<uuid>"}``
# * **Stufe B** (Refs #622, mehrere Anhänge je Feld):
#   ``{"__files__": True, "entries": [{"id": "<uuid>", "sort": 0}, ...]}``


def is_file_marker(value: Any) -> bool:
    """Return True if value is a Stufe-A (__file__) or Stufe-B (__files__) marker."""
    if not isinstance(value, dict):
        return False
    if value.get("__file__") is True and "attachment_id" in value:
        return True
    return value.get("__files__") is True and isinstance(value.get("entries"), list)


#: Alt-Alias auf den jetzt öffentlichen Namen (Muster wie ``_validate_data_json``).
#: ``events/fields.py`` re-exportiert ihn; Bestands-Importe/-Monkeypatches auf
#: ``_is_file_marker`` bleiben damit unverändert gültig.
_is_file_marker = is_file_marker


def is_singleton_file_marker(value: Any) -> bool:
    """Alt-Format (Stufe A): ``{"__file__": True, "attachment_id": "<uuid>"}``."""
    return isinstance(value, dict) and value.get("__file__") is True and "attachment_id" in value


def is_multi_file_marker(value: Any) -> bool:
    """Neu-Format (Stufe B): ``{"__files__": True, "entries": [{"id": ..., "sort": ...}]}``."""
    return isinstance(value, dict) and value.get("__files__") is True and isinstance(value.get("entries"), list)


def file_marker_slugs(data_json: Mapping[str, Any] | None) -> set[str]:
    """Slugs in ``data_json``, deren Wert ein FILE-Marker ist (beide Formate).

    E17 (M1, Refs #1057): Der Update-Pfad reicht diese Menge über den Form-kwarg
    ``existing_file_slugs`` an die Datenformulare, damit ein bereits vorhandener
    Datei-Anhang beim Bearbeiten als „gefüllt" zählt. Die Klartext-Vorbelegung
    (:func:`decrypt_dynamic_text_data`) strippt die Marker — sie erreichen die
    Form also nicht mehr über ``initial_data``. Reiner Lese-Helfer.
    """
    if not data_json:
        return set()
    return {slug for slug, value in data_json.items() if is_file_marker(value)}


def normalize_file_marker(value: Any) -> list[dict[str, Any]]:
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


def resolve_orphan_field_templates(
    document_type: DocumentType, orphan_slugs: Iterable[str]
) -> dict[str, FieldTemplate]:
    """Facility-weites ``{slug: FieldTemplate}`` für verwaiste Slugs (Refs #1725).

    Verwaist = Slug mit Daten in ``data_json``, aber ohne Zuordnung am aktuellen
    ``document_type`` (z. B. nachdem eine Feldzuordnung entfernt wurde). Ein
    solcher Wert darf seine Sensitivity/Verschlüsselung NICHT verlieren:
    ``FieldTemplate`` ist unique je (facility, slug) und bleibt durch den
    Löschschutz am Leben, solange Daten unter dem Slug existieren.

    Gemeinsame Auflösungs-Quelle für alle vier Anzeige-/Ausliefer-Flächen —
    Ereignis-Detail (:func:`~core.services.events.context.build_event_detail_context`),
    Waisen-Sektion (:func:`build_orphan_fields_display`), Konflikt-Diff
    (:func:`~core.services.events.context.filtered_server_data_json`) und
    Offline-Bundle (:func:`~core.services.system.offline._visible_data_fields`).
    Ein Slug fehlt im Ergebnis, wenn (ausnahmsweise) kein Template existiert; der
    Aufrufer MUSS ihn dann konservativ als HIGH gaten
    (:func:`orphan_gating_sensitivity`), nie auf NORMAL (``""``) zurückfallen.
    """
    from core.models import FieldTemplate

    orphan_slugs = list(orphan_slugs)
    if not orphan_slugs:
        return {}
    return {
        ft.slug: ft
        for ft in FieldTemplate.objects.filter(
            facility_id=document_type.facility_id,
            slug__in=orphan_slugs,
        )
    }


def orphan_gating_sensitivity(field_template: FieldTemplate | None) -> str:
    """Gating-Sensitivity eines verwaisten Feldes (Refs #1725).

    Aufgelöstes Template → dessen Sensitivity (``""`` = erbt vom Typ/NORMAL).
    Fehlt das Template, wird der Wert **konservativ als HIGH** gegatet — ein Slug
    mit Daten, aber ohne auffindbare Vorlage, darf nie versehentlich offengelegt
    werden.
    """
    from core.models import DocumentType

    return (field_template.sensitivity or "") if field_template is not None else DocumentType.Sensitivity.HIGH


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


def check_document_type(
    facility,
    document_type,
    *,
    entity_type,
    entity_type_error,
    facility_error: Exception | None = None,
):
    """Validate facility and entity scope for a ``DocumentType`` (Refs #1833).

    ``None`` remains valid for optional Client/Case document types.  Their
    shared facility failure is a German ``ValidationError``.  The older Event
    service passes its established ``ValueError`` via ``facility_error`` so
    exception class, message, and guard ordering remain unchanged.
    """
    if document_type is None:
        return None
    if document_type.facility_id != facility.pk:
        if facility_error is not None:
            raise facility_error
        raise ValidationError(_("Der Dokumentationstyp gehört nicht zu dieser Einrichtung."))
    if document_type.entity_type != entity_type:
        raise ValidationError(entity_type_error)
    return document_type


def clean_dynamic_data(
    facility,
    document_type,
    data_json: Mapping[str, Any] | None,
    *,
    entity_type,
    entity_type_error,
    facility_error: Exception | None = None,
    old_data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Guard a document type and whitelist its dynamic payload (Refs #1833)."""
    document_type = check_document_type(
        facility,
        document_type,
        entity_type=entity_type,
        entity_type_error=entity_type_error,
        facility_error=facility_error,
    )
    if document_type is None:
        if data_json:
            raise ValidationError(_("Ohne Dokumentationstyp können keine dynamischen Daten gespeichert werden."))
        return {}
    return validate_data_json(document_type, data_json, old_data=old_data)


def preserve_inactive_values(document_type, data_json, old_data=None) -> dict[str, Any]:
    """Retain assigned inactive values verbatim and reject new inactive input."""
    merged = dict(data_json or {})
    existing = old_data or {}
    inactive_slugs = document_type.fields.filter(field_template__is_active=False).values_list(
        "field_template__slug", flat=True
    )
    for slug in inactive_slugs:
        if slug in existing:
            merged[slug] = existing[slug]
        else:
            merged.pop(slug, None)
    return merged


def validate_data_json(
    document_type: DocumentType,
    data_json: Mapping[str, Any] | None,
    *,
    old_data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
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
    # M24/AP2 (Refs #1738): ``is_file_marker`` ist jetzt modul-lokal (SSOT hier).
    # Nur ``_serialize_json_value`` bleibt event-seitig und wird funktionslokal
    # geholt — ein Modul-Level-Import wäre ein Zyklus, weil ``events/fields.py``
    # dieses Modul re-exportiert.
    from core.services.events.fields import _serialize_json_value

    if old_data is not None:
        data_json = preserve_inactive_values(document_type, data_json, old_data)
    if not data_json:
        return {}
    field_templates = build_field_template_lookup(document_type)
    allowed_slugs = set(field_templates)
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
        if (old_data is not None and not field_templates[k].is_active) or k in file_slugs and is_file_marker(v):
            cleaned[k] = v
        elif k in file_slugs:
            continue
        else:
            serialized = _serialize_json_value(v)
            field_template = field_templates[k]
            if field_template.field_type == FieldTemplateModel.FieldType.NUMBER and serialized is not None:
                from core.services.file_vault import is_encrypted_value
                from core.services.system import validate_number_value

                # Ein bereits gespeichertes Token kann aus einem fuer die
                # aktuelle Rolle unsichtbaren Feld stammen. Nur für passend
                # verschlüsselte Vorlagen darf es erneut passieren.
                if not (field_template.is_encrypted and is_encrypted_value(serialized)):
                    try:
                        validate_number_value(serialized, field_template.min_value, field_template.max_value)
                    except ValidationError as error:
                        raise ValidationError({k: error.messages}) from error
            cleaned[k] = serialized
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
    return validate_data_json(document_type, merged, old_data=existing)


def count_filled_dynamic_values(data_json: Mapping[str, Any] | None) -> int:
    """Anzahl der Slugs in ``data_json``, die tatsächlich einen Wert tragen.

    Beziffert, was ein Typwechsel bzw. -entfernen an einer Person/einem Fall
    verwerfen würde (:func:`merge_dynamic_data`, E11) — Grundlage der Rückfrage
    vor dem Speichern (Refs #1733) nach dem Muster der Feld-Entfernen-Bestätigung
    im Feld-Board.

    Bewusst wertneutral gezählt, ohne Sensitivity-Gate: die Zahl geht in einen
    Bestätigungstext, nicht in eine Anzeige — sie nennt keine Slugs und keine
    Werte. Restricted Felder würden beim Wechsel genauso verworfen und müssen
    deshalb mitgezählt werden.

    „Gefüllt" heisst: nicht ``None``, kein leerer/nur aus Leerzeichen
    bestehender String, keine leere Liste/Map und kein ``False`` (ein nicht
    gesetztes Häkchen ist kein Datenverlust). Eine ``0`` zählt dagegen als
    erfasster Wert. Verschlüsselte Werte liegen als Fernet-Token vor und werden
    für die reine Zählung **nicht** entschlüsselt.

    **Datei-Marker zählen NICHT** (M24/AP4, Refs #1738): Ein FILE-Slug trägt
    keinen Feldwert, sondern einen Zeiger auf ``Attachment``-Zeilen. Ein
    Typwechsel verwirft ihn zwar ebenfalls — aber als *Datei*, und Dateien
    beziffert der Dialog separat über die Zahl der Versionsketten
    (:func:`~core.services.attachments.discard_type_switch_file_chains`).
    Ohne diese Trennung stünde im Bestätigungstext „1 Feldwert", wo in
    Wahrheit eine Datei gemeint ist.
    """
    count = 0
    for value in (data_json or {}).values():
        if is_file_marker(value):
            continue
        if value is None or isinstance(value, bool) and not value:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, list | tuple | dict | set) and not value:
            continue
        count += 1
    return count


def decrypt_dynamic_text_data(data_json: Mapping[str, Any] | None) -> dict[str, Any]:
    """Klartext-``data_json`` für die Formular-Vorbelegung (M2 P7, Refs #1058).

    Entity-neutrales Pendant zu
    :func:`core.services.events.crud.decrypt_event_text_data`: File-Marker
    (legacy ``__file__`` und Stufe-B ``__files__``) werden übersprungen, alle
    übrigen Werte via :func:`~core.services.file_vault.safe_decrypt` (Default
    ``""``) entschlüsselt.

    Für Client/Case kann der Marker-Zweig praktisch nicht auftreten (FILE ist
    dort dreischichtig ausgeschlossen, E8) — er bleibt trotzdem drin, damit die
    Funktion auf JEDEM ``data_json`` sicher läuft, auch auf einem Bestandsrest.
    """
    # Funktionslokal wie im Event-Pendant: ``file_vault`` zieht Modelle und
    # Storage-Helfer nach, die dieses Modul zur Importzeit nicht braucht.
    from core.services.file_vault import safe_decrypt

    result: dict[str, Any] = {}
    for key, value in (data_json or {}).items():
        if isinstance(value, dict) and (value.get("__file__") or value.get("__files__")):
            continue
        result[key] = safe_decrypt(value, default="")
    return result


def option_label_map(options_json: Iterable[Any] | None) -> dict[str, Any]:
    """``options_json`` → ``{slug: label}``.

    Refs #1827: Vier Aufrufstellen (hier zweimal — SELECT/MULTI_SELECT —,
    ``views/config/conditions.py``, ``services/events/feed.py``,
    ``services/system/export.py``) bauten dieselbe Reduktion mit drei leicht
    unterschiedlichen defensiven Filtern (mal ohne ``"slug"``-Check, mal ohne
    ``"label"``-Check). Vereinheitlicht auf den staerksten Filter — ein
    Eintrag muss ein Dict mit BEIDEN Schluesseln sein, sonst faellt er aus der
    Map und der Aufrufer faellt auf den rohen Wert zurueck statt mit
    ``KeyError`` zu crashen (kein Test — auch keiner der mutmut-Follow-ups —
    exerciert bisher eine Dict-Option ohne ``"slug"``/``"label"``; diese
    Verschaerfung ist eine reine Robustheits-Verbesserung).
    """
    return {o["slug"]: o["label"] for o in (options_json or []) if isinstance(o, dict) and "slug" in o and "label" in o}


def format_dynamic_display_value(value: Any, ft: FieldTemplate | None) -> Any:
    """Formatiere einen ``data_json``-Wert für die lesbare Detail-Anzeige.

    Wandelt SELECT/MULTI_SELECT-Slugs in Labels (``['beratung']`` ->
    ``Beratung``) und BOOLEAN-Werte in ``Ja``/``Nein`` um. Refs #749.

    M2 (Refs #1058): aus ``services/events/context.py`` hierher gezogen — die
    Formatierung war nie event-spezifisch und wird ab P7 auch vom Personen-
    (und ab P8 vom Fall-)Detail gebraucht. ``events/context.py`` behält den
    Alt-Namen ``_format_field_display_value`` als Alias.
    """
    if ft is None:
        return value
    from core.services.system.field_types import get_spec

    return get_spec(ft.field_type).format_display(value, ft)


def owner_attachment_index(owner: Any) -> tuple[dict, dict]:
    """``(attachments_by_pk, predecessor_index)`` eines Owners (M24/AP4, Refs #1738).

    Beide Indizes stammen aus EINEM Query (Refs #894/#662) — die Anzeige loest
    damit sowohl die Marker-Entries als auch die ``superseded_by``-Ketten ohne
    N+1 auf. ``owner=None`` (kein Traeger, z. B. Konfigurations-Vorschau)
    liefert leere Indizes; der FILE-Zweig zeigt dann gar nichts an, statt das
    rohe Marker-Dict zu rendern.
    """
    if owner is None:
        return {}, {}
    all_attachments = list(owner.attachments.all())
    return (
        {att.pk: att for att in all_attachments},
        {att.superseded_by_id: att for att in all_attachments if att.superseded_by_id is not None},
    )


def _file_display_entry(
    label: str,
    value: Any,
    *,
    field_sensitivity: str,
    is_encrypted: bool,
    attachments_by_pk: Mapping[Any, Any],
    predecessor_index: Mapping[Any, Any],
    orphaned: bool = False,
) -> dict[str, Any] | None:
    """Anzeige-Dict eines FILE-Feldes — oder ``None``, wenn nichts aufloesbar ist.

    M24/AP4 (Refs #1738, ADR-039): Schluessel-identisch zum Event-Detail
    (:func:`core.services.events.context._build_field_display_entry`), damit
    Ereignis-, Personen- und Fall-Detail dieselbe Darstellung bekommen —
    ``entries`` mit Dateiname, Groesse, ``download_url`` und den
    ``prior_versions`` je Kette.

    ``None`` (⇒ Aufrufer ueberspringt das Feld) bei geloeschten, nicht
    aufloesbaren oder ownerlosen Markern. Das ist die einzig sichere Antwort:
    ein durchgereichter Marker landete sonst als rohes ``{"__files__": …}``-Dict
    im HTML.
    """
    # Funktionslokal: ``services/attachments/context`` importiert dieses Modul
    # (``normalize_file_marker``) — ein Modul-Level-Import waere ein Zyklus.
    from core.services.attachments import resolve_file_entries

    file_entries = resolve_file_entries(value, attachments_by_pk, predecessor_index)
    if not file_entries:
        return None
    return {
        "label": label,
        "is_file": True,
        "entries": file_entries,
        "is_sensitive": bool(field_sensitivity),
        "is_encrypted": is_encrypted,
        "restricted": False,
        "orphaned": orphaned,
    }


def _display_entry(
    label: str,
    value: Any,
    *,
    field_sensitivity: str,
    is_encrypted: bool,
    ft: FieldTemplate | None,
    user: Any,
    doc_sensitivity: str,
    attachments_by_pk: Mapping[Any, Any],
    predecessor_index: Mapping[Any, Any],
    orphaned: bool = False,
) -> dict[str, Any] | None:
    """Anzeige-Dict eines einzelnen dynamischen Feldwerts — der Drei-Fall
    (restricted/Datei/normal).

    Refs #1827: 2x identisch dupliziert in ``build_dynamic_fields_display``/
    ``build_orphan_fields_display`` (entstanden mit #1738s FILE-Zweig). Gibt
    ``None`` zurück, wenn ein Datei-Eintrag verworfen wird (kein Attachment
    mehr auflösbar) — die Aufrufstelle überspringt den Slug dann wie zuvor.

    Der ``"orphaned"``-Schlüssel erscheint in restricted-/normal-Einträgen NUR
    wenn ``orphaned=True`` (unverändert gegenüber vorher — der reguläre Aufruf
    trug diesen Schlüssel dort nie); ``_file_display_entry`` trägt ihn ohnehin
    immer (Default ``False``).
    """
    from core.services.file_vault import safe_decrypt

    if not user_can_see_field(user, doc_sensitivity, field_sensitivity):
        entry = {
            "label": label,
            "value": _("[Eingeschränkt]"),
            "is_sensitive": bool(field_sensitivity),
            "is_encrypted": is_encrypted,
            "restricted": True,
        }
        if orphaned:
            entry["orphaned"] = True
        return entry

    if is_file_marker(value):
        return _file_display_entry(
            label,
            value,
            field_sensitivity=field_sensitivity,
            is_encrypted=is_encrypted,
            attachments_by_pk=attachments_by_pk,
            predecessor_index=predecessor_index,
            orphaned=orphaned,
        )

    decrypted = safe_decrypt(value, default=_("[verschlüsselt]"))
    entry = {
        "label": label,
        "value": format_dynamic_display_value(decrypted, ft),
        "is_sensitive": bool(field_sensitivity),
        "is_encrypted": is_encrypted,
        "restricted": False,
    }
    if orphaned:
        entry["orphaned"] = True
    return entry


def build_dynamic_fields_display(
    document_type: DocumentType,
    data_json: Mapping[str, Any] | None,
    user: Any,
    owner: Any = None,
) -> list[dict[str, Any]]:
    """Read-only Anzeige-Dicts der dynamischen Felder (M2 P7, Refs #1058).

    Entity-neutrales Pendant zu
    :func:`core.services.events.context.build_event_detail_context`. Je Feld:

    * ``label``     — Name des ``FieldTemplate``,
    * ``value``     — entschlüsselt (:func:`~core.services.file_vault.safe_decrypt`)
      und über :func:`format_dynamic_display_value` lesbar gemacht,
    * ``restricted``/``is_sensitive``/``is_encrypted`` — Flags für die Anzeige.

    Felder, die *user* wegen seiner Rolle nicht sehen darf, erscheinen mit dem
    Platzhalter ``[Eingeschränkt]`` — der Wert wird gar nicht erst
    entschlüsselt.

    **M24/AP4 (Refs #1738, ADR-039):** ``owner`` (Person/Fall/Ereignis) ist
    optional und schaltet den **FILE-Zweig** frei: Marker-Slugs werden über
    :func:`~core.services.attachments.resolve_file_entries` zu Datei-Einträgen
    (Name, Größe, ``download_url``, Vorversionen) aufgelöst, statt als rohes
    ``{"__files__": …}``-Dict im HTML zu landen. Das Sensitivity-Gate läuft
    unverändert **davor**: ein restringiertes FILE-Feld nennt keinen
    Dateinamen. Ohne ``owner`` — und bei nicht auflösbaren oder soft-gelöschten
    Anhängen — wird das Feld übersprungen.

    **Reihenfolge:** iteriert wird über die konfigurierte Feldreihenfolge des
    ``DocumentType`` (nicht über die ``data_json``-Schlüssel). PostgreSQL
    speichert ``jsonb`` normalisiert (Sortierung nach Schlüssellänge/Bytes) —
    eine Iteration über das Dict ergäbe eine für Benutzer willkürliche
    Reihenfolge. Slugs ohne Feld am aktuellen Typ (Bestandsreste vor der
    Whitelist) werden dabei nicht angezeigt.
    """
    values = data_json or {}
    doc_sensitivity = document_type.sensitivity
    field_templates = build_field_template_lookup(document_type, ordered=True)
    attachments_by_pk, predecessor_index = owner_attachment_index(owner)

    entries: list[dict[str, Any]] = []
    for slug, ft in field_templates.items():
        if slug not in values:
            continue
        entry = _display_entry(
            ft.name,
            values[slug],
            field_sensitivity=ft.sensitivity or "",
            is_encrypted=ft.is_encrypted,
            ft=ft,
            user=user,
            doc_sensitivity=doc_sensitivity,
            attachments_by_pk=attachments_by_pk,
            predecessor_index=predecessor_index,
        )
        if entry is not None:
            entries.append(entry)
    return entries


def build_orphan_fields_display(
    document_type: DocumentType,
    data_json: Mapping[str, Any] | None,
    user: Any,
    owner: Any = None,
) -> list[dict[str, Any]]:
    """Anzeige-Dicts der **verwaisten** dynamischen Felder (Refs #1725(a)).

    Verwaist = Slug in ``data_json``, aber nicht (mehr) am aktuellen
    ``document_type`` zugeordnet — z. B. nachdem eine Feldzuordnung entfernt
    wurde. :func:`build_dynamic_fields_display` überspringt solche Slugs still;
    dieser Helfer macht sie sichtbar, damit die betroffenen Werte nicht
    unbemerkt und unauskunftsfähig im ``data_json`` liegen bleiben.

    **Sensitivity/Verschlüsselung** werden facility-weit über das
    ``FieldTemplate`` aufgelöst (unique je (facility, slug), am Leben durch den
    Löschschutz, solange Daten unter dem Slug existieren). Die Waisen-Einträge
    durchlaufen dieselbe Gating-Logik (:func:`user_can_see_field`) wie
    zugeordnete Felder — ein einst als elevated/high eingestuftes Feld bleibt
    also restricted (``[Eingeschränkt]``). **Defensiv:** findet sich für einen
    Slug ausnahmsweise kein ``FieldTemplate``, wird der Eintrag konservativ als
    HIGH behandelt (nur Leitung/Admin sehen den Wert) und mit dem Slug als Label
    ausgewiesen.

    Reihenfolge: nach Slug sortiert (stabil, Refs #1729). Entry-Form identisch zu
    :func:`build_dynamic_fields_display` plus ``orphaned=True``.
    """
    values = data_json or {}
    field_templates = build_field_template_lookup(document_type)
    orphan_slugs = sorted(set(values.keys()) - set(field_templates.keys()))
    if not orphan_slugs:
        return []

    doc_sensitivity = document_type.sensitivity
    resolved = resolve_orphan_field_templates(document_type, orphan_slugs)
    attachments_by_pk, predecessor_index = owner_attachment_index(owner)

    entries: list[dict[str, Any]] = []
    for slug in orphan_slugs:
        ft = resolved.get(slug)
        # Ohne auffindbares Template konservativ als HIGH gaten (Löschschutz
        # sollte das verhindern) — lieber verbergen als versehentlich offenlegen.
        # M24/AP4 (Refs #1738): derselbe FILE-Zweig wie für zugeordnete Felder —
        # ein verwaister Datei-Slug darf ebenso wenig als Roh-Dict erscheinen.
        entry = _display_entry(
            ft.name if ft is not None else slug,
            values[slug],
            field_sensitivity=orphan_gating_sensitivity(ft),
            is_encrypted=ft.is_encrypted if ft is not None else False,
            ft=ft,
            user=user,
            doc_sensitivity=doc_sensitivity,
            attachments_by_pk=attachments_by_pk,
            predecessor_index=predecessor_index,
            orphaned=True,
        )
        if entry is not None:
            entries.append(entry)
    return entries


def build_dynamic_data_context(
    document_type: DocumentType | None,
    data_json: Mapping[str, Any] | None,
    user: Any,
    owner: Any = None,
) -> dict[str, Any]:
    """Kontext für ``core/partials/dynamic_data_display.html`` (E18, Refs #1058).

    Die Sensitivität des ``DocumentType`` gated die **ganze** Sektion: darf
    *user* den Typ nicht sehen, liefert der Helfer ``dynamic_document_type =
    None`` und leere Feldlisten — das Partial rendert dann gar nichts, und
    weder Typname noch Feldlabels gelangen ins HTML (#774-Muster).

    Rückgabe-Keys (Vertrag des Partials):

    * ``dynamic_document_type`` — ``DocumentType`` oder ``None`` (Gate),
    * ``dynamic_fields`` — Liste aus :func:`build_dynamic_fields_display`,
    * ``dynamic_orphan_fields`` — Liste aus :func:`build_orphan_fields_display`
      (verwaiste Slugs, Refs #1725(a); leer, wenn keine existieren).

    ``owner`` (M24/AP4, Refs #1738): Träger der ``data_json`` (Person oder
    Fall). Nur mit ihm lassen sich Datei-Marker zu Anhängen auflösen; ohne ihn
    bleibt der FILE-Zweig stumm (kein Roh-Dict im HTML).
    """
    if document_type is None or not user_can_see_document_type(user, document_type):
        return {"dynamic_document_type": None, "dynamic_fields": [], "dynamic_orphan_fields": []}
    return {
        "dynamic_document_type": document_type,
        "dynamic_fields": build_dynamic_fields_display(document_type, data_json, user, owner=owner),
        "dynamic_orphan_fields": build_orphan_fields_display(document_type, data_json, user, owner=owner),
    }
