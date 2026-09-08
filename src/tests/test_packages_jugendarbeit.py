"""Vertrag des Jugendarbeit-Katalogpakets (Refs #2139)."""

from __future__ import annotations

import io
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Client,
    DocumentType,
    DocumentTypeField,
    Event,
    FieldCondition,
    FieldGroup,
    FieldTemplate,
    Organization,
    QuickTemplate,
    TimeFilter,
)
from core.packages.catalog import parse_catalog_package
from core.packages.loader import load_package
from core.reporting.backends.ban_overview import BanOverviewBackend
from core.reporting.backends.sample_report_1 import SampleReport1Backend
from core.services.events import create_event

EXPECTED_TYPES = {
    "kontakt": ("normal", True, None, None, "contact"),
    "veranstaltungsteilnahme": ("normal", True, None, None, "group_activity"),
    "bezugsarbeit": ("normal", True, None, None, "contact"),
    "krisengespraech-jugend": ("elevated", False, "identified", "crisis", "crisis"),
    "beratung-jugend": ("elevated", False, "qualified", None, "counseling"),
    "vermittlung": ("normal", True, None, None, "referral"),
    "hausverbot-jugend": ("elevated", False, "identified", "ban", "excluded"),
    "notiz": ("normal", True, None, None, "excluded"),
}

EXPECTED_FIELDS = {
    "aktiv",
    "alterscluster",
    "anlass",
    "angebotsname",
    "art-der-krise",
    "bis",
    "dauer",
    "grund",
    "leistungen",
    "notiz",
    "notiz-krise",
    "ort",
    "sorgeberechtigte-informiert",
    "strassenkontakt",
    "teilnehmende",
    "thema",
    "vereinbarungen",
    "weitervermittlung-jugend",
    "zielgruppe",
}

ANONYMOUS_TYPES = ["kontakt", "veranstaltungsteilnahme", "bezugsarbeit", "vermittlung", "notiz"]


def _counts() -> tuple[int, int, int, int, int]:
    return (
        FieldTemplate.objects.count(),
        DocumentType.objects.count(),
        FieldGroup.objects.count(),
        DocumentTypeField.objects.count(),
        FieldCondition.objects.count(),
    )


def _loaded_types(facility) -> dict[str, DocumentType]:
    load_package(facility, parse_catalog_package("jugendarbeit"))
    return {item.slug: item for item in DocumentType.objects.for_facility(facility)}


def _option_slugs(package, field_slug: str) -> list[str]:
    template = next(item for item in package.field_templates if item.slug == field_slug)
    return [option["slug"] for option in template.options]


def test_jugendarbeit_package_parses_strictly_with_exact_catalog_contract():
    package = parse_catalog_package("jugendarbeit")

    assert {item.slug for item in package.field_templates} == EXPECTED_FIELDS
    assert {
        item.slug: (
            item.sensitivity,
            item.tally_enabled,
            item.min_contact_stage,
            item.system_type,
            item.report_key,
        )
        for item in package.document_types
    } == EXPECTED_TYPES
    assert all(item.entity_type == "event" for item in package.document_types)
    assert all(item.field_type != "file" for item in package.field_templates)
    assert _option_slugs(package, "leistungen") == [
        "essen",
        "getraenk",
        "wlan",
        "spiele",
        "aufenthaltsraum",
        "ladestation",
        "sonstiges",
    ]
    assert _option_slugs(package, "zielgruppe") == [
        "grundschule",
        "sek-i",
        "sek-ii",
        "gefluechtete-jugendliche",
        "queere-jugendliche",
        "offen",
    ]
    assert _option_slugs(package, "anlass") == [
        "streetwork",
        "schulhof",
        "fest",
        "unterwegs-angesprochen",
    ]
    assert _option_slugs(package, "art-der-krise") == [
        "mobbing",
        "familienkrise",
        "substanz",
        "psychische-belastung",
        "selbstverletzendes-verhalten",
        "suizidalitaet",
        "sexualitaet-identitaet",
        "gewalt",
    ]
    assert _option_slugs(package, "weitervermittlung-jugend") == [
        "jugendamt",
        "schulsozialarbeit",
        "therapeutin",
        "notdienst",
        "keine",
    ]

    fields = {item.slug: item for item in package.field_templates}
    assert (fields["dauer"].field_type, fields["dauer"].report_key) == ("number", "dauer_minuten")
    assert (fields["leistungen"].field_type, fields["leistungen"].report_key) == ("multi_select", "leistungen")
    assert (fields["art-der-krise"].field_type, fields["art-der-krise"].report_key) == (
        "select",
        "art_der_krise",
    )
    assert (fields["teilnehmende"].field_type, fields["teilnehmende"].min_value) == ("number", 1)
    assert fields["weitervermittlung-jugend"].field_type == "select"
    for slug in ("notiz-krise", "thema", "vereinbarungen"):
        assert fields[slug].sensitivity == "high"
        assert fields[slug].is_encrypted is True
    assert fields["sorgeberechtigte-informiert"].sensitivity == "normal"
    assert fields["sorgeberechtigte-informiert"].is_encrypted is False
    assert fields["grund"].is_required is True


