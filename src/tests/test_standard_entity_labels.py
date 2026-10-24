"""Standard field labels use the active language in forms and displays."""

from unittest.mock import patch

import pytest
from django.utils.translation import gettext, override

from core.forms.dynamic import DynamicDataForm
from core.models import DocumentType
from core.packages.catalog import parse_catalog_package
from core.packages.loader import load_package
from core.services.dynamic_data import build_dynamic_data_context


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["client", "case", "episode", "workitem"])
@pytest.mark.parametrize("output", ["form", "display"])
def test_standard_labels_translate_at_render_time(facility, staff_user, kind, output):
    load_package(facility, parse_catalog_package("default"))
    document_type = DocumentType.objects.for_facility(facility).get(entity_type=kind)
    field = document_type.fields.get().field_template
    with override("en"):
        expected = gettext(field.name)
        with patch("django.utils.translation.gettext", wraps=gettext) as translate:
            if output == "form":
                form = DynamicDataForm(document_type=document_type, facility=facility)
                rendered_label = str(form[field.slug].label)
            else:
                context = build_dynamic_data_context(document_type, {field.slug: "Example"}, staff_user)
                rendered_label = str(context["dynamic_fields"][0]["label"])
        assert rendered_label == expected
        translate.assert_any_call(field.name)
    field.refresh_from_db()
    assert field.name in ("Notizen", "Beschreibung", "Details")


@pytest.mark.django_db
@pytest.mark.parametrize("output", ["form", "display", "orphan"])
def test_custom_label_matching_ui_translation_is_preserved(facility, staff_user, output):
    from core.models import DocumentTypeField, FieldTemplate
    from tests._factories import make_document_type

    document_type = make_document_type(facility=facility, name="Custom event", entity_type="event")
    field = FieldTemplate.objects.create(facility=facility, name="Abbrechen", slug="custom-label", field_type="text")
    DocumentTypeField.objects.create(document_type=document_type, field_template=field)
    if output == "orphan":
        document_type.fields.all().delete()
    with override("en"):
        assert gettext(field.name) == "Cancel"
        if output == "form":
            form = DynamicDataForm(document_type=document_type, facility=facility)
            label = str(form[field.slug].label)
        else:
            context = build_dynamic_data_context(document_type, {field.slug: "Example"}, staff_user)
            entries = context["dynamic_orphan_fields" if output == "orphan" else "dynamic_fields"]
            label = str(entries[0]["label"])
        assert label == "Abbrechen"
