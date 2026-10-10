"""Public configuration and rotation contracts for sensitivity changes (Refs #2525)."""

from io import StringIO
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.forms.config import DocumentTypeForm, FieldTemplateForm
from core.models import Case, Client, DocumentType, DocumentTypeField, Event, EventHistory, FieldTemplate
from core.services.config import save_document_type, save_field_template
from core.services.file_vault import encrypt_dynamic_data, is_encrypted_value, safe_decrypt

pytestmark = pytest.mark.django_db


def make_record(facility, user, entity, *, field=None, value="SecretToken"):
    doc_type = DocumentType.objects.create(
        facility=facility, name=f"Transition {uuid4()}", entity_type=entity, sensitivity="normal"
    )
    if field is None:
        field = FieldTemplate.objects.create(facility=facility, name=f"Secret {uuid4()}", sensitivity="normal")
    DocumentTypeField.objects.create(document_type=doc_type, field_template=field)
    kwargs = {"facility": facility, "created_by": user, "document_type": doc_type, "data_json": {field.slug: value}}
    if entity == "event":
        record = Event.objects.create(**kwargs, occurred_at=timezone.now())
    elif entity == "client":
        record = Client.objects.create(**kwargs, pseudonym=f"P-{uuid4()}")
    else:
        owner = Client.objects.create(facility=facility, created_by=user, pseudonym=f"P-{uuid4()}")
        record = Case.objects.create(**kwargs, client=owner, title="Transition case")
    return record, field


def request_for(rf, facility, user):
    request = rf.post("/config/")
    request.user = user
    request.current_facility = facility
    return request


def field_form(field, facility, *, level="high", encrypted=False):
    data = {"name": field.name, "field_type": field.field_type, "sensitivity": level, "is_active": "on"}
    if encrypted or level == "high":
        data["is_encrypted"] = "on"
    form = FieldTemplateForm(data, instance=field, facility=facility)
    assert form.is_valid(), form.errors
    return form


@pytest.mark.parametrize("change", ["high", "encrypted", "elevated"])
def test_field_change_rewrites_all_assigned_entities_in_its_facility(facility, lead_user, foreign_event, rf, change):
    event, field = make_record(facility, lead_user, "event")
    person, _ = make_record(facility, lead_user, "client", field=field)
    case, _ = make_record(facility, lead_user, "case", field=field)
    other_event, _ = make_record(facility, lead_user, "event", field=field)
    Event.objects.filter(pk=other_event.pk).update(is_deleted=True)
    foreign_source = foreign_event.data_json.copy()
    foreign_index = foreign_event.search_text
    form = field_form(
        field, facility, level="normal" if change == "encrypted" else change, encrypted=change == "encrypted"
    )
    save_field_template(request_for(rf, facility, lead_user), form, creating=False, facility=facility)
    for record in (event, person, case, other_event):
        record.refresh_from_db()
        stored = record.data_json[field.slug]
        assert is_encrypted_value(stored) is (change != "elevated")
        assert safe_decrypt(stored) == "SecretToken"
        if isinstance(record, Event):
            assert record.search_text == ""
    foreign_event.refresh_from_db()
    assert foreign_event.data_json == foreign_source
    assert foreign_event.search_text == foreign_index


@pytest.mark.parametrize("entity", ["event", "client", "case"])
@pytest.mark.parametrize("level", ["elevated", "high"])
def test_type_change_rewrites_values_and_index(facility, lead_user, rf, entity, level):
    record, field = make_record(facility, lead_user, entity)
    # An encrypted template may still have a pending plaintext value before this mutation.
    FieldTemplate.objects.filter(pk=field.pk).update(is_encrypted=True)
    doc_type = record.document_type
    form = DocumentTypeForm(
        {"name": doc_type.name, "sensitivity": level, "is_active": "on"}, instance=doc_type, facility=facility
    )
    assert form.is_valid(), form.errors
    save_document_type(request_for(rf, facility, lead_user), form, creating=False)
    record.refresh_from_db()
    assert is_encrypted_value(record.data_json[field.slug])
    assert safe_decrypt(record.data_json[field.slug]) == "SecretToken"
    if entity == "event":
        assert record.search_text == ""


