"""Model and admin protection contracts for explicit High fields.

Refs #2525
"""

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.admin.documents import DocumentTypeAdmin
from core.admin_site import anlaufstelle_admin_site
from core.models import DocumentType, DocumentTypeField, FieldTemplate
from tests.test_admin_config_audit import _request
from tests.test_sensitivity_transitions import make_record

pytestmark = pytest.mark.django_db


def configuration(facility, *, level="normal", field_level=""):
    doc_type = DocumentType.objects.create(facility=facility, name="Protected type", sensitivity=level)
    field = FieldTemplate.objects.create(facility=facility, name="Protected note", sensitivity=field_level)
    return doc_type, field


@pytest.mark.parametrize("change", ["assignment", "type", "field"])
def test_model_save_rejects_inheritance_in_high_types(facility, change):
    doc_type, field = configuration(
        facility, level="normal" if change == "type" else "high", field_level="elevated" if change == "field" else ""
    )
    if change != "assignment":
        DocumentTypeField.objects.create(document_type=doc_type, field_template=field)
    with pytest.raises(ValidationError, match="Protected note.*erben"):
        if change == "assignment":
            DocumentTypeField(document_type=doc_type, field_template=field).save()
        elif change == "type":
            doc_type.sensitivity = "high"
            doc_type.save()
        else:
            field.sensitivity = ""
            field.save()
    doc_type.refresh_from_db()
    field.refresh_from_db()
    assert doc_type.sensitivity == ("normal" if change == "type" else "high")
    assert field.sensitivity == ("elevated" if change == "field" else "")
    assert doc_type.fields.count() == (0 if change == "assignment" else 1)


@pytest.mark.parametrize("stale", ["type", "field"])
def test_assignment_uses_current_protection_instead_of_cached_relations(facility, stale):
    doc_type, field = configuration(facility, field_level="elevated")
    assignment = DocumentTypeField(document_type=doc_type, field_template=field)
    if stale == "type":
        current = DocumentType.objects.get(pk=doc_type.pk)
        current.sensitivity = "high"
        current.save()
        field.sensitivity = ""
        field.save()
    else:
        current = FieldTemplate.objects.get(pk=field.pk)
        current.sensitivity = ""
        current.save()
        doc_type.sensitivity = "high"
        doc_type.save()
    with pytest.raises(ValidationError, match="Protected note.*erben"):
        assignment.save()
    assert not doc_type.fields.exists()


def test_stale_template_cannot_inherit_after_assigned_type_is_raised(facility):
    doc_type, field = configuration(facility, field_level="elevated")
    DocumentTypeField.objects.create(document_type=doc_type, field_template=field)
    current = DocumentType.objects.get(pk=doc_type.pk)
    current.sensitivity = "high"
    current.save()
    field.sensitivity = ""
    with pytest.raises(ValidationError, match="Protected note.*erben"):
        field.save(update_fields=["sensitivity"])
    field.refresh_from_db()
    assert field.sensitivity == "elevated"


def admin_payload(page):
    data = {}
    for name, field in page.context_data["adminform"].form.fields.items():
        value = page.context_data["adminform"].form[name].value()
        if isinstance(value, bool):
            data.pop(name, None)
            if value:
                data[name] = "on"
        elif value is not None:
            data[name] = value
    for inline in page.context_data["inline_admin_formsets"]:
        formset = inline.formset
        for name in ("TOTAL_FORMS", "INITIAL_FORMS", "MIN_NUM_FORMS", "MAX_NUM_FORMS"):
            data[f"{formset.prefix}-{name}"] = formset.management_form[name].value()
        for index, form in enumerate(formset.forms):
            for name in form.fields:
                value = form[name].value()
                if value is not None and value is not False:
                    data[f"{formset.prefix}-{index}-{name}"] = "on" if value is True else value
    data["_save"] = "Save"
    return data


@pytest.mark.parametrize("change", ["assignment", "type", "field", "type_and_assignment"])
def test_admin_reports_high_inheritance_as_form_error(client, admin_user, facility, change):
    doc_type, field = configuration(
        facility,
        level="high" if change in {"assignment", "field"} else "normal",
        field_level="elevated" if change == "field" else "",
    )
    if change in {"type", "field"}:
        DocumentTypeField.objects.create(document_type=doc_type, field_template=field)
    model = "fieldtemplate" if change == "field" else "documenttype"
    obj = field if change == "field" else doc_type
    admin_user.user_permissions.add(*Permission.objects.filter(content_type__model="documenttypefield"))
    client.force_login(admin_user)
    url = reverse(f"admin:core_{model}_change", args=[obj.pk])
    page = client.get(url)
    assert page.status_code == 200
    data = admin_payload(page)
    if change in {"type", "type_and_assignment"}:
        data["sensitivity"] = "high"
    elif change == "field":
        data["sensitivity"] = ""
    if change in {"assignment", "type_and_assignment"}:
        prefix = page.context_data["inline_admin_formsets"][0].formset.prefix
        data[f"{prefix}-0-field_template"] = str(field.pk)
        data[f"{prefix}-0-sort_order"] = "0"
    response = client.post(url, data)
    assert response.status_code == 200
    assert "Protected note" in response.content.decode()
    assert "erben" in response.content.decode()
    doc_type.refresh_from_db()
    field.refresh_from_db()
    assert doc_type.sensitivity == ("high" if change in {"assignment", "field"} else "normal")
    assert field.sensitivity == ("elevated" if change == "field" else "")
    assert doc_type.fields.count() == (1 if change in {"type", "field"} else 0)


