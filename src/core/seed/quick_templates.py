"""Seed-QuickTemplates: vorbefüllte Dokumentvorlagen für Schnelleinträge.

Läuft für **alle** Seed-Scales (im seed-Command unbedingt aufgerufen), damit
Screenshots/Demo den Schnelleintrags-Workflow zeigen.

Die Vorlagen hängen bewusst an DocumentTypes mit Sensitivität NORMAL
(``Kontakt``, ``Begleitung``, ``Notiz``) und befüllen nur NORMAL-Felder —
so überleben die Werte den Sensitivitäts-Whitelist-Filter aus
``core.services.dashboard.quick_templates.filter_prefilled_data``.

Refs #494, #1003, #1004.
"""

from __future__ import annotations

from core.models import DocumentType, QuickTemplate

# DocumentType-Name -> Liste (Anzeigename, prefilled_data).
# prefilled_data: Feld-Slug -> Wert. Slugs müssen zu den von
# ``seed_document_types`` erzeugten FieldTemplate-Slugs passen.
_TEMPLATES: dict[str, list[tuple[str, dict]]] = {
    "Kontakt": [
        ("Frühstück & Beratung", {"dauer": 15, "leistungen": ["essen", "beratung"]}),
        ("Dusche & Wäsche", {"dauer": 20, "leistungen": ["duschen", "waesche"]}),
        ("Kurz-Check-in", {"dauer": 5, "leistungen": ["post"]}),
    ],
    "Begleitung": [
        ("Behördengang Jobcenter", {"ziel": "Jobcenter"}),
        ("Arztbegleitung", {"ziel": "Hausarzt"}),
    ],
    "Notiz": [
        ("Team-Info", {"notiz": "Bitte im Blick behalten."}),
    ],
}


def seed_quick_templates(facility) -> list[QuickTemplate]:
    """Erzeugt QuickTemplates pro NORMAL-DocumentType der Facility (idempotent)."""
    created: list[QuickTemplate] = []
    doc_types = {dt.name: dt for dt in DocumentType.objects.filter(facility=facility)}
    sort_order = 0
    for dt_name, templates in _TEMPLATES.items():
        doc_type = doc_types.get(dt_name)
        if doc_type is None:
            continue
        for name, prefilled in templates:
            tpl, _ = QuickTemplate.objects.get_or_create(
                facility=facility,
                document_type=doc_type,
                name=name,
                defaults={
                    "prefilled_data": prefilled,
                    "sort_order": sort_order,
                    "is_active": True,
                },
            )
            created.append(tpl)
            sort_order += 1
    return created
