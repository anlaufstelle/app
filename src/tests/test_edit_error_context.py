"""Shared edit error presentation must disclose only permitted fields."""

import pytest
from django import forms
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string

from core.views._edit_errors import edit_error_context, is_version_conflict


@pytest.mark.parametrize("code", ["version_conflict", "invalid_token", "missing_token"])
def test_version_errors_are_conflicts(code):
    assert is_version_conflict(ValidationError("Conflict", code=code))


def test_field_error_is_not_a_version_conflict():
    assert not is_version_conflict(ValidationError({"note": "Invalid"}))


def test_comparison_uses_filtered_forms_and_marks_differences():
    own = forms.Form({"note": "Own draft", "secret": "Posted secret"})
    own.fields["note"] = forms.CharField(label="Note")
    current = forms.Form(initial={"note": "Server draft", "secret": "Server secret"})
    current.fields["note"] = forms.CharField(label="Note")
    context = edit_error_context(
        ValidationError("Conflict", code="version_conflict"),
        own,
        current_forms=[(own, current)],
        current_version="fresh",
        current_form_state="fresh-state",
    )
    html = render_to_string("core/partials/_edit_conflict.html", context)
    assert "Aktueller Stand" in html
    assert "Server draft" in html
    assert "Abweichend" in html
    assert "bewusst erneut speichern" in html
    assert "secret" not in html
    assert "Posted secret" not in html
    assert "Server secret" not in html
    assert context["expected_updated_at"] == "fresh"
    assert context["form_state_token"] == "fresh-state"


def test_other_errors_do_not_refresh_tokens_or_show_server_state():
    own = forms.Form({"note": "Own draft"})
    own.fields["note"] = forms.CharField()
    context = edit_error_context(ValidationError({"note": "Invalid"}), own)
    assert own["note"].value() == "Own draft"
    assert own.has_error("note")
    assert "expected_updated_at" not in context
    assert "form_state_token" not in context
    assert not context["has_edit_conflict"]
    assert "Aktueller Stand" not in render_to_string("core/partials/_edit_conflict.html", context)


def test_replacement_upload_requires_file_reselection():
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.utils.datastructures import MultiValueDict

    own = forms.Form(
        {},
        MultiValueDict({"attachment__replace__entry": [SimpleUploadedFile("scan.pdf", b"pdf")]}),
    )
    own.fields["attachment"] = forms.FileField(label="Attachment", required=False)
    context = edit_error_context(ValidationError("Rejected"), own)
    assert context["file_reselection_fields"] == ["Attachment"]


def test_distinct_choice_values_with_equal_labels_are_marked():
    own = forms.Form({"selection": "one"})
    own.fields["selection"] = forms.ChoiceField(choices=[("one", "Same label"), ("two", "Same label")])
    current = forms.Form(initial={"selection": "two"})
    current.fields["selection"] = own.fields["selection"]
    context = edit_error_context(
        ValidationError("Conflict", code="version_conflict"),
        own,
        current_forms=[(own, current)],
        current_version="fresh",
    )
    assert context["edit_conflict_rows"][0]["differs"]
