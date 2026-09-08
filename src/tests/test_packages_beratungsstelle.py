"""Tests für das Konfigurationspaket „Beratungsstelle“ (Refs #2145)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.test import Client as RequestClient
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Attachment,
    Client,
    DocumentType,
    DocumentTypeField,
    Event,
    FieldTemplate,
    QuickTemplate,
    TimeFilter,
)
from core.packages.catalog import parse_catalog_package
from core.packages.loader import load_package
from core.reporting.backends.counseling_hours import CounselingHoursBackend
from core.reporting.backends.sample_report_1 import SampleReport1Backend
from core.services.events.crud import create_event
from core.services.file_vault.encryption import encrypt_field
from core.services.system.bans import get_active_bans

pytestmark = pytest.mark.django_db

EXPECTED_TYPES = (
    "kontakt",
    "kurzberatung",
    "beratungsgespraech",
    "krisengespraech",
    "begleitung",
    "vermittlung",
    "notiz",
    "hausverbot",
)
ANONYMOUS_TYPES = ("kontakt", "kurzberatung", "vermittlung", "notiz")


def _counts() -> tuple[int, int, int, int, int]:
    return (
        FieldTemplate.objects.count(),
        DocumentType.objects.count(),
        DocumentTypeField.objects.count(),
        QuickTemplate.objects.count(),
        TimeFilter.objects.count(),
    )


def _load_package(facility):
    return load_package(facility, parse_catalog_package("beratungsstelle"))


def _assert_catalog(facility):
    types = DocumentType.objects.for_facility(facility)
    assert set(types.values_list("slug", flat=True)) == set(EXPECTED_TYPES)
    assert not types.filter(slug__in=("spritzentausch", "medizinische-versorgung")).exists()

    by_slug = {doc_type.slug: doc_type for doc_type in types}
    assert by_slug["kontakt"].name == "Kontakt (Sprechstunde)"
    assert by_slug["kontakt"].tally_enabled is True
    assert by_slug["kontakt"].report_key == "contact"
    assert by_slug["kurzberatung"].name == "Kurzberatung (anonym)"
    assert by_slug["kurzberatung"].tally_enabled is True
    assert by_slug["kurzberatung"].report_key == "counseling"
    assert by_slug["kurzberatung"].min_contact_stage is None
    assert by_slug["beratungsgespraech"].sensitivity == DocumentType.Sensitivity.ELEVATED
    assert by_slug["beratungsgespraech"].min_contact_stage == Client.ContactStage.IDENTIFIED
    assert by_slug["beratungsgespraech"].report_key == "counseling"
    assert by_slug["krisengespraech"].system_type == DocumentType.SystemType.CRISIS
    assert by_slug["krisengespraech"].report_key == "crisis"
    assert by_slug["begleitung"].min_contact_stage == Client.ContactStage.IDENTIFIED
    assert by_slug["begleitung"].report_key == "accompaniment"
    assert by_slug["vermittlung"].tally_enabled is True
    assert by_slug["vermittlung"].report_key == "referral"
    assert by_slug["notiz"].report_key == "excluded"
    assert by_slug["hausverbot"].system_type == DocumentType.SystemType.BAN
    assert by_slug["hausverbot"].report_key == "excluded"
    assert by_slug["hausverbot"].sort_order == 7

    fields_for_type = {
        doc_type.slug: list(
            DocumentTypeField.objects.filter(document_type=doc_type)
            .select_related("field_template")
            .order_by("sort_order")
            .values_list("field_template__slug", flat=True)
        )
        for doc_type in types
    }
    assert fields_for_type["kontakt"] == ["dauer", "alterscluster", "leistungen", "notiz"]
    assert fields_for_type["kurzberatung"] == ["thema-kurz", "dauer", "weiterverweis"]
    assert fields_for_type["beratungsgespraech"] == [
        "thema",
        "dauer",
        "vereinbarungen",
        "naechster-termin",
        "scan-bescheid",
    ]
    assert fields_for_type["krisengespraech"] == [
        "art-der-krise",
        "dauer",
        "notiz-krise",
        "weitervermittlung",
    ]
    assert fields_for_type["begleitung"] == ["ziel", "datum", "uhrzeit", "notiz-begleitung"]
    assert fields_for_type["vermittlung"] == []
    assert fields_for_type["notiz"] == ["notiz"]
    assert fields_for_type["hausverbot"] == ["grund", "bis", "aktiv"]

    templates = {template.slug: template for template in FieldTemplate.objects.for_facility(facility)}
    assert set(templates) == {
        "dauer",
        "notiz",
        "leistungen",
        "alterscluster",
        "thema-kurz",
        "weiterverweis",
        "art-der-krise",
        "notiz-krise",
        "weitervermittlung",
        "ziel",
        "datum",
        "uhrzeit",
        "notiz-begleitung",
        "thema",
        "vereinbarungen",
        "naechster-termin",
        "scan-bescheid",
        "grund",
        "bis",
        "aktiv",
    }
    assert templates["grund"].field_type == FieldTemplate.FieldType.TEXTAREA
    assert templates["grund"].is_required is True
    assert templates["bis"].field_type == FieldTemplate.FieldType.DATE
    assert templates["aktiv"].field_type == FieldTemplate.FieldType.BOOLEAN
    assert templates["thema"].sensitivity == DocumentType.Sensitivity.HIGH
    assert templates["thema"].is_encrypted is True
    assert templates["scan-bescheid"].field_type == FieldTemplate.FieldType.FILE
    assert templates["scan-bescheid"].is_encrypted is True

    options = {option["slug"] for option in templates["leistungen"].options_json}
    assert options == {
        "information",
        "formularhilfe",
        "telefonat-fuer-klientin",
        "weiterverweis",
        "wartebereich",
        "sonstiges",
    }
    options = {option["slug"] for option in templates["thema-kurz"].options_json}
    assert options == {
        "wohnen",
        "geld-schulden",
        "arbeit",
        "gesundheit",
        "familie-beziehung",
        "behoerden-antraege",
        "aufenthalt",
        "sonstiges",
    }
    options = {option["slug"] for option in templates["weiterverweis"].options_json}
    assert options == {"interne-beratung", "fachstelle", "behoerde", "keine"}
    options = {option["slug"] for option in templates["art-der-krise"].options_json}
    assert options == {
        "akute-belastung",
        "suizidal",
        "gewalt",
        "wohnungsverlust",
        "finanzielle-notlage",
        "psychische-krise",
        "sonstiges",
    }


def test_parse_und_erstlauf_gegen_frische_einrichtung(facility):
    report = _load_package(facility)

    assert report.errors == []
    assert report.warnings == []
    assert report.field_templates.created == [
        "dauer",
        "notiz",
        "leistungen",
        "alterscluster",
        "thema-kurz",
        "weiterverweis",
        "art-der-krise",
        "notiz-krise",
        "weitervermittlung",
        "ziel",
        "datum",
        "uhrzeit",
        "notiz-begleitung",
        "thema",
        "vereinbarungen",
        "naechster-termin",
        "scan-bescheid",
        "grund",
        "bis",
        "aktiv",
    ]
    assert report.document_types.created == list(EXPECTED_TYPES)
    _assert_catalog(facility)


def test_doppellauf_ist_vollstaendig_unveraendert(facility):
    _load_package(facility)
    before = _counts()

    second = _load_package(facility)

    assert second.errors == []
    assert second.warnings == []
    for _section_name, section in second.sections():
        assert section.created == []
        assert section.updated == []
    assert _counts() == before
    _assert_catalog(facility)


def test_setup_facility_legt_katalog_und_komfortobjekte_an():
    output = StringIO()
    from core.management.commands import setup_facility as command_module

    with (
        patch.object(command_module, "_has_rls_bypass_context", return_value=True),
        patch("builtins.input", side_effect=["Org", "Beratungsstelle", "admin"]),
        patch("getpass.getpass", side_effect=["pw1234", "pw1234"]),
    ):
        call_command("setup_facility", package="beratungsstelle", stdout=output)

    facility = DocumentType.objects.get(slug="kontakt").facility
    _assert_catalog(facility)
    assert "Beratungsstelle eingespielt" in output.getvalue()
    assert set(TimeFilter.objects.for_facility(facility).values_list("label", flat=True)) == {
        "Sprechstunde Vormittag",
        "Sprechstunde Nachmittag",
        "Termine",
    }

    templates = {
        template.name: (template.document_type.slug, template.prefilled_data)
        for template in QuickTemplate.objects.for_facility(facility)
    }
    assert templates == {
        "Kurzinfo Sprechstunde": ("kurzberatung", {"dauer": 10, "weiterverweis": "keine"}),
        "Formularhilfe": ("kontakt", {"leistungen": ["formularhilfe"]}),
        "Telefonische Kurzberatung": ("kurzberatung", {"dauer": 15}),
    }


def test_alle_typen_sind_im_sachbericht_zugeordnet(facility, staff_user, client_identified):
    _load_package(facility)
    occurred_at = timezone.make_aware(datetime(2026, 5, 12, 10, 0))
    for slug in EXPECTED_TYPES:
        Event.objects.create(
            facility=facility,
            document_type=DocumentType.objects.for_facility(facility).get(slug=slug),
            occurred_at=occurred_at,
            client=client_identified,
            data_json={},
            created_by=staff_user,
        )

    issues = SampleReport1Backend().validate_completeness(facility, date(2026, 5, 1), date(2026, 5, 31))

    assert [(issue.code, issue.count) for issue in issues] == []


@pytest.mark.parametrize("slug", ANONYMOUS_TYPES)
def test_anonyme_typen_ohne_person(facility, staff_user, slug):
    _load_package(facility)
    document_type = DocumentType.objects.for_facility(facility).get(slug=slug)

    event = create_event(
        facility=facility,
        user=staff_user,
        document_type=document_type,
        occurred_at=timezone.now(),
        data_json={},
        client=None,
    )

    assert event.is_anonymous is True


def test_beratungsgespraech_reicht_pseudonym(facility, staff_user, client_identified):
    _load_package(facility)
    document_type = DocumentType.objects.for_facility(facility).get(slug="beratungsgespraech")

    event = create_event(
        facility=facility,
        user=staff_user,
        document_type=document_type,
        occurred_at=timezone.now(),
        data_json={},
        client=client_identified,
    )

    assert event.client == client_identified
    assert event.is_anonymous is False


def test_assistent_sieht_nur_normale_pakettypen(facility, assistant_user, staff_user):
    _load_package(facility)
    events = {
        slug: Event.objects.create(
            facility=facility,
            document_type=DocumentType.objects.for_facility(facility).get(slug=slug),
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        for slug in ("kontakt", "kurzberatung", "beratungsgespraech", "krisengespraech", "hausverbot")
    }

    assistant_client = RequestClient()
    assistant_client.force_login(assistant_user)

    assert assistant_client.get(reverse("core:event_detail", kwargs={"pk": events["kontakt"].pk})).status_code == 200
    assert (
        assistant_client.get(reverse("core:event_detail", kwargs={"pk": events["kurzberatung"].pk})).status_code == 200
    )
    for slug in ("beratungsgespraech", "krisengespraech", "hausverbot"):
        response = assistant_client.get(reverse("core:event_detail", kwargs={"pk": events[slug].pk}))
        assert response.status_code == 404


def test_high_felder_bleiben_fuer_staff_verborgen(facility, staff_user, lead_user):
    _load_package(facility)
    document_type = DocumentType.objects.for_facility(facility).get(slug="beratungsgespraech")
    event = Event.objects.create(
        facility=facility,
        document_type=document_type,
        occurred_at=timezone.now(),
        data_json={
            "thema": "Wohnungsverlust",
            "vereinbarungen": "Sofortige Unterstützung klären",
        },
        created_by=staff_user,
    )
    attachment = Attachment.objects.create(
        facility=facility,
        event=event,
        field_template=FieldTemplate.objects.for_facility(facility).get(slug="scan-bescheid"),
        storage_filename="bescheid.enc",
        original_filename_encrypted=encrypt_field("Bescheid.pdf"),
        file_size=12,
        mime_type="application/pdf",
    )
    event.data_json["scan-bescheid"] = {"__files__": True, "entries": [{"id": str(attachment.pk), "sort": 0}]}
    event.save(update_fields=["data_json"])

    staff_client = RequestClient()
    staff_client.force_login(staff_user)
    response = staff_client.get(reverse("core:event_detail", kwargs={"pk": event.pk}))
    assert response.status_code == 200
    staff_content = response.content.decode()
    assert "Wohnungsverlust" not in staff_content
    assert "Sofortige Unterstützung klären" not in staff_content
    assert "Bescheid.pdf" not in staff_content

    lead_client = RequestClient()
    lead_client.force_login(lead_user)
    response = lead_client.get(reverse("core:event_detail", kwargs={"pk": event.pk}))
    assert response.status_code == 200
    lead_content = response.content.decode()
    assert "Wohnungsverlust" in lead_content
    assert "Sofortige Unterstützung klären" in lead_content
    assert "Bescheid.pdf" in lead_content


def test_kurzberatung_zaehlt_in_beratungsstunden(facility, staff_user):
    _load_package(facility)
    occurred_at = timezone.make_aware(datetime(2026, 5, 12, 10, 0))
    Event.objects.create(
        facility=facility,
        document_type=DocumentType.objects.for_facility(facility).get(slug="kurzberatung"),
        occurred_at=occurred_at,
        data_json={"dauer": 17},
        created_by=staff_user,
    )

    result = CounselingHoursBackend().build_dataset(facility, date(2026, 5, 1), date(2026, 5, 31))
    issues = CounselingHoursBackend().validate_completeness(facility, date(2026, 5, 1), date(2026, 5, 31))

    assert result.records == [{"kategorie": "counseling", "events": 1, "minuten": 17, "beitragende": 1}]
    assert result.summary == {"events_gesamt": 1, "minuten_gesamt": 17}
    assert "unknown_report_keys" not in [issue.code for issue in issues]


def test_hausverbot_projektion(facility, staff_user, client_identified):
    _load_package(facility)
    document_type = DocumentType.objects.for_facility(facility).get(slug="hausverbot")
    Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=document_type,
        occurred_at=timezone.now(),
        data_json={
            "grund": "Wiederholte Bedrohung",
            "bis": (date.today() + timedelta(days=30)).isoformat(),
            "aktiv": True,
        },
        created_by=staff_user,
    )

    bans = get_active_bans(facility, user=staff_user)

    assert len(bans) == 1
    assert bans[0]["grund"] == "Wiederholte Bedrohung"
    assert bans[0]["bis_datum"] == date.today() + timedelta(days=30)
    assert bans[0]["client"] == client_identified


@pytest.mark.architecture  # liest docs/ — in mutmut-Runs deselektiert (Kopie ohne docs/)
def test_dokumentation_ist_gefuehrt():
    # Repo-verankert statt CWD-relativ: mutmut startet die Suite nicht im Repo-Root (Refs #2145).
    repo_root = Path(__file__).resolve().parents[2]
    package_doc = repo_root / "docs" / "packages-beratungsstelle.md"
    packages_doc = (repo_root / "docs" / "packages.md").read_text(encoding="utf-8")
    readme = (repo_root / "docs" / "README.md").read_text(encoding="utf-8")

    assert package_doc.exists()
    assert "`beratungsstelle`" in packages_doc
    assert "[packages-beratungsstelle.md](packages-beratungsstelle.md)" in readme
