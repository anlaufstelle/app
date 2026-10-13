"""Preserve stored values through configuration type changes (Refs #2572)."""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.models import EventHistory, FieldTemplate
from core.services.file_vault import safe_decrypt
from tests.test_sensitivity_transitions import make_record

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("entity", ["event", "client", "case"])
@pytest.mark.parametrize("target", ["number", "select", "file"])
@pytest.mark.parametrize("trashed", [False, True])
def test_configuration_rejects_incompatible_used_type_change(client, admin_user, facility, entity, target, trashed):
    record, field = make_record(facility, admin_user, entity, value="Keep this note")
    if trashed:
        type(record).objects.for_facility(facility).filter(pk=record.pk).update(is_deleted=True)
    field.is_encrypted = True
    field.save()
    record.refresh_from_db()
    before = record.data_json.copy()
    client.force_login(admin_user)
    response = client.post(
        reverse("core:config_fieldtemplate_update", args=[field.pk]),
        {"name": field.name, "field_type": target, "sensitivity": "normal", "is_active": "on"},
    )
    assert response.status_code == 200
    assert "field_type" in response.context["form"].errors
    field.refresh_from_db()
    assert field.field_type == "text"
    record.save()
    record.refresh_from_db()
    assert record.data_json == before
    assert safe_decrypt(record.data_json[field.slug]) == "Keep this note"


@pytest.mark.parametrize("source,target", [("text", "textarea"), ("textarea", "text")])
def test_compatible_text_change_preserves_round_trip(facility, lead_user, source, target):
    record, field = make_record(facility, lead_user, "event")
    field.field_type = source
    field.save()
    field.field_type = target
    field.save()
    record.save()
    record.refresh_from_db()
    assert record.data_json[field.slug] == "SecretToken"


def test_model_save_rejects_file_marker_type_change(facility, lead_user):
    field = FieldTemplate.objects.create(facility=facility, name="File", field_type="file")
    record, _ = make_record(
        facility,
        lead_user,
        "event",
        field=field,
        value={"__files__": True, "entries": [{"attachment_id": "stored-file"}]},
    )
    before = record.data_json.copy()
    field.field_type = "text"
    with pytest.raises(ValidationError):
        field.save()
    record.refresh_from_db()
    assert record.data_json == before


def test_history_only_usage_prevents_type_change(facility, lead_user):
    record, field = make_record(facility, lead_user, "event")
    EventHistory.objects.create(
        event=record,
        action="update",
        changed_by=lead_user,
        data_before=record.data_json,
        data_after={},
    )
    record.data_json = {}
    record.save()
    field.field_type = "number"
    with pytest.raises(ValidationError):
        field.save()


@pytest.mark.parametrize("target", ["number", "select", "file"])
def test_unused_templates_can_change_type(facility, target):
    field = FieldTemplate.objects.create(facility=facility, name="Unused template")
    field.field_type = target
    field.save()
    field.refresh_from_db()
    assert field.field_type == target


def test_prefilled_template_usage_prevents_type_change(facility, lead_user):
    from core.models import QuickTemplate

    record, field = make_record(facility, lead_user, "event")
    QuickTemplate.objects.create(
        facility=facility,
        document_type=record.document_type,
        name="Stored preset",
        prefilled_data=record.data_json,
    )
    record.data_json = {}
    record.save()
    field.field_type = "file"
    with pytest.raises(ValidationError):
        field.save()


@pytest.mark.parametrize("encrypted", [False, True])
def test_multiline_textarea_cannot_be_narrowed_to_text(facility, lead_user, encrypted):
    field = FieldTemplate.objects.create(
        facility=facility,
        name="Multiline note",
        field_type="textarea",
        is_encrypted=encrypted,
    )
    record, _ = make_record(facility, lead_user, "event", field=field, value="First line\nSecond line")
    before = record.data_json.copy()
    field.field_type = "text"
    with pytest.raises(ValidationError):
        field.save()
    field.refresh_from_db()
    assert field.field_type == "textarea"
    record.save()
    record.refresh_from_db()
    assert record.data_json == before
    assert safe_decrypt(record.data_json[field.slug]) == "First line\nSecond line"
