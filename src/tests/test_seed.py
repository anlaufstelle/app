"""Tests for the seed management command."""

import copy

import pytest
from django.core.management import call_command

# Der Seed-Command erzeugt Datei-Uploads via ``store_encrypted_file``, das seit
# #610 libmagic für Magic-Bytes-Validierung verwendet. Ohne libmagic (z.B.
# Host ohne ``libmagic1``-Paket) ist der Command nicht lauffähig — im
# Docker-Image/CI ist die Bibliothek vorhanden.
try:
    import magic

    magic.from_buffer(b"%PDF-1.4\n", mime=True)
except Exception as _libmagic_exc:  # noqa: BLE001 — libmagic-Shared-Library fehlt
    pytest.skip(
        f"libmagic nicht lauffähig ({_libmagic_exc}) — Seed-Tests erfordern libmagic1.",
        allow_module_level=True,
    )

from core.models import (  # noqa: E402
    Case,
    Client,
    DocumentType,
    DocumentTypeField,
    Facility,
    FieldGroup,
    FieldTemplate,
    Organization,
    Settings,
    TimeFilter,
    User,
    WorkItem,
)
from core.seed import doc_types  # noqa: E402

# ---------------------------------------------------------------------------
# Idempotency (existing tests)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_idempotent_no_duplicate_field_templates():
    """Running seed twice does not create duplicate FieldTemplates."""
    call_command("seed")
    count_after_first = FieldTemplate.objects.count()

    call_command("seed")
    count_after_second = FieldTemplate.objects.count()

    assert count_after_second == count_after_first


@pytest.mark.django_db
def test_seed_updates_field_template_properties():
    """Re-running seed updates FieldTemplate properties to match the seed definition."""
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    ft = FieldTemplate.objects.get(facility=facility, name="Ausgabe")

    # Verify initial state from seed
    assert ft.is_required is True

    # Manually change a property
    ft.is_required = False
    ft.save()
    ft.refresh_from_db()
    assert ft.is_required is False

    # Re-run seed — property should be restored
    call_command("seed")
    ft.refresh_from_db()
    assert ft.is_required is True


# ---------------------------------------------------------------------------
# Scale: small (default)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_small_creates_one_facility():
    """Default (small) scale creates exactly one facility named 'Hauptstelle'."""
    call_command("seed")

    assert Facility.objects.count() == 1
    assert Facility.objects.filter(name="Hauptstelle").exists()


@pytest.mark.django_db
def test_seed_small_creates_expected_clients():
    """Small scale creates 7 clients with the hard-coded pseudonyms."""
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    clients = Client.objects.filter(facility=facility)
    assert clients.count() == 7

    expected_pseudonyms = {"Stern-42", "Wolke-17", "Blitz-08", "Regen-55", "Wind-33", "Nebel-71", "Sonne-99"}
    actual_pseudonyms = set(clients.values_list("pseudonym", flat=True))
    assert actual_pseudonyms == expected_pseudonyms


@pytest.mark.django_db
def test_seed_small_creates_six_users():
    """Small scale creates 6 users (admin, lead, 2 staff, 2 assistant)."""
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    users = User.objects.filter(facility=facility)
    assert users.count() == 6

    roles = set(users.values_list("role", flat=True))
    assert roles == {User.Role.FACILITY_ADMIN, User.Role.LEAD, User.Role.STAFF, User.Role.ASSISTANT}


@pytest.mark.django_db
def test_seed_small_creates_settings_and_time_filters():
    """Small scale creates Settings and TimeFilters for the facility."""
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    assert Settings.objects.filter(facility=facility).exists()
    assert TimeFilter.objects.filter(facility=facility).count() == 3


@pytest.mark.django_db
def test_seed_small_creates_document_types():
    """Small scale creates at least one DocumentType per facility."""
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    assert DocumentType.objects.filter(facility=facility).count() > 0


@pytest.mark.django_db
def test_seed_small_creates_cases_and_work_items():
    """Small scale creates cases and work items as per config."""
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    assert Case.objects.filter(facility=facility).count() == 3
    assert WorkItem.objects.filter(facility=facility).count() == 5


# ---------------------------------------------------------------------------
# Scale: solo
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_solo_creates_one_facility():
    """Solo scale creates exactly one facility."""
    call_command("seed", scale="solo")

    assert Facility.objects.count() == 1


