"""Configuration mutation service pins.

Source: mutation delta of the release-0.99.7 checkpoint, 26 mutants.
Groups: exclusive gates (9), no-key target locks (6), scoped lookup (1),
assigned-type locks (2), early High validation and exact messages (7),
eager template validation (1). SQL checks inspect tables and WHERE parameters,
without fixing query or lock counts. No rls_test_role is needed.
"""

from contextlib import contextmanager

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.forms import modelform_factory
from django.utils.translation import gettext as _

from core.models import DocumentType, DocumentTypeField, FieldGroup, FieldTemplate
from core.services.config.mutations import add_field_to_doctype, save_document_type, save_field_template

pytestmark = pytest.mark.django_db


def _inheritance_message(name):
    return _(
        "Feldvorlage „%(field)s“ darf in einem Dokumentationstyp mit Stufe „Hoch“ nicht erben. "
        "Wählen Sie eine ausdrückliche Feldstufe: „Erhöht“ für Berichtsfelder, sonst „Hoch“."
    ) % {"field": name}


@contextmanager
def _statements():
    statements = []

    def record(execute, sql, params, many, context):
        statements.append((sql, tuple(params or ())))
        return execute(sql, params, many, context)

    with connection.execute_wrapper(record):
        yield statements


def _assert_exclusive_gate_before_rows(statements, facility):
    gates = [(index, sql, params) for index, (sql, params) in enumerate(statements) if "pg_advisory_xact_lock" in sql]
    row_positions = [index for index, (sql, _) in enumerate(statements) if "FOR " in sql.upper()]
    assert gates and row_positions
    index, sql, params = gates[0]
    assert "pg_advisory_xact_lock(" in sql
    assert str(facility.pk) in params
    assert index < min(row_positions)


def _assert_no_key_lock(statements, model, *where_ids):
    locks = [(sql, params) for sql, params in statements if f'FROM "{model._meta.db_table}"' in sql and "FOR " in sql]
    assert locks
    assert all("FOR NO KEY UPDATE" in sql for sql, _ in locks)
    assert all("WHERE" in sql for sql, _ in locks)
    assert all(any(str(value) in {str(p) for p in params} for _, params in locks) for value in where_ids)


@pytest.fixture
def config_request(rf, facility, admin_user):
    request = rf.post("/config/")
    request.user = admin_user
    request.current_facility = facility
    return request


@pytest.fixture
def assignment(facility):
    target = DocumentType.objects.create(facility=facility, name="Assigned type")
    field = FieldTemplate.objects.create(facility=facility, name="Assigned field", sensitivity="normal")
    DocumentTypeField.objects.create(document_type=target, field_template=field)
    return target, field


def test_document_type_edit_gates_and_locks_before_save(facility, config_request, assignment, monkeypatch):
    """Document type edit gates and locks before save.

    Pins:
    * ``x_save_document_type__mutmut_4``
    * ``x_save_document_type__mutmut_6``
    * ``x_save_document_type__mutmut_7``
    * ``x_save_document_type__mutmut_12``
    * ``x_save_document_type__mutmut_14``
    * ``x_save_document_type__mutmut_17``
    """
    target, _ = assignment
    form = modelform_factory(DocumentType, fields=["name", "description"])(
        data={"name": target.name, "description": "Edited"}, instance=target
    )
    assert form.is_valid(), form.errors
    original_save = form.save
    before_save = []

    def save(*args, **kwargs):
        before_save.extend(statements)
        return original_save(*args, **kwargs)

    monkeypatch.setattr(form, "save", save)
    with _statements() as statements:
        save_document_type(config_request, form, creating=False)
    _assert_exclusive_gate_before_rows(before_save, facility)
    _assert_no_key_lock(before_save, DocumentType, target.pk, facility.pk)


def test_field_edit_locks_template_and_assigned_types_before_save(facility, config_request, assignment, monkeypatch):
    """Field edit locks template and assigned types before save.

    Pins:
    * ``x_save_field_template__mutmut_8``
    * ``x_save_field_template__mutmut_10``
    * ``x_save_field_template__mutmut_11``
    * ``x_save_field_template__mutmut_16``
    * ``x_save_field_template__mutmut_18``
    * ``x_save_field_template__mutmut_21``
    * ``x_save_field_template__mutmut_26``
    * ``x_save_field_template__mutmut_37``
    """
    target, field = assignment
    second = DocumentType.objects.create(facility=facility, name="Second assigned type")
    DocumentTypeField.objects.create(document_type=second, field_template=field)
    form = modelform_factory(FieldTemplate, fields=["name", "help_text"])(
        data={"name": field.name, "help_text": "Edited"}, instance=field
    )
    assert form.is_valid(), form.errors
    original_save = field.save
    before_save = []

    def save(*args, **kwargs):
        before_save.extend(statements)
        return original_save(*args, **kwargs)

    monkeypatch.setattr(field, "save", save)
    with _statements() as statements:
        save_field_template(config_request, form, creating=False, facility=facility)
    _assert_exclusive_gate_before_rows(before_save, facility)
    _assert_no_key_lock(before_save, FieldTemplate, field.pk, facility.pk)
    _assert_no_key_lock(before_save, DocumentType, field.pk, facility.pk)
    assigned_locks = [sql for sql, _ in before_save if 'FROM "core_documenttype"' in sql and "FOR " in sql]
    assert any('"core_documenttypefield"' in sql for sql in assigned_locks)
    assert set(field.document_type_fields.values_list("document_type_id", flat=True)) == {target.pk, second.pk}


