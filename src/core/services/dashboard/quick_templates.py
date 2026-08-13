"""Service-Logik für Quick-Templates.

Refs #494.
"""

from core.models import DocumentType, QuickTemplate
from core.services.compliance import effective_sensitivity, user_can_see_document_type
from core.services.system.field_types import active_option_slugs


def list_templates_for_user(user, facility):
    """Liefert aktive Quick-Templates, gefiltert nach Sensitivität und entity_type.

    Der User sieht ein Template nur, wenn er den zugehörigen ``DocumentType``
    sehen darf (Role-Sensitivity-Cap). So erscheinen Templates für
    ELEVATED/HIGH-DocTypes nicht für Assistenzen. M2 (Refs #1058, Fix-Runde 1
    F-2): zusätzlich nur Templates, deren ``DocumentType.entity_type=event``
    ist — Quick-Templates sind ein Event-Recording-Feature; ein Template auf
    einem client-/case-Typ fällt aus der Liste, als gäbe es es nicht.

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
    return [
        t
        for t in qs
        if t.document_type.entity_type == DocumentType.EntityType.EVENT
        and user_can_see_document_type(user, t.document_type)
    ]


def _filter_active_choices(field_template, value):
    """Entfernt deaktivierte SELECT/MULTI_SELECT-Werte.

    Siehe #2 in #494: Templates sollen sich bei Option-
    Deaktivierung automatisch "heilen".
    """
    from core.models import FieldTemplate

    active = active_option_slugs(field_template.options_json)
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
    inaktiv ist, der User den zugehörigen DocumentType nicht sehen darf, oder
    (M2, Refs #1058, Fix-Runde 1 F-2) der DocumentType kein Event-Typ ist —
    ein client-/case-Typ-Template wirkt für den Aufrufer wie „kein Template",
    exakt das Fallback-Verhalten von :func:`core.services.events.context.resolve_default_document_type`.
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
    if template.document_type.entity_type != DocumentType.EntityType.EVENT:
        return None
    return template


def get_templates_for_document_type(user, facility, document_type):
    """Liefert sichtbare aktive Templates für genau einen DocumentType.

    M2 (Refs #1058, Fix-Runde 1 F-2): ``document_type`` muss zusätzlich ein
    Event-Typ sein — Defense-in-Depth, konsistent mit den beiden anderen
    Funktionen dieses Moduls (der einzige heutige Aufrufer,
    ``EventCreateView.get``, uebergibt ohnehin nur bereits entity_type-
    gefilterte ``default_doc_type``-Werte).
    """
    if not user_can_see_document_type(user, document_type):
        return []
    if not isinstance(document_type, DocumentType):
        return []
    if document_type.entity_type != DocumentType.EntityType.EVENT:
        return []
    qs = (
        QuickTemplate.objects.for_facility(facility)
        .filter(is_active=True, document_type=document_type)
        .order_by("sort_order", "name")
    )
    return list(qs)