@pytest.mark.django_db
def test_jugendarbeit_package_load_is_warning_free_and_idempotent(facility):
    package = parse_catalog_package("jugendarbeit")

    first = load_package(facility, package)
    before = _counts()
    second = load_package(facility, package)

    assert first.errors == first.warnings == []
    assert first.field_templates.created == [item.slug for item in package.field_templates]
    assert first.document_types.created == [item.slug for item in package.document_types]
    assert second.errors == second.warnings == []
    for _section_name, section in second.sections():
        assert section.created == []
        assert section.updated == []
    assert second.field_templates.unchanged == [item.slug for item in package.field_templates]
    assert second.document_types.unchanged == [item.slug for item in package.document_types]
    assert len(second.field_refs.unchanged) == 21
    assert second.groups.unchanged == second.conditions.unchanged == []
    assert _counts() == before


@pytest.mark.django_db
def test_setup_facility_installs_only_jugendarbeit_and_safe_comfort_objects():
    out = io.StringIO()
    with (
        patch("core.management.commands.setup_facility._has_rls_bypass_context", return_value=True),
        patch("builtins.input", side_effect=["Jugendarbeit-Orga", "Jugendzentrum", "jugend-admin"]),
        patch("getpass.getpass", side_effect=["pw1234", "pw1234"]),
    ):
        call_command("setup_facility", package="jugendarbeit", stdout=out)

    facility = Organization.objects.get(name="Jugendarbeit-Orga").facilities.get(name="Jugendzentrum")
    assert set(DocumentType.objects.for_facility(facility).values_list("slug", flat=True)) == set(EXPECTED_TYPES)
    assert (
        not DocumentType.objects.for_facility(facility)
        .filter(slug__in={"spritzentausch", "medizinische-versorgung"})
        .exists()
    )
    assert list(TimeFilter.objects.for_facility(facility).values_list("label", flat=True)) == [
        "Nachmittag",
        "Abend",
    ]
    assert list(
        QuickTemplate.objects.for_facility(facility).values_list("name", "document_type__slug", "prefilled_data")
    ) == [
        ("Kontakt JZ-Raum", "kontakt", {"leistungen": ["aufenthaltsraum"]}),
        ("Veranstaltung (Personenzahl)", "veranstaltungsteilnahme", {"teilnehmende": 10}),
    ]
    assert "Jugendarbeit eingespielt: 8 Dokumentationstypen angelegt" in out.getvalue()


def test_quick_templates_reference_normal_types_and_valid_fields():
    from core.services.facility_bootstrap import PACKAGE_COMFORT

    package = parse_catalog_package("jugendarbeit")
    types = {item.slug: item for item in package.document_types}
    fields = {item.slug: item for item in package.field_templates}
    definitions = PACKAGE_COMFORT["jugendarbeit"].quick_templates

    assert [(item.name, item.prefilled_data) for item in definitions] == [
        ("Kontakt JZ-Raum", {"leistungen": ["aufenthaltsraum"]}),
        ("Veranstaltung (Personenzahl)", {"teilnehmende": 10}),
    ]
    assert all(types[item.document_type_slug].sensitivity == "normal" for item in definitions)
    for item in definitions:
        doc_type = types[item.document_type_slug]
        referenced = {ref.template_slug for ref in doc_type.field_refs}
        for field_slug, value in item.prefilled_data.items():
            assert field_slug in referenced
            if fields[field_slug].options:
                active = {option["slug"] for option in fields[field_slug].options if option["is_active"]}
                assert set(value) <= active


