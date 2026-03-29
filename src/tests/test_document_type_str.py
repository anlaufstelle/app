"""Tests for DocumentType and FieldTemplate __str__ methods."""

import pytest

from core.models import DocumentType, FieldTemplate


@pytest.mark.django_db
def test_document_type_str_includes_facility(facility):
    """DocumentType.__str__() includes facility name."""
    dt = DocumentType.objects.create(
        facility=facility,
        name="Kontakt",
        category=DocumentType.Category.CONTACT,
    )
    assert str(dt) == f"{facility.name} — Kontakt"


@pytest.mark.django_db
def test_field_template_str_includes_facility(facility):
    """FieldTemplate.__str__() includes facility name and field type."""
    ft = FieldTemplate.objects.create(
        facility=facility,
        name="Dauer",
        field_type=FieldTemplate.FieldType.NUMBER,
    )
    assert str(ft) == f"{facility.name} — Dauer (Zahl)"
