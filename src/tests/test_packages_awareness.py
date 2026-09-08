"""Vertrag des Awareness-Katalogpakets (Refs #2137)."""

from __future__ import annotations

import io
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
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
from core.reporting.backends.sample_report_1 import SampleReport1Backend
from core.services.events import create_event
from core.services.system import get_active_bans

EXPECTED_TYPES = {
    "awareness-kontakt": ("Awareness-Kontakt", "normal", True, None, "contact"),
    "versorgung": ("Versorgung", "normal", True, None, "supply"),
    "gewaltvorfall": ("Gewaltvorfall", "elevated", False, None, "incident"),
    "krisensituation": ("Krisensituation", "elevated", False, "crisis", "crisis"),
    "orga-meldung": ("Orga-Meldung (Platzverweis)", "elevated", False, "ban", "excluded"),
    "vermittlung": ("Vermittlung", "normal", True, None, "referral"),
    "notiz": ("Notiz", "normal", True, None, "excluded"),
}

EXPECTED_FIELDS = {
    "aktiv": "boolean",
    "alterscluster": "select",
    "anliegen": "multi_select",
    "art-der-krise": "select",
    "art-des-vorfalls": "multi_select",
    "bedarf": "multi_select",
    "beteiligung": "select",
    "bis": "date",
    "dauer": "number",
    "folgesituation": "select",
    "grund": "textarea",
    "notiz": "textarea",
    "notiz-krise": "textarea",
    "notiz-vorfall": "textarea",
    "ort-im-veranstaltungsgelaende": "text",
    "veranstaltung": "text",
    "vermittelt-an": "select",
    "weitergabe-an": "multi_select",
    "weitervermittlung": "text",
}


def _counts() -> tuple[int, int, int, int, int]:
    return (
        FieldTemplate.objects.count(),
        DocumentType.objects.count(),
        FieldGroup.objects.count(),
        DocumentTypeField.objects.count(),
        FieldCondition.objects.count(),
    )


def _loaded_types(facility) -> dict[str, DocumentType]:
    load_package(facility, parse_catalog_package("awareness"))
    return {item.slug: item for item in DocumentType.objects.for_facility(facility)}


def _option_slugs(package, field_slug: str) -> list[str]:
    template = next(item for item in package.field_templates if item.slug == field_slug)
    return [option["slug"] for option in template.options]


def test_awareness_package_parses_strictly_with_exact_catalog_contract():
    package = parse_catalog_package("awareness")

    assert {item.slug: item.field_type for item in package.field_templates} == EXPECTED_FIELDS
    assert {
        item.slug: (
            item.name,
            item.sensitivity,
            item.tally_enabled,
            item.system_type,
            item.report_key,
        )
        for item in package.document_types
    } == EXPECTED_TYPES
    assert all(item.entity_type == "event" for item in package.document_types)
    assert all(item.min_contact_stage is None for item in package.document_types)
    assert all(item.field_type != "file" for item in package.field_templates)
    assert _option_slugs(package, "art-des-vorfalls") == [
        "sexualisierte-gewalt",
        "koerperliche-gewalt",
        "verbale-gewalt",
        "diskriminierung",
        "transphobie",
        "homophobie",
        "rassismus",
        "sonstiges",
    ]
    assert _option_slugs(package, "art-der-krise") == [
        "akute-belastung",
        "suizidal",
        "substanzintoxikation",
        "psychische-krise",
        "gewaltfolge",
        "medizinisch",
        "sonstiges",
    ]
    assert {
        slug: _option_slugs(package, slug)
        for slug in ("anliegen", "bedarf", "beteiligung", "weitergabe-an", "folgesituation", "vermittelt-an")
    } == {
        "anliegen": ["orientierung", "gespraech", "belaestigung", "unwohlsein", "begleitung", "sonstiges"],
        "bedarf": ["wasser", "decke", "ruheraum", "gehoerschutz", "hygieneartikel", "sonstiges"],
        "beteiligung": ["betroffene-person", "zeugin-zeuge", "begleitperson"],
        "weitergabe-an": ["orga", "security", "sanitaet", "polizei", "niemand", "offen"],
        "folgesituation": ["beruhigt", "weiterbegleitet", "abgegeben", "abgeholt", "unbekannt"],
        "vermittelt-an": ["sanitaet", "psy-support", "security", "taxi-safe-home", "sonstiges"],
    }
    fields = {item.slug: item for item in package.field_templates}
    assert (fields["dauer"].field_type, fields["dauer"].report_key) == ("number", "dauer_minuten")
    assert (fields["art-der-krise"].field_type, fields["art-der-krise"].report_key) == (
        "select",
        "art_der_krise",
    )
    for slug in ("ort-im-veranstaltungsgelaende", "notiz-vorfall", "notiz-krise"):
        assert fields[slug].sensitivity == "high"
        assert fields[slug].is_encrypted is True
    assert fields["grund"].is_required is True