@pytest.mark.django_db
def test_seed_solo_creates_bulk_clients():
    """Solo scale uses bulk client creation with 30 clients."""
    call_command("seed", scale="solo")

    facility = Facility.objects.get(name="Hauptstelle")
    assert Client.objects.filter(facility=facility).count() == 30


# ---------------------------------------------------------------------------
# Scale: medium
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.django_db
def test_seed_medium_creates_two_facilities():
    """Medium scale creates two facilities."""
    call_command("seed", scale="medium")

    assert Facility.objects.count() == 2
    assert Facility.objects.filter(name="Hauptstelle").exists()
    assert Facility.objects.filter(name="Zweigstelle Nord").exists()


@pytest.mark.slow
@pytest.mark.django_db
def test_seed_medium_creates_users_per_facility():
    """Medium scale creates 6 users per facility + 1 installation-wide super-admin.

    Refs #867: ``seed_super_admin()`` legt zusaetzlich zur Per-Facility-User-
    Liste einen installation-weiten ``superadmin`` an (Persona Jonas).
    Daher 6 (Hauptstelle) + 6 (Zweigstelle Nord) + 1 (super-admin) = 13.
    """
    call_command("seed", scale="medium")

    assert User.objects.count() == 13
    # Sicherheitsprobe: genau ein super_admin, der Rest sind facility-User.
    assert User.objects.filter(role=User.Role.SUPER_ADMIN).count() == 1


@pytest.mark.slow
@pytest.mark.django_db
def test_seed_medium_second_facility_users_have_suffix():
    """Users in the second facility have a '_1' suffix to avoid name collision."""
    call_command("seed", scale="medium")

    assert User.objects.filter(username="admin").exists()
    assert User.objects.filter(username="admin_1").exists()


# ---------------------------------------------------------------------------
# Organization
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_creates_single_organization():
    """Seed always creates exactly one organization named 'Anlaufstelle'."""
    call_command("seed")

    assert Organization.objects.count() == 1
    assert Organization.objects.filter(name="Anlaufstelle").exists()


@pytest.mark.slow
@pytest.mark.django_db
def test_seed_medium_shares_single_organization():
    """Medium scale: all facilities belong to the same organization."""
    call_command("seed", scale="medium")

    org = Organization.objects.get(name="Anlaufstelle")
    assert Facility.objects.filter(organization=org).count() == 2


# ---------------------------------------------------------------------------
# Idempotency across scales
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_idempotent_user_count():
    """Running seed twice produces the same number of users."""
    call_command("seed")
    count_first = User.objects.count()

    call_command("seed")
    count_second = User.objects.count()

    assert count_second == count_first


@pytest.mark.django_db
def test_seed_idempotent_client_count():
    """Running seed twice produces the same number of clients."""
    call_command("seed")
    count_first = Client.objects.count()

    call_command("seed")
    count_second = Client.objects.count()

    assert count_second == count_first


@pytest.mark.django_db
def test_seed_idempotent_facility_count():
    """Running seed twice produces the same number of facilities."""
    call_command("seed")
    count_first = Facility.objects.count()

    call_command("seed")
    count_second = Facility.objects.count()

    assert count_second == count_first


# ---------------------------------------------------------------------------
# Flush option
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_flush_recreates_data():
    """--flush deletes existing data and recreates from scratch.

    Refs #867: small seed legt 6 Facility-User + 1 installation-weiten
    super_admin an = 7.
    """
    call_command("seed")
    first_user_ids = set(User.objects.values_list("id", flat=True))

    call_command("seed", flush=True)
    second_user_ids = set(User.objects.values_list("id", flat=True))

    # After flush+recreate, IDs differ (new objects)
    assert first_user_ids != second_user_ids
    # But counts are the same — 6 Facility-User + 1 super_admin.
    assert User.objects.count() == 7


# ---------------------------------------------------------------------------
# Invalid scale (argparse catches this)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_invalid_scale_raises():
    """Passing an invalid scale value raises a KeyError (not in SCALE_CONFIG)."""
    with pytest.raises(KeyError):
        call_command("seed", scale="nonexistent")


