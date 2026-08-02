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
                {"name": "Basisdaten", "slug": "basisdaten", "sort_order": 0, "fields": ["dauer", "alterscluster"]},
                {
                    "name": "Leistungen & Notizen",
                    "slug": "leistungen-notizen",
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
                    "slug": "krisendetails",
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

        field_order = [field_def["slug"] for field_def in dt_def.get("fields", [])]
        _seed_field_groups(dt, dt_def.get("groups"), field_order)


def _seed_field_groups(dt: DocumentType, groups_def: list[dict] | None, field_order: list[str]) -> None:
    """Create/refresh a DocumentType's FieldGroups and field assignment.

    Refs #1056 (M0), reworked Refs #1710. ``update_or_create`` looks the
    group up via ``(document_type, slug)`` — each group definition in the
    catalog above carries an explicit, stable ``"slug"``, chosen to match
    what ``FieldGroup._generate_unique_slug()`` would have produced for the
    existing (pre-#1710) auto-slugged rows (``slugify(name)``, no umlauts in
    these names). This is what lets a facility that already ran the seed
    before #1710 find and reuse its existing FieldGroup row on re-seed
    instead of creating a duplicate: the slug in the catalog IS the row's
    current slug, so the lookup hits and only ``name``/``sort_order``/
    ``is_collapsible`` are updated in place — the slug itself is never
    written by this lookup, so the model's immutability guard never fires.
    A catalog rename (name-only change, same slug) is therefore a safe,
    idempotent in-place update; changing the *slug* is not supported here
    (nor needed — see the catalog comment on each group).

    ``groups_def is None`` means the catalog has no "groups" key at all for
    this DocumentType — FieldGroups for it (if any exist, e.g. created
    manually via Admin/Config-UI) are left completely untouched: no cleanup,
    no assignment pass. Only DocumentTypes that opt in with an explicit
    "groups" key (even an empty list) participate in the cleanup below.

    Cleanup: any existing FieldGroup for this DocumentType whose slug is not
    in the catalog's group slugs is deleted (its fields fall back to
    ungrouped via SET_NULL, corrected explicitly below in case the removed
    group's fields need reassigning to a different target instead of NULL).
    This also transparently handles the legacy-slug transition: an existing
    row from before #1710 that happens to carry a slug the catalog does NOT
    use (e.g. a collision-suffixed "-2" auto-slug) gets deleted, and the
    catalog-slug row gets created fresh in the same run — a second run is
    then a true no-op.

    Field assignment is re-applied on every run via an explicit ``.save()``
    when it differs from the target (not get_or_create-defaults, which only
    fire on first creation), so a changed grouping in the catalog also takes
    effect on re-seed. Fields with a slug that appears in no group's
    "fields" list (e.g. "strassenkontakt" for Kontakt) are explicitly reset
    to ungrouped, so removing a slug from a group in this file also
    un-groups it on the next seed run.

    Finally, a normalization pass rewrites ``sort_order`` to a canonical
    form (see :func:`_normalize_block_order`) so that re-seeding always
    yields the same deterministic layout, independent of creation-order
    history.
    """
    if groups_def is None:
        return

    katalog_slugs = {group_def["slug"] for group_def in groups_def}
    FieldGroup.objects.filter(document_type=dt).exclude(slug__in=katalog_slugs).delete()

    group_by_slug: dict[str, FieldGroup] = {}
    slug_to_group: dict[str, FieldGroup] = {}
    for group_def in groups_def:
        fg, _ = FieldGroup.objects.update_or_create(
            document_type=dt,
            slug=group_def["slug"],
            defaults={
                "name": group_def["name"],
                "sort_order": group_def.get("sort_order", 0),
                "is_collapsible": group_def.get("is_collapsible", False),
            },
        )
        group_by_slug[group_def["slug"]] = fg
        for slug in group_def.get("fields", []):
            slug_to_group[slug] = fg

    dtfs = list(DocumentTypeField.objects.filter(document_type=dt).select_related("field_template"))
    for dtf in dtfs:
        target = slug_to_group.get(dtf.field_template.slug)
        if dtf.field_group_id != (target.id if target else None):
            dtf.field_group = target
            dtf.save(update_fields=["field_group"])

    _normalize_block_order(groups_def, field_order, group_by_slug, dtfs)


def _normalize_block_order(
    groups_def: list[dict],
    field_order: list[str],
    group_by_slug: dict[str, FieldGroup],
    dtfs: list[DocumentTypeField],
) -> None:
    """Bring group/field ``sort_order`` into canonical form after each seed run.

    Refs #1710. Vorgriff auf die globale Block-Sequenz (#1713), aber unter der
    heutigen (Gruppen-innere Sortierung) Semantik ebenso korrekt:

    (a) Gruppen behalten die Katalog-Reihenfolge, kompakt als ``0..k-1``.
    (b) Gruppierte Felder bekommen den Innen-Index ``0..n-1`` je Gruppe, in
        der Reihenfolge von deren "fields"-Liste im Katalog.
    (c) Ungruppierte Felder bekommen ``k, k+1, …`` (nach den Gruppen), in der
        Reihenfolge von ``field_order`` (der top-level "fields"-Liste im
        Katalog) — erhält das heutige Layout "Ohne Gruppe zuletzt" exakt.
    """
    dtf_by_slug = {dtf.field_template.slug: dtf for dtf in dtfs}

    for idx, group_def in enumerate(groups_def):
        fg = group_by_slug[group_def["slug"]]
        if fg.sort_order != idx:
            fg.sort_order = idx
            fg.save(update_fields=["sort_order"])

        for inner_idx, slug in enumerate(group_def.get("fields", [])):
            dtf = dtf_by_slug.get(slug)
            if dtf is not None and dtf.sort_order != inner_idx:
                dtf.sort_order = inner_idx
                dtf.save(update_fields=["sort_order"])

    grouped_slugs = {slug for group_def in groups_def for slug in group_def.get("fields", [])}
    position = len(groups_def)
    for slug in field_order:
        if slug in grouped_slugs:
            continue
        dtf = dtf_by_slug.get(slug)
        if dtf is None:
            continue
        if dtf.sort_order != position:
            dtf.sort_order = position
            dtf.save(update_fields=["sort_order"])
        position += 1
