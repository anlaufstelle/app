"""Regression pins for three surviving mutations from earlier test reviews."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db.models.query import QuerySet
from django.utils import timezone

from core.models import FieldCondition, FieldTemplate, User, WorkItem
from core.services.client.merge import reject_client_merge, request_client_merge
from core.services.conditions import build_conditions_payload
from core.services.dashboard import build_focus_box

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def test_reject_merge_reports_foreign_facility_exactly(
    second_facility,
    client_identified,
    client_qualified,
    staff_user,
):
    """Pin ``core.services.client.merge.x_reject_client_merge__mutmut_21``."""
    reviewer = User.objects.create_user(
        username="fremde-pruefleitung-altluecke",
        role=User.Role.LEAD,
        facility=second_facility,
        can_confirm_deletion=True,
    )
    deletion_request = request_client_merge(
        client_identified,
        client_qualified,
        staff_user,
        reason="Doppelt angelegt",
    )

    with pytest.raises(ValidationError) as excinfo:
        reject_client_merge(deletion_request, reviewer)

    assert excinfo.value.messages == ["Antrag gehört zu einer anderen Einrichtung."]


def test_dropped_condition_does_not_hide_later_valid_condition(facility, doc_type_contact):
    """Pin ``core.services.conditions.x_build_conditions_payload__mutmut_31``."""
    source = doc_type_contact.fields.get(field_template__slug="dauer")
    target = doc_type_contact.fields.get(field_template__slug="notiz")
    foreign_template = FieldTemplate.objects.create(
        facility=facility,
        name="Nicht im Formular",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    dropped_target = doc_type_contact.fields.create(field_template=foreign_template, sort_order=2)
    dropped = FieldCondition.objects.create(
        target_field=dropped_target,
        depends_on_field=source,
        operator=FieldCondition.Operator.GREATER_THAN,
        value=5,
        group_index=0,
    )
    kept = FieldCondition.objects.create(
        target_field=target,
        depends_on_field=source,
        operator=FieldCondition.Operator.GREATER_THAN,
        value=5,
        group_index=0,
    )

    payload = build_conditions_payload(
        doc_type_contact,
        {"dauer", "notiz"},
        conditions=[dropped, kept],
    )

    assert payload is not None
    assert payload["conditions"] == [
        {
            "target": "notiz",
            "source": "dauer",
            "operator": "greater_than",
            "value": 5,
            "group": 0,
        }
    ]


def test_focus_box_orders_by_created_at_as_final_tiebreak(facility, staff_user, monkeypatch):
    """Pin ``core.services.dashboard.focus_box.x_build_focus_box__mutmut_10``."""
    WorkItem.objects.create(
        facility=facility,
        created_by=staff_user,
        item_type=WorkItem.ItemType.TASK,
        status=WorkItem.Status.OPEN,
        title="Überfällig",
        due_date=timezone.localdate() - timedelta(days=1),
    )
    calls: list[tuple[object, ...]] = []
    original_order_by = QuerySet.order_by

    def record_order_by(self, *fields):
        calls.append(fields)
        return original_order_by(self, *fields)

    monkeypatch.setattr(QuerySet, "order_by", record_order_by)

    build_focus_box(facility)

    assert ("due_date_bucket", "due_date", "priority_order", "-created_at") in calls
