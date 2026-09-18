"""Template tags and filters for Anlaufstelle Core."""

import json

from django import template
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext as _

register = template.Library()


@register.simple_tag
def aria_field(field):
    """Refs #809 (C-42): rendert ein Form-Feld mit ARIA-Fehler-Annotation.

    Setzt ``aria-invalid="true"`` falls Validierungsfehler vorliegen und
    verknuepft via ``aria-describedby`` mit den Help-/Error-Spans, deren
    IDs ``<field-id>-help`` und ``<field-id>-error`` sind. Bei
    Pflichtfeldern setzt das Tag zusaetzlich ``aria-required="true"`` —
    das `*`-Sternchen im Label bleibt visuell, ist aber nicht der
    einzige Indikator (WCAG 1.3.1).
    """
    attrs: dict[str, str] = {}
    described_by: list[str] = []
    if getattr(field.field, "help_text", ""):
        described_by.append(f"{field.id_for_label}-help")
    if field.errors:
        described_by.append(f"{field.id_for_label}-error")
        attrs["aria-invalid"] = "true"
    if described_by:
        attrs["aria-describedby"] = " ".join(described_by)
    if field.field.required:
        attrs["aria-required"] = "true"
    return field.as_widget(attrs=attrs)


@register.filter
def get_item(container, key):
    """Dict-Lookup via Key im Template (z.B. ``existing_attachments|get_item:slug``).

    Gibt ``None`` zurück, wenn Key fehlt oder Container kein Dict ist.
    """
    if container is None:
        return None
    try:
        return container.get(key)
    except AttributeError:
        return None


@register.filter
def split(value, sep=","):
    """Split a string into a list for true membership tests in templates.

    Django's ``{% if x in 'a,b,c' %}`` tests substring containment on a string,
    not list membership — so ``'dashboard'`` matches ``'system_dashboard'``.
    Piping through ``|split:','`` yields a real list, making ``in`` an exact-
    element check (Nav-Aktivmarkierung, vermeidet Substring-Kollisionen).

    Die einzigen Konsumenten waren die Nav-Templates; seit #1813 kommen die
    Aktiv-Familien als Python-Mengen aus ``core.nav``, wo die Element-Semantik
    strukturell gilt statt per Filter hergestellt zu werden. Der Filter bleibt
    als allgemeines Werkzeug — und als Merkposten für die Fehlerklasse.
    """
    if value is None:
        return []
    return str(value).split(sep)


@register.filter
def decrypt(value):
    """Decrypt a field value, or return [encrypted] as fallback."""
    from core.services.file_vault import is_encrypted_value, safe_decrypt

    if is_encrypted_value(value):
        return safe_decrypt(value, default="[verschlüsselt]")
    return value


@register.filter
def display_dynamic_value(value):
    """Rendert einen dynamischen Feldwert für die Detailanzeige (Refs #2243, #2244).

    Gemeinsame Stelle für ``core/events/detail.html`` und
    ``core/partials/dynamic_data_display.html`` — beide Templates rufen
    diesen Filter statt ``field.value`` roh oder nur mit
    ``default_if_none:""`` auszugeben.

    - Leerwert (``None``/``""``/``[]``): visuell weiterhin leer (kein
      sichtbarer Platzhalter — Refs #2220/#2224), aber ein ``sr-only``-Text
      „Nicht angegeben" für Screenreader (Maintainer-Entscheid 2026-09-18,
      #2244). Exporte/PDF und die Änderungshistorie rufen diesen Filter
      NICHT auf und bleiben unverändert.
    - ``list`` (Refs #2243: ``multi_select`` ohne ``options_json`` liefert die
      rohen Slugs statt gelabelter Strings — ``format_dynamic_display_value``
      reicht sie unverändert durch): kommagetrennte, escapte Aufzählung statt
      der Python-``repr()``-Darstellung (``['a']``).
    - alles andere (bereits formatierte Strings, z. B. Select-Labels oder
      „[Eingeschränkt]"): unverändert — Django escaped beim Rendern
      automatisch.
    """
    if value is None or value == "" or value == []:
        return format_html('<span class="sr-only">{}</span>', _("Nicht angegeben"))
    if isinstance(value, list):
        return format_html_join(", ", "{}", ((item,) for item in value))
    return value