@pytest.mark.django_db
def test_every_jugendarbeit_type_is_known_and_group_activity_is_counted(facility, staff_user):
    types = _loaded_types(facility)
    occurred_at = timezone.make_aware(datetime(2026, 9, 1, 12, 0))
    for doc_type in types.values():
        Event.objects.create(
            facility=facility,
            document_type=doc_type,
            occurred_at=occurred_at,
            data_json={},
            created_by=staff_user,
            is_anonymous=True,
        )

    result = SampleReport1Backend().build_dataset(facility, date(2026, 9, 1), date(2026, 9, 2))

    assert ("Gruppenangebote", 1) in result.summary["by_category"]
    assert "unknown_report_keys" not in {issue.code for issue in result.completeness}
    assert "unmapped_system_types" not in {issue.code for issue in result.completeness}


@pytest.mark.django_db
@pytest.mark.parametrize("slug", ANONYMOUS_TYPES)
def test_types_without_minimum_stage_accept_an_anonymous_event(facility, staff_user, slug):
    doc_type = _loaded_types(facility)[slug]

    event = create_event(
        facility=facility,
        user=staff_user,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json={},
        client=None,
        is_anonymous=False,
    )

    assert event.is_anonymous is True


@pytest.mark.django_db
@pytest.mark.parametrize("slug", ["krisengespraech-jugend", "beratung-jugend", "hausverbot-jugend"])
def test_assistant_cannot_see_elevated_jugendarbeit_events(client, facility, staff_user, assistant_user, slug):
    doc_type = _loaded_types(facility)[slug]
    event = Event.objects.create(
        facility=facility,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
        is_anonymous=True,
    )
    client.force_login(assistant_user)

    assert client.get(reverse("core:event_detail", kwargs={"pk": event.pk})).status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize("slug", ANONYMOUS_TYPES)
def test_assistant_can_see_normal_jugendarbeit_events(client, facility, staff_user, assistant_user, slug):
    doc_type = _loaded_types(facility)[slug]
    event = Event.objects.create(
        facility=facility,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
        is_anonymous=True,
    )
    client.force_login(assistant_user)

    assert client.get(reverse("core:event_detail", kwargs={"pk": event.pk})).status_code == 200


@pytest.mark.django_db
def test_participant_count_below_one_is_rejected(facility, staff_user):
    doc_type = _loaded_types(facility)["veranstaltungsteilnahme"]

    with pytest.raises(ValidationError) as excinfo:
        create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"teilnehmende": 0},
            client=None,
            is_anonymous=True,
        )

    assert "teilnehmende" in excinfo.value.error_dict
    assert not Event.objects.filter(document_type=doc_type).exists()


@pytest.mark.django_db
def test_hausverbot_jugend_appears_in_ban_overview(facility, staff_user):
    doc_type = _loaded_types(facility)["hausverbot-jugend"]
    person = Client.objects.create(facility=facility, pseudonym="Jugend-01", created_by=staff_user)
    today = timezone.localdate()
    Event.objects.create(
        facility=facility,
        client=person,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json={"grund": "Regelverstoß", "bis": (today + timedelta(days=1)).isoformat(), "aktiv": True},
        created_by=staff_user,
    )

    result = BanOverviewBackend().build_dataset(facility, today - timedelta(days=1), today + timedelta(days=1))

    assert result.summary["ausgesprochen"] == 1
    assert result.summary["aktiv_aktuell"] == 1


def test_jugendarbeit_documentation_is_indexed():
    root = Path(__file__).resolve().parents[2]
    assert (root / "docs/packages-jugendarbeit.md").is_file()
    assert "[packages-jugendarbeit.md](packages-jugendarbeit.md)" in (root / "docs/README.md").read_text(
        encoding="utf-8"
    )
