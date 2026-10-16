"""Inactive dynamic-value preservation pins.

Source: mutation delta of the release-0.99.8 checkpoint, 3 mutants.
Groups: protected old values in merges (2), absent inactive values (1).
Related pattern: test_mutation_pins_0997_config_mutations.py.
"""

import pytest

from core.models import DocumentType, DocumentTypeField, FieldTemplate
from core.services.dynamic_data import merge_dynamic_data, preserve_inactive_values

pytestmark = pytest.mark.django_db


@pytest.fixture
def inactive_assignment(facility):
    target = DocumentType.objects.create(facility=facility, name="Inactive values")
    field = FieldTemplate.objects.create(facility=facility, name="Inactive text", is_active=False)
    DocumentTypeField.objects.create(document_type=target, field_template=field)
    return target, field


def test_merge_preserves_assigned_inactive_value(inactive_assignment):
    """Submitted inactive text cannot replace the stored value during merging.

    x_merge_dynamic_data__mutmut_9
    x_merge_dynamic_data__mutmut_12
    """
    target, field = inactive_assignment
    old = {field.slug: "Original"}
    submitted = {field.slug: "Replacement"}
    assert merge_dynamic_data(target, submitted, old_data=old, restricted_slugs=[]) == old
    assert old == {field.slug: "Original"}
    assert submitted == {field.slug: "Replacement"}


def test_absent_inactive_value_leaves_other_data_unchanged(inactive_assignment):
    """An inactive key absent from both inputs is harmless.

    x_preserve_inactive_values__mutmut_20
    """
    target, _ = inactive_assignment
    submitted = {"other": "Keep"}
    assert preserve_inactive_values(target, submitted, old_data={}) == submitted
    assert submitted == {"other": "Keep"}
