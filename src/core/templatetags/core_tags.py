"""Template tags and filters for Anlaufstelle Core."""

import json

from django import template
from django.urls import reverse
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def decrypt(value):
    """Decrypt a field value, or return [encrypted] as fallback."""
    from core.services.encryption import is_encrypted_value, safe_decrypt

    if is_encrypted_value(value):
        return safe_decrypt(value, default="[verschlüsselt]")
    return value


@register.filter
def pretty_json(value):
    """Render a dict/list as indented JSON wrapped in <pre><code>."""
    if not value:
        return "–"
    try:
        formatted = json.dumps(value, indent=2, ensure_ascii=False)
        return mark_safe(f'<pre class="text-sm bg-gray-50 rounded p-3 overflow-x-auto"><code>{formatted}</code></pre>')
    except (TypeError, ValueError):
        return str(value)


@register.filter
def json_summary(value):
    """One-line summary of a JSON detail dict for table display."""
    if not value:
        return "–"
    if isinstance(value, dict):
        parts = [f"{k}: {v}" for k, v in value.items()]
        return ", ".join(parts)
    return str(value)


# --- Badge color classes (Tailwind-safe: full strings for purge scanning) ---
_BADGE_COLOR_MAP = {
    "indigo": "bg-indigo-100 text-indigo-800",
    "amber": "bg-amber-100 text-amber-800",
    "red": "bg-red-100 text-red-800",
    "green": "bg-green-100 text-green-800",
    "blue": "bg-blue-100 text-blue-800",
    "purple": "bg-purple-100 text-purple-800",
    "teal": "bg-teal-100 text-teal-800",
    "rose": "bg-rose-100 text-rose-800",
    "gray": "bg-gray-100 text-gray-800",
}
_DEFAULT_BADGE_CLASSES = "bg-indigo-100 text-indigo-800"


@register.filter
def doctype_badge_classes(color):
    """Return Tailwind badge classes for a DocumentType color string."""
    if not color:
        return _DEFAULT_BADGE_CLASSES
    return _BADGE_COLOR_MAP.get(color, _DEFAULT_BADGE_CLASSES)


# --- Activity verb badge ---
_VERB_COLOR_MAP = {
    "updated": "gray",
    "deleted": "red",
    "qualified": "indigo",
    "completed": "green",
    "reopened": "amber",
}


@register.filter
def verb_badge_classes(verb):
    """Return Tailwind badge classes for an Activity verb string."""
    color = _VERB_COLOR_MAP.get(verb, "gray")
    return _BADGE_COLOR_MAP[color]


# --- Activity target type label (German) ---
_TARGET_TYPE_LABEL_MAP = {
    "client": "Klientel",
    "event": "Kontakt",
    "workitem": "Aufgabe",
    "case": "Fall",
}


@register.filter
def target_type_label(activity):
    """Return a German label for an Activity's target content type."""
    model_name = activity.target_type.model if activity.target_type else None
    if model_name and model_name in _TARGET_TYPE_LABEL_MAP:
        return _TARGET_TYPE_LABEL_MAP[model_name]
    if model_name:
        return model_name.capitalize()
    return ""


# --- Activity target URL ---
_MODEL_URL_MAP = {
    "client": "core:client_detail",
    "event": "core:event_detail",
    "workitem": "core:workitem_detail",
    "case": "core:case_detail",
}


@register.simple_tag
def activity_target_url(activity):
    """Resolve a detail URL for an Activity's GenericFK target."""
    if activity.verb == "deleted":
        return ""
    model_name = activity.target_type.model if activity.target_type else None
    url_name = _MODEL_URL_MAP.get(model_name)
    if not url_name:
        return ""
    try:
        return reverse(url_name, args=[activity.target_id])
    except Exception:
        return ""
