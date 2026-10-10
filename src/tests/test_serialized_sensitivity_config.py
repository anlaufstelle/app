"""Public configuration guards after another request changes protection (Refs #2525)."""

import pytest
from django.core.exceptions import ValidationError

from core.forms.config import DocumentTypeFieldAddForm, DocumentTypeForm, FieldTemplateForm
from core.models import DocumentType, DocumentTypeField, FieldTemplate
from core.services.config import add_field_to_doctype, save_document_type, save_field_template
from tests.test_sensitivity_transitions import request_for

pytestmark = pytest.mark.django_db


def type_form(target, facility, level):
    form = DocumentTypeForm(
        {"name": target.name, "sensitivity": level, "is_active": "on"},
        instance=target,
        facility=facility,
    )
    assert form.is_valid(), form.errors
    return form


@pytest.mark.parametrize("path", ["service", "form"])
def test_stale_assignment_target_rejects_inheritance(facility, lead_user, rf, path):
    target = DocumentType.objects.create(facility=facility, name="Assignment target", sensitivity="normal")
    field = FieldTemplate.objects.create(facility=facility, name="Inherited secret")
    current = DocumentType.objects.for_facility(facility).get(pk=target.pk)
    save_document_type(request_for(rf, facility, lead_user), type_form(current, facility, "high"), creating=False)
    if path == "form":
        form = DocumentTypeFieldAddForm({"field_template": field.pk}, doc_type=target)
        assert not form.is_valid()
        assert field.name in str(form.errors)
    else:
        with pytest.raises(ValidationError, match="erben"):
            add_field_to_doctype(request_for(rf, facility, lead_user), target, field)
    assert not DocumentTypeField.objects.filter(document_type=target).exists()


def test_prevalidated_type_change_rechecks_new_assignment(facility, lead_user, rf):
    target = DocumentType.objects.create(facility=facility, name="Type target", sensitivity="normal")
    field = FieldTemplate.objects.create(facility=facility, name="Inherited secret")
    form = type_form(target, facility, "high")
    request = request_for(rf, facility, lead_user)
    current = DocumentType.objects.for_facility(facility).get(pk=target.pk)
    add_field_to_doctype(request, current, field)
    with pytest.raises(ValidationError, match="erben"):
        save_document_type(request, form, creating=False)
    target.refresh_from_db()
    assert target.sensitivity == "normal"
    assert target.fields.filter(field_template=field).exists()


def test_prevalidated_field_change_rechecks_current_type(facility, lead_user, rf):
    target = DocumentType.objects.create(facility=facility, name="Field target", sensitivity="normal")
    field = FieldTemplate.objects.create(facility=facility, name="Explicit secret", sensitivity="normal")
    DocumentTypeField.objects.create(document_type=target, field_template=field)
    form = FieldTemplateForm(
        {"name": field.name, "field_type": field.field_type, "sensitivity": "", "is_active": "on"},
        instance=field,
        facility=facility,
    )
    assert form.is_valid(), form.errors
    request = request_for(rf, facility, lead_user)
    save_document_type(request, type_form(target, facility, "high"), creating=False)
    with pytest.raises(ValidationError, match="erben"):
        save_field_template(request, form, creating=False, facility=facility)
    field.refresh_from_db()
    assert field.sensitivity == "normal"


def test_stale_assignment_template_rechecks_current_field_level(facility, lead_user, rf):
    target = DocumentType.objects.create(facility=facility, name="High target", sensitivity="high")
    field = FieldTemplate.objects.create(facility=facility, name="Explicit secret", sensitivity="normal")
    current = FieldTemplate.objects.for_facility(facility).get(pk=field.pk)
    form = FieldTemplateForm(
        {"name": current.name, "field_type": current.field_type, "sensitivity": "", "is_active": "on"},
        instance=current,
        facility=facility,
    )
    assert form.is_valid(), form.errors
    request = request_for(rf, facility, lead_user)
    save_field_template(request, form, creating=False, facility=facility)
    with pytest.raises(ValidationError, match="erben"):
        add_field_to_doctype(request, target, field)
    assert not target.fields.exists()


@pytest.mark.parametrize("change", ["type", "field"])
def test_update_view_shows_guard_failure_after_form_validation(facility, admin_user, rf, client, monkeypatch, change):
    from django.urls import reverse

    target = DocumentType.objects.create(facility=facility, name="Concurrent target", sensitivity="normal")
    field = FieldTemplate.objects.create(facility=facility, name="Concurrent field", sensitivity="normal")
    request = request_for(rf, facility, admin_user)
    if change == "type":
        form_class = DocumentTypeForm
        data = {"name": target.name, "sensitivity": "high", "is_active": "on"}
        url = reverse("core:config_doctype_update", kwargs={"pk": target.pk})
    else:
        DocumentTypeField.objects.create(document_type=target, field_template=field)
        form_class = FieldTemplateForm
        data = {"name": field.name, "field_type": field.field_type, "sensitivity": "", "is_active": "on"}
        url = reverse("core:config_fieldtemplate_update", kwargs={"pk": field.pk})
    original_validate = form_class.is_valid

    def validate_then_change_configuration(form):
        valid = original_validate(form)
        if valid:
            if change == "type":
                inherited = FieldTemplate.objects.create(facility=facility, name="Concurrent inherited field")
                add_field_to_doctype(request, target, inherited)
            else:
                save_document_type(request, type_form(target, facility, "high"), creating=False)
        return valid

    monkeypatch.setattr(form_class, "is_valid", validate_then_change_configuration)
    client.force_login(admin_user)
    response = client.post(url, data)
    assert response.status_code == 200
    assert "erben" in response.content.decode()
    target.refresh_from_db()
    field.refresh_from_db()
    assert target.sensitivity == ("normal" if change == "type" else "high")
    assert field.sensitivity == "normal"
