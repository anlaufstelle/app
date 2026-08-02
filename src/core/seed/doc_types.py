"""DocumentType + FieldTemplate + DocumentTypeField seeding."""

from core.models import DocumentType, DocumentTypeField, Facility, FieldGroup, FieldTemplate


def get_document_type_definitions() -> list[dict]:
    """Return the canonical list of document-type definitions."""
    return [
        {
            "name": "Kontakt",
            "category": DocumentType.Category.CONTACT,
            "system_type": "contact",
            "icon": "users",
            "color": "indigo",
            "sort_order": 0,
            "fields": [
                {"name": "Dauer", "slug": "dauer", "type": "number", "help_text": "Dauer in Minuten"},
                {
                    "name": "Leistungen",
                    "slug": "leistungen",
                    "type": "multi_select",
                    "options": [
                        {"slug": "beratung", "label": "Beratung", "is_active": True},
                        {"slug": "essen", "label": "Essen", "is_active": True},
                        {"slug": "kleidung", "label": "Kleidung", "is_active": True},
                        {"slug": "duschen", "label": "Duschen", "is_active": True},
                        {"slug": "waesche", "label": "Wäsche", "is_active": True},
                        {"slug": "telefon", "label": "Telefon", "is_active": True},
                        {"slug": "post", "label": "Post", "is_active": True},
                        {"slug": "sonstiges", "label": "Sonstiges", "is_active": True},
                        {"slug": "sachspenden", "label": "Sachspenden", "is_active": False},
                    ],
                },
                {
                    "name": "Alterscluster",
                    "slug": "alterscluster",
                    "type": "select",
                    "options": [
                        {"slug": "u18", "label": "U18", "is_active": True},
                        {"slug": "18-26", "label": "18-26", "is_active": True},
                        {"slug": "27-plus", "label": "27+", "is_active": True},
                        {"slug": "unbekannt", "label": "Unbekannt", "is_active": True},
                    ],
                    "help_text": "Geschätztes Alter",
                },
                {"name": "Notiz", "slug": "notiz", "type": "textarea"},
                {"name": "Straßenkontakt", "slug": "strassenkontakt", "type": "boolean"},
                # Refs #1142: Erreichbarer Datei-Upload im Default-Flow. Ohne
                # "encrypted": True im field_def bleibt die Seed-Default-Sensibilität
                # "" (NORMAL) — damit ist das Feld für Fachkraft/Assistenz sichtbar
                # (remove_restricted_fields). encrypted=True würde sensitivity="high"
                # setzen und das Feld wieder ausblenden. At-rest verschlüsselt wird
                # die Datei trotzdem: FieldTemplate.save() erzwingt is_encrypted=True
                # für FILE, store_encrypted_file verschlüsselt zusätzlich den Blob.
                {"name": "Anhang", "slug": "anhang", "type": FieldTemplate.FieldType.FILE, "required": False},
            ],
            # Beispiel-Feldgruppen (M0, Refs #1056): "strassenkontakt" bleibt
            # bewusst in keiner Gruppe gelistet — demonstriert "Ohne Gruppe"
            # im UI. Slugs müssen zu "fields" oben passen.
            "groups": [
                {"name": "Basisdaten", "sort_order": 0, "fields": ["dauer", "alterscluster"]},
                {
                    "name": "Leistungen & Notizen",
                    "sort_order": 1,
                    "is_collapsible": True,
                    "fields": ["leistungen", "notiz", "anhang"],
                },
            ],
        },
        {
            "name": "Krisengespräch",
            "category": DocumentType.Category.SERVICE,
            "system_type": "crisis",
            "sensitivity": DocumentType.Sensitivity.ELEVATED,
            "icon": "alert-triangle",
            "color": "amber",
            "sort_order": 1,
            "fields": [
                {
                    "name": "Art der Krise",
                    "slug": "art-der-krise",
                    "type": "select",
                    "options": [
                        {"slug": "suizidal", "label": "Suizidal", "is_active": True},
                        {"slug": "psychische-krise", "label": "Psychische Krise", "is_active": True},
                        {"slug": "substanzkrise", "label": "Substanzkrise", "is_active": True},
                        {"slug": "gewalt", "label": "Gewalt", "is_active": True},
                        {"slug": "obdachlosigkeit", "label": "Obdachlosigkeit", "is_active": True},
                        {"slug": "sonstiges", "label": "Sonstiges", "is_active": True},
                    ],
                },
                {"name": "Dauer", "slug": "dauer", "type": "number", "help_text": "Dauer in Minuten"},
                {"name": "Notiz (Krise)", "slug": "notiz-krise", "type": "textarea", "encrypted": True},
                {"name": "Weitervermittlung", "slug": "weitervermittlung", "type": "text"},
            ],
            "groups": [
                {
                    "name": "Krisendetails",
                    "sort_order": 0,
                    "fields": ["art-der-krise", "dauer", "notiz-krise", "weitervermittlung"],
                },
            ],
        },
        {
            "name": "Medizinische Versorgung",
            "category": DocumentType.Category.SERVICE,
            "system_type": "medical",
            "sensitivity": DocumentType.Sensitivity.HIGH,
            "icon": "heart",
            "color": "rose",
            "sort_order": 2,
            "fields": [
                {
                    "name": "Art der Versorgung",
                    "slug": "art-der-versorgung",
                    "type": "select",
                    "options": [
                        {"slug": "wundversorgung", "label": "Wundversorgung", "is_active": True},
                        {"slug": "medikamentenausgabe", "label": "Medikamentenausgabe", "is_active": True},
                        {"slug": "beratung", "label": "Beratung", "is_active": True},
                        {"slug": "sonstiges", "label": "Sonstiges", "is_active": True},
                    ],
                },
                {"name": "Notiz (Medizin)", "slug": "notiz-medizin", "type": "textarea", "encrypted": True},
                {"name": "Krankenhaus", "slug": "krankenhaus", "type": "boolean"},
            ],
        },
        {
            "name": "Spritzentausch",
            "category": DocumentType.Category.SERVICE,
            "system_type": "needle_exchange",
            "icon": "repeat",
            "color": "teal",
            "sort_order": 3,
            "fields": [
                {"name": "Ausgabe", "slug": "ausgabe", "type": "number", "required": True},
                {"name": "Rückgabe", "slug": "rueckgabe", "type": "number", "required": True},
            ],
        },
        {
            "name": "Begleitung",
            "category": DocumentType.Category.SERVICE,
            "system_type": "accompaniment",
            "icon": "map-pin",
            "color": "green",
            "sort_order": 4,
            "fields": [
                {"name": "Ziel", "slug": "ziel", "type": "text", "required": True},
                {"name": "Datum", "slug": "datum", "type": "date"},
                {"name": "Uhrzeit", "slug": "uhrzeit", "type": "time"},
                {"name": "Notiz (Begleitung)", "slug": "notiz-begleitung", "type": "textarea", "encrypted": True},
            ],
        },
        {
            "name": "Beratungsgespräch",
            "category": DocumentType.Category.SERVICE,
            "system_type": "counseling",
            "sensitivity": DocumentType.Sensitivity.ELEVATED,
            "min_contact_stage": "qualified",
            "icon": "message-circle",
            "color": "purple",
            "sort_order": 5,
            "fields": [
                {"name": "Thema", "slug": "thema", "type": "text", "encrypted": True},
                {"name": "Dauer", "slug": "dauer", "type": "number", "help_text": "Dauer in Minuten"},
                {"name": "Vereinbarungen", "slug": "vereinbarungen", "type": "textarea", "encrypted": True},
                {"name": "Nächster Termin", "slug": "naechster-termin", "type": "date"},
                {
                    "name": "Scan/Bescheid",
                    "slug": "scan-bescheid",
                    "type": FieldTemplate.FieldType.FILE,
                    "required": False,
                    "encrypted": True,
                },
            ],
        },
        {
            "name": "Vermittlung",
            "category": DocumentType.Category.SERVICE,
            "system_type": "referral",
            "icon": "share-2",
            "color": "blue",
            "sort_order": 6,
            "fields": [],
        },
        {
            "name": "Notiz",
            "category": DocumentType.Category.NOTE,
            "system_type": "note",
            "icon": "file-text",
            "color": "gray",
            "sort_order": 7,
            "fields": [
                {"name": "Notiz", "slug": "notiz", "type": "textarea"},
            ],
        },
        {
            "name": "Hausverbot",
            "category": DocumentType.Category.ADMIN,
            "system_type": "ban",
            "sensitivity": DocumentType.Sensitivity.ELEVATED,
            "icon": "slash",
            "color": "red",
            "sort_order": 8,
            "fields": [
                {"name": "Grund", "slug": "grund", "type": "textarea", "required": True},
                {"name": "Bis", "slug": "bis", "type": "date"},
                {"name": "Aktiv", "slug": "aktiv", "type": "boolean"},
            ],
        },
    ]


