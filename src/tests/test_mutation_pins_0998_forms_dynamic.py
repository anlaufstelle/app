"""Dynamic choice and classic upload form pins.

Source: mutation delta of the release-0.99.8 checkpoint, 2 mutants.
Groups: empty initial choices (1), classic single-file validation (1).
Related pattern: test_mutation_pins_0997_forms_config.py.
"""

import pytest
from django import forms
from django.core.files.uploadedfile import SimpleUploadedFile

from core.forms.dynamic import DynamicDataForm, build_dynamic_form_field
from core.models import FieldTemplate


def test_empty_initial_value_does_not_restore_inactive_empty_option():
    """An empty scalar initial value cannot restore a legacy inactive empty choice.

    x__select_choices__mutmut_19
    """
    template = FieldTemplate(
        name="Legacy selection",
        slug="legacy-selection",
        field_type="select",
        options_json=[
            {"slug": "", "label": "Inactive empty choice", "is_active": False},
            {"slug": "active", "label": "Active choice", "is_active": True},
        ],
    )
    field = build_dynamic_form_field(template, {template.slug: ""})
    assert [value for value, _label in field.choices] == ["", "active"]


@pytest.mark.django_db
def test_classic_file_field_validates_nonempty_upload(facility):
    """A classic FileField validates its single UploadedFile without iterating its byte lines.

    xǁDynamicDataFormǁclean__mutmut_26
    """
    upload = SimpleUploadedFile("allowed.pdf", b"%PDF-1.4\nNonempty content\n", content_type="application/pdf")
    form = DynamicDataForm(data={}, files={"upload": upload}, document_type=None, facility=facility)
    form.fields["upload"] = forms.FileField()
    assert form.is_valid(), form.errors
    assert form.cleaned_data["upload"] is upload