def test_assignment_takes_exclusive_gate_before_row_locks(facility, config_request):
    """Assignment takes exclusive gate before row locks.

    Pins:
    * ``x_add_field_to_doctype__mutmut_2``
    * ``x_add_field_to_doctype__mutmut_4``
    * ``x_add_field_to_doctype__mutmut_5``
    """
    target = DocumentType.objects.create(facility=facility, name="New target")
    field = FieldTemplate.objects.create(facility=facility, name="New field")
    with _statements() as statements:
        result = add_field_to_doctype(config_request, target, field)
    assert result.document_type_id == target.pk
    _assert_exclusive_gate_before_rows(statements, facility)


def test_field_edit_rejects_foreign_template_before_persistence(facility, second_facility, config_request, monkeypatch):
    """Field edit rejects foreign template before persistence.

    Pins:
    * ``x_save_field_template__mutmut_14``
    """
    field = FieldTemplate.objects.create(facility=second_facility, name="Foreign template")
    form = modelform_factory(FieldTemplate, fields=["name"])(data={"name": field.name}, instance=field)
    assert form.is_valid(), form.errors

    def forbid_save(*args, **kwargs):
        pytest.fail("A foreign template must not reach model persistence")

    monkeypatch.setattr(field, "save", forbid_save)
    with pytest.raises(FieldTemplate.DoesNotExist):
        save_field_template(config_request, form, creating=False, facility=facility)


def test_high_assignment_rejects_inheritance_before_foreign_group(config_request, facility, second_facility):
    """High assignment rejects inheritance before foreign group.

    Pins:
    * ``x_add_field_to_doctype__mutmut_26``
    * ``x_add_field_to_doctype__mutmut_27``
    * ``x_add_field_to_doctype__mutmut_28``
    """
    target = DocumentType.objects.create(facility=facility, name="High target", sensitivity="high")
    field = FieldTemplate.objects.create(facility=facility, name="Named inherited field")
    foreign = DocumentType.objects.create(facility=second_facility, name="Foreign type")
    group = FieldGroup.objects.create(document_type=foreign, name="Foreign group")
    with pytest.raises(ValidationError) as caught:
        add_field_to_doctype(config_request, target, field, field_group=group)
    assert caught.value.messages == [_inheritance_message(field.name)]
    assert not hasattr(caught.value, "message_dict")


def test_stale_type_edit_reports_current_inherited_field(facility, config_request):
    """Stale type edit reports current inherited field.

    Pins:
    * ``x_save_document_type__mutmut_34``
    """
    target = DocumentType.objects.create(facility=facility, name="Stale target")
    form = modelform_factory(DocumentType, fields=["name", "sensitivity"])(
        data={"name": target.name, "sensitivity": "high"}, instance=target
    )
    assert form.is_valid(), form.errors
    field = FieldTemplate.objects.create(facility=facility, name="New inherited field")
    DocumentTypeField.objects.create(document_type=DocumentType.objects.get(pk=target.pk), field_template=field)
    with pytest.raises(ValidationError) as caught:
        save_document_type(config_request, form, creating=False)
    assert caught.value.messages == [_inheritance_message(field.name)]
    assert not hasattr(caught.value, "message_dict")


def test_stale_field_edit_rejects_high_inheritance_at_service_boundary(facility, config_request, assignment):
    """Stale field edit rejects high inheritance at service boundary.

    Pins:
    * ``x_save_field_template__mutmut_43``
    * ``x_save_field_template__mutmut_44``
    * ``x_save_field_template__mutmut_45``
    """
    target, field = assignment
    form = modelform_factory(FieldTemplate, fields=["name", "sensitivity"])(
        data={"name": "Renamed template", "sensitivity": ""}, instance=field
    )
    assert form.is_valid(), form.errors
    target.sensitivity = "high"
    target.save()
    with pytest.raises(ValidationError) as caught:
        save_field_template(config_request, form, creating=False, facility=facility)
    assert caught.value.messages == [_inheritance_message("Renamed template")]
    assert not hasattr(caught.value, "message_dict")


def test_type_service_fetches_templates_with_assignments(facility, config_request):
    """Type service fetches templates with assignments.

    Pins:
    * ``x_save_document_type__mutmut_28``
    """
    target = DocumentType.objects.create(facility=facility, name="Eager service type")
    for index in range(3):
        field = FieldTemplate.objects.create(facility=facility, name=f"Explicit template {index}", sensitivity="high")
        DocumentTypeField.objects.create(document_type=target, field_template=field)
    form = modelform_factory(DocumentType, fields=["name"])(data={"name": target.name}, instance=target)
    assert form.is_valid(), form.errors
    with _statements() as statements:
        save_document_type(config_request, form, creating=False)
    assert not any('FROM "core_fieldtemplate"' in sql and '"core_fieldtemplate"."id" =' in sql for sql, _ in statements)