# ---------------------------------------------------------------------------
# Admin user properties
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_admin_is_not_superuser():
    """Refs #1271: der geseedete facility_admin ist KEIN Django-superuser.

    Least-Privilege: die Rolle ``facility_admin`` darf nicht mit Djangos
    ``is_superuser`` gleichgesetzt werden (Autorisierung laeuft ueber die
    Rolle, nicht ueber das Django-Flag). ``is_staff`` bleibt fuer den
    Admin-Site-Login erhalten.
    """
    call_command("seed")

    admin = User.objects.get(username="admin")
    assert admin.is_superuser is False
    assert admin.is_staff is True
    assert admin.role == User.Role.FACILITY_ADMIN


@pytest.mark.django_db
def test_seed_non_admin_users_are_not_superusers():
    """Non-admin seeded users do not have is_superuser."""
    call_command("seed")

    for user in User.objects.exclude(role=User.Role.FACILITY_ADMIN).exclude(role=User.Role.SUPER_ADMIN):
        assert user.is_superuser is False


# ---------------------------------------------------------------------------
# Super-Admin (Refs #867)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_creates_super_admin():
    """Seed legt einen installation-weiten Super-Admin (Persona Jonas) an."""
    call_command("seed")

    super_admin = User.objects.get(username="superadmin")
    assert super_admin.role == User.Role.SUPER_ADMIN
    assert super_admin.facility is None
    # Refs #1271: auch der geseedete super_admin ist KEIN Django-superuser —
    # konsistent mit der Produktion (create_super_admin) und der
    # super_admin_user-Fixture (Autorisierung laeuft ueber die Rolle).
    assert super_admin.is_superuser is False


@pytest.mark.django_db
def test_seed_super_admin_idempotent():
    """Doppelter Seed-Lauf erzeugt keinen zweiten Super-Admin."""
    call_command("seed")
    call_command("seed")

    assert User.objects.filter(username="superadmin").count() == 1


# ---------------------------------------------------------------------------
# Field groups (M0, Refs #1056)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_creates_kontakt_field_groups():
    """Kontakt bekommt die zwei Beispiel-Feldgruppen aus dem Seed-Katalog."""
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    kontakt = DocumentType.objects.get(facility=facility, name="Kontakt")
    groups = {g.name: g for g in FieldGroup.objects.filter(document_type=kontakt)}

    assert set(groups) == {"Basisdaten", "Leistungen & Notizen"}
    assert groups["Basisdaten"].sort_order == 0
    assert groups["Basisdaten"].is_collapsible is False
    assert groups["Leistungen & Notizen"].sort_order == 1
    assert groups["Leistungen & Notizen"].is_collapsible is True


@pytest.mark.django_db
def test_seed_kontakt_field_group_assignment():
    """Kontakt-Felder landen slugweise in der jeweils passenden Gruppe."""
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    kontakt = DocumentType.objects.get(facility=facility, name="Kontakt")
    assignment = {
        dtf.field_template.slug: (dtf.field_group.name if dtf.field_group else None)
        for dtf in DocumentTypeField.objects.filter(document_type=kontakt).select_related(
            "field_template", "field_group"
        )
    }

    assert assignment["dauer"] == "Basisdaten"
    assert assignment["alterscluster"] == "Basisdaten"
    assert assignment["leistungen"] == "Leistungen & Notizen"
    assert assignment["notiz"] == "Leistungen & Notizen"
    assert assignment["anhang"] == "Leistungen & Notizen"


@pytest.mark.django_db
def test_seed_strassenkontakt_stays_ungrouped():
    """Refs #1056: strassenkontakt demonstriert 'Ohne Gruppe' im UI bewusst."""
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    kontakt = DocumentType.objects.get(facility=facility, name="Kontakt")
    dtf = DocumentTypeField.objects.get(document_type=kontakt, field_template__slug="strassenkontakt")

    assert dtf.field_group is None


@pytest.mark.django_db
def test_seed_creates_krisengespraech_field_group():
    """Krisengespräch bekommt eine Gruppe 'Krisendetails' mit allen Feldern."""
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    krise = DocumentType.objects.get(facility=facility, name="Krisengespräch")
    groups = list(FieldGroup.objects.filter(document_type=krise))

    assert len(groups) == 1
    assert groups[0].name == "Krisendetails"

    assignment = {
        dtf.field_template.slug: (dtf.field_group.name if dtf.field_group else None)
        for dtf in DocumentTypeField.objects.filter(document_type=krise).select_related("field_template", "field_group")
    }
    assert assignment == {
        "art-der-krise": "Krisendetails",
        "dauer": "Krisendetails",
        "notiz-krise": "Krisendetails",
        "weitervermittlung": "Krisendetails",
    }


