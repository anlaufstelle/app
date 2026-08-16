"""Striktes Strichlisten-Opt-in und Pflichtfeld-Guards (Refs #1637)."""

from __future__ import annotations

import pytest

from core.admin.documents import DocumentTypeAdmin
from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate
from core.packages.loader import load_package
from core.packages.schema import PackageValidationError, parse_package
from core.seed.doc_types import load_default_package_data
from core.services.events import build_tally_summary
from core.services.system.facility_export import build_facility_export
from tests._tally_helpers import post_tally


def _package_document_type(**overrides):
    document_type = {"slug": "kontakt", "name": "Kontakt"}
    document_type.update(overrides)
    return {"schema_version": 1, "field_templates": [], "document_types": [document_type]}


@pytest.mark.django_db
class TestTallyConfiguration:
    def test_document_type_defaults_to_tally_disabled(self, facility):
        document_type = DocumentType.objects.create(facility=facility, name="Nicht kuratiert")

        assert document_type.tally_enabled is False

    def test_admin_exposes_tally_flag_in_list_and_filter(self):
        assert "tally_enabled" in DocumentTypeAdmin.list_display
        assert "tally_enabled" in DocumentTypeAdmin.list_filter

    def test_package_schema_accepts_boolean_and_rejects_other_types(self):
        package = parse_package(_package_document_type(tally_enabled=True))

        assert package.document_types[0].tally_enabled is True
        with pytest.raises(PackageValidationError) as exc_info:
            parse_package(_package_document_type(tally_enabled="yes"))
        assert any(path.endswith("tally_enabled") for path, _message in exc_info.value.errors)

    def test_package_missing_key_preserves_existing_opt_in(self, facility):
        document_type = DocumentType.objects.create(
            facility=facility,
            slug="kontakt",
            name="Kontakt",
            tally_enabled=True,
        )

        load_package(facility, parse_package(_package_document_type()))

        document_type.refresh_from_db()
        assert document_type.tally_enabled is True

    def test_package_explicit_false_updates_existing_opt_in(self, facility):
        document_type = DocumentType.objects.create(
            facility=facility,
            slug="kontakt",
            name="Kontakt",
            tally_enabled=True,
        )

        load_package(facility, parse_package(_package_document_type(tally_enabled=False)))

        document_type.refresh_from_db()
        assert document_type.tally_enabled is False

    def test_package_explicit_true_enables_existing_type(self, facility):
        document_type = DocumentType.objects.create(
            facility=facility,
            slug="kontakt",
            name="Kontakt",
        )

        load_package(facility, parse_package(_package_document_type(tally_enabled=True)))

        document_type.refresh_from_db()
        assert document_type.tally_enabled is True

    def test_package_missing_key_uses_model_default_for_new_type(self, facility):
        load_package(facility, parse_package(_package_document_type()))

        document_type = DocumentType.objects.get(facility=facility, slug="kontakt")
        assert document_type.tally_enabled is False

    def test_default_package_enables_only_curated_types(self):
        definitions = parse_package(load_default_package_data()).document_types
        enabled_slugs = {definition.slug for definition in definitions if definition.tally_enabled}

        assert enabled_slugs == {"kontakt", "spritzentausch", "vermittlung", "notiz"}

    def test_facility_export_includes_tally_flag(self, facility):
        document_type = DocumentType.objects.create(
            facility=facility,
            name="Strichlisten-Typ",
            tally_enabled=True,
        )

        exported = build_facility_export(facility)
        entry = next(
            item for item in exported["configuration"]["document_types"] if item["id"] == str(document_type.pk)
        )

        assert entry["tally_enabled"] is True


@pytest.fixture
def tally_enabled_type(facility):
    return DocumentType.objects.create(
        facility=facility,
        name="Strichlisten-Kontakt",
        tally_enabled=True,
    )


@pytest.fixture
def tally_required_type(facility):
    document_type = DocumentType.objects.create(
        facility=facility,
        name="Strichlisten-Typ mit Pflichtfeld",
        tally_enabled=True,
    )
    required = FieldTemplate.objects.create(
        facility=facility,
        name="Pflichtangabe",
        field_type=FieldTemplate.FieldType.TEXT,
        is_required=True,
    )
    DocumentTypeField.objects.create(document_type=document_type, field_template=required)
    return document_type


@pytest.mark.django_db
class TestTallyDocumentTypeOptIn:
    def test_summary_excludes_type_without_opt_in(self, facility, staff_user):
        document_type = DocumentType.objects.create(facility=facility, name="Nicht freigegeben")

        summary = build_tally_summary(facility, staff_user)

        assert document_type.pk not in {row["document_type"].pk for row in summary["rows"]}

    def test_summary_includes_enabled_type_without_required_fields(self, facility, staff_user, tally_enabled_type):
        summary = build_tally_summary(facility, staff_user)

        assert tally_enabled_type.pk in {row["document_type"].pk for row in summary["rows"]}

    def test_summary_excludes_enabled_type_with_required_field(self, facility, staff_user, tally_required_type):
        summary = build_tally_summary(facility, staff_user)

        assert tally_required_type.pk not in {row["document_type"].pk for row in summary["rows"]}


@pytest.mark.django_db
class TestTallyIncrementOptInGuards:
    def test_post_rejects_type_without_opt_in(self, client, facility, staff_user):
        document_type = DocumentType.objects.create(facility=facility, name="Nicht freigegeben")
        client.force_login(staff_user)

        response = post_tally(
            client,
            {"document_type": str(document_type.pk)},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 400
        assert "nicht für die Strichliste freigegeben" in response.content.decode()
        assert not Event.objects.filter(document_type=document_type).exists()

    def test_post_rejects_enabled_type_with_required_field(
        self,
        client,
        staff_user,
        tally_required_type,
    ):
        client.force_login(staff_user)

        response = post_tally(
            client,
            {"document_type": str(tally_required_type.pk)},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 400
        assert "Pflichtfelder" in response.content.decode()
        assert not Event.objects.filter(document_type=tally_required_type).exists()
