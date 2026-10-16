"""Configuration validation and activation pins.

Source: mutation delta of the release-0.99.8 checkpoint, 24 mutants.
Groups: historical and preset text compatibility (15), partial type saves (2),
activation lock strength (3), scoped report bindings and diagnostics (4).
Related pattern: test_mutation_pins_0997_config_mutations.py.
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.utils.translation import gettext as _

from core.models import DocumentType, DocumentTypeField, EventHistory, FieldTemplate, QuickTemplate
from core.services.config.mutations import set_field_template_active, validate_report_key_activation
from tests.test_config_reactivation_conflicts import binding_pair
from tests.test_sensitivity_transitions import make_record, request_for

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("side", ["data_before", "data_after"])
def test_history_only_multiline_values_prevent_narrowing(facility, lead_user, side):
    """Either historical snapshot alone prevents narrowing to a single line.

    x__has_incompatible_single_line_values__mutmut_14
    x__has_incompatible_single_line_values__mutmut_15
    x__has_incompatible_single_line_values__mutmut_17
    x__has_incompatible_single_line_values__mutmut_18
    x__has_incompatible_single_line_values__mutmut_39
    x__has_incompatible_single_line_values__mutmut_46
    """
    field = FieldTemplate.objects.create(facility=facility, name="Historical note", field_type="textarea")
    record, _ = make_record(facility, lead_user, "event", field=field)
    record.data_json = {}
    record.save()
    EventHistory.objects.create(
        event=record, action="update", changed_by=lead_user, **{side: {field.slug: "First\nSecond"}}
    )
    field.field_type = "text"
    with pytest.raises(ValidationError) as error:
        field.save()
    assert set(error.value.message_dict) == {"field_type"}
    field.refresh_from_db()
    assert field.field_type == "textarea"


def test_preset_only_multiline_values_prevent_narrowing(facility):
    """A preset alone prevents narrowing its multiline values.

    x__has_incompatible_single_line_values__mutmut_28
    x__has_incompatible_single_line_values__mutmut_30
    x__has_incompatible_single_line_values__mutmut_32
    x__has_incompatible_single_line_values__mutmut_35
    x__has_incompatible_single_line_values__mutmut_40
    """
    field = FieldTemplate.objects.create(facility=facility, name="Preset note", field_type="textarea")
    target = DocumentType.objects.create(facility=facility, name="Preset type")
    QuickTemplate.objects.create(
        facility=facility, document_type=target, name="Multiline preset", prefilled_data={field.slug: "First\nSecond"}
    )
    field.field_type = "text"
    with pytest.raises(ValidationError):
        field.save()
    field.refresh_from_db()
    assert field.field_type == "textarea"


@pytest.mark.parametrize(
    "value", [{"__encrypted__": True, "value": "invalid-token"}, "First\rSecond"], ids=["unreadable", "carriage-return"]
)
def test_unreadable_or_carriage_return_values_prevent_narrowing(facility, lead_user, value):
    """Unreadable ciphertext and bare carriage returns cannot become single-line text.

    x__has_incompatible_single_line_values__mutmut_50
    x__has_incompatible_single_line_values__mutmut_56
    """
    field = FieldTemplate.objects.create(facility=facility, name="Stored note", field_type="textarea")
    record, _ = make_record(facility, lead_user, "event", field=field)
    type(record).objects.filter(pk=record.pk).update(data_json={field.slug: value})
    field.field_type = "text"
    with pytest.raises(ValidationError):
        field.save()
    field.refresh_from_db()
    assert field.field_type == "textarea"


def test_partial_type_save_cannot_reinterpret_stored_text(facility, lead_user):
    """An explicit field_type write still validates stored values.

    x_validate_field_type_change__mutmut_5
    x_validate_field_type_change__mutmut_6
    """
    _, field = make_record(facility, lead_user, "event", value="Keep this text")
    field.field_type = "number"
    with pytest.raises(ValidationError):
        field.save(update_fields=["field_type"])
    field.refresh_from_db()
    assert field.field_type == "text"


@pytest.mark.postgres
def test_activation_uses_scoped_no_key_target_lock(facility, admin_user, rf):
    """Activation locks its target without blocking foreign-key key-share readers.

    x_set_field_template_active__mutmut_4
    x_set_field_template_active__mutmut_6
    x_set_field_template_active__mutmut_9
    """
    field = FieldTemplate.objects.create(facility=facility, name="Activation target")
    statements = []

    def observe(execute, sql, params, many, context):
        statements.append((sql, tuple(params or ())))
        return execute(sql, params, many, context)

    with connection.execute_wrapper(observe):
        result = set_field_template_active(request_for(rf, facility, admin_user), field, False)
    locks = [(sql, params) for sql, params in statements if 'FROM "core_fieldtemplate"' in sql and "FOR " in sql]
    assert locks
    assert all("FOR NO KEY UPDATE" in sql and "WHERE" in sql for sql, _ in locks)
    for identifier in (field.pk, facility.pk):
        assert any(str(identifier) in {str(p) for p in params} for _, params in locks)
    assert result.is_active is False


def test_single_line_history_skips_absent_and_null_values(facility, lead_user):
    """Empty snapshots and null values permit a safe single-line conversion.

    x__has_incompatible_single_line_values__mutmut_41
    x__has_incompatible_single_line_values__mutmut_42
    """
    field = FieldTemplate.objects.create(facility=facility, name="Compatible note", field_type="textarea")
    record, _ = make_record(facility, lead_user, "event", field=field, value=None)
    EventHistory.objects.create(
        event=record, action="update", changed_by=lead_user, data_before={field.slug: "One line"}, data_after={}
    )
    field.field_type = "text"
    field.save()
    field.refresh_from_db()
    assert field.field_type == "text"


def test_report_activation_ignores_legacy_foreign_type_assignments(facility, second_facility):
    """Foreign types cannot introduce a report-binding activation conflict.

    x_validate_report_key_activation__mutmut_11
    """
    _, field, conflict = binding_pair(facility)
    foreign = DocumentType.objects.create(facility=second_facility, name="Foreign legacy type")
    field.document_type_fields.all().delete()
    DocumentTypeField.objects.bulk_create(
        [DocumentTypeField(document_type=foreign, field_template=item) for item in (field, conflict)]
    )
    field.is_active = True
    field.save(update_fields=["is_active"])
    field.refresh_from_db()
    assert field.is_active is True


def test_activation_reports_exact_field_error(facility, admin_user, rf):
    """Conflicts identify is_active and name both the type and competing field.

    x_validate_report_key_activation__mutmut_29
    x_validate_report_key_activation__mutmut_27
    """
    target, field, conflict = binding_pair(facility)
    with pytest.raises(ValidationError) as error:
        set_field_template_active(request_for(rf, facility, admin_user), field, True)
    assert error.value.message_dict == {
        "is_active": [
            _(
                "Die Feldvorlage kann nicht aktiviert werden: Im Dokumentationstyp „%(type)s“ "
                "verwendet „%(field)s“ bereits denselben Berichtsschlüssel."
            )
            % {"type": target.name, "field": conflict.name}
        ]
    }


def test_report_validation_does_not_reload_each_assigned_type(facility):
    """Validating several bindings loads their types together without per-type queries.

    x_validate_report_key_activation__mutmut_7
    """
    field = FieldTemplate.objects.create(facility=facility, name="Shared report field", report_key="duration")
    for index in range(3):
        target = DocumentType.objects.create(facility=facility, name=f"Report target {index}")
        DocumentTypeField.objects.create(document_type=target, field_template=field)
    queries = []

    def observe(execute, sql, params, many, context):
        queries.append(sql)
        return execute(sql, params, many, context)

    with connection.execute_wrapper(observe):
        validate_report_key_activation(field, field.report_key, is_active=True)
    assert not any(
        'FROM "core_documenttype"' in sql
        and '"core_documenttype"."name"' in sql
        and '"core_documenttype"."id" = %s' in sql
        for sql in queries
    )
