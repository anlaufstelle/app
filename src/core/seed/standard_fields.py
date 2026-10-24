"""Shared standard field lookup for synthetic entity data."""

from core.models import DocumentType, Facility, FieldTemplate


def standard_field(facility: Facility, entity_type: str) -> tuple[DocumentType, FieldTemplate]:
    """Require the packaged encrypted textarea before generating sample values."""
    document_type = DocumentType.objects.for_facility(facility).get(
        slug=f"standard-{entity_type}", entity_type=entity_type
    )
    field = document_type.fields.get(field_template__field_type="textarea").field_template
    if not field.is_encrypted:
        raise ValueError("Standard sample fields must be encrypted.")
    return document_type, field