@pytest.mark.parametrize("change", ["field", "type"])
def test_configuration_change_rolls_back_after_a_mid_run_failure(facility, lead_user, rf, monkeypatch, change):
    first, field = make_record(facility, lead_user, "event")
    second = Event.objects.create(
        facility=facility,
        created_by=lead_user,
        document_type=first.document_type,
        occurred_at=timezone.now(),
        data_json={field.slug: "SecondToken"},
    )
    original_save = Event.save
    writes = []

    def fail_on_second_save(self, *args, **kwargs):
        writes.append(self.pk)
        if len(writes) == 2:
            raise RuntimeError("Injected rewrite failure")
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(Event, "save", fail_on_second_save)
    request = request_for(rf, facility, lead_user)
    with pytest.raises(RuntimeError, match="Injected rewrite failure"):
        if change == "field":
            save_field_template(request, field_form(field, facility), creating=False, facility=facility)
        else:
            form = DocumentTypeForm(
                {"name": first.document_type.name, "sensitivity": "elevated", "is_active": "on"},
                instance=first.document_type,
                facility=facility,
            )
            assert form.is_valid(), form.errors
            save_document_type(request, form, creating=False)
    field.refresh_from_db()
    first.document_type.refresh_from_db()
    assert field.sensitivity == "normal"
    assert field.is_encrypted is False
    assert first.document_type.sensitivity == "normal"
    for record, value in ((first, "SecretToken"), (second, "SecondToken")):
        record.refresh_from_db()
        assert record.data_json == {field.slug: value}
        assert record.search_text == value


@pytest.mark.parametrize("value,field_type", [(False, "boolean"), (0, "number")])
@pytest.mark.parametrize("entity", ["event", "client", "case", "bulk"])
def test_encrypted_fields_store_false_and_zero_as_ciphertext(facility, lead_user, value, field_type, entity):
    field = FieldTemplate.objects.create(
        facility=facility, name=f"Encrypted {uuid4()}", field_type=field_type, sensitivity="high"
    )
    record, _ = make_record(facility, lead_user, "event" if entity == "bulk" else entity, field=field, value=value)
    if entity == "bulk":
        stored = encrypt_dynamic_data(record.document_type, {field.slug: value})[field.slug]
    else:
        record.refresh_from_db()
        stored = record.data_json[field.slug]
    assert is_encrypted_value(stored)
    assert safe_decrypt(stored) == value
    assert type(safe_decrypt(stored)) is type(value)


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("value", ["HistoryToken", False, 0])
def test_rotation_encrypts_plaintext_history_idempotently(facility, lead_user, dry_run, value):
    event, field = make_record(facility, lead_user, "event", value=value)
    history = EventHistory.objects.create(
        event=event,
        changed_by=lead_user,
        action="update",
        data_before={field.slug: value, "public": "PublicToken"},
        data_after={field.slug: value},
    )
    FieldTemplate.objects.filter(pk=field.pk).update(is_encrypted=True)
    call_command("reencrypt_fields", dry_run=dry_run, stdout=StringIO())
    history.refresh_from_db()
    for payload in (history.data_before, history.data_after):
        assert is_encrypted_value(payload[field.slug]) is (not dry_run)
        assert safe_decrypt(payload[field.slug]) == value
    assert history.data_before["public"] == "PublicToken"
    call_command("reencrypt_fields", dry_run=dry_run, stdout=StringIO())
    history.refresh_from_db()
    assert is_encrypted_value(history.data_after[field.slug]) is (not dry_run)
    assert safe_decrypt(history.data_after[field.slug]) == value