@pytest.mark.django_db
def test_seed_doctypes_without_groups_key_have_no_field_groups():
    """DocTypes ohne 'groups'-Key im Katalog bleiben ohne FieldGroup."""
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    for name in ["Medizinische Versorgung", "Spritzentausch", "Begleitung", "Beratungsgespräch", "Notiz", "Hausverbot"]:
        dt = DocumentType.objects.get(facility=facility, name=name)
        assert FieldGroup.objects.filter(document_type=dt).count() == 0


@pytest.mark.django_db
def test_reseed_regroups_manually_moved_field():
    """Refs #1056: Ein manuell in eine ANDERE Gruppe gehängtes Kontakt-Feld
    wird beim Re-Seed auf die Katalog-Gruppe zurückgesetzt.

    Deckt den Abgleich-Branch in ``_seed_field_groups`` ab (dtf.field_group_id
    weicht vom Ziel ab → explizites ``.save()``), Richtung „Umgruppierung".
    """
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    kontakt = DocumentType.objects.get(facility=facility, name="Kontakt")
    basis = FieldGroup.objects.get(document_type=kontakt, name="Basisdaten")
    other = FieldGroup.objects.get(document_type=kontakt, name="Leistungen & Notizen")
    dtf = DocumentTypeField.objects.get(document_type=kontakt, field_template__slug="dauer")
    assert dtf.field_group_id == basis.id  # Ausgangslage aus dem Katalog

    # Manuell in die falsche Gruppe hängen und erneut seeden.
    dtf.field_group = other
    dtf.save(update_fields=["field_group"])
    call_command("seed")

    dtf.refresh_from_db()
    assert dtf.field_group_id == basis.id, "Re-Seed hat das umgehängte Feld nicht auf die Katalog-Gruppe zurückgesetzt."


@pytest.mark.django_db
def test_reseed_resets_catalog_ungrouped_field_to_none():
    """Refs #1056: 'strassenkontakt' ist im Katalog in keiner Gruppe gelistet.

    Ein manuell gesetzter ``field_group`` wird beim Re-Seed auf ``None``
    zurückgesetzt (Reset-Richtung des Abgleich-Branch).
    """
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    kontakt = DocumentType.objects.get(facility=facility, name="Kontakt")
    basis = FieldGroup.objects.get(document_type=kontakt, name="Basisdaten")
    dtf = DocumentTypeField.objects.get(document_type=kontakt, field_template__slug="strassenkontakt")
    assert dtf.field_group_id is None  # Ausgangslage: bewusst ohne Gruppe

    # Manuell in eine Gruppe hängen und erneut seeden.
    dtf.field_group = basis
    dtf.save(update_fields=["field_group"])
    call_command("seed")

    dtf.refresh_from_db()
    assert dtf.field_group_id is None, "Re-Seed hat das katalog-gruppenlose Feld nicht auf None zurückgesetzt."


@pytest.mark.django_db
def test_seed_field_groups_idempotent_no_duplicates():
    """Zweiter Seed-Lauf erzeugt keine neuen FieldGroup-/Zuordnungs-Rows."""
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    kontakt = DocumentType.objects.get(facility=facility, name="Kontakt")
    krise = DocumentType.objects.get(facility=facility, name="Krisengespräch")

    group_ids_first = set(FieldGroup.objects.values_list("id", flat=True))
    kontakt_assignment_first = {
        dtf.field_template.slug: dtf.field_group_id for dtf in DocumentTypeField.objects.filter(document_type=kontakt)
    }
    krise_assignment_first = {
        dtf.field_template.slug: dtf.field_group_id for dtf in DocumentTypeField.objects.filter(document_type=krise)
    }
    total_dtf_first = DocumentTypeField.objects.count()

    call_command("seed")

    group_ids_second = set(FieldGroup.objects.values_list("id", flat=True))
    kontakt_assignment_second = {
        dtf.field_template.slug: dtf.field_group_id for dtf in DocumentTypeField.objects.filter(document_type=kontakt)
    }
    krise_assignment_second = {
        dtf.field_template.slug: dtf.field_group_id for dtf in DocumentTypeField.objects.filter(document_type=krise)
    }
    total_dtf_second = DocumentTypeField.objects.count()

    assert FieldGroup.objects.count() == 3  # 2× Kontakt + 1× Krisengespräch
    assert group_ids_second == group_ids_first
    assert kontakt_assignment_second == kontakt_assignment_first
    assert krise_assignment_second == krise_assignment_first
    assert total_dtf_second == total_dtf_first


