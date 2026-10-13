"""Inactive inputs retain their values across public updates (Refs #2573)."""

import pytest

from core.forms.dynamic import DynamicDataForm
from core.services.case import update_case
from core.services.client import update_client
from core.services.config import set_field_template_active
from core.services.events import update_event
from core.services.file_vault import safe_decrypt
from tests.test_sensitivity_transitions import make_record, request_for

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("entity", ["event", "client", "case"])
@pytest.mark.parametrize("encrypted", [False, True])
def test_deactivate_save_reactivate_retains_value(facility, lead_user, rf, entity, encrypted):
    record, field = make_record(facility, lead_user, entity)
    field.is_required = True
    field.is_encrypted = encrypted
    field.save()
    record.refresh_from_db()
    before = record.data_json.copy()
    request = request_for(rf, facility, lead_user)
    set_field_template_active(request, field, active=False)
    for initial in (None, record.data_json):
        form = DynamicDataForm({}, document_type=record.document_type, initial_data=initial, facility=facility)
        assert field.slug not in form.fields
        assert form.is_valid(), form.errors
    if entity == "event":
        update_event(record, lead_user, {})
    elif entity == "client":
        update_client(record, lead_user, data_json={}, notes="Other edit")
    else:
        update_case(record, lead_user, data_json={}, title="Other edit")
    record.refresh_from_db()
    assert record.data_json == before
    set_field_template_active(request, field, active=True)
    form = DynamicDataForm(document_type=record.document_type, initial_data={field.slug: "SecretToken"})
    assert field.slug in form.fields
    assert safe_decrypt(record.data_json[field.slug]) == "SecretToken"


@pytest.mark.parametrize("entity", ["event", "client", "case"])
def test_new_inactive_values_are_not_written(facility, lead_user, entity):
    record, field = make_record(facility, lead_user, entity)
    record.data_json = {}
    record.save()
    field.is_active = False
    field.save()
    if entity == "event":
        update_event(record, lead_user, {field.slug: "Injected"})
    elif entity == "client":
        update_client(record, lead_user, data_json={field.slug: "Injected"})
    else:
        update_case(record, lead_user, data_json={field.slug: "Injected"})
    record.refresh_from_db()
    assert field.slug not in record.data_json


@pytest.mark.parametrize("entity", ["event", "client", "case"])
def test_public_update_preserves_inactive_value_without_form(facility, lead_user, entity):
    record, field = make_record(facility, lead_user, entity)
    field.is_active = False
    field.save()
    if entity == "event":
        update_event(record, lead_user, {})
    elif entity == "client":
        update_client(record, lead_user, data_json={}, notes="Other edit")
    else:
        update_case(record, lead_user, data_json={}, title="Other edit")
    record.refresh_from_db()
    assert record.data_json[field.slug] == "SecretToken"


def test_inactive_source_does_not_remove_active_target_requirement(facility, lead_user):
    from core.models import DocumentTypeField, FieldCondition, FieldTemplate

    record, source = make_record(facility, lead_user, "event")
    target = FieldTemplate.objects.create(facility=facility, name="Required target", is_required=True)
    assignment = DocumentTypeField.objects.create(document_type=record.document_type, field_template=target)
    FieldCondition.objects.create(
        target_field=assignment,
        depends_on_field=record.document_type.fields.get(field_template=source),
        operator="equals",
        value="trigger",
    )
    source.is_active = False
    source.save()
    form = DynamicDataForm({}, document_type=record.document_type)
    assert source.slug not in form.fields
    assert not form.is_valid()
    assert target.slug in form.errors


def test_inactive_target_cannot_block_active_inputs(facility, lead_user):
    from core.models import DocumentTypeField, FieldCondition, FieldTemplate

    record, source = make_record(facility, lead_user, "event")
    target = FieldTemplate.objects.create(facility=facility, name="Inactive target", is_required=True, is_active=False)
    assignment = DocumentTypeField.objects.create(document_type=record.document_type, field_template=target)
    FieldCondition.objects.create(
        target_field=assignment,
        depends_on_field=record.document_type.fields.get(field_template=source),
        operator="equals",
        value="trigger",
    )
    form = DynamicDataForm({source.slug: "trigger"}, document_type=record.document_type)
    assert target.slug not in form.fields
    assert form.is_valid(), form.errors


def test_historical_payload_validation_keeps_inactive_values(facility, lead_user):
    from core.services.dynamic_data import validate_data_json

    record, field = make_record(facility, lead_user, "event")
    field.is_active = False
    field.save()
    assert validate_data_json(record.document_type, record.data_json) == record.data_json


def test_offline_form_schema_omits_inactive_fields(facility, lead_user):
    from core.services.system.offline import build_facility_offline_bundle

    record, field = make_record(facility, lead_user, "event")
    field.is_active = False
    field.save()
    bundle = build_facility_offline_bundle(lead_user, facility)
    definition = next(item for item in bundle["document_types"] if item["pk"] == str(record.document_type_id))
    assert field.slug not in {item["slug"] for item in definition["fields"]}


def test_inactive_file_marker_survives_update(facility, lead_user):
    from core.models import FieldTemplate

    field = FieldTemplate.objects.create(facility=facility, name="Inactive file", field_type="file")
    record, _ = make_record(
        facility,
        lead_user,
        "event",
        field=field,
        value={"__files__": True, "entries": [{"attachment_id": "stored-file"}]},
    )
    before = record.data_json.copy()
    field.is_active = False
    field.save()
    update_event(record, lead_user, {})
    record.refresh_from_db()
    assert record.data_json == before


def test_generic_validator_still_rejects_invalid_inactive_number(facility, lead_user):
    from django.core.exceptions import ValidationError

    from core.models import FieldTemplate
    from core.services.dynamic_data import validate_data_json

    field = FieldTemplate.objects.create(facility=facility, name="Inactive number", field_type="number")
    record, _ = make_record(facility, lead_user, "event", field=field, value=42)
    field.is_active = False
    field.save()
    with pytest.raises(ValidationError):
        validate_data_json(record.document_type, {field.slug: "invalid"})


def test_generic_validator_still_rejects_non_marker_inactive_file(facility, lead_user):
    from core.models import FieldTemplate
    from core.services.dynamic_data import validate_data_json

    field = FieldTemplate.objects.create(facility=facility, name="Inactive attachment", field_type="file")
    record, _ = make_record(facility, lead_user, "event", field=field, value={"__files__": True, "entries": []})
    field.is_active = False
    field.save()
    assert validate_data_json(record.document_type, {field.slug: "invalid"}) == {}
