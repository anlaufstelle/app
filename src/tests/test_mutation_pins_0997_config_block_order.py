"""Block order result pins.

Source: mutation delta of the release-0.99.7 checkpoint, 4 mutants.
Groups: free-block annotations (3), cleared in-memory group reference (1).
No rls_test_role is needed.
"""

import pytest

from core.models import DocumentType, DocumentTypeField, FieldGroup, FieldTemplate
from core.services.config.block_order import _move_field_to_top_level, block_ordered_dtf_queryset

pytestmark = pytest.mark.django_db


@pytest.fixture
def blocks(facility):
    target = DocumentType.objects.create(facility=facility, name="Block target")
    group = FieldGroup.objects.create(document_type=target, name="Tied group", sort_order=0)
    free_template = FieldTemplate.objects.create(facility=facility, name="Free field")
    inner_template = FieldTemplate.objects.create(facility=facility, name="Grouped field")
    free = DocumentTypeField.objects.create(document_type=target, field_template=free_template, sort_order=0)
    inner = DocumentTypeField.objects.create(
        document_type=target, field_template=inner_template, field_group=group, sort_order=9
    )
    return target, group, free, inner


def test_queryset_exposes_free_and_grouped_block_flags(blocks):
    """Queryset exposes free and grouped block flags.

    Pins:
    * ``x_block_ordered_dtf_queryset__mutmut_37``
    * ``x_block_ordered_dtf_queryset__mutmut_39``
    * ``x_block_ordered_dtf_queryset__mutmut_41``
    """
    target, _, free, inner = blocks
    rows = list(block_ordered_dtf_queryset(target))
    assert [row.pk for row in rows] == [inner.pk, free.pk]
    assert {row.pk: row.block_is_free for row in rows} == {inner.pk: 0, free.pk: 1}


def test_move_clears_group_on_the_same_instance(blocks):
    """Move clears the group reference on the same instance.

    The private helper is required because move_field reloads and hides this instance.

    Pins:
    * ``x__move_field_to_top_level__mutmut_46``
    """
    target, group, free, inner = blocks
    assert inner.field_group == group
    _move_field_to_top_level(
        target, inner, [("group", str(group.pk)), ("field", str(inner.pk)), ("field", str(free.pk))]
    )
    assert inner.field_group_id is None
    assert inner.field_group is None
    assert DocumentTypeField.objects.get(pk=inner.pk).field_group_id is None
