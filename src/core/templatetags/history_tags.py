"""Template tags for EventHistory diff display."""

from django import template

from core.services.sensitivity import user_can_see_field

register = template.Library()

ENCRYPTED_PLACEHOLDER = "\u25cf\u25cf\u25cf\u25cf\u25cf"
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
        return "\u2013"
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


@register.simple_tag
def compute_diff(entry, user=None):
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
    # Prefer frozen metadata snapshot; fall back to live FieldTemplates for legacy entries.
    if entry.field_metadata:
        slug_info = _build_slug_info_from_metadata(entry.field_metadata)
    else:
        slug_info = _build_slug_info(entry.event)
    doc_type_sensitivity = entry.event.document_type.sensitivity
    action = entry.action
    data_before = entry.data_before or {}
    data_after = entry.data_after or {}

    fields = []

    if action == "create":
        for slug, value in data_after.items():
            encrypted = _is_encrypted(slug, slug_info)
            restricted = _is_restricted(slug, slug_info, user, doc_type_sensitivity)
            fields.append(
                {
                    "label": _resolve_label(slug, slug_info),
                    "value": _display_value(value, encrypted, restricted=restricted),
                }
            )

    elif action == "update":
        all_keys = list(dict.fromkeys(list(data_before.keys()) + list(data_after.keys())))
        for slug in all_keys:
            encrypted = _is_encrypted(slug, slug_info)
            restricted = _is_restricted(slug, slug_info, user, doc_type_sensitivity)
            old_val = data_before.get(slug)
            new_val = data_after.get(slug)
            changed = old_val != new_val
            if changed:
                fields.append(
                    {
                        "label": _resolve_label(slug, slug_info),
                        "old_value": _display_value(old_val, encrypted, restricted=restricted),
                        "new_value": _display_value(new_val, encrypted, restricted=restricted),
                        "changed": True,
                    }
                )

    elif action == "delete":
        if data_before.get("_redacted"):
            # Redacted soft-delete: only field names are stored, no values.
            for slug in data_before.get("fields", []):
                encrypted = _is_encrypted(slug, slug_info)
                restricted = _is_restricted(slug, slug_info, user, doc_type_sensitivity)
                if restricted:
                    value = RESTRICTED_PLACEHOLDER
                elif encrypted:
                    value = ENCRYPTED_PLACEHOLDER
                else:
                    value = "\u2013 (gelöscht)"
                fields.append(
                    {
                        "label": _resolve_label(slug, slug_info),
                        "value": value,
                    }
                )
        else:
            for slug, value in data_before.items():
                encrypted = _is_encrypted(slug, slug_info)
                restricted = _is_restricted(slug, slug_info, user, doc_type_sensitivity)
                fields.append(
                    {
                        "label": _resolve_label(slug, slug_info),
                        "value": _display_value(value, encrypted, restricted=restricted),
                    }
                )

    return {
        "action": action,
        "fields": fields,
    }
