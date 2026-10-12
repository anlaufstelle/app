"""Pins for the public event conflict state builder.

Source: mutation delta of the release-0.99.7 checkpoint, 2 mutants.
Groups: skip non-selection fields (1); skip restricted selections (1)
without losing subsequent visible labels or exposing restricted replay values.
"""

import pytest
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate
from core.services.events.context import build_event_conflict_state

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "preceding_type, sensitivity",
    [
        (FieldTemplate.FieldType.TEXT, ""),
        (FieldTemplate.FieldType.MULTI_SELECT, DocumentType.Sensitivity.HIGH),
    ],
    ids=["text-first", "restricted-selection-first"],
)
def test_visible_selection_labels_survive_preceding_skipped_fields(facility, staff_user, preceding_type, sensitivity):
    """Skipped fields do not remove later visible conflict labels or expose restricted data.

    x_build_event_conflict_state__mutmut_17, x_build_event_conflict_state__mutmut_27.
    """
    document_type = DocumentType.objects.create(facility=facility, name="Conflict type")
    options = [
        {"slug": "a", "label": "Alpha", "is_active": True},
        {"slug": "b", "label": "Former label", "is_active": False},
    ]
    for position, (slug, kind, level) in enumerate(
        [
            ("preceding", preceding_type, sensitivity),
            ("visible", FieldTemplate.FieldType.MULTI_SELECT, ""),
        ]
    ):
        template = FieldTemplate.objects.create(
            facility=facility,
            name=slug,
            slug=slug,
            field_type=kind,
            sensitivity=level,
            options_json=options if kind == FieldTemplate.FieldType.MULTI_SELECT else [],
        )
        DocumentTypeField.objects.create(document_type=document_type, field_template=template, sort_order=position)
    event = Event.objects.create(
        facility=facility,
        document_type=document_type,
        created_by=staff_user,
        occurred_at=timezone.now(),
        data_json={"preceding": ["a"] if sensitivity else "Notes", "visible": ["a", "b"]},
    )
    state = build_event_conflict_state(staff_user, event)

    assert state["field_options"] == {
        "visible": [{"value": "a", "label": "Alpha"}, {"value": "b", "label": "Former label"}]
    }
    assert state["data_json"]["visible"] == ["a", "b"]
    if sensitivity:
        assert "preceding" not in state["data_json"]
    else:
        assert state["data_json"]["preceding"] == "Notes"
