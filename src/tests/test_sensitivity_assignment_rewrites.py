"""Protection of retained values when a template is assigned again (Refs #2525)."""

import pytest
from django.core.management import call_command

from core.models import DocumentTypeField, Event, EventHistory, FieldTemplate
from core.services.config import add_field_to_doctype, remove_field_from_doctype, save_field_template
from core.services.file_vault import is_encrypted_value, safe_decrypt
from tests.test_sensitivity_transitions import field_form, make_record, request_for

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("entity", ["event", "client", "case"])
def test_reassigned_high_template_protects_retained_values(facility, lead_user, rf, entity):
    record, field = make_record(facility, lead_user, entity, value="ReassignedSecret")
    request = request_for(rf, facility, lead_user)
    target = record.document_type
    assignment = DocumentTypeField.objects.get(document_type=target, field_template=field)
    remove_field_from_doctype(request, assignment)
    save_field_template(request, field_form(field, facility), creating=False, facility=facility)
    add_field_to_doctype(request, target, field)
    record.refresh_from_db()
    assert is_encrypted_value(record.data_json[field.slug])
    assert safe_decrypt(record.data_json[field.slug]) == "ReassignedSecret"
    assert getattr(record, "search_text", "") == ""


def test_encrypted_assignment_rewrites_deleted_records_but_not_another_facility(facility, lead_user, foreign_event, rf):
    record, field = make_record(facility, lead_user, "event")
    request = request_for(rf, facility, lead_user)
    remove_field_from_doctype(request, record.document_type.fields.get(field_template=field))
    Event.objects.for_facility(facility).filter(pk=record.pk).update(is_deleted=True)
    encrypted = FieldTemplate.objects.create(
        facility=facility, name="Protected replacement", slug=field.slug + "-new", is_encrypted=True
    )
    # Retained values may predate their first assignment, including imported values.
    Event.objects.for_facility(facility).filter(pk=record.pk).update(
        data_json={encrypted.slug: "RetainedSecret"}, search_text="RetainedSecret"
    )
    before = (foreign_event.data_json.copy(), foreign_event.search_text)
    add_field_to_doctype(request, record.document_type, encrypted)
    record.refresh_from_db()
    foreign_event.refresh_from_db()
    assert is_encrypted_value(record.data_json[encrypted.slug])
    assert safe_decrypt(record.data_json[encrypted.slug]) == "RetainedSecret"
    assert record.search_text == ""
    assert (foreign_event.data_json, foreign_event.search_text) == before


def test_failed_assignment_rewrite_rolls_back_assignment_values_and_audit(facility, lead_user, rf, monkeypatch):
    from core.models import AuditLog

    record, field = make_record(facility, lead_user, "event")
    request = request_for(rf, facility, lead_user)
    remove_field_from_doctype(request, record.document_type.fields.get(field_template=field))
    save_field_template(request, field_form(field, facility), creating=False, facility=facility)
    audit_count = AuditLog.objects.for_facility(facility).count()

    def fail_save(*args, **kwargs):
        raise RuntimeError("Injected assignment rewrite failure")

    monkeypatch.setattr(Event, "save", fail_save)
    with pytest.raises(RuntimeError, match="Injected assignment rewrite failure"):
        add_field_to_doctype(request, record.document_type, field)
    assert not record.document_type.fields.filter(field_template=field).exists()
    record.refresh_from_db()
    assert record.data_json[field.slug] == "SecretToken"
    assert AuditLog.objects.for_facility(facility).count() == audit_count


def test_reassigned_history_remains_rotatable_with_current_protection(facility, lead_user, rf):
    from io import StringIO

    record, field = make_record(facility, lead_user, "event")
    history = EventHistory.objects.create(
        event=record, action="update", data_before={field.slug: False}, data_after={field.slug: "HistoricalSecret"}
    )
    request = request_for(rf, facility, lead_user)
    remove_field_from_doctype(request, record.document_type.fields.get(field_template=field))
    save_field_template(request, field_form(field, facility), creating=False, facility=facility)
    add_field_to_doctype(request, record.document_type, field)
    call_command("reencrypt_fields", stdout=StringIO())
    history.refresh_from_db()
    assert is_encrypted_value(history.data_before[field.slug])
    assert safe_decrypt(history.data_before[field.slug]) is False
    assert is_encrypted_value(history.data_after[field.slug])
    assert safe_decrypt(history.data_after[field.slug]) == "HistoricalSecret"
