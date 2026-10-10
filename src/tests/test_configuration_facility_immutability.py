"""Existing configuration stays in the facility that owns its dynamic records."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event as ThreadEvent

import pytest
from django.core.exceptions import ValidationError
from django.db import connections, transaction
from django.test import RequestFactory
from django.utils import timezone

from core.admin.documents import DocumentTypeAdmin, FieldTemplateAdmin
from core.admin_site import anlaufstelle_admin_site
from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate
from core.services.events import create_event
from core.services.file_vault import is_encrypted_value, safe_decrypt
from tests.test_admin_config_audit import _request
from tests.test_sensitivity_transitions import make_record
from tests.test_sensitivity_write_locking import run_connection, wait_for_completion_or_lock

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("model", [DocumentType, FieldTemplate])
@pytest.mark.parametrize("operation", ["save", "clean"])
def test_existing_configuration_refuses_facility_changes(facility, other_facility, model, operation):
    obj = model.objects.create(facility=facility, name="Fixed ownership", sensitivity=DocumentType.Sensitivity.NORMAL)
    obj.facility = other_facility
    obj.sensitivity = DocumentType.Sensitivity.HIGH
    if model is FieldTemplate:
        obj.is_encrypted = True
    with pytest.raises(ValidationError, match="Einrichtung.*nicht.*geändert"):
        getattr(obj, operation)()
    obj.refresh_from_db()
    assert obj.facility_id == facility.pk
    assert obj.sensitivity == DocumentType.Sensitivity.NORMAL


@pytest.mark.parametrize("operation", ["save", "clean"])
def test_assignment_refuses_moves_to_another_facility(facility, other_facility, operation):
    original_type = DocumentType.objects.create(facility=facility, name="Original")
    original_field = FieldTemplate.objects.create(facility=facility, name="Original field")
    assignment = DocumentTypeField.objects.create(document_type=original_type, field_template=original_field)
    assignment.document_type = DocumentType.objects.create(facility=other_facility, name="Destination")
    assignment.field_template = FieldTemplate.objects.create(facility=other_facility, name="Destination field")
    with pytest.raises(ValidationError, match="Einrichtung.*nicht.*geändert"):
        getattr(assignment, operation)()
    assignment.refresh_from_db()
    assert assignment.document_type_id == original_type.pk
    assert assignment.field_template_id == original_field.pk


def test_assignment_can_change_type_within_its_facility(facility):
    original_type = DocumentType.objects.create(facility=facility, name="Original")
    destination = DocumentType.objects.create(facility=facility, name="Destination")
    field = FieldTemplate.objects.create(facility=facility, name="Shared field")
    assignment = DocumentTypeField.objects.create(document_type=original_type, field_template=field)
    assignment.document_type = destination
    assignment.clean()
    assignment.save()
    assignment.refresh_from_db()
    assert assignment.document_type_id == destination.pk


@pytest.mark.parametrize("model,admin_class", [(DocumentType, DocumentTypeAdmin), (FieldTemplate, FieldTemplateAdmin)])
def test_existing_admin_facility_is_readonly(facility, super_admin_user, model, admin_class):
    obj = model.objects.create(facility=facility, name="Admin ownership")
    request = _request(RequestFactory(), super_admin_user)
    request.current_facility = facility
    admin = admin_class(model, anlaufstelle_admin_site)
    assert "facility" in admin.get_readonly_fields(request, obj)
    assert "facility" not in admin.get_form(request, obj).base_fields
    assert "facility" in admin.get_form(request).base_fields


def test_admin_field_change_waits_for_source_event_and_rewrites_values(
    facility, other_facility, lead_user, super_admin_user, _encryption_key
):
    existing, field = make_record(facility, lead_user, "event")
    started, pids = ThreadEvent(), {}

    def change_field():
        pids["changer"] = connections["default"].connection.info.backend_pid
        request = _request(RequestFactory(), super_admin_user)
        request.current_facility = facility
        admin = FieldTemplateAdmin(FieldTemplate, anlaufstelle_admin_site)
        form_class = admin.get_form(request, obj=field)
        form = form_class(
            data={
                "facility": str(other_facility.pk),
                "name": field.name,
                "field_type": "text",
                "sensitivity": "high",
                "is_encrypted": "on",
                "is_active": "on",
            },
            instance=field,
        )
        assert form.is_valid(), form.errors
        obj = form.save(commit=False)
        started.set()
        with transaction.atomic():
            admin.save_model(request, obj, form, change=True)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            event = create_event(
                facility=facility,
                user=lead_user,
                document_type=existing.document_type,
                occurred_at=timezone.now(),
                data_json={field.slug: "Concurrent value"},
                is_anonymous=True,
            )
            changer = pool.submit(run_connection, change_field)
            assert started.wait(5), "Admin did not validate the field change"
            blocked = wait_for_completion_or_lock(changer, pids["changer"])
            during_write = FieldTemplate.objects.get(pk=field.pk)
        changer.result(timeout=12)
    assert blocked, "Admin committed during the source event transaction"
    assert during_write.facility_id == facility.pk
    assert during_write.sensitivity == DocumentType.Sensitivity.NORMAL
    field.refresh_from_db()
    assert field.facility_id == facility.pk
    assert field.sensitivity == DocumentType.Sensitivity.HIGH
    event = Event.objects.get(pk=event.pk)
    assert is_encrypted_value(event.data_json[field.slug])
    assert safe_decrypt(event.data_json[field.slug]) == "Concurrent value"
    assert "Concurrent value" not in event.search_text
