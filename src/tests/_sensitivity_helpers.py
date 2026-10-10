"""Explicit field configuration for tests of type visibility.

Refs #2525
"""

from core.models import DocumentType


def make_inherited_fields_explicit(document_type):
    """Preserve field storage while preparing a valid High type transition."""
    for assignment in document_type.fields.filter(field_template__sensitivity="").select_related("field_template"):
        field = assignment.field_template
        field.sensitivity = DocumentType.Sensitivity.NORMAL
        field.save(update_fields=["sensitivity"])
