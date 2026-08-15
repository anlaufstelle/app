"""Fokussierte Form-/Template-Vertraege fuer die Tally-Zahleneingabe (Refs #1906)."""

import pytest
from django.template.loader import render_to_string

from core.forms.tally import TallyNumberDataForm
from core.models import DocumentType, DocumentTypeField, FieldTemplate


def _add_field(document_type, *, name, field_type, required, sort_order):
    field_template = FieldTemplate.objects.create(
        facility=document_type.facility,
        name=name,
        field_type=field_type,
        is_required=required,
    )
    DocumentTypeField.objects.create(
        document_type=document_type,
        field_template=field_template,
        sort_order=sort_order,
    )
    return field_template


@pytest.mark.django_db
class TestTallyNumberDataForm:
    def test_builds_only_required_number_fields_in_document_order(self, facility):
        document_type = DocumentType.objects.create(facility=facility, name="Spritzentausch")
        _add_field(
            document_type,
            name="Optionale Notiz",
            field_type=FieldTemplate.FieldType.TEXT,
            required=False,
            sort_order=0,
        )
        rueckgabe = _add_field(
            document_type,
            name="Rueckgabe",
            field_type=FieldTemplate.FieldType.NUMBER,
            required=True,
            sort_order=2,
        )
        ausgabe = _add_field(
            document_type,
            name="Ausgabe",
            field_type=FieldTemplate.FieldType.NUMBER,
            required=True,
            sort_order=1,
        )
        _add_field(
            document_type,
            name="Optionale Zahl",
            field_type=FieldTemplate.FieldType.NUMBER,
            required=False,
            sort_order=3,
        )

        form = TallyNumberDataForm(document_type=document_type)

        assert list(form.fields) == [ausgabe.slug, rueckgabe.slug]
        assert all(field.required for field in form.fields.values())
        assert all(field.widget.input_type == "number" for field in form.fields.values())

    def test_returns_clean_data_for_create_event(self, facility):
        document_type = DocumentType.objects.create(facility=facility, name="Spritzentausch")
        ausgabe = _add_field(
            document_type,
            name="Ausgabe",
            field_type=FieldTemplate.FieldType.NUMBER,
            required=True,
            sort_order=0,
        )
        rueckgabe = _add_field(
            document_type,
            name="Rueckgabe",
            field_type=FieldTemplate.FieldType.NUMBER,
            required=True,
            sort_order=1,
        )

        form = TallyNumberDataForm(
            {ausgabe.slug: "12", rueckgabe.slug: "0", "unbekannt": "wird ignoriert"},
            document_type=document_type,
        )

        assert form.is_valid()
        assert form.cleaned_data == {ausgabe.slug: 12, rueckgabe.slug: 0}

    def test_requires_every_number_field(self, facility):
        document_type = DocumentType.objects.create(facility=facility, name="Spritzentausch")
        ausgabe = _add_field(
            document_type,
            name="Ausgabe",
            field_type=FieldTemplate.FieldType.NUMBER,
            required=True,
            sort_order=0,
        )
        rueckgabe = _add_field(
            document_type,
            name="Rueckgabe",
            field_type=FieldTemplate.FieldType.NUMBER,
            required=True,
            sort_order=1,
        )

        form = TallyNumberDataForm(
            {ausgabe.slug: "keine-zahl", rueckgabe.slug: ""},
            document_type=document_type,
        )

        assert not form.is_valid()
        assert set(form.errors) == {ausgabe.slug, rueckgabe.slug}

    def test_decimal_error_does_not_make_corrected_integer_invalid_in_browser(self, facility):
        document_type = DocumentType.objects.create(facility=facility, name="Spritzentausch")
        ausgabe = _add_field(
            document_type,
            name="Ausgabe",
            field_type=FieldTemplate.FieldType.NUMBER,
            required=True,
            sort_order=0,
        )
        form = TallyNumberDataForm({ausgabe.slug: "1.5"}, document_type=document_type)

        assert not form.is_valid()
        assert form.fields[ausgabe.slug].widget.attrs["step"] == "any"

    def test_forms_for_different_types_use_unique_field_ids(self, facility):
        first_type = DocumentType.objects.create(facility=facility, name="Erste Zählung")
        second_type = DocumentType.objects.create(facility=facility, name="Zweite Zählung")
        anzahl = _add_field(
            first_type,
            name="Anzahl",
            field_type=FieldTemplate.FieldType.NUMBER,
            required=True,
            sort_order=0,
        )
        DocumentTypeField.objects.create(
            document_type=second_type,
            field_template=anzahl,
            sort_order=0,
        )

        first_form = TallyNumberDataForm(document_type=first_type)
        second_form = TallyNumberDataForm(document_type=second_type)

        assert first_form[anzahl.slug].id_for_label != second_form[anzahl.slug].id_for_label

    def test_rejects_document_type_with_non_number_required_field(self, facility):
        document_type = DocumentType.objects.create(facility=facility, name="Nicht inline-faehig")
        number = _add_field(
            document_type,
            name="Anzahl",
            field_type=FieldTemplate.FieldType.NUMBER,
            required=True,
            sort_order=0,
        )
        text = _add_field(
            document_type,
            name="Begruendung",
            field_type=FieldTemplate.FieldType.TEXT,
            required=True,
            sort_order=1,
        )

        form = TallyNumberDataForm(
            {number.slug: "3", text.slug: "umgehen"},
            document_type=document_type,
        )

        assert not form.is_valid()
        assert "__all__" in form.errors
        assert text.slug not in form.fields


