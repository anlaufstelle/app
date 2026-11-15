"""Mutation regression pins for ``core.forms.dynamic.DynamicDataForm.grouped_fields``.

Source: targeted mutation delta "final" (end state ``b8f08a4d2``), part 2a.
Refs #1058

``grouped_fields`` orders blocks by their position and breaks ties by name and
by the assignment id, never by the iteration order of ``form.fields``.

Each test names the mutants it kills as ``xǁDynamicDataFormǁgrouped_fields__mutmut_N``.
"""

from __future__ import annotations

import uuid

import pytest
from django import forms

from core.forms.dynamic import DynamicDataForm
from core.models import DocumentType, DocumentTypeField, FieldGroup, FieldTemplate

pytestmark = pytest.mark.django_db


def _assign(doc_type, facility, name, *, pk, group=None, sort_order=0):
    template = FieldTemplate.objects.create(facility=facility, name=name, field_type=FieldTemplate.FieldType.TEXT)
    return DocumentTypeField.objects.create(
        id=uuid.UUID(int=pk), document_type=doc_type, field_template=template, field_group=group, sort_order=sort_order
    )


def test_a_field_unknown_to_the_assignments_sorts_at_position_zero_before_an_equal_name(facility):
    """Pins xǁDynamicDataFormǁgrouped_fields__mutmut_7, …_9, …_10, …_17, …_20, …_21, …_22, …_23 and …_24.

    A field added to the form without an assignment counts as a free field at
    position 0 and with an empty id. Next to an assigned field of the same
    position and name it therefore comes first.
    """
    doc_type = DocumentType.objects.create(facility=facility, name="Typ")
    _assign(doc_type, facility, "Dauer", pk=1)
    form = DynamicDataForm(document_type=doc_type, facility=facility)
    form.fields["Dauer"] = forms.CharField(required=False)

    grouped = form.grouped_fields()

    assert [(group, [bound.name for bound in bounds]) for group, bounds in grouped] == [(None, ["Dauer", "dauer"])]


def test_members_of_a_group_with_equal_positions_are_ordered_by_assignment_id(facility):
    """Pins xǁDynamicDataFormǁgrouped_fields__mutmut_36 and …_38.

    The order does not follow the iteration order of ``form.fields``: with the
    fields reversed, equal inner positions are still resolved by the id.
    """
    doc_type = DocumentType.objects.create(facility=facility, name="Typ")
    group = FieldGroup.objects.create(document_type=doc_type, name="Gruppe", sort_order=0)
    _assign(doc_type, facility, "Erst", pk=1, group=group)
    _assign(doc_type, facility, "Zweit", pk=2, group=group)
    form = DynamicDataForm(document_type=doc_type, facility=facility)
    form.fields = dict(reversed(list(form.fields.items())))

    grouped = form.grouped_fields()

    assert [(g.name, [bound.name for bound in bounds]) for g, bounds in grouped] == [("Gruppe", ["erst", "zweit"])]