def seed_document_types(facility: Facility) -> None:
    """Create/refresh the standard document-type catalog for a facility."""
    doc_types = get_document_type_definitions()
    for dt_def in doc_types:
        # get_or_create uses (facility, name) as lookup.
        # On name collision the existing object is reused,
        # even if attributes differ (defaults only apply on create).
        defaults = {
            "category": dt_def["category"],
            "sensitivity": dt_def.get("sensitivity", DocumentType.Sensitivity.NORMAL),
            "icon": dt_def.get("icon", ""),
            "color": dt_def.get("color", ""),
            "sort_order": dt_def.get("sort_order", 0),
            "min_contact_stage": dt_def.get("min_contact_stage"),
        }
        if "system_type" in dt_def:
            defaults["system_type"] = dt_def["system_type"]
        dt, _ = DocumentType.objects.get_or_create(
            facility=facility,
            name=dt_def["name"],
            defaults=defaults,
        )
        for idx, field_def in enumerate(dt_def.get("fields", [])):
            # update_or_create: For the same (facility, slug) the existing
            # FieldTemplate is reused — defaults only apply on create.
            ft, _ = FieldTemplate.objects.update_or_create(
                facility=facility,
                slug=field_def["slug"],
                defaults={
                    "name": field_def["name"],
                    "field_type": field_def.get("type", FieldTemplate.FieldType.TEXT),
                    "is_required": field_def.get("required", False),
                    "is_encrypted": field_def.get("encrypted", False),
                    "sensitivity": field_def.get("sensitivity", "high" if field_def.get("encrypted", False) else ""),
                    "options_json": field_def.get("options", []),
                    "help_text": field_def.get("help_text", ""),
                },
            )
            DocumentTypeField.objects.get_or_create(
                document_type=dt,
                field_template=ft,
                defaults={"sort_order": idx},
            )

        _seed_field_groups(dt, dt_def.get("groups", []))


