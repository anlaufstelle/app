"""Pins for offline bundle metadata and relation loading.

Source: mutation delta of the release-0.99.8 checkpoint, 6 mutants.
Groups: assignment metadata (1), relation loading (4), unavailable owner (1).
"""

import re

import pytest
from django.db import connection
from django.http import Http404
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Case, Client, DocumentType, DocumentTypeField, Event, FieldTemplate, WorkItem
from core.services.system.offline import build_client_offline_bundle

pytestmark = pytest.mark.django_db


def test_unassigned_task_has_null_assignee(facility, client_identified, staff_user):
    """An unassigned task uses JSON null: x__serialize_workitem__mutmut_44."""
    task = WorkItem.objects.create(
        facility=facility, client=client_identified, created_by=staff_user, title="Team task"
    )
    bundle = build_client_offline_bundle(staff_user, facility, client_identified)
    item = next(item for item in bundle["workitems"] if item["pk"] == str(task.pk))
    assert item["assigned_to_pk"] is None


def test_bundle_relations_need_no_individual_fetches(facility, client_identified, staff_user, lead_user, admin_user):
    """Bundle values and permissions need no individual related-object lookups.

    x__visible_data_fields__mutmut_4, x_build_client_offline_bundle__mutmut_69,
    x_build_client_offline_bundle__mutmut_83, x_build_client_offline_bundle__mutmut_84.
    """
    doc = DocumentType.objects.create(facility=facility, name="Bundle type")
    data = {}
    cases = []
    tasks = []
    for index in range(3):
        field = FieldTemplate.objects.create(
            facility=facility, name=f"Field {index}", slug=f"field-{index}", field_type=FieldTemplate.FieldType.TEXT
        )
        DocumentTypeField.objects.create(document_type=doc, field_template=field, sort_order=index)
        data[field.slug] = f"Value {index}"
        cases.append(
            Case.objects.create(facility=facility, client=client_identified, title=f"Case {index}", lead_user=lead_user)
        )
        tasks.append(
            WorkItem.objects.create(
                facility=facility,
                client=client_identified,
                title=f"Task {index}",
                created_by=admin_user,
                assigned_to=lead_user,
            )
        )
    event = Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc,
        created_by=staff_user,
        occurred_at=timezone.now(),
        data_json=data,
    )
    with CaptureQueriesContext(connection) as queries:
        bundle = build_client_offline_bundle(staff_user, facility, client_identified)
    assert next(item for item in bundle["events"] if item["pk"] == str(event.pk))["data_fields"] == data
    assert {item["pk"] for item in bundle["cases"]} == {str(case.pk) for case in cases}
    assert all(item["lead_user_display"] == lead_user.username for item in bundle["cases"])
    assert {item["pk"] for item in bundle["workitems"]} == {str(task.pk) for task in tasks}
    assert all(item["can_edit"] is False for item in bundle["workitems"])
    individual_fetches = [
        query["sql"]
        for query in queries
        if re.search(r'FROM "(core_user|core_fieldtemplate)" WHERE .*"\1"\."id" =', query["sql"])
    ]
    assert not individual_fetches


def test_unavailable_person_has_explanatory_404(facility, client_identified, staff_user):
    """The service explains an unavailable owner: x_build_client_offline_bundle__mutmut_12."""
    Client.objects.filter(pk=client_identified.pk).update(is_active=False)
    with pytest.raises(Http404) as error:
        build_client_offline_bundle(staff_user, facility, client_identified)
    assert str(error.value) == "Person not found"