@pytest.mark.parametrize("level", ["elevated", "high"])
def test_type_change_keeps_explicit_reporting_plaintext_out_of_the_index(facility, lead_user, rf, level):
    event, field = make_record(facility, lead_user, "event")
    doc_type = event.document_type
    for target, expected_index in ((level, ""), ("normal", "SecretToken")):
        form = DocumentTypeForm(
            {"name": doc_type.name, "sensitivity": target, "is_active": "on"}, instance=doc_type, facility=facility
        )
        assert form.is_valid(), form.errors
        save_document_type(request_for(rf, facility, lead_user), form, creating=False)
        event.refresh_from_db()
        assert event.data_json[field.slug] == "SecretToken"
        assert event.search_text == expected_index


@pytest.mark.parametrize("source", ["removed-assignment", "snapshot"])
def test_rotation_protects_historical_fields_without_a_current_assignment(facility, lead_user, foreign_event, source):
    event, field = make_record(facility, lead_user, "event")
    history = EventHistory.objects.create(
        event=event,
        action="update",
        data_after={field.slug: "HistoryToken"},
        field_metadata={field.slug: {"is_encrypted": source == "snapshot"}},
    )
    foreign_history = EventHistory.objects.create(
        event=foreign_event,
        action="update",
        data_after={field.slug: "ForeignToken"},
    )
    FieldTemplate.objects.filter(pk=field.pk).update(is_encrypted=source == "removed-assignment")
    DocumentTypeField.objects.filter(document_type=event.document_type, field_template=field).delete()
    call_command("reencrypt_fields", stdout=StringIO())
    history.refresh_from_db()
    foreign_history.refresh_from_db()
    assert is_encrypted_value(history.data_after[field.slug])
    assert safe_decrypt(history.data_after[field.slug]) == "HistoryToken"
    assert foreign_history.data_after[field.slug] == "ForeignToken"


@pytest.mark.parametrize("marker", [{"_redacted": True, "fields": ["note"]}, {"_redacted": True, "anonymized": True}])
def test_rotation_preserves_redaction_metadata(facility, lead_user, marker):
    event, _ = make_record(facility, lead_user, "event")
    for slug in ("fields", "anonymized"):
        FieldTemplate.objects.create(facility=facility, name=slug, slug=slug, sensitivity="high")
    history = EventHistory.objects.create(event=event, action="delete", data_before=marker)
    call_command("reencrypt_fields", stdout=StringIO())
    history.refresh_from_db()
    assert history.data_before == marker


@pytest.mark.parametrize("value", [False, True, 0, 7])
def test_encrypted_boolean_and_integer_roundtrip_preserves_type(value):
    from core.services.file_vault import decrypt_field, encrypt_field

    stored = encrypt_field(value)
    assert is_encrypted_value(stored)
    restored = decrypt_field(stored)
    assert restored == value
    assert type(restored) is type(value)


def test_encrypted_false_stays_no_in_display_and_unchecked_in_edit_form(facility, lead_user):
    from core.forms.dynamic import DynamicDataForm
    from core.services.dynamic_data import format_dynamic_display_value
    from core.services.events import decrypt_event_text_data

    field = FieldTemplate.objects.create(
        facility=facility, name=f"Boolean {uuid4()}", field_type="boolean", sensitivity="high"
    )
    event, _ = make_record(facility, lead_user, "event", field=field, value=False)
    event.refresh_from_db()
    initial = decrypt_event_text_data(event)
    assert format_dynamic_display_value(initial[field.slug], field) == "Nein"
    form = DynamicDataForm(document_type=event.document_type, initial_data=initial)
    assert "checked" not in str(form[field.slug])


def test_public_update_uses_changed_encryption_with_an_existing_event_instance(facility, lead_user, rf):
    from core.services.events import update_event

    event, field = make_record(facility, lead_user, "event")
    save_field_template(
        request_for(rf, facility, lead_user), field_form(field, facility), creating=False, facility=facility
    )
    update_event(event=event, user=lead_user, data_json={field.slug: "NextSecret"})
    event.refresh_from_db()
    assert is_encrypted_value(event.data_json[field.slug])
    assert safe_decrypt(event.data_json[field.slug]) == "NextSecret"
    assert event.search_text == ""
