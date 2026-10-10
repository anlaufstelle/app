"""Regression contracts for writes with stale type instances (Refs #2525)."""

import pytest
from django.utils import timezone

from core.forms.config import DocumentTypeForm
from core.models import DocumentType, Event
from core.services.config import save_document_type, save_field_template
from core.services.events import update_event
from core.services.events.fields import compute_event_search_text
from core.services.file_vault import encrypt_dynamic_data, is_encrypted_value, safe_decrypt
from tests.test_sensitivity_transitions import field_form, make_record, request_for

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("level", ["elevated", "high"])
@pytest.mark.parametrize("path", ["service", "save", "bulk"])
def test_stale_type_cannot_restore_plaintext_index(facility, lead_user, rf, level, path):
    event, field = make_record(facility, lead_user, "event")
    stale_type = event.document_type
    config_type = DocumentType.objects.for_facility(facility).get(pk=stale_type.pk)
    form = DocumentTypeForm(
        {"name": config_type.name, "sensitivity": level, "is_active": "on"},
        instance=config_type,
        facility=facility,
    )
    assert form.is_valid(), form.errors
    save_document_type(request_for(rf, facility, lead_user), form, creating=False)
    assert Event.objects.get(pk=event.pk).search_text == ""
    data = {field.slug: "AfterTransitionSecret"}
    if path == "service":
        update_event(
            event=event,
            user=lead_user,
            data_json=data,
            expected_updated_at=event.updated_at.isoformat(),
            require_version_token=True,
        )
    elif path == "save":
        event.data_json = data
        event.save(update_fields=["data_json", "search_text"])
    else:
        event = Event(
            facility=facility,
            created_by=lead_user,
            document_type=stale_type,
            occurred_at=timezone.now(),
            data_json=encrypt_dynamic_data(stale_type, data),
            search_text=compute_event_search_text(data, stale_type),
        )
        Event.objects.bulk_create([event])
    event.refresh_from_db()
    assert event.document_type.sensitivity == level
    assert event.search_text == ""
    # Explicit Normal reporting fields remain plaintext, even in High types.
    assert event.data_json[field.slug] == "AfterTransitionSecret"


@pytest.mark.parametrize("path", ["save", "bulk"])
def test_stale_type_uses_current_template_encryption(facility, lead_user, rf, path):
    event, field = make_record(facility, lead_user, "event")
    stale_type = event.document_type
    list(stale_type.fields.select_related("field_template"))
    save_field_template(
        request_for(rf, facility, lead_user),
        field_form(field, facility),
        creating=False,
        facility=facility,
    )
    data = {field.slug: "AfterEncryptionSecret"}
    if path == "save":
        event.data_json = data
        event.save(update_fields=["data_json", "search_text"])
    else:
        event = Event(
            facility=facility,
            created_by=lead_user,
            document_type=stale_type,
            occurred_at=timezone.now(),
            data_json=encrypt_dynamic_data(stale_type, data),
            search_text=compute_event_search_text(data, stale_type),
        )
        Event.objects.bulk_create([event])
    event.refresh_from_db()
    assert is_encrypted_value(event.data_json[field.slug])
    assert safe_decrypt(event.data_json[field.slug]) == "AfterEncryptionSecret"
    assert event.search_text == ""


@pytest.mark.parametrize("state", ["unknown", "missing"])
def test_index_fails_closed_when_current_type_is_unknown_or_missing(facility, lead_user, state):
    event, field = make_record(facility, lead_user, "event")
    stale_type = event.document_type
    if state == "unknown":
        DocumentType.objects.for_facility(facility).filter(pk=stale_type.pk).update(sensitivity="unexpected")
    else:
        stale_type.pk = None
    assert compute_event_search_text({field.slug: "Secret"}, stale_type) == ""
