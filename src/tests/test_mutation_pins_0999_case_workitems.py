"""Pins for task update protection, audit details and validation messages.

Source: mutation delta of the release-0.99.9 checkpoint, 9 mutants.
Groups: bulk protection gate (1), inactive values (2), audit detail (1),
deferred payload (1), wrong entity validation (4).
"""

import re

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils.translation import gettext

from core.models import AuditLog, DocumentType, DocumentTypeField, FieldTemplate, WorkItem
from core.services.case.workitems import (
    bulk_assign_workitems,
    bulk_update_workitem_priority,
    create_workitem,
    update_workitem,
)

pytestmark = pytest.mark.django_db


@pytest.mark.postgres
@pytest.mark.parametrize("operation", ["priority", "assignment"])
def test_bulk_metadata_updates_do_not_take_configuration_gate(facility, staff_user, lead_user, operation):
    """Unlinked metadata-only batches skip the dynamic gate: x__locked_workitems__mutmut_1."""
    task = WorkItem.objects.create(facility=facility, created_by=staff_user, title="Unlinked task")
    with CaptureQueriesContext(connection) as queries:
        if operation == "priority":
            bulk_update_workitem_priority([task], staff_user, WorkItem.Priority.URGENT)
        else:
            bulk_assign_workitems([task], staff_user, lead_user)

    task.refresh_from_db()
    if operation == "priority":
        assert task.priority == WorkItem.Priority.URGENT
    else:
        assert task.assigned_to_id == lead_user.pk
    configuration_locks = [
        query["sql"] for query in queries if "pg_advisory_xact_lock" in query["sql"] and "2525" in query["sql"]
    ]
    assert not configuration_locks


@pytest.fixture
def typed_task(facility, staff_user):
    doc_type = DocumentType.objects.create(
        facility=facility, name="Task data", entity_type=DocumentType.EntityType.WORKITEM
    )
    field = FieldTemplate.objects.create(facility=facility, name="Stored detail", slug="detail", field_type="text")
    DocumentTypeField.objects.create(document_type=doc_type, field_template=field)
    task = create_workitem(
        facility, staff_user, title="Typed task", document_type=doc_type, data_json={field.slug: "Original"}
    )
    return task, field


@pytest.mark.parametrize("payload", [{}, {"detail": "Tampered"}], ids=["omitted", "tampered"])
def test_update_preserves_stored_inactive_values(typed_task, staff_user, payload):
    """Direct updates retain inactive source values even when omitted or forged.

    x_update_workitem__mutmut_185, x_update_workitem__mutmut_191.
    """
    task, field = typed_task
    field.is_active = False
    field.save(update_fields=["is_active"])

    update_workitem(task, staff_user, data_json=payload)

    task.refresh_from_db()
    assert task.data_json == {field.slug: "Original"}


def test_rename_audit_omits_absent_data_loss(sample_workitem, staff_user):
    """An ordinary rename logs only changed fields: x_update_workitem__mutmut_265."""
    update_workitem(sample_workitem, staff_user, title="Renamed task")

    audit = AuditLog.objects.get(action=AuditLog.Action.WORKITEM_UPDATE, target_id=str(sample_workitem.pk))
    assert audit.detail == {"changed_fields": ["title"]}


def test_dynamic_update_does_not_reload_deferred_payload(typed_task, staff_user):
    """The locked state carries its payload: x_update_workitem__mutmut_71."""
    task, field = typed_task
    with CaptureQueriesContext(connection) as queries:
        update_workitem(task, staff_user, data_json={field.slug: "Updated"})

    task.refresh_from_db()
    assert task.data_json == {field.slug: "Updated"}
    assert not any(
        re.search(r'^SELECT "core_workitem"\."id", "core_workitem"\."data_json" FROM "core_workitem" WHERE', q["sql"])
        for q in queries
    )


@pytest.mark.parametrize("operation", ["create", "update"])
def test_wrong_entity_type_keeps_task_validation_message(facility, staff_user, sample_workitem, operation):
    """Wrong entity types produce a readable task-specific validation error.

    x_create_workitem__mutmut_62, x_create_workitem__mutmut_74,
    x_update_workitem__mutmut_184, x_update_workitem__mutmut_198.
    """
    wrong_type = DocumentType.objects.create(
        facility=facility, name="Client profile", entity_type=DocumentType.EntityType.CLIENT
    )
    with pytest.raises(ValidationError) as error:
        if operation == "create":
            create_workitem(facility, staff_user, title="Invalid task", document_type=wrong_type)
        else:
            update_workitem(sample_workitem, staff_user, document_type=wrong_type, data_json={})

    assert error.value.messages == [gettext("Der Dokumentationstyp muss für Aufgaben gelten.")]
