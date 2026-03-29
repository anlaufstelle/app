"""Template tags for EventHistory diff display."""

from django import template

register = template.Library()

ENCRYPTED_PLACEHOLDER = "\u25cf\u25cf\u25cf\u25cf\u25cf"


def _build_slug_info(event):
    """Build a dict mapping slugs to {name, is_encrypted} for an event's document type."""
    slug_info = {}
    for dtf in event.document_type.fields.select_related("field_template"):
        ft = dtf.field_template
        slug_info[ft.slug] = {
            "name": ft.name,
            "is_encrypted": ft.is_encrypted,
        }
    return slug_info


def _display_value(value, is_encrypted):
    """Return display string for a value, masking encrypted fields."""
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


@register.simple_tag
def compute_diff(entry):
    """Compute diff information for an EventHistory entry.

    Returns a dict with:
      - action: 'create' | 'update' | 'delete'
      - fields: list of dicts with keys depending on action
        CREATE:  [{label, value}]
        UPDATE:  [{label, old_value, new_value, changed}]
        DELETE:  [{label, value}]
    """
    slug_info = _build_slug_info(entry.event)
    action = entry.action
    data_before = entry.data_before or {}
    data_after = entry.data_after or {}

    fields = []

    if action == "create":
        for slug, value in data_after.items():
            encrypted = _is_encrypted(slug, slug_info)
            fields.append(
                {
                    "label": _resolve_label(slug, slug_info),
                    "value": _display_value(value, encrypted),
                }
            )

    elif action == "update":
        all_keys = list(dict.fromkeys(list(data_before.keys()) + list(data_after.keys())))
        for slug in all_keys:
            encrypted = _is_encrypted(slug, slug_info)
            old_val = data_before.get(slug)
            new_val = data_after.get(slug)
            changed = old_val != new_val
            if changed:
                fields.append(
                    {
                        "label": _resolve_label(slug, slug_info),
                        "old_value": _display_value(old_val, encrypted),
                        "new_value": _display_value(new_val, encrypted),
                        "changed": True,
                    }
                )

    elif action == "delete":
        if data_before.get("_redacted"):
            # Redacted soft-delete: only field names are stored, no values.
            for slug in data_before.get("fields", []):
                encrypted = _is_encrypted(slug, slug_info)
                fields.append(
                    {
                        "label": _resolve_label(slug, slug_info),
                        "value": ENCRYPTED_PLACEHOLDER if encrypted else "\u2013 (gelöscht)",
                    }
                )
        else:
            for slug, value in data_before.items():
                encrypted = _is_encrypted(slug, slug_info)
                fields.append(
                    {
                        "label": _resolve_label(slug, slug_info),
                        "value": _display_value(value, encrypted),
                    }
                )

    return {
        "action": action,
        "fields": fields,
    }
