"""Vertrag des Frauenhaus-Katalogpakets (Refs #2138)."""

from __future__ import annotations

import io
from datetime import date, datetime, time
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client as RequestClient
from django.urls import reverse
from django.utils import timezone

from core.models import (
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
from core.reporting.backends.counseling_hours import CounselingHoursBackend
from core.reporting.backends.sample_report_1 import SampleReport1Backend
from core.services.case import build_handover_summary
from core.services.compliance import user_can_see_field
from core.services.events import create_event
from core.services.file_vault import get_attachment_path, get_decrypted_file_stream, store_encrypted_file
from core.services.system import get_active_bans

pytestmark = pytest.mark.django_db

EXPECTED_TYPES = {
    "anfrage": ("Anfrage (Erstkontakt)", "normal", True, "identified", None, "contact"),
    "beratung": ("Beratungsgespräch", "elevated", False, "qualified", None, "counseling"),
    "gefaehrdungslage": ("Gefährdungslage", "high", False, "qualified", "crisis", "crisis"),
    "begleitung": (
        "Begleitung (Behörde/Gericht/Polizei)",
        "normal",
        False,
        "identified",
        None,
        "accompaniment",
    ),
    "schutzmassnahme": ("Schutzmaßnahme", "elevated", False, "identified", None, "protection"),
    "wohnungswechsel": ("Wohnungswechsel/Übergang", "normal", False, "identified", None, "contact"),
    "vermittlung": ("Vermittlung", "normal", True, "identified", None, "referral"),
    "notiz": ("Notiz", "normal", True, None, None, "excluded"),
}

EXPECTED_FIELDS = {
    "anliegen": "multi_select",
    "art": "select",
    "aufnahme-moeglich": "boolean",
    "ausloeser": "select",
    "datum": "date",
    "dauer": "number",
    "eskalationskanal": "select",
    "gueltig-bis": "date",
    "kontaktweg": "select",
    "naechster-schritt": "date",
    "notiz": "textarea",
    "notiz-begleitung": "textarea",
    "notiz-gefaehrdung": "textarea",
    "scan-dokument": "file",
    "thema": "text",
    "uhrzeit": "time",
    "vereinbarungen": "textarea",
    "vermittelt-an": "select",
    "von-nach": "select",
    "ziel": "text",
}

EXPECTED_FIELDS_BY_TYPE = {
    "anfrage": ["kontaktweg", "anliegen", "aufnahme-moeglich"],
    "beratung": ["thema", "dauer", "vereinbarungen", "naechster-schritt", "scan-dokument"],
    "gefaehrdungslage": ["ausloeser", "notiz-gefaehrdung", "eskalationskanal"],
    "begleitung": ["ziel", "datum", "uhrzeit", "notiz-begleitung"],
    "schutzmassnahme": ["art", "gueltig-bis"],
    "wohnungswechsel": ["von-nach", "datum"],
    "vermittlung": ["vermittelt-an"],
    "notiz": ["notiz"],
}

EXPECTED_OPTIONS = {
    "kontaktweg": ["telefon", "persoenlich", "mail-weitergeleitet", "dritte-stelle"],
    "anliegen": ["unterbringung", "beratung", "rechtliches", "finanzen", "kind", "sonstiges"],
    "ausloeser": [
        "aktuelle-eskalation",
        "drohlage",
        "stalking",
        "kindeswohl",
        "nachverfolgung",
        "sonstiges",
    ],
    "eskalationskanal": ["polizei", "jugendamt", "anwalt", "medizinisch", "keine"],
    "art": [
        "einstweilige-anordnung",
        "schutzantrag",
        "kontaktverbot",
        "raeumungsankuendigung",
        "sonstiges",
    ],
    "von-nach": [
        "aufnahme",
        "interner-wechsel",
        "auszug-eigene-wohnung",
        "auszug-andere-einrichtung",
        "sonstiges",
    ],
    "vermittelt-an": ["rechtsberatung", "jugendamt", "jobcenter", "aerztlich", "therapie", "sonstiges"],
}

ROLE_VISIBLE_TYPES = {
    "assistant": {"anfrage", "begleitung", "wohnungswechsel", "vermittlung", "notiz"},
    "staff": {"anfrage", "beratung", "begleitung", "schutzmassnahme", "wohnungswechsel", "vermittlung", "notiz"},
    "lead": set(EXPECTED_TYPES),
    "admin": set(EXPECTED_TYPES),
}

PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _counts() -> tuple[int, int, int, int, int]:
    return (
        FieldTemplate.objects.count(),
        DocumentType.objects.count(),
        FieldGroup.objects.count(),
        DocumentTypeField.objects.count(),
        FieldCondition.objects.count(),
    )


def _load_package(facility):
    return load_package(facility, parse_catalog_package("frauenhaus"))


def _loaded_types(facility) -> dict[str, DocumentType]:
    _load_package(facility)
    return {item.slug: item for item in DocumentType.objects.for_facility(facility)}


def _option_slugs(package, field_slug: str) -> list[str]:
    template = next(item for item in package.field_templates if item.slug == field_slug)
    return [option["slug"] for option in template.options]


def test_frauenhaus_package_parses_strictly_with_exact_catalog_contract():
    package = parse_catalog_package("frauenhaus")

    assert {item.slug: item.field_type for item in package.field_templates} == EXPECTED_FIELDS
    assert {
        item.slug: (
            item.name,
            item.sensitivity,
            item.tally_enabled,
            item.min_contact_stage,
            item.system_type,
            item.report_key,
        )
        for item in package.document_types
    } == EXPECTED_TYPES
    assert all(item.entity_type == "event" for item in package.document_types)
    assert {slug: _option_slugs(package, slug) for slug in EXPECTED_OPTIONS} == EXPECTED_OPTIONS

    fields = {item.slug: item for item in package.field_templates}
    assert (fields["dauer"].field_type, fields["dauer"].report_key) == ("number", "dauer_minuten")
    assert (fields["ausloeser"].field_type, fields["ausloeser"].report_key) == ("select", "art_der_krise")
    for slug in ("thema", "vereinbarungen", "scan-dokument", "notiz-gefaehrdung", "notiz-begleitung"):
        assert fields[slug].sensitivity == "high"
        assert fields[slug].is_encrypted is True
    assert fields["scan-dokument"].field_type == "file"
    assert [item.slug for item in package.field_templates if item.field_type == "file"] == ["scan-dokument"]

    assert {
        item.slug: [ref.template_slug for ref in item.field_refs] for item in package.document_types
    } == EXPECTED_FIELDS_BY_TYPE


def test_frauenhaus_package_load_is_warning_free_and_idempotent(facility):
    package = parse_catalog_package("frauenhaus")

    first = load_package(facility, package)
    before = _counts()
    second = load_package(facility, package)

    assert first.errors == first.warnings == []
    assert first.field_templates.created == [item.slug for item in package.field_templates]
    assert first.document_types.created == [item.slug for item in package.document_types]
    assert len(first.field_refs.created) == 21
    assert second.errors == second.warnings == []
    for _section_name, section in second.sections():
        assert section.created == []
        assert section.updated == []
    assert second.field_templates.unchanged == [item.slug for item in package.field_templates]
    assert second.document_types.unchanged == [item.slug for item in package.document_types]
    assert len(second.field_refs.unchanged) == 21
    assert second.groups.unchanged == second.conditions.unchanged == []
    assert _counts() == before


def test_setup_facility_installs_only_frauenhaus_and_exact_comfort_objects():
    out = io.StringIO()
    with (
        patch("core.management.commands.setup_facility._has_rls_bypass_context", return_value=True),
        patch("builtins.input", side_effect=["Frauenhaus-Träger", "Schutzwohnung", "frauenhaus-admin"]),
        patch("getpass.getpass", side_effect=["pw1234", "pw1234"]),
    ):
        call_command("setup_facility", package="frauenhaus", stdout=out)

    facility = Organization.objects.get(name="Frauenhaus-Träger").facilities.get(name="Schutzwohnung")
    assert set(DocumentType.objects.for_facility(facility).values_list("slug", flat=True)) == set(EXPECTED_TYPES)
    assert (
        not DocumentType.objects.for_facility(facility)
        .filter(slug__in={"spritzentausch", "medizinische-versorgung"})
        .exists()
    )
    assert list(TimeFilter.objects.for_facility(facility).values_list("label", flat=True)) == [
        "Frühdienst",
        "Spätdienst",
        "Nachtdienst",
    ]
    assert list(
        QuickTemplate.objects.for_facility(facility).values_list("name", "document_type__slug", "prefilled_data")
    ) == [
        (
            "Telefonische Anfrage Unterbringung",
            "anfrage",
            {"kontaktweg": "telefon", "anliegen": ["unterbringung"]},
        ),
        ("Belegstand vermerkt", "notiz", {}),
    ]
    assert "Frauenhaus eingespielt: 8 Dokumentationstypen angelegt" in out.getvalue()


def test_quick_templates_reference_only_normal_types_and_valid_active_options():
    from core.services.facility_bootstrap import PACKAGE_COMFORT

    package = parse_catalog_package("frauenhaus")
    types = {item.slug: item for item in package.document_types}
    fields = {item.slug: item for item in package.field_templates}
    templates = PACKAGE_COMFORT["frauenhaus"].quick_templates

    assert [(item.document_type_slug, item.name, item.prefilled_data) for item in templates] == [
        (
            "anfrage",
            "Telefonische Anfrage Unterbringung",
            {"kontaktweg": "telefon", "anliegen": ["unterbringung"]},
        ),
        ("notiz", "Belegstand vermerkt", {}),
    ]
    for item in templates:
        doc_type = types[item.document_type_slug]
        assert doc_type.sensitivity == "normal"
        referenced = {ref.template_slug for ref in doc_type.field_refs}
        for field_slug, value in item.prefilled_data.items():
            assert field_slug in referenced
            option_map = {option["slug"]: option["is_active"] for option in fields[field_slug].options}
            values = value if isinstance(value, list) else [value]
            assert all(option_map[option_slug] is True for option_slug in values)


@pytest.mark.parametrize("slug", EXPECTED_TYPES)
def test_every_frauenhaus_type_is_known_to_sample_report(facility, staff_user, client_qualified, slug):
    types = _loaded_types(facility)
    occurred_at = timezone.make_aware(datetime(2026, 9, 1, 12, 0))
    Event.objects.create(
        facility=facility,
        document_type=types[slug],
        occurred_at=occurred_at,
        client=client_qualified,
        data_json={},
        created_by=staff_user,
    )

    backend = SampleReport1Backend()
    issues = backend.validate_completeness(facility, date(2026, 9, 1), date(2026, 9, 2))

    assert issues == []


def test_protection_has_own_sample_report_row(facility, staff_user, client_qualified):
    types = _loaded_types(facility)
    Event.objects.create(
        facility=facility,
        document_type=types["schutzmassnahme"],
        occurred_at=timezone.make_aware(datetime(2026, 9, 1, 12, 0)),
        client=client_qualified,
        data_json={},
        created_by=staff_user,
    )

    result = SampleReport1Backend().build_dataset(facility, date(2026, 9, 1), date(2026, 9, 2))

    assert {row["category"]: row["count"] for row in result.records}["Schutz"] == 1


def test_beratung_counts_in_counseling_hours(facility, staff_user, client_qualified):
    types = _loaded_types(facility)
    Event.objects.create(
        facility=facility,
        document_type=types["beratung"],
        occurred_at=timezone.make_aware(datetime(2026, 9, 1, 12, 0)),
        client=client_qualified,
        data_json={"dauer": 37},
        created_by=staff_user,
    )

    backend = CounselingHoursBackend()
    result = backend.build_dataset(facility, date(2026, 9, 1), date(2026, 9, 2))

    assert backend.is_available(facility) is True
    assert result.records == [{"kategorie": "counseling", "events": 1, "minuten": 37, "beitragende": 1}]


def test_notiz_accepts_an_anonymous_event(facility, staff_user):
    doc_type = _loaded_types(facility)["notiz"]

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


def test_type_visibility_matrix_matches_roles(facility, staff_user, assistant_user, lead_user, admin_user):
    types = _loaded_types(facility)
    events = {
        slug: Event.objects.create(
            facility=facility,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        for slug, doc_type in types.items()
    }
    users = {
        "assistant": assistant_user,
        "staff": staff_user,
        "lead": lead_user,
        "admin": admin_user,
    }

    request_client = RequestClient()
    for role, user in users.items():
        request_client.force_login(user)
        for slug, event in events.items():
            response = request_client.get(reverse("core:event_detail", kwargs={"pk": event.pk}))
            expected_status = 200 if slug in ROLE_VISIBLE_TYPES[role] else 404
            assert response.status_code == expected_status


def test_field_visibility_matrix_includes_effective_type_sensitivity(
    facility, assistant_user, staff_user, lead_user, admin_user
):
    types = _loaded_types(facility)
    users = {
        "assistant": assistant_user,
        "staff": staff_user,
        "lead": lead_user,
        "admin": admin_user,
    }
    expected = {
        "anfrage": {
            "assistant": {"kontaktweg", "anliegen", "aufnahme-moeglich"},
            "staff": {"kontaktweg", "anliegen", "aufnahme-moeglich"},
            "lead": {"kontaktweg", "anliegen", "aufnahme-moeglich"},
            "admin": {"kontaktweg", "anliegen", "aufnahme-moeglich"},
        },
        "beratung": {
            "assistant": set(),
            "staff": {"dauer", "naechster-schritt"},
            "lead": set(EXPECTED_FIELDS_BY_TYPE["beratung"]),
            "admin": set(EXPECTED_FIELDS_BY_TYPE["beratung"]),
        },
        "gefaehrdungslage": {
            "assistant": set(),
            "staff": set(),
            "lead": set(EXPECTED_FIELDS_BY_TYPE["gefaehrdungslage"]),
            "admin": set(EXPECTED_FIELDS_BY_TYPE["gefaehrdungslage"]),
        },
        "begleitung": {
            "assistant": {"ziel", "datum", "uhrzeit"},
            "staff": {"ziel", "datum", "uhrzeit"},
            "lead": set(EXPECTED_FIELDS_BY_TYPE["begleitung"]),
            "admin": set(EXPECTED_FIELDS_BY_TYPE["begleitung"]),
        },
        "schutzmassnahme": {
            "assistant": set(),
            "staff": {"art", "gueltig-bis"},
            "lead": {"art", "gueltig-bis"},
            "admin": {"art", "gueltig-bis"},
        },
        "wohnungswechsel": {role: {"von-nach", "datum"} for role in users},
        "vermittlung": {role: {"vermittelt-an"} for role in users},
        "notiz": {role: {"notiz"} for role in users},
    }

    for type_slug, doc_type in types.items():
        fields = [
            relation.field_template
            for relation in DocumentTypeField.objects.filter(document_type=doc_type).select_related("field_template")
        ]
        for role, user in users.items():
            visible = {
                field.slug for field in fields if user_can_see_field(user, doc_type.sensitivity, field.sensitivity)
            }
            assert visible == expected[type_slug][role]


def test_gefaehrdungslage_is_crisis_highlight_but_schutzmassnahme_is_not_a_ban(
    facility, staff_user, lead_user, client_qualified
):
    types = _loaded_types(facility)
    shift = TimeFilter.objects.create(
        facility=facility,
        label="Ganztag",
        start_time=time(0, 0),
        end_time=time(23, 59),
        sort_order=0,
    )
    now = timezone.make_aware(datetime.combine(timezone.localdate(), time(12, 0)))
    danger = Event.objects.create(
        facility=facility,
        document_type=types["gefaehrdungslage"],
        client=client_qualified,
        occurred_at=now,
        data_json={"ausloeser": "drohlage"},
        created_by=staff_user,
    )
    Event.objects.create(
        facility=facility,
        document_type=types["schutzmassnahme"],
        client=client_qualified,
        occurred_at=now,
        data_json={"art": "kontaktverbot"},
        created_by=staff_user,
    )

    handover = build_handover_summary(facility, timezone.localdate(), shift, lead_user)

    assert types["schutzmassnahme"].system_type is None
    assert [item["object"] for item in handover["highlights"] if item["type"] == "crisis"] == [danger]
    assert [item for item in handover["highlights"] if item["type"] == "ban"] == []
    assert handover["stats"]["bans_new"] == 0
    assert get_active_bans(facility, user=lead_user) == []
    assert BanOverviewBackend().is_available(facility) is False


def test_scan_dokument_uses_encrypted_attachment_pipeline(
    facility,
    staff_user,
    client_qualified,
    settings_obj,
    settings,
    tmp_path,
    _encryption_key,
):
    types = _loaded_types(facility)
    event = Event.objects.create(
        facility=facility,
        document_type=types["beratung"],
        client=client_qualified,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )
    field = FieldTemplate.objects.for_facility(facility).get(slug="scan-dokument")
    settings.MEDIA_ROOT = tmp_path
    uploaded = SimpleUploadedFile("schutzantrag.pdf", PDF_BYTES, content_type="application/pdf")

    attachment = store_encrypted_file(facility, uploaded, field, event, staff_user)
    stored = get_attachment_path(attachment).read_bytes()

    assert field.field_type == FieldTemplate.FieldType.FILE
    assert field.sensitivity == DocumentType.Sensitivity.HIGH
    assert field.is_encrypted is True
    assert stored != PDF_BYTES
    assert b"schutzantrag" not in stored
    assert b"".join(get_decrypted_file_stream(attachment)) == PDF_BYTES


def test_frauenhaus_documentation_is_indexed():
    root = Path(__file__).resolve().parents[2]
    packages_doc = (root / "docs/packages.md").read_text(encoding="utf-8")
    readme = (root / "docs/README.md").read_text(encoding="utf-8")

    assert (root / "docs/packages-frauenhaus.md").is_file()
    assert "`frauenhaus`" in packages_doc
    assert "[packages-frauenhaus.md](packages-frauenhaus.md)" in readme
