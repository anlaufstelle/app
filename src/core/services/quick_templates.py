"""Service-Logik für Quick-Templates.

Refs #494.
"""

from core.models import DocumentType, QuickTemplate
from core.services.sensitivity import effective_sensitivity, user_can_see_document_type


def list_templates_for_user(user, facility):
    """Liefert aktive Quick-Templates, gefiltert nach Sensitivität.

    Der User sieht ein Template nur, wenn er den zugehörigen ``DocumentType``
    sehen darf (Role-Sensitivity-Cap). So erscheinen Templates für
    ELEVATED/HIGH-DocTypes nicht für Assistenzen.

    Sortierung: ``sort_order`` (aufsteigend), dann ``name``.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return []

    qs = (
        QuickTemplate.objects.for_facility(facility)
        .filter(is_active=True)
        .select_related("document_type")
        .order_by("sort_order", "name")
    )
    return [t for t in qs if user_can_see_document_type(user, t.document_type)]


def _active_option_slugs(field_template):
    """Hilfsfunktion: Gibt die Slugs aller aktiven Options zurück."""
    return {o["slug"] for o in (field_template.options_json or []) if o.get("is_active", True) and "slug" in o}


def _filter_active_choices(field_template, value):
    """Entfernt deaktivierte SELECT/MULTI_SELECT-Werte.

    Siehe Audit-Finding #2 in #494: Templates sollen sich bei Option-
    Deaktivierung automatisch "heilen".
    """
    from core.models import FieldTemplate

    active = _active_option_slugs(field_template)
    if field_template.field_type == FieldTemplate.FieldType.SELECT:
        return value if value in active else None
    if field_template.field_type == FieldTemplate.FieldType.MULTI_SELECT:
        if not isinstance(value, list):
            return []
        return [v for v in value if v in active]
    return value


def filter_prefilled_data(document_type, raw_data):
    """Whitelist-Filter für ``prefilled_data``.

    Zulässig sind ausschließlich Slugs, deren effektive Sensitivität =
    NORMAL ist und die nicht verschlüsselt sind / kein FILE-Feld sind.
    Unbekannte oder verschlüsselte Slugs werden verworfen.

    Zusätzlich werden SELECT/MULTI_SELECT-Werte gegen die aktuell aktiven
    Options geprüft (siehe :func:`_filter_active_choices`).
    """
    from core.models import FieldTemplate

    if not raw_data:
        return {}

    field_templates = {
        dtf.field_template.slug: dtf.field_template for dtf in document_type.fields.select_related("field_template")
    }
    cleaned = {}
    for slug, value in raw_data.items():
        ft = field_templates.get(slug)
        if ft is None:
            continue
        if ft.is_encrypted or ft.field_type == FieldTemplate.FieldType.FILE:
            continue
        rank = effective_sensitivity(document_type.sensitivity, ft.sensitivity)
        if rank != 0:  # 0 == NORMAL
            continue
        filtered_value = _filter_active_choices(ft, value)
        if filtered_value is None:
            continue
        if ft.field_type == FieldTemplate.FieldType.MULTI_SELECT and not filtered_value:
            continue
        cleaned[slug] = filtered_value
    return cleaned


def apply_template(template, form_data=None):
    """Füllt fehlende Felder in ``form_data`` mit Template-Werten.

    Bestehende Werte in ``form_data`` werden *nicht* überschrieben — das
    Template liefert nur Defaults. ``prefilled_data`` wird vor dem Merge
    durch :func:`filter_prefilled_data` geleitet, damit zwischenzeitlich
    deaktivierte Options oder erhöhte Sensitivitäten nicht durchschlagen.
    """
    merged = dict(form_data or {})
    cleaned = filter_prefilled_data(template.document_type, template.prefilled_data or {})
    for slug, value in cleaned.items():
        if slug not in merged or merged[slug] in (None, "", []):
            merged[slug] = value
    return merged


def get_template_for_user(user, facility, template_id):
    """Lädt ein Template, nur wenn sichtbar für den User.

    Gibt ``None`` zurück, wenn das Template in einer anderen Facility liegt,
    inaktiv ist oder der User den zugehörigen DocumentType nicht sehen darf.
    """
    try:
        template = (
            QuickTemplate.objects.for_facility(facility)
            .select_related("document_type")
            .get(pk=template_id, is_active=True)
        )
    except (QuickTemplate.DoesNotExist, ValueError):
        return None
    if not user_can_see_document_type(user, template.document_type):
        return None
    return template


def get_templates_for_document_type(user, facility, document_type):
    """Liefert sichtbare aktive Templates für genau einen DocumentType."""
    if not user_can_see_document_type(user, document_type):
        return []
    if not isinstance(document_type, DocumentType):
        return []
    qs = (
        QuickTemplate.objects.for_facility(facility)
        .filter(is_active=True, document_type=document_type)
        .order_by("sort_order", "name")
    )
    return list(qs)