def _seed_field_groups(dt: DocumentType, groups_def: list[dict]) -> None:
    """Create/refresh a DocumentType's FieldGroups and field assignment.

    Refs #1056 (M0). ``update_or_create`` looks the group up via
    ``(document_type, name)`` — never via ``slug``: FieldGroup.slug is
    auto-generated on creation and immutable afterwards (raises on change),
    so passing it as a lookup value would risk trying to overwrite it.
    ``name`` is the stable, human-authored key in the seed catalog above and
    is itself unique per document_type, so it doubles as the idempotency key
    without ever touching ``slug``.

    Field assignment is re-applied on every run via an explicit ``.save()``
    when it differs from the target (not get_or_create-defaults, which only
    fire on first creation), so a changed grouping in the catalog also takes
    effect on re-seed. Fields with a slug that appears in no group's
    "fields" list (e.g. "strassenkontakt" for Kontakt) are explicitly reset
    to ungrouped, so removing a slug from a group in this file also
    un-groups it on the next seed run.
    """
    if not groups_def:
        return

    slug_to_group: dict[str, FieldGroup] = {}
    for group_def in groups_def:
        fg, _ = FieldGroup.objects.update_or_create(
            document_type=dt,
            name=group_def["name"],
            defaults={
                "sort_order": group_def.get("sort_order", 0),
                "is_collapsible": group_def.get("is_collapsible", False),
            },
        )
        for slug in group_def.get("fields", []):
            slug_to_group[slug] = fg

    for dtf in DocumentTypeField.objects.filter(document_type=dt).select_related("field_template"):
        target = slug_to_group.get(dtf.field_template.slug)
        if dtf.field_group_id != (target.id if target else None):
            dtf.field_group = target
            dtf.save(update_fields=["field_group"])
