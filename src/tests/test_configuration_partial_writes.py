"""Configuration saves require an explicit write set for deferred instances."""

import pytest

from core.models import DocumentType, DocumentTypeField, FieldCondition, FieldGroup, FieldTemplate, QuickTemplate

pytestmark = pytest.mark.django_db


def configuration_record(facility, model_name):
    doc_type = DocumentType.objects.create(facility=facility, name="Partial type")
    if model_name == "DocumentType":
        return doc_type, "name", "Updated type"
    field = FieldTemplate.objects.create(facility=facility, name="Partial field", sensitivity="normal")
    if model_name == "FieldTemplate":
        return field, "name", "Updated field"
    assignment = DocumentTypeField.objects.create(document_type=doc_type, field_template=field)
    if model_name == "DocumentTypeField":
        return assignment, "sort_order", 42
    if model_name == "FieldGroup":
        return FieldGroup.objects.create(document_type=doc_type, name="Partial group"), "name", "Updated group"
    if model_name == "QuickTemplate":
        return (
            QuickTemplate.objects.create(facility=facility, document_type=doc_type, name="Partial quick"),
            "name",
            "Updated quick",
        )
    source = FieldTemplate.objects.create(facility=facility, name="Source", field_type="boolean")
    source_assignment = DocumentTypeField.objects.create(document_type=doc_type, field_template=source)
    condition = FieldCondition.objects.create(
        target_field=assignment, depends_on_field=source_assignment, operator="equals", value=True
    )
    return condition, "group_index", 42


CONFIGURATION_MODELS = [
    "DocumentType",
    "FieldTemplate",
    "DocumentTypeField",
    "FieldGroup",
    "FieldCondition",
    "QuickTemplate",
]


@pytest.mark.parametrize("model_name", CONFIGURATION_MODELS)
@pytest.mark.parametrize("loading", ["only", "defer"])
def test_implicit_configuration_save_rejects_deferred_fields_without_queries(
    facility, django_assert_num_queries, model_name, loading
):
    original, column, value = configuration_record(facility, model_name)
    model = type(original)
    before = model.objects.filter(pk=original.pk).values().get()
    queryset = model.objects.only(column) if loading == "only" else model.objects.defer(column)
    record = queryset.get(pk=original.pk)
    if loading == "only":
        setattr(record, column, value)
    assert record.get_deferred_fields()
    with django_assert_num_queries(0), pytest.raises(ValueError, match="deferred"):
        record.save()
    assert model.objects.filter(pk=original.pk).values().get() == before


@pytest.mark.parametrize("model_name", CONFIGURATION_MODELS)
@pytest.mark.parametrize("explicit", [False, True])
def test_complete_and_explicit_configuration_saves_remain_supported(facility, model_name, explicit):
    original, column, value = configuration_record(facility, model_name)
    model = type(original)
    record = (model.objects.only(column) if explicit else model.objects.all()).get(pk=original.pk)
    setattr(record, column, value)
    record.save(**({"update_fields": [column]} if explicit else {}))
    original.refresh_from_db()
    assert getattr(original, column) == value
