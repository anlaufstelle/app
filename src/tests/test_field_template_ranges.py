"""Wertebereichs-Vertraege für dynamische Zahlenfelder (Refs #1914)."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.forms.config import FieldTemplateForm
from core.forms.dynamic import DynamicDataForm
from core.forms.tally import TallyNumberDataForm
from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate
from core.packages.loader import load_package
from core.packages.schema import PackageValidationError, parse_package
from core.seed.doc_types import load_default_package_data
from core.services.dynamic_data import merge_dynamic_data, validate_data_json
from core.services.events import create_event
from core.services.file_vault import encrypt_field
from core.services.system import validate_number_value
from core.services.system.offline import _serialize_field_template


def _number_document_type(facility, *, minimum=None, maximum=None):
    document_type = DocumentType.objects.create(facility=facility, name="Zählung")
    field_template = FieldTemplate.objects.create(
        facility=facility,
        name="Stückzahl",
        field_type=FieldTemplate.FieldType.NUMBER,
        is_required=True,
        min_value=minimum,
        max_value=maximum,
    )
    DocumentTypeField.objects.create(document_type=document_type, field_template=field_template)
    return document_type, field_template


@pytest.fixture
def fernet_key():
    return Fernet.generate_key().decode()


@pytest.mark.django_db
class TestFieldTemplateRangeValidation:
    def test_model_rejects_reversed_bounds(self, facility):
        field_template = FieldTemplate(
            facility=facility,
            name="Stückzahl",
            field_type=FieldTemplate.FieldType.NUMBER,
            min_value=5,
            max_value=4,
        )

        with pytest.raises(ValidationError) as excinfo:
            field_template.full_clean()

        assert {"min_value", "max_value"} <= set(excinfo.value.error_dict)

    def test_model_rejects_bounds_on_non_numeric_field(self, facility):
        field_template = FieldTemplate(
            facility=facility,
            name="Notiz",
            field_type=FieldTemplate.FieldType.TEXT,
            min_value=0,
        )

        with pytest.raises(ValidationError) as excinfo:
            field_template.full_clean()

        assert "min_value" in excinfo.value.error_dict

    def test_model_rejects_default_outside_configured_range(self, facility):
        field_template = FieldTemplate(
            facility=facility,
            name="Stückzahl",
            field_type=FieldTemplate.FieldType.NUMBER,
            min_value=0,
            default_value="-1",
        )

        with pytest.raises(ValidationError) as excinfo:
            field_template.full_clean()

        assert "default_value" in excinfo.value.error_dict

    def test_direct_save_cannot_persist_bounds_on_non_numeric_field(self, facility):
        with pytest.raises(ValidationError):
            FieldTemplate.objects.create(
                facility=facility,
                name="Notiz mit Bereich",
                field_type=FieldTemplate.FieldType.TEXT,
                min_value=0,
            )

    def test_direct_save_cannot_persist_default_outside_number_range(self, facility):
        with pytest.raises(ValidationError) as excinfo:
            FieldTemplate.objects.create(
                facility=facility,
                name="Negativer Default",
                field_type=FieldTemplate.FieldType.NUMBER,
                min_value=0,
                default_value="-1",
            )

        assert "default_value" in excinfo.value.error_dict

    def test_unset_bounds_keep_existing_number_behaviour(self, facility):
        document_type, field_template = _number_document_type(facility)

        form = DynamicDataForm({field_template.slug: "-7"}, document_type=document_type, facility=facility)

        assert form.is_valid()


@pytest.mark.django_db
class TestNumberRangeAcrossEntryPaths:
    def test_shared_number_validator_keeps_arbitrarily_large_integers_comparable(self):
        validate_number_value(10**1000, None, None)

    @pytest.mark.parametrize("value", [True, "3", float("nan"), float("inf")])
    def test_shared_number_validator_rejects_non_numeric_boolean_and_non_finite_values(self, facility, value):
        document_type, field_template = _number_document_type(facility, minimum=0)

        with pytest.raises(ValidationError):
            validate_data_json(document_type, {field_template.slug: value})

    def test_optional_number_none_and_restricted_encrypted_number_marker_survive_shared_merge(
        self, facility, fernet_key
    ):
        document_type, optional = _number_document_type(facility, minimum=0)
        optional.is_required = False
        optional.is_encrypted = True
        optional.save(update_fields=["is_required", "is_encrypted"])

        # HTML-, Offline- und Rectification-Updates laufen über dieselbe
        # Whitelist/merge-Policy. Ein leeres optionales Zahlenfeld bleibt ein
        # gültiger JSON-Wert; ein verborgenes Alt-Token darf dabei nicht als
        # Klartext- oder beliebiger Dict-Marker durchrutschen.
        assert validate_data_json(document_type, {optional.slug: None}) == {optional.slug: None}
        assert merge_dynamic_data(
            document_type,
            {optional.slug: None},
            old_data={},
            restricted_slugs=set(),
        ) == {optional.slug: None}

        from django.test import override_settings

        with override_settings(ENCRYPTION_KEY=fernet_key):
            encrypted_old_value = encrypt_field(3)
            assert merge_dynamic_data(
                document_type,
                {},
                old_data={optional.slug: encrypted_old_value},
                restricted_slugs={optional.slug},
            ) == {optional.slug: encrypted_old_value}

        optional.is_encrypted = False
        optional.save(update_fields=["is_encrypted"])
        with pytest.raises(ValidationError):
            validate_data_json(document_type, {optional.slug: encrypted_old_value})

    def test_dynamic_and_tally_forms_enforce_bounds_and_render_html_hints(self, facility):
        document_type, field_template = _number_document_type(facility, minimum=0, maximum=10)

        dynamic_form = DynamicDataForm({field_template.slug: "-1"}, document_type=document_type, facility=facility)
        tally_form = TallyNumberDataForm({field_template.slug: "11"}, document_type=document_type)

        assert not dynamic_form.is_valid()
        assert not tally_form.is_valid()
        assert dynamic_form.fields[field_template.slug].widget.attrs["min"] == 0
        assert dynamic_form.fields[field_template.slug].widget.attrs["max"] == 10
        assert tally_form.fields[field_template.slug].widget.attrs["min"] == 0
        assert tally_form.fields[field_template.slug].widget.attrs["max"] == 10

    def test_event_service_rejects_out_of_range_values_before_reporting(self, facility, staff_user):
        document_type, field_template = _number_document_type(facility, minimum=0)

        with pytest.raises(ValidationError) as excinfo:
            create_event(
                facility=facility,
                user=staff_user,
                document_type=document_type,
                occurred_at=timezone.now(),
                data_json={field_template.slug: -1},
                is_anonymous=True,
            )

        assert field_template.slug in excinfo.value.error_dict
        assert not Event.objects.filter(document_type=document_type).exists()

    def test_offline_metadata_carries_bounds_for_browser_hints(self, facility):
        _document_type, field_template = _number_document_type(facility, minimum=0, maximum=10)

        assert _serialize_field_template(field_template)["min_value"] == 0
        assert _serialize_field_template(field_template)["max_value"] == 10

    def test_config_form_rejects_ranges_for_non_numeric_templates(self, facility):
        form = FieldTemplateForm(
            {
                "name": "Notiz",
                "field_type": FieldTemplate.FieldType.TEXT,
                "min_value": "0",
                "options_json": "[]",
                "is_active": "on",
            },
            facility=facility,
        )

        assert not form.is_valid()
        assert "min_value" in form.errors


class TestPackageNumberRanges:
    def test_package_round_trip_keeps_number_bounds(self):
        package = parse_package(
            {
                "schema_version": 1,
                "field_templates": [
                    {"slug": "ausgabe", "name": "Ausgabe", "field_type": "number", "min_value": 0, "max_value": 50}
                ],
            }
        )

        assert package.field_templates[0].min_value == 0
        assert package.field_templates[0].max_value == 50

    @pytest.mark.django_db
    def test_loader_persists_and_resynchronizes_bounds(self, facility):
        raw = {
            "schema_version": 1,
            "field_templates": [{"slug": "ausgabe", "name": "Ausgabe", "field_type": "number", "min_value": 0}],
        }

        load_package(facility, parse_package(raw))
        raw["field_templates"][0]["max_value"] = 50
        report = load_package(facility, parse_package(raw))

        assert report.field_templates.updated == ["ausgabe"]
        assert FieldTemplate.objects.get(facility=facility, slug="ausgabe").max_value == 50

    def test_default_package_sets_non_negative_needle_exchange_quantities(self):
        field_templates = {field.slug: field for field in parse_package(load_default_package_data()).field_templates}

        assert field_templates["ausgabe"].min_value == 0
        assert field_templates["rueckgabe"].min_value == 0

    @pytest.mark.parametrize(
        "field_template",
        [
            {"slug": "notiz", "name": "Notiz", "field_type": "text", "min_value": 0},
            {"slug": "ausgabe", "name": "Ausgabe", "field_type": "number", "min_value": 2, "max_value": 1},
        ],
    )
    def test_package_rejects_invalid_number_bounds(self, field_template):
        with pytest.raises(PackageValidationError):
            parse_package({"schema_version": 1, "field_templates": [field_template]})
