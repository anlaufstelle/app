"""Form validation pins.

Source: mutation delta of the release-0.99.7 checkpoint, 36 mutants.
Groups: High inheritance and field errors (26), condition sources (1),
optional group scope (1), option default references (6), eager relations (2).
Public clean methods are called before ModelForm post-clean so that later model
validation cannot conceal a missing form error. No rls_test_role is needed.
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils.translation import gettext as _

from core.forms.config import DocumentTypeForm, FieldConditionForm, FieldGroupForm, FieldTemplateForm
from core.models import DocumentType, DocumentTypeField, FieldTemplate

pytestmark = pytest.mark.django_db


def _inheritance_message(name):
    return _(
        "Feldvorlage „%(field)s“ darf in einem Dokumentationstyp mit Stufe „Hoch“ nicht erben. "
        "Wählen Sie eine ausdrückliche Feldstufe: „Erhöht“ für Berichtsfelder, sonst „Hoch“."
    ) % {"field": name}


def _prepare_clean(form, **data):
    # Initialize the public errors property without running model post-clean.
    assert not form.errors
    form.cleaned_data = data
    return form


@pytest.fixture
def high_assignment(facility):
    target = DocumentType.objects.create(facility=facility, name="High target", sensitivity="high")
    field = FieldTemplate.objects.create(facility=facility, name="Stored field", sensitivity="high")
    DocumentTypeField.objects.create(document_type=target, field_template=field)
    return target, field


def test_type_edit_places_inheritance_error_on_sensitivity(facility):
    """Type edit places inheritance error on sensitivity.

    Pins:
    * ``xǁDocumentTypeFormǁclean__mutmut_38``
    * ``xǁDocumentTypeFormǁclean__mutmut_39``
    * ``xǁDocumentTypeFormǁclean__mutmut_40``
    * ``xǁDocumentTypeFormǁclean__mutmut_42``
    * ``xǁDocumentTypeFormǁclean__mutmut_47``
    * ``xǁDocumentTypeFormǁclean__mutmut_48``
    * ``xǁDocumentTypeFormǁclean__mutmut_53``
    """
    target = DocumentType.objects.create(facility=facility, name="Normal target")
    field = FieldTemplate.objects.create(facility=facility, name="Inherited field")
    DocumentTypeField.objects.create(document_type=target, field_template=field)
    form = _prepare_clean(DocumentTypeForm(instance=target, facility=facility), sensitivity="high")
    cleaned = form.clean()
    assert form.errors["sensitivity"] == [_inheritance_message(field.name)]
    assert "sensitivity" not in cleaned
    assert not form.non_field_errors()


def test_field_edit_uses_submitted_name_and_field_error(facility, high_assignment):
    """Field edit uses submitted name and field error.

    Pins:
    * ``xǁFieldTemplateFormǁclean__mutmut_4``
    * ``xǁFieldTemplateFormǁclean__mutmut_5``
    * ``xǁFieldTemplateFormǁclean__mutmut_6``
    * ``xǁFieldTemplateFormǁclean__mutmut_7``
    * ``xǁFieldTemplateFormǁclean__mutmut_8``
    * ``xǁFieldTemplateFormǁclean__mutmut_11``
    * ``xǁFieldTemplateFormǁclean__mutmut_12``
    * ``xǁFieldTemplateFormǁclean__mutmut_17``
    * ``xǁFieldTemplateFormǁclean__mutmut_18``
    * ``xǁFieldTemplateFormǁclean__mutmut_19``
    * ``xǁFieldTemplateFormǁclean__mutmut_23``
    * ``xǁFieldTemplateFormǁclean__mutmut_24``
    * ``xǁFieldTemplateFormǁclean__mutmut_25``
    * ``xǁFieldTemplateFormǁclean__mutmut_26``
    * ``xǁFieldTemplateFormǁclean__mutmut_27``
    * ``xǁFieldTemplateFormǁclean__mutmut_28``
    * ``xǁFieldTemplateFormǁclean__mutmut_29``
    """
    _, field = high_assignment
    form = _prepare_clean(
        FieldTemplateForm(instance=field, facility=facility), name="Renamed field", sensitivity="", is_active=True
    )
    cleaned = form.clean()
    assert cleaned is form.cleaned_data
    assert form.errors["sensitivity"] == [_inheritance_message("Renamed field")]
    assert "sensitivity" not in cleaned
    assert not form.non_field_errors()


def test_field_edit_accepts_explicit_sensitivity(facility, high_assignment):
    """Explicit sensitivity remains valid for a High type; xǁFieldTemplateFormǁclean__mutmut_7."""
    _, field = high_assignment
    form = _prepare_clean(FieldTemplateForm(instance=field, facility=facility), name=field.name, sensitivity="high")
    assert form.clean()["sensitivity"] == "high"
    assert not form.errors


def test_field_edit_ignores_cross_facility_assignment(facility, second_facility, high_assignment):
    """Field edit ignores cross facility assignment.

    Pins:
    * ``xǁFieldTemplateFormǁclean__mutmut_13``
    """
    _, field = high_assignment
    foreign = DocumentType.objects.create(facility=second_facility, name="Foreign high", sensitivity="high")
    DocumentTypeField.objects.filter(field_template=field).update(document_type=foreign)
    form = _prepare_clean(FieldTemplateForm(instance=field, facility=facility), name=field.name, sensitivity="")
    assert form.clean()["sensitivity"] == ""
    assert not form.errors


def test_condition_sources_exclude_unencrypted_file_fields(facility):
    """Condition sources exclude unencrypted file fields.

    Pins:
    * ``xǁFieldConditionFormǁ__init____mutmut_14``
    """
    target = DocumentType.objects.create(facility=facility, name="Condition target")
    unsupported = FieldTemplate.objects.create(facility=facility, name="Unsupported source")
    supported = FieldTemplate.objects.create(facility=facility, name="Supported source")
    file_assignment = DocumentTypeField.objects.create(document_type=target, field_template=unsupported, sort_order=0)
    text_assignment = DocumentTypeField.objects.create(document_type=target, field_template=supported, sort_order=1)
    FieldTemplate.objects.filter(pk=unsupported.pk).update(field_type="file", is_encrypted=False)
    form = FieldConditionForm(document_type=target)
    assert list(form.fields["depends_on_field"].queryset.values_list("pk", flat=True)) == [text_assignment.pk]
    assert file_assignment.pk in form.fields["target_field"].queryset.values_list("pk", flat=True)


def test_inheritance_error_keeps_report_key_validation(facility, high_assignment):
    """Inheritance error keeps report key validation.

    Pins:
    * ``xǁFieldTemplateFormǁclean__mutmut_34``
    """
    target, field = high_assignment
    existing = FieldTemplate.objects.create(
        facility=facility,
        name="Existing duration",
        field_type="number",
        sensitivity="elevated",
        report_key="dauer_minuten",
    )
    DocumentTypeField.objects.create(document_type=target, field_template=existing)
    form = _prepare_clean(
        FieldTemplateForm(instance=field, facility=facility),
        name="Renamed field",
        sensitivity="",
        report_key="dauer_minuten",
        is_active=True,
    )
    assert form.clean() is form.cleaned_data
    assert form.errors["sensitivity"] == [_inheritance_message("Renamed field")]
    assert form.errors["report_key"] == [
        _(
            "„%(neu)s“ trägt denselben Berichtsschlüssel wie die bereits zugeordnete Feldvorlage "
            "„%(bestand)s“. Je Dokumentationstyp ist höchstens eine Feldvorlage je Berichtsschlüssel "
            "zulässig — sonst zählt der Bericht keine von beiden."
        )
        % {"neu": "Renamed field", "bestand": existing.name}
    ]


def test_group_name_without_type_skips_collision_lookup():
    """Group name without type skips collision lookup.

    Pins:
    * ``xǁFieldGroupFormǁclean_name__mutmut_5``
    """
    form = _prepare_clean(FieldGroupForm(doc_type=None), name="Unscoped group")
    with CaptureQueriesContext(connection) as queries:
        assert form.clean_name() == "Unscoped group"
    assert not any("core_fieldgroup" in row["sql"] for row in queries)


def test_high_type_validation_fetches_templates_with_assignments(facility):
    """High type validation fetches templates with assignments.

    Pins:
    * ``xǁDocumentTypeFormǁclean__mutmut_43``
    """
    target = DocumentType.objects.create(facility=facility, name="Eager type")
    for index in range(3):
        field = FieldTemplate.objects.create(facility=facility, name=f"Explicit field {index}", sensitivity="high")
        DocumentTypeField.objects.create(document_type=target, field_template=field)
    form = _prepare_clean(DocumentTypeForm(instance=target), sensitivity="high")
    with CaptureQueriesContext(connection) as queries:
        assert form.clean()["sensitivity"] == "high"
    assert not any(
        'FROM "core_fieldtemplate"' in row["sql"] and '"core_fieldtemplate"."id" =' in row["sql"] for row in queries
    )


def test_field_validation_fetches_assigned_type_with_assignment(facility, high_assignment):
    """Field validation fetches assigned type with assignment.

    Pins:
    * ``xǁFieldTemplateFormǁclean__mutmut_10``
    """
    _, field = high_assignment
    form = _prepare_clean(FieldTemplateForm(instance=field), sensitivity="", name=field.name)
    with CaptureQueriesContext(connection) as queries:
        form.clean()
    assert form.errors["sensitivity"] == [_inheritance_message(field.name)]
    assert not any(
        'FROM "core_documenttype"' in row["sql"] and '"core_documenttype"."id" =' in row["sql"] for row in queries
    )


def test_retained_default_prevents_option_removal_in_field_cleaner(facility):
    """Retained default prevents option removal in field cleaner.

    Pins:
    * ``xǁFieldTemplateFormǁclean_options_json__mutmut_126``
    * ``xǁFieldTemplateFormǁclean_options_json__mutmut_130``
    * ``xǁFieldTemplateFormǁclean_options_json__mutmut_131``
    """
    field = FieldTemplate.objects.create(
        facility=facility,
        name="Select field",
        field_type="select",
        options_json=[{"slug": "saved", "label": "Saved"}],
        default_value="saved",
    )
    form = FieldTemplateForm(data={"default_value": "saved"}, instance=field)
    form.cleaned_data = {"field_type": "select", "options_json": []}
    with pytest.raises(ValidationError) as caught:
        form.clean_options_json()
    assert caught.value.messages == [
        _(
            "Verwendete Options-Slugs dürfen nicht entfernt oder umbenannt werden: %(slugs)s. "
            "Bitte die Option deaktivieren statt entfernen oder umbenennen."
        )
        % {"slugs": "saved"}
    ]
    assert "saved" in form.option_editor_errors


@pytest.mark.parametrize("saved_slug", ["saved", "XXXX"])
def test_omitted_default_clears_its_option_reference(facility, saved_slug):
    """Omitted default clears its option reference.

    Pins:
    * ``xǁFieldTemplateFormǁclean_options_json__mutmut_127``
    * ``xǁFieldTemplateFormǁclean_options_json__mutmut_129``
    * ``xǁFieldTemplateFormǁclean_options_json__mutmut_132``
    """
    field = FieldTemplate.objects.create(
        facility=facility,
        name="Omitted default",
        field_type="select",
        options_json=[{"slug": saved_slug, "label": "Saved"}],
        default_value=saved_slug,
    )
    form = FieldTemplateForm(data={}, instance=field)
    form.cleaned_data = {"field_type": "select", "options_json": []}
    assert form.clean_options_json() == []
