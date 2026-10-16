"""Configuration form persistence and guidance pins.

Source: mutation delta of the release-0.99.8 checkpoint, 3 mutants.
Groups: omitted edit positions (1), compatible-conversion guidance (1),
late-bound translated help text (1).
Related pattern: test_mutation_pins_0997_forms_config.py.
"""

import pytest
from django.utils.html import escape
from django.utils.translation import gettext as _
from django.utils.translation import override

from core.forms.config import DocumentTypeForm, FieldTemplateForm
from core.models import DocumentType

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("position", [None, ""], ids=["omitted", "blank"])
def test_edit_without_sort_order_preserves_existing_position(facility, position):
    """An omitted or blank position remains unchanged after a valid edit is saved.

    xǁDocumentTypeFormǁclean_sort_order__mutmut_5
    """
    target = DocumentType.objects.create(facility=facility, name="Existing position", sort_order=17)
    data = {"name": target.name, "entity_type": "event", "sensitivity": "normal", "is_active": "on"}
    if position is not None:
        data["sort_order"] = position
    form = DocumentTypeForm(
        data,
        instance=target,
        facility=facility,
    )
    assert form.is_valid(), form.errors
    form.save()
    target.refresh_from_db()
    assert target.sort_order == 17


def test_field_type_guidance_is_available_in_rendered_form(facility):
    """The form explains compatible text conversions and the new-template alternative.

    xǁFieldTemplateFormǁ__init____mutmut_3
    """
    form = FieldTemplateForm(facility=facility)
    expected = _(
        "Bei gespeicherten Werten ist nur ein kompatibler Wechsel zwischen Text und Textbereich möglich. "
        "Mehrzeilige oder nicht lesbare Textbereiche können nicht in einzeilige Textfelder geändert werden. "
        "Für andere Feldtypen die Vorlage deaktivieren und eine neue anlegen."
    )
    assert str(form.fields["field_type"].help_text) == expected
    assert escape(expected) in form["field_type"].as_field_group()


def test_empty_model_help_text_follows_rendering_language(facility):
    """A form constructed in German resolves its empty-field hint in the rendering language.

    xǁReportKeyFormMixinǁ_append_help_text__mutmut_4
    """
    with override("de"):
        form = DocumentTypeForm(facility=facility)
        german = str(form.fields["icon"].help_text)
    with override("en"):
        expected = _("Technischer Icon-Name, zum Beispiel users, heart oder file-text. Leer verwendet den Standard.")
        assert expected != german
        assert str(form.fields["icon"].help_text) == expected
        assert escape(expected) in form["icon"].as_field_group()
