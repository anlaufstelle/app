"""Tests für FieldTemplate.default_value (Refs #624)."""

from datetime import date, time

import pytest
from django.core.exceptions import ValidationError

from core.forms.events import DynamicEventDataForm
from core.models import DocumentType, DocumentTypeField, FieldTemplate


@pytest.mark.django_db
class TestDefaultValueCasting:
    """get_default_initial() castet String nach Feldtyp."""

    def test_number(self, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Dauer", field_type="number", default_value="15")
        assert ft.get_default_initial() == 15

    def test_text(self, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Kurztext", field_type="text", default_value="Hallo")
        assert ft.get_default_initial() == "Hallo"

    def test_textarea(self, facility):
        ft = FieldTemplate.objects.create(
            facility=facility, name="Beschreibung", field_type="textarea", default_value="Standard"
        )
        assert ft.get_default_initial() == "Standard"

    def test_date(self, facility):
        ft = FieldTemplate.objects.create(
            facility=facility, name="Stichtag", field_type="date", default_value="2026-01-15"
        )
        assert ft.get_default_initial() == date(2026, 1, 15)

    def test_time(self, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Uhrzeit", field_type="time", default_value="09:30")
        assert ft.get_default_initial() == time(9, 30)

    def test_boolean_true(self, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Aktiv", field_type="boolean", default_value="true")
        assert ft.get_default_initial() is True

    def test_boolean_false(self, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Aktiv", field_type="boolean", default_value="false")
        assert ft.get_default_initial() is False

    def test_multi_select(self, facility):
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Themen",
            field_type="multi_select",
            options_json=[
                {"slug": "a", "label": "A"},
                {"slug": "b", "label": "B"},
                {"slug": "c", "label": "C"},
            ],
            default_value="a, b",
        )
        assert ft.get_default_initial() == ["a", "b"]

    def test_empty_default(self, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Leer", field_type="number", default_value="")
        assert ft.get_default_initial() is None

    def test_file_type_returns_none(self, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Datei", field_type="file", default_value="")
        assert ft.get_default_initial() is None


@pytest.mark.django_db
class TestDefaultValueValidation:
    """FieldTemplate.clean() verifiziert Default-Werte typgerecht."""

    def test_valid_number_default(self, facility):
        ft = FieldTemplate.objects.create(
            facility=facility, name="Dauer-Valid", field_type="number", default_value="15"
        )
        ft.full_clean()

    def test_invalid_number_raises(self, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Dauer-Inv", field_type="number")
        ft.default_value = "abc"
        with pytest.raises(ValidationError) as exc:
            ft.full_clean()
        assert "default_value" in exc.value.message_dict

    def test_invalid_date_raises(self, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Datum-Inv", field_type="date")
        ft.default_value = "irgendwas"
        with pytest.raises(ValidationError):
            ft.full_clean()

    def test_invalid_time_raises(self, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Uhrzeit-Inv", field_type="time")
        ft.default_value = "25:99"
        with pytest.raises(ValidationError):
            ft.full_clean()

    def test_invalid_boolean_raises(self, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Aktiv-Inv", field_type="boolean")
        ft.default_value = "vielleicht"
        with pytest.raises(ValidationError):
            ft.full_clean()

    def test_select_default_must_be_active_slug(self, facility):
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Status-SelInv",
            field_type="select",
            options_json=[{"slug": "open", "label": "Offen"}],
        )
        ft.default_value = "closed"
        with pytest.raises(ValidationError) as exc:
            ft.full_clean()
        assert "default_value" in exc.value.message_dict

    def test_select_default_valid_slug(self, facility):
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Status-SelOK",
            field_type="select",
            options_json=[{"slug": "open", "label": "Offen"}, {"slug": "closed", "label": "Zu"}],
            default_value="open",
        )
        ft.full_clean()

    def test_multi_select_partial_invalid_raises(self, facility):
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Themen-Inv",
            field_type="multi_select",
            options_json=[{"slug": "a", "label": "A"}, {"slug": "b", "label": "B"}],
        )
        ft.default_value = "a, c"
        with pytest.raises(ValidationError):
            ft.full_clean()

    def test_file_default_not_allowed(self, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Datei-Inv", field_type="file")
        ft.default_value = "unmöglich"
        with pytest.raises(ValidationError) as exc:
            ft.full_clean()
        assert "default_value" in exc.value.message_dict

    def test_empty_default_always_valid(self, facility):
        """Leerer Default ist für alle Feldtypen zulässig — auch für FILE."""
        for i, ftype in enumerate(("text", "number", "date", "time", "boolean", "file")):
            ft = FieldTemplate.objects.create(
                facility=facility, name=f"Feld-empty-{i}-{ftype}", field_type=ftype, default_value=""
            )
            ft.full_clean()


@pytest.mark.django_db
class TestDefaultValueInForm:
    """DynamicEventDataForm zeigt Default-Werte beim Neu-Anlegen."""

    def test_number_default_in_create(self, facility):
        doc_type = DocumentType.objects.create(facility=facility, name="Kontakt")
        ft = FieldTemplate.objects.create(facility=facility, name="Dauer", field_type="number", default_value="15")
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft)

        form = DynamicEventDataForm(document_type=doc_type, facility=facility)
        assert form.fields[ft.slug].initial == 15

    def test_initial_data_overrides_default(self, facility):
        """Edit-Flow: bestehender Wert gewinnt gegen default_value."""
        doc_type = DocumentType.objects.create(facility=facility, name="Kontakt")
        ft = FieldTemplate.objects.create(facility=facility, name="Dauer", field_type="number", default_value="15")
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft)

        form = DynamicEventDataForm(
            document_type=doc_type,
            facility=facility,
            initial_data={ft.slug: 42},
        )
        assert form.fields[ft.slug].initial == 42

    def test_empty_default_no_initial(self, facility):
        doc_type = DocumentType.objects.create(facility=facility, name="Kontakt")
        ft = FieldTemplate.objects.create(facility=facility, name="Dauer", field_type="number", default_value="")
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft)

        form = DynamicEventDataForm(document_type=doc_type, facility=facility)
        assert form.fields[ft.slug].initial in (None, "")

    def test_text_default_in_create(self, facility):
        doc_type = DocumentType.objects.create(facility=facility, name="Kontakt")
        ft = FieldTemplate.objects.create(
            facility=facility, name="Ort", field_type="text", default_value="Anlaufstelle"
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft)

        form = DynamicEventDataForm(document_type=doc_type, facility=facility)
        assert form.fields[ft.slug].initial == "Anlaufstelle"

    def test_select_default_in_create(self, facility):
        doc_type = DocumentType.objects.create(facility=facility, name="Kontakt")
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Stimmung",
            field_type="select",
            options_json=[
                {"slug": "gut", "label": "Gut"},
                {"slug": "ok", "label": "OK"},
                {"slug": "schlecht", "label": "Schlecht"},
            ],
            default_value="ok",
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft)

        form = DynamicEventDataForm(document_type=doc_type, facility=facility)
        assert form.fields[ft.slug].initial == "ok"

    def test_file_no_default_ever(self, facility):
        doc_type = DocumentType.objects.create(facility=facility, name="Kontakt")
        ft = FieldTemplate.objects.create(facility=facility, name="Datei", field_type="file")
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft)

        form = DynamicEventDataForm(document_type=doc_type, facility=facility)
        assert form.fields[ft.slug].initial in (None, "")
