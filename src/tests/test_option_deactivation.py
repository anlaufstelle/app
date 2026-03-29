"""Tests für Option-Deaktivierung (is_active Flag in options_json)."""

import pytest

from core.forms.events import DynamicEventDataForm
from core.models import DocumentType, DocumentTypeField, FieldTemplate

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ft_select(facility):
    """FieldTemplate SELECT mit aktiven und inaktiven Optionen."""
    return FieldTemplate.objects.create(
        facility=facility,
        name="Alterscluster",
        field_type=FieldTemplate.FieldType.SELECT,
        options_json=[
            {"slug": "u18", "label": "U18", "is_active": True},
            {"slug": "18-26", "label": "18-26", "is_active": True},
            {"slug": "veraltet", "label": "Veraltet", "is_active": False},
        ],
    )


@pytest.fixture
def ft_multi(facility):
    """FieldTemplate MULTI_SELECT mit aktiven und inaktiven Optionen."""
    return FieldTemplate.objects.create(
        facility=facility,
        name="Leistungen",
        field_type=FieldTemplate.FieldType.MULTI_SELECT,
        options_json=[
            {"slug": "beratung", "label": "Beratung", "is_active": True},
            {"slug": "essen", "label": "Essen", "is_active": True},
            {"slug": "sachspenden", "label": "Sachspenden", "is_active": False},
        ],
    )


@pytest.fixture
def doc_type_with_options(facility, ft_select, ft_multi):
    """DocumentType mit SELECT und MULTI_SELECT Feldern."""
    dt = DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.CONTACT,
        name="Kontakt mit Optionen",
    )
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_select, sort_order=0)
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_multi, sort_order=1)
    return dt


# ---------------------------------------------------------------------------
# choices Property
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestChoicesProperty:
    def test_choices_filters_inactive(self, ft_select):
        choices = ft_select.choices
        slugs = [slug for slug, _ in choices]
        assert "u18" in slugs
        assert "18-26" in slugs
        assert "veraltet" not in slugs

    def test_choices_filters_inactive_multi(self, ft_multi):
        choices = ft_multi.choices
        slugs = [slug for slug, _ in choices]
        assert "beratung" in slugs
        assert "essen" in slugs
        assert "sachspenden" not in slugs

    def test_backward_compat_missing_is_active(self, facility):
        """Optionen ohne is_active werden als aktiv behandelt."""
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Legacy-Feld",
            field_type=FieldTemplate.FieldType.SELECT,
            options_json=[
                {"slug": "alt", "label": "Alt"},
                {"slug": "neu", "label": "Neu"},
            ],
        )
        assert len(ft.choices) == 2
        assert ("alt", "Alt") in ft.choices
        assert ("neu", "Neu") in ft.choices

    def test_empty_options(self, facility):
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Leer",
            field_type=FieldTemplate.FieldType.SELECT,
            options_json=[],
        )
        assert ft.choices == []

    def test_all_inactive(self, facility):
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Alles-inaktiv",
            field_type=FieldTemplate.FieldType.SELECT,
            options_json=[
                {"slug": "a", "label": "A", "is_active": False},
                {"slug": "b", "label": "B", "is_active": False},
            ],
        )
        assert ft.choices == []


# ---------------------------------------------------------------------------
# DynamicEventDataForm
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFormNewEvent:
    """Neues Event: nur aktive Optionen als Choices."""

    def test_select_only_active_choices(self, doc_type_with_options, ft_select):
        form = DynamicEventDataForm(document_type=doc_type_with_options)
        field = form.fields[ft_select.slug]
        choice_slugs = [slug for slug, _ in field.choices if slug]
        assert "u18" in choice_slugs
        assert "18-26" in choice_slugs
        assert "veraltet" not in choice_slugs

    def test_multi_select_only_active_choices(self, doc_type_with_options, ft_multi):
        form = DynamicEventDataForm(document_type=doc_type_with_options)
        field = form.fields[ft_multi.slug]
        choice_slugs = [slug for slug, _ in field.choices if slug]
        assert "beratung" in choice_slugs
        assert "essen" in choice_slugs
        assert "sachspenden" not in choice_slugs


@pytest.mark.django_db
class TestFormEditEvent:
    """Bestehendes Event mit inaktivem Wert: Option sichtbar mit (deaktiviert)."""

    def test_select_inactive_value_shown(self, doc_type_with_options, ft_select):
        initial_data = {ft_select.slug: "veraltet"}
        form = DynamicEventDataForm(document_type=doc_type_with_options, initial_data=initial_data)
        field = form.fields[ft_select.slug]
        choice_slugs = [slug for slug, _ in field.choices if slug]
        choice_labels = {slug: label for slug, label in field.choices}

        assert "veraltet" in choice_slugs
        assert choice_labels["veraltet"] == "Veraltet (deaktiviert)"

    def test_multi_select_inactive_value_shown(self, doc_type_with_options, ft_multi):
        initial_data = {ft_multi.slug: ["beratung", "sachspenden"]}
        form = DynamicEventDataForm(document_type=doc_type_with_options, initial_data=initial_data)
        field = form.fields[ft_multi.slug]
        choice_slugs = [slug for slug, _ in field.choices if slug]
        choice_labels = {slug: label for slug, label in field.choices}

        assert "sachspenden" in choice_slugs
        assert choice_labels["sachspenden"] == "Sachspenden (deaktiviert)"
        assert choice_labels["beratung"] == "Beratung"

    def test_inactive_not_in_initial_not_shown(self, doc_type_with_options, ft_select):
        """Inaktive Option, die nicht im Event-Wert ist, wird nicht angezeigt."""
        initial_data = {ft_select.slug: "u18"}
        form = DynamicEventDataForm(document_type=doc_type_with_options, initial_data=initial_data)
        field = form.fields[ft_select.slug]
        choice_slugs = [slug for slug, _ in field.choices if slug]
        assert "veraltet" not in choice_slugs

    def test_edit_without_initial_data(self, doc_type_with_options, ft_select):
        """Kein initial_data -> nur aktive Optionen."""
        form = DynamicEventDataForm(document_type=doc_type_with_options)
        field = form.fields[ft_select.slug]
        choice_slugs = [slug for slug, _ in field.choices if slug]
        assert "veraltet" not in choice_slugs

    def test_bound_form_validates_inactive_value(self, doc_type_with_options, ft_multi):
        """POST mit inaktivem Wert: Formular akzeptiert ihn, wenn initial_data uebergeben wird."""
        initial_data = {ft_multi.slug: ["beratung", "sachspenden"]}
        post_data = {ft_multi.slug: ["beratung", "sachspenden"]}
        form = DynamicEventDataForm(post_data, document_type=doc_type_with_options, initial_data=initial_data)
        assert form.is_valid(), form.errors


# ---------------------------------------------------------------------------
# Export Regression
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestExportLabelResolution:
    """Export greift direkt auf options_json zu -- Labels fuer inaktive Optionen muessen aufgeloest werden."""

    def test_label_map_includes_inactive(self, ft_multi):
        """Simulate export label_map building -- includes inactive options."""
        label_map = {o["slug"]: o["label"] for o in ft_multi.options_json if isinstance(o, dict)}
        assert label_map["sachspenden"] == "Sachspenden"
        assert label_map["beratung"] == "Beratung"