# ---------------------------------------------------------------------------
# Field groups über stabilen Slug-Key + Cleanup + Normalisierung (Refs #1710)
# ---------------------------------------------------------------------------


def _renamed_catalog(*, doc_type_name: str, group_slug: str, new_group_name: str) -> list[dict]:
    """Katalog-Kopie mit einer Gruppen-Umbenennung (per Slug adressiert)."""
    defs = copy.deepcopy(doc_types.get_document_type_definitions())
    for dt_def in defs:
        if dt_def["name"] != doc_type_name:
            continue
        for group_def in dt_def.get("groups", []):
            if group_def["slug"] == group_slug:
                group_def["name"] = new_group_name
    return defs


def _catalog_without_group(*, doc_type_name: str, group_slug: str) -> list[dict]:
    """Katalog-Kopie, aus der eine Gruppe (per Slug) entfernt wurde."""
    defs = copy.deepcopy(doc_types.get_document_type_definitions())
    for dt_def in defs:
        if dt_def["name"] != doc_type_name:
            continue
        dt_def["groups"] = [g for g in dt_def.get("groups", []) if g["slug"] != group_slug]
    return defs


@pytest.mark.django_db
def test_reseed_after_catalog_rename_keeps_same_fieldgroup_row(monkeypatch):
    """Refs #1710: Katalog-Umbenennung adressiert die Gruppe über den stabilen
    Slug — die FieldGroup-Row bleibt (gleiche pk), nur der Name ändert sich.
    Kein Orphan/Duplikat entsteht.
    """
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    kontakt = DocumentType.objects.get(facility=facility, name="Kontakt")
    basis = FieldGroup.objects.get(document_type=kontakt, slug="basisdaten")
    basis_pk = basis.pk

    renamed = _renamed_catalog(doc_type_name="Kontakt", group_slug="basisdaten", new_group_name="Stammdaten")
    monkeypatch.setattr(doc_types, "get_document_type_definitions", lambda: renamed)

    call_command("seed")

    basis.refresh_from_db()
    assert basis.pk == basis_pk
    assert basis.name == "Stammdaten"
    assert FieldGroup.objects.filter(document_type=kontakt, slug="basisdaten").count() == 1


@pytest.mark.django_db
def test_reseed_after_catalog_group_removal_deletes_row_and_ungroups_fields(monkeypatch):
    """Refs #1710: Entfernt der Katalog eine Gruppe, verschwindet die Row und
    ihre Felder werden ungruppiert (statt verwaist mit totem FK)."""
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    kontakt = DocumentType.objects.get(facility=facility, name="Kontakt")
    removed = FieldGroup.objects.get(document_type=kontakt, slug="leistungen-notizen")
    removed_pk = removed.pk

    reduced = _catalog_without_group(doc_type_name="Kontakt", group_slug="leistungen-notizen")
    monkeypatch.setattr(doc_types, "get_document_type_definitions", lambda: reduced)

    call_command("seed")

    assert not FieldGroup.objects.filter(pk=removed_pk).exists()
    for slug in ("leistungen", "notiz", "anhang"):
        dtf = DocumentTypeField.objects.get(document_type=kontakt, field_template__slug=slug)
        assert dtf.field_group is None