@register.filter
def pretty_json(value):
    """Render a dict/list as indented JSON wrapped in <pre><code>.

    Uses format_html so that any HTML metacharacters in keys or string values
    (e.g. user-controlled data in AuditLog.detail like failed-login usernames
    or client pseudonyms) are escaped before reaching the DOM. Prevents stored
    XSS in the audit detail view.
    """
    if not value:
        return "–"
    try:
        formatted = json.dumps(value, indent=2, ensure_ascii=False)
        return format_html(
            '<pre class="text-sm bg-gray-50 rounded p-3 overflow-x-auto"><code>{}</code></pre>',
            formatted,
        )
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


# --- Semantic status badge ---
# Maps a semantic status (open/closed/pending/...) to a color in _BADGE_COLOR_MAP.
# Single source of truth for status colors across the app.
_STATUS_COLOR_MAP = {
    "open": "green",
    "active": "green",
    "success": "green",
    "done": "green",
    "completed": "green",
    "closed": "gray",
    "discarded": "gray",
    "neutral": "gray",
    "draft": "gray",
    "deferred": "gray",
    "pending": "amber",
    "warning": "amber",
    "important": "amber",
    "yellow": "amber",
    "danger": "red",
    "error": "red",
    "urgent": "red",
    "rejected": "red",
    "info": "blue",
    # Retention: RetentionProposal.Status.HELD ("Legal Hold") / APPROVED
    # ("Freigegeben"). Routet die Löschvorschlag-Badges über die zentrale Karte
    # statt hartkodierter Labels/Shades (Refs #1478, COD-03).
    "held": "blue",
    "approved": "green",
    "qualified": "purple",
}


@register.simple_tag
def status_badge(status, label):
    """Render a semantic status badge.

    Usage::

        {% status_badge case.status case.get_status_display %}
        {% status_badge "urgent" "Dringend" %}

    The ``status`` value is mapped through ``_STATUS_COLOR_MAP`` to one of the
    palette colors in ``_BADGE_COLOR_MAP``. Unknown statuses fall back to gray.
    Gerendert über ``components/_badge.html`` (Single Source, Refs #1016 C5).
    """
    color = _STATUS_COLOR_MAP.get(str(status).lower(), "gray")
    return render_to_string("components/_badge.html", {"text": label, "color": color})


@register.filter
def target_type_label(activity):
    """Return the localized label for an Activity's target content type."""
    labels = {
        "client": _("Person"),
        "event": _("Kontakt"),
        "workitem": _("Aufgabe"),
        "case": _("Fall"),
        "milestone": _("Meilenstein"),
    }
    model_name = activity.target_type.model if activity.target_type else None
    if model_name and model_name in labels:
        return labels[model_name]
    if model_name:
        return model_name.capitalize()
    return ""


@register.filter
def activity_summary(activity):
    """Render an Activity through the shared PII-free display-time renderer."""
    from core.services.dashboard import render_activity_summary

    return render_activity_summary(activity)


# --- Activity target URL ---
_MODEL_URL_MAP = {
    "client": "core:client_detail",
    "event": "core:event_detail",
    "workitem": "core:workitem_detail",
    "case": "core:case_detail",
}


@register.simple_tag
def activity_target_url(activity):
    """Resolve a detail URL for an Activity's GenericFK target.

    Zwei Sperren, nicht eine: ``verb == "deleted"`` faengt die Zeile ab, die
    die Loeschung SELBST protokolliert — aber nicht die aelteren
    ``created``/``updated``-Zeilen desselben Objekts. Fuer Faelle ist das seit
    dem Loeschpfad (Refs #1818, ADR-044) ein echter Verweis ins Leere: der Fall
    im Papierkorb faellt aus ``cases_with_live_client()``, ``case_detail``
    antwortet 404, und ein Kartenlink dorthin sieht nach kaputter Anwendung
    aus. Der Zeitstrom filtert diese Karten bereits weg
    (:func:`core.services.events.feed.build_feed_items`); diese Sperre ist die
    zweite Verteidigungslinie fuer jede kuenftige Flaeche, die
    ``_activity_card.html`` einbindet, ohne durch den Feed zu laufen.
    """
    if activity.verb == "deleted":
        return ""
    model_name = activity.target_type.model if activity.target_type else None
    url_name = _MODEL_URL_MAP.get(model_name)
    if not url_name:
        return ""
    if model_name == "case":
        from core.models import Case

        if not Case.objects.filter(pk=activity.target_id, is_deleted=False).exists():
            return ""
    try:
        return reverse(url_name, args=[activity.target_id])
    except Exception:
        return ""