@pytest.mark.django_db
def test_awareness_package_load_is_warning_free_and_idempotent(facility):
    package = parse_catalog_package("awareness")

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
def test_setup_facility_installs_only_awareness_and_its_comfort_objects():
    out = io.StringIO()
    with (
        patch("core.management.commands.setup_facility._has_rls_bypass_context", return_value=True),
        patch("builtins.input", side_effect=["Awareness-Orga", "Awareness-Team", "awareness-admin"]),
        patch("getpass.getpass", side_effect=["pw1234", "pw1234"]),
    ):
        call_command("setup_facility", package="awareness", stdout=out)

    facility = Organization.objects.get(name="Awareness-Orga").facilities.get(name="Awareness-Team")
    assert set(DocumentType.objects.for_facility(facility).values_list("slug", flat=True)) == set(EXPECTED_TYPES)
    assert (
        not DocumentType.objects.for_facility(facility)
        .filter(slug__in={"spritzentausch", "medizinische-versorgung"})
        .exists()
    )
    assert list(TimeFilter.objects.for_facility(facility).values_list("label", flat=True)) == ["Abend", "Nacht"]
    assert list(
        QuickTemplate.objects.for_facility(facility).values_list("name", "document_type__slug", "prefilled_data")
    ) == [
        ("Wasser gegeben", "versorgung", {"bedarf": ["wasser"]}),
        ("Ruheraum begleitet", "versorgung", {"bedarf": ["ruheraum"]}),
    ]
    assert "Awareness eingespielt: 7 Dokumentationstypen angelegt" in out.getvalue()


@pytest.mark.django_db
def test_quick_templates_reference_existing_fields_and_active_options(facility):
    from core.services.facility_bootstrap import PACKAGE_COMFORT

    package = parse_catalog_package("awareness")
    types = {item.slug: item for item in package.document_types}
    fields = {item.slug: item for item in package.field_templates}
    assert [item.prefilled_data for item in PACKAGE_COMFORT["awareness"].quick_templates] == [
        {"bedarf": ["wasser"]},
        {"bedarf": ["ruheraum"]},
    ]

    for item in PACKAGE_COMFORT["awareness"].quick_templates:
        doc_type = types[item.document_type_slug]
        referenced = {ref.template_slug for ref in doc_type.field_refs}
        for field_slug, value in item.prefilled_data.items():
            assert field_slug in referenced
            option_map = {option["slug"]: option["is_active"] for option in fields[field_slug].options}
            for option_slug in value:
                assert option_map[option_slug] is True


@pytest.mark.django_db
def test_every_awareness_type_is_known_to_sample_report(facility, staff_user):
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

    issues = SampleReport1Backend().validate_completeness(
        facility,
        date(2026, 9, 1),
        date(2026, 9, 2),
    )

    assert "unknown_report_keys" not in {issue.code for issue in issues}
    assert "unmapped_system_types" not in {issue.code for issue in issues}


@pytest.mark.django_db
@pytest.mark.parametrize("slug", EXPECTED_TYPES)
def test_every_awareness_type_accepts_an_anonymous_event(facility, staff_user, slug):
    doc_type = _loaded_types(facility)[slug]
    required_data = {"grund": "Platzverweis"} if slug == "orga-meldung" else {}

    event = create_event(
        facility=facility,
        user=staff_user,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json=required_data,
        client=None,
        is_anonymous=False,
    )

    assert event.is_anonymous is True


@pytest.mark.django_db
@pytest.mark.parametrize("slug", ["gewaltvorfall", "krisensituation", "orga-meldung"])
def test_assistant_cannot_see_elevated_awareness_events(client, facility, staff_user, assistant_user, slug):
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
@pytest.mark.parametrize("slug", ["awareness-kontakt", "versorgung", "vermittlung", "notiz"])
def test_assistant_can_see_normal_awareness_events(client, facility, staff_user, assistant_user, slug):
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
def test_orga_meldung_uses_the_ban_projection(facility, staff_user):
    doc_type = _loaded_types(facility)["orga-meldung"]
    person = Client.objects.create(facility=facility, pseudonym="Awareness-01", created_by=staff_user)
    until = (timezone.localdate() + timedelta(days=1)).isoformat()
    event = Event.objects.create(
        facility=facility,
        client=person,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json={"grund": "Platzverweis", "bis": until, "aktiv": True},
        created_by=staff_user,
    )

    bans = get_active_bans(facility, user=staff_user)

    assert len(bans) == 1
    assert bans[0]["event"] == event
    assert bans[0]["grund"] == "Platzverweis"
    assert bans[0]["bis_datum"] == date.fromisoformat(until)


def test_awareness_documentation_is_indexed():
    root = Path(__file__).resolve().parents[2]
    assert (root / "docs/packages-awareness.md").is_file()
    assert "[packages-awareness.md](packages-awareness.md)" in (root / "docs/README.md").read_text(encoding="utf-8")