@pytest.mark.django_db
def test_reseed_transition_from_legacy_autoslug_is_idempotent():
    """Refs #1710: Übergangs-Szenario — eine Bestands-FieldGroup mit einem vom
    Katalog-Slug abweichenden Slug (z.B. Kollisionshistorie eines alten
    Auto-Slugs) wird im ersten Lauf durch Cleanup + Neuanlage in EINEM Lauf
    ersetzt; ein zweiter Lauf darf daran nichts mehr ändern.
    """
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    kontakt = DocumentType.objects.get(facility=facility, name="Kontakt")

    # Simuliert eine Bestands-Gruppe mit abweichendem Legacy-Slug.
    FieldGroup.objects.filter(document_type=kontakt, slug="basisdaten").delete()
    legacy = FieldGroup.objects.create(
        document_type=kontakt, name="Basisdaten", slug="basisdaten-legacy", sort_order=0
    )
    dtf_dauer = DocumentTypeField.objects.get(document_type=kontakt, field_template__slug="dauer")
    dtf_dauer.field_group = legacy
    dtf_dauer.save(update_fields=["field_group"])

    call_command("seed")

    assert not FieldGroup.objects.filter(pk=legacy.pk).exists()
    basis = FieldGroup.objects.get(document_type=kontakt, slug="basisdaten")
    dtf_dauer.refresh_from_db()
    assert dtf_dauer.field_group_id == basis.id

    def _state():
        return {
            "group_ids": set(FieldGroup.objects.filter(document_type=kontakt).values_list("id", flat=True)),
            "assignment": {
                dtf.field_template.slug: dtf.field_group_id
                for dtf in DocumentTypeField.objects.filter(document_type=kontakt).select_related("field_template")
            },
            "sort_orders": {
                dtf.field_template.slug: dtf.sort_order
                for dtf in DocumentTypeField.objects.filter(document_type=kontakt).select_related("field_template")
            },
        }

    state_after_first = _state()
    call_command("seed")
    assert _state() == state_after_first


@pytest.mark.django_db
def test_seed_normalizes_group_and_field_sort_order():
    """Refs #1710: Nach dem Seed-Lauf sind Gruppen 0..k-1 kompakt (Katalog-
    Reihenfolge), gruppierte Felder tragen den Innen-Index (Katalog-
    Feldreihenfolge je Gruppe), ungruppierte Felder folgen ab Position k in
    Katalog-Feldreihenfolge."""
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    kontakt = DocumentType.objects.get(facility=facility, name="Kontakt")

    basis = FieldGroup.objects.get(document_type=kontakt, slug="basisdaten")
    leistungen_gruppe = FieldGroup.objects.get(document_type=kontakt, slug="leistungen-notizen")
    assert basis.sort_order == 0
    assert leistungen_gruppe.sort_order == 1

    dtf_by_slug = {
        dtf.field_template.slug: dtf
        for dtf in DocumentTypeField.objects.filter(document_type=kontakt).select_related("field_template")
    }
    assert dtf_by_slug["dauer"].sort_order == 0
    assert dtf_by_slug["alterscluster"].sort_order == 1
    assert dtf_by_slug["leistungen"].sort_order == 0
    assert dtf_by_slug["notiz"].sort_order == 1
    assert dtf_by_slug["anhang"].sort_order == 2
    assert dtf_by_slug["strassenkontakt"].sort_order == 2  # k=2 Gruppen -> Position 2

    krise = DocumentType.objects.get(facility=facility, name="Krisengespräch")
    krisendetails = FieldGroup.objects.get(document_type=krise, slug="krisendetails")
    assert krisendetails.sort_order == 0

    krise_dtf_by_slug = {
        dtf.field_template.slug: dtf
        for dtf in DocumentTypeField.objects.filter(document_type=krise).select_related("field_template")
    }
    assert krise_dtf_by_slug["art-der-krise"].sort_order == 0
    assert krise_dtf_by_slug["dauer"].sort_order == 1
    assert krise_dtf_by_slug["notiz-krise"].sort_order == 2
    assert krise_dtf_by_slug["weitervermittlung"].sort_order == 3


@pytest.mark.django_db
def test_reseed_does_not_touch_field_groups_of_doctype_without_groups_key():
    """Refs #1710: Ein DocType ohne 'groups'-Key im Katalog (z.B. Spritzentausch)
    bekommt beim Re-Seed KEINEN Cleanup — eine manuell (Admin/Config-UI)
    angelegte FieldGroup übersteht mehrfaches Re-Seeden unangetastet."""
    call_command("seed")

    facility = Facility.objects.get(name="Hauptstelle")
    spritzentausch = DocumentType.objects.get(facility=facility, name="Spritzentausch")
    manual_group = FieldGroup.objects.create(document_type=spritzentausch, name="Manuell angelegt", slug="manuell")

    call_command("seed")
    call_command("seed")

    manual_group.refresh_from_db()
    assert FieldGroup.objects.filter(pk=manual_group.pk).exists()
    assert manual_group.name == "Manuell angelegt"