@pytest.mark.django_db
def test_inline_partial_posts_numbers_to_existing_increment_endpoint(facility):
    document_type = DocumentType.objects.create(facility=facility, name="Spritzentausch")
    ausgabe = _add_field(
        document_type,
        name="Ausgabe",
        field_type=FieldTemplate.FieldType.NUMBER,
        required=True,
        sort_order=0,
    )
    form = TallyNumberDataForm(document_type=document_type)

    html = render_to_string(
        "core/zeitstrom/partials/tally_inline_form.html",
        {"tally_form": form, "document_type": document_type},
    )

    assert f'data-testid="tally-inline-form-{document_type.pk}"' in html
    assert 'hx-post="/partials/tally/increment/"' in html
    assert html.count(f'hx-target="#tally-action-{document_type.pk}"') == 2
    assert f'name="document_type" value="{document_type.pk}"' in html
    assert f'name="{ausgabe.slug}"' in html
    assert 'type="number"' in html
    assert "hx-vals=" in html
    assert '"cancel": "1"' in html


@pytest.mark.django_db
def test_tally_panel_opens_number_form_via_htmx_get(facility):
    document_type = DocumentType.objects.create(facility=facility, name="Spritzentausch")

    html = render_to_string(
        "core/zeitstrom/partials/tally_panel.html",
        {
            "tally_summary": {
                "rows": [
                    {
                        "document_type": document_type,
                        "count": 4,
                        "requires_number_input": True,
                    }
                ]
            }
        },
    )

    assert f'data-testid="tally-increment-{document_type.pk}"' in html
    assert f'data-testid="tally-count-{document_type.pk}"' in html
    assert 'hx-get="/partials/tally/increment/"' in html
    assert 'hx-post="/partials/tally/increment/"' not in html
    assert f'hx-target="#tally-action-{document_type.pk}"' in html

    action_html = render_to_string(
        "core/zeitstrom/partials/tally_action.html",
        {
            "row": {
                "document_type": document_type,
                "count": 4,
                "requires_number_input": True,
            }
        },
    )
    assert f'data-testid="tally-count-{document_type.pk}"' not in action_html


@pytest.mark.django_db
def test_tally_panel_keeps_direct_post_for_type_without_required_numbers(facility):
    document_type = DocumentType.objects.create(facility=facility, name="Kontakt")

    html = render_to_string(
        "core/zeitstrom/partials/tally_panel.html",
        {
            "tally_summary": {
                "rows": [
                    {
                        "document_type": document_type,
                        "count": 2,
                        "requires_number_input": False,
                    }
                ]
            }
        },
    )

    assert 'hx-post="/partials/tally/increment/"' in html
    assert 'hx-get="/partials/tally/increment/"' not in html
    assert 'hx-target="#tally-panel"' in html