def test_admin_save_model_rechecks_assignments_after_form_validation(facility, admin_user, rf):
    doc_type, field = configuration(facility)
    admin = DocumentTypeAdmin(DocumentType, anlaufstelle_admin_site)
    request = _request(rf, admin_user)
    form = admin.get_form(request, doc_type)(
        data={"name": doc_type.name, "sensitivity": "high", "sort_order": 0}, instance=doc_type
    )
    assert form.is_valid(), form.errors
    current = DocumentType.objects.get(pk=doc_type.pk)
    DocumentTypeField.objects.create(document_type=current, field_template=field)
    with pytest.raises(ValidationError, match="Protected note.*erben"):
        admin.save_model(request, form.instance, form, change=True)
    current.refresh_from_db()
    assert current.sensitivity == "normal"


@pytest.mark.parametrize("path", ["save", "bulk_create", "bulk_update"])
@pytest.mark.parametrize("entity", ["event", "client", "case"])
def test_dynamic_write_rejects_inherited_high_values_from_invalid_configuration(facility, lead_user, entity, path):
    record, field = make_record(facility, lead_user, entity)
    FieldTemplate.objects.filter(pk=field.pk).update(sensitivity="")
    DocumentType.objects.filter(pk=record.document_type_id).update(sensitivity="high")
    record.data_json = {field.slug: "InheritedHighSecret"}
    fields = ["data_json", "search_text"] if entity == "event" else ["data_json"]
    with pytest.raises(ValidationError, match="erben"):
        if path == "save":
            record.save(update_fields=fields)
        elif path == "bulk_update":
            type(record).objects.bulk_update([record], fields)
        else:
            from copy import copy
            from uuid import uuid4

            new_record = copy(record)
            new_record.pk = uuid4()
            new_record._state.adding = True
            type(record).objects.bulk_create([new_record])
    record.refresh_from_db()
    assert record.data_json == {field.slug: "SecretToken"}
    assert "InheritedHighSecret" not in getattr(record, "search_text", "")


@pytest.mark.django_db(transaction=True)
def test_admin_waits_for_configuration_before_validating_inheritance(client, admin_user, facility):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event as ThreadEvent

    from django.db import connections

    from core.services.dynamic_protection import configuration_write
    from tests.test_sensitivity_write_locking import run_connection, wait_for_completion_or_lock

    doc_type, field = configuration(facility, field_level="normal")
    DocumentTypeField.objects.create(document_type=doc_type, field_template=field)
    client.force_login(admin_user)
    url = reverse("admin:core_fieldtemplate_change", args=[field.pk])
    data = admin_payload(client.get(url))
    data["sensitivity"] = ""
    ready = ThreadEvent()
    pids = {}

    def post():
        def capture_connection(execute, sql, params, many, context):
            pids["admin"] = connections["default"].connection.info.backend_pid
            ready.set()
            return execute(sql, params, many, context)

        with connections["default"].execute_wrapper(capture_connection):
            return client.post(url, data)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with configuration_write(facility.pk):
            doc_type.sensitivity = "high"
            doc_type.save()
            pending = pool.submit(run_connection, post)
            assert ready.wait(5), "Admin request did not start"
            wait_for_completion_or_lock(pending, pids["admin"])
        response = pending.result(timeout=12)
    assert response.status_code == 200
    assert "Protected note" in response.content.decode()
    assert "erben" in response.content.decode()
    field.refresh_from_db()
    assert field.sensitivity == "normal"


def test_stale_partial_template_cannot_disable_high_encryption(facility, lead_user):
    from core.services.file_vault import is_encrypted_value, safe_decrypt

    record, stale = make_record(facility, lead_user, "event")
    current = FieldTemplate.objects.get(pk=stale.pk)
    current.sensitivity = "high"
    current.save()
    stale.save(update_fields=["is_encrypted"])
    current.refresh_from_db()
    assert current.is_encrypted
    record.data_json = {stale.slug: "AfterHighTransitionSecret"}
    record.save(update_fields=["data_json", "search_text"])
    record.refresh_from_db()
    assert is_encrypted_value(record.data_json[stale.slug])
    assert safe_decrypt(record.data_json[stale.slug]) == "AfterHighTransitionSecret"
    assert record.search_text == ""


@pytest.mark.parametrize("model", ["type", "assignment"])
def test_partial_write_accepts_update_field_iterators(facility, model):
    doc_type, field = configuration(facility)
    if model == "type":
        doc_type.name = "Renamed type"
        doc_type.save(update_fields=iter(["name"]))
        doc_type.refresh_from_db()
        assert doc_type.name == "Renamed type"
    else:
        assignment = DocumentTypeField.objects.create(document_type=doc_type, field_template=field)
        assignment.sort_order = 7
        assignment.save(update_fields=iter(["sort_order"]))
        assignment.refresh_from_db()
        assert assignment.sort_order == 7


def test_high_type_creation_accepts_an_unset_primary_key(facility):
    doc_type = DocumentType(facility=facility, name="Unassigned High type", sensitivity="high", id=None)
    doc_type.save()
    doc_type.refresh_from_db()
    assert doc_type.pk is not None
    assert doc_type.sensitivity == "high"
    assert not doc_type.fields.exists()
