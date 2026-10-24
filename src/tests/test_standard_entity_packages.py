"""Public loader contracts for the standard entity fields."""

import pytest

from core.models import DocumentType
from core.packages.catalog import list_packages, parse_catalog_package
from core.packages.loader import load_package

STANDARD_FIELDS = {
    "client": ("Notizen", "elevated"),
    "case": ("Beschreibung", "elevated"),
    "episode": ("Beschreibung", "elevated"),
    "workitem": ("Details", "normal"),
}


@pytest.mark.django_db
@pytest.mark.parametrize("package_name", list_packages())
def test_catalog_loads_one_protected_standard_field_per_entity(facility, package_name):
    package = parse_catalog_package(package_name)
    load_package(facility, package)
    for entity_type, (label, sensitivity) in STANDARD_FIELDS.items():
        doc_type = DocumentType.objects.for_facility(facility).get(entity_type=entity_type)
        assert doc_type.is_active
        assignment = doc_type.fields.get()
        field = assignment.field_template
        assert field.name == label
        assert field.field_type == "textarea"
        assert field.is_encrypted
        assert field.sensitivity == sensitivity
        assert field.is_active
        assert not field.is_required
    report = load_package(facility, package)
    assert not report.errors
    for _name, section in report.sections():
        assert not section.created
        assert not section.updated
