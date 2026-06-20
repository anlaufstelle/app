"""Diff- und Maskierungs-Logik fuer EventHistory-Eintraege (Refs #1162).

Sicherheitskritischer Service-Layer: berechnet aus einem ``EventHistory``-
Eintrag die anzuzeigenden Diff-Felder und maskiert dabei verschluesselte
(``encrypted``) und fuer die Rolle nicht sichtbare (``restricted``) Werte.

Vorrang der Maskierung (byte-identisch zur frueheren Templatetag-Logik):

    restricted  >  encrypted  >  Roh-/Formatierungswert

``restricted`` greift ausschliesslich, wenn ein ``user`` uebergeben wird und
:func:`core.services.compliance.user_can_see_field` False liefert (die
effektive Feld-Sensitivity uebersteigt das Rollen-Maximum). Ohne ``user`` ist
nie etwas restricted.

Der Templatetag ``core.templatetags.history_tags.compute_diff`` ist nur noch
ein duenner Wrapper um :func:`compute_event_diff`.
"""

from core.services.compliance import user_can_see_field

ENCRYPTED_PLACEHOLDER = "●●●●●"
RESTRICTED_PLACEHOLDER = "[Eingeschränkt]"


def _build_slug_info_from_metadata(field_metadata):
    """Convert stored field_metadata to slug_info format."""
    return {
        slug: {
            "name": meta["name"],
            "is_encrypted": meta.get("is_encrypted", False),
            "sensitivity": meta.get("sensitivity", ""),
        }
        for slug, meta in field_metadata.items()
    }


def _build_slug_info(event):
    """Build a dict mapping slugs to {name, is_encrypted, sensitivity} for an event's document type."""
    slug_info = {}
    for dtf in event.document_type.fields.select_related("field_template"):
        ft = dtf.field_template
        slug_info[ft.slug] = {
            "name": ft.name,
            "is_encrypted": ft.is_encrypted,
            "sensitivity": ft.sensitivity,
        }
    return slug_info


def _display_value(value, is_encrypted, *, restricted=False):
    """Return display string for a value, masking encrypted or restricted fields."""
    if restricted:
        return RESTRICTED_PLACEHOLDER
    if is_encrypted:
        return ENCRYPTED_PLACEHOLDER
    if value is None:
        return "–"
    if isinstance(value, bool):
        return "Ja" if value else "Nein"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _resolve_label(slug, slug_info):
    """Resolve a slug to a human-readable label."""
    info = slug_info.get(slug)
    if info:
        return info["name"]
    return slug.replace("-", " ").title()


def _is_encrypted(slug, slug_info):
    """Check whether a slug corresponds to an encrypted field."""
    info = slug_info.get(slug)
    return info["is_encrypted"] if info else False


def _is_restricted(slug, slug_info, user, doc_type_sensitivity):
    """Check whether a slug is restricted for the given user based on sensitivity."""
    if user is None:
        return False
    info = slug_info.get(slug)
    field_sensitivity = info["sensitivity"] if info else ""
    return not user_can_see_field(user, doc_type_sensitivity, field_sensitivity)


def _resolve_slug_info(entry):
    """Prefer frozen metadata snapshot; fall back to live FieldTemplates for legacy entries.

    Refs #824 (C-57): ``build_event_detail_context`` haengt einmalig
    ``_slug_info`` an alle Entries — der Aufrufer spart so pro Entry einen
    Field-Query.
    """
    if entry.field_metadata:
        return _build_slug_info_from_metadata(entry.field_metadata)
    if getattr(entry, "_slug_info", None):
        return entry._slug_info
    return _build_slug_info(entry.event)


def _field_masking(slug, slug_info, user, doc_type_sensitivity):
    """Return the (encrypted, restricted) masking flags for a single slug."""
    encrypted = _is_encrypted(slug, slug_info)
    restricted = _is_restricted(slug, slug_info, user, doc_type_sensitivity)
    return encrypted, restricted


def _diff_create(data_after, slug_info, user, doc_type_sensitivity):
    """Build diff entries for a CREATE action: [{label, value}]."""
    fields = []
    for slug, value in data_after.items():
        encrypted, restricted = _field_masking(slug, slug_info, user, doc_type_sensitivity)
        fields.append(
            {
                "label": _resolve_label(slug, slug_info),
                "value": _display_value(value, encrypted, restricted=restricted),
            }
        )
    return fields


def _diff_update(data_before, data_after, slug_info, user, doc_type_sensitivity):
    """Build diff entries for an UPDATE action: [{label, old_value, new_value, changed}]."""
    fields = []
    all_keys = list(dict.fromkeys(list(data_before.keys()) + list(data_after.keys())))
    for slug in all_keys:
        old_val = data_before.get(slug)
        new_val = data_after.get(slug)
        if old_val == new_val:
            continue
        encrypted, restricted = _field_masking(slug, slug_info, user, doc_type_sensitivity)
        fields.append(
            {
                "label": _resolve_label(slug, slug_info),
                "old_value": _display_value(old_val, encrypted, restricted=restricted),
                "new_value": _display_value(new_val, encrypted, restricted=restricted),
                "changed": True,
            }
        )
    return fields


def _redacted_delete_value(encrypted, restricted):
    """Display value for a single field of a redacted soft-delete (no original values stored)."""
    if restricted:
        return RESTRICTED_PLACEHOLDER
    if encrypted:
        return ENCRYPTED_PLACEHOLDER
    return "– (gelöscht)"


def _diff_delete(data_before, slug_info, user, doc_type_sensitivity):
    """Build diff entries for a DELETE action: [{label, value}].

    Redacted soft-deletes store only field names (no values); legacy/full
    deletes still carry the original values.
    """
    fields = []
    if data_before.get("_redacted"):
        for slug in data_before.get("fields", []):
            encrypted, restricted = _field_masking(slug, slug_info, user, doc_type_sensitivity)
            fields.append(
                {
                    "label": _resolve_label(slug, slug_info),
                    "value": _redacted_delete_value(encrypted, restricted),
                }
            )
    else:
        for slug, value in data_before.items():
            encrypted, restricted = _field_masking(slug, slug_info, user, doc_type_sensitivity)
            fields.append(
                {
                    "label": _resolve_label(slug, slug_info),
                    "value": _display_value(value, encrypted, restricted=restricted),
                }
            )
    return fields


def compute_event_diff(entry, user=None):
    """Compute diff information for an EventHistory entry.

    Args:
        entry: EventHistory instance.
        user: Optional User instance. When set, fields the user may not see
              based on sensitivity are masked with ``[Eingeschränkt]``.

    Returns a dict with:
      - action: 'create' | 'update' | 'delete'
      - fields: list of dicts with keys depending on action
        CREATE:  [{label, value}]
        UPDATE:  [{label, old_value, new_value, changed}]
        DELETE:  [{label, value}]
    """
    slug_info = _resolve_slug_info(entry)
    doc_type_sensitivity = entry.event.document_type.sensitivity
    action = entry.action
    data_before = entry.data_before or {}
    data_after = entry.data_after or {}

    if action == "create":
        fields = _diff_create(data_after, slug_info, user, doc_type_sensitivity)
    elif action == "update":
        fields = _diff_update(data_before, data_after, slug_info, user, doc_type_sensitivity)
    elif action == "delete":
        fields = _diff_delete(data_before, slug_info, user, doc_type_sensitivity)
    else:
        fields = []

    return {
        "action": action,
        "fields": fields,
    }
