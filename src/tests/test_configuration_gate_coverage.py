"""Configuration changes wait before taking any rows (Refs #2525)."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event as ThreadEvent

import pytest
from django.db import connections, transaction
from django.test import RequestFactory

from core.forms.config import FieldGroupForm
from core.models import DocumentType, DocumentTypeField, Facility, FieldCondition, FieldGroup, FieldTemplate
from core.services.config import (
    create_field_condition,
    delete_field_condition,
    delete_field_group,
    move_field,
    reorder_blocks,
    reorder_fields,
    save_field_group,
    update_field_condition,
)
from core.services.dynamic_protection import lock_dynamic_protection
from tests.test_admin_config_audit import _request
from tests.test_sensitivity_write_locking import run_connection, wait_for_completion_or_lock

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "operation",
    [
        "reorder_blocks",
        "reorder_fields",
        "delete_group",
        "move_field",
        "save_group",
        "create_condition",
        "update_condition",
        "delete_condition",
    ],
)
def test_assignment_changes_wait_on_the_configuration_gate(facility, admin_user, operation):
    doc_type_contact = DocumentType.objects.create(facility=facility, name="Assignment gate")
    group = FieldGroup.objects.create(document_type=doc_type_contact, name="Gate group", sort_order=2)
    fields = [
        DocumentTypeField.objects.create(
            document_type=doc_type_contact,
            field_template=FieldTemplate.objects.create(
                facility=facility, name=f"Gate field {index}", field_type=FieldTemplate.FieldType.TEXT
            ),
            field_group=group if operation == "delete_group" else None,
            sort_order=index,
        )
        for index in range(2)
    ]
    condition = FieldCondition.objects.create(
        target_field=fields[0], depends_on_field=fields[1], operator=FieldCondition.Operator.EQUALS, value="old"
    )
    started = ThreadEvent()
    pids = {}

    def change():
        pids["changer"] = connections["default"].connection.info.backend_pid
        started.set()
        request = _request(RequestFactory(), admin_user)
        if operation == "reorder_blocks":
            reorder_blocks(
                request, doc_type_contact, [("field", fields[1].pk), ("field", fields[0].pk), ("group", group.pk)]
            )
        elif operation == "reorder_fields":
            reorder_fields(request, doc_type_contact, None, [fields[1].pk, fields[0].pk])
        elif operation == "delete_group":
            delete_field_group(request, group)
        elif operation == "save_group":
            form = FieldGroupForm(data={"name": "Saved group", "is_collapsible": False}, doc_type=doc_type_contact)
            assert form.is_valid(), form.errors
            save_field_group(request, form, doc_type=doc_type_contact, creating=True)
        elif operation in {"create_condition", "update_condition"}:
            arguments = dict(
                target_field=fields[0],
                depends_on_field=fields[1],
                operator=FieldCondition.Operator.EQUALS,
                value="new",
                group_index=0,
            )
            if operation == "create_condition":
                create_field_condition(request, doc_type_contact, **arguments)
            else:
                update_field_condition(request, condition, **arguments)
        elif operation == "delete_condition":
            delete_field_condition(request, condition)
        else:
            move_field(request, doc_type_contact, fields[0].pk, group.pk, ordered_dtf_ids=[fields[0].pk])

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            lock_dynamic_protection(facility.pk)
            changer = pool.submit(run_connection, change)
            assert started.wait(5)
            assert wait_for_completion_or_lock(changer, pids["changer"]), "Configuration changed while SHARED was held"
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT pg_stat_clear_snapshot()")
                cursor.execute("SELECT query FROM pg_stat_activity WHERE pid = %s", [pids["changer"]])
                assert "pg_advisory_xact_lock(2525" in cursor.fetchone()[0]
            for index, field in enumerate(fields):
                field.refresh_from_db()
                assert field.sort_order == index
                assert field.field_group_id == (group.pk if operation == "delete_group" else None)
        changer.result(timeout=12)

    for field in fields:
        field.refresh_from_db()
    if operation in {"reorder_blocks", "reorder_fields"}:
        assert [field.sort_order for field in fields] == [1, 0]
    elif operation == "delete_group":
        assert not FieldGroup.objects.filter(pk=group.pk).exists()
        assert all(field.field_group_id is None for field in fields)
    elif operation == "save_group":
        assert FieldGroup.objects.filter(document_type=doc_type_contact, name="Saved group").exists()
    elif operation in {"create_condition", "update_condition"}:
        assert FieldCondition.objects.filter(target_field=fields[0], value="new").count() == 1
    elif operation == "delete_condition":
        assert not FieldCondition.objects.filter(pk=condition.pk).exists()
    else:
        assert fields[0].field_group_id == group.pk


@pytest.mark.parametrize("operation", ["create", "update"])
def test_condition_changes_lock_the_actual_target_facility(facility, admin_user, operation):
    old_type = DocumentType.objects.create(facility=facility, name="Old condition type")
    scope = Facility.objects.create(organization=facility.organization, name="Condition target facility")
    new_type = DocumentType.objects.create(facility=scope, name="New condition type")

    def fields_for(document_type):
        return [
            DocumentTypeField.objects.create(
                document_type=document_type,
                field_template=FieldTemplate.objects.create(
                    facility=document_type.facility, name=f"Condition {index}", field_type=FieldTemplate.FieldType.TEXT
                ),
            )
            for index in range(2)
        ]

    old_fields, new_fields = fields_for(old_type), fields_for(new_type)
    condition = FieldCondition.objects.create(
        target_field=old_fields[0], depends_on_field=old_fields[1], operator=FieldCondition.Operator.EQUALS, value="old"
    )
    started, pids = ThreadEvent(), {}

    def change():
        pids["changer"] = connections["default"].connection.info.backend_pid
        started.set()
        request = _request(RequestFactory(), admin_user)
        arguments = dict(
            target_field=new_fields[0],
            depends_on_field=new_fields[1],
            operator=FieldCondition.Operator.EQUALS,
            value="new",
            group_index=0,
        )
        if operation == "create":
            return create_field_condition(request, old_type, **arguments).pk
        return update_field_condition(request, condition, **arguments).pk

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            lock_dynamic_protection(scope.pk)
            changer = pool.submit(run_connection, change)
            assert started.wait(5)
            assert wait_for_completion_or_lock(changer, pids["changer"]), "The target facility was not gated"
            assert not FieldCondition.objects.filter(target_field=new_fields[0], value="new").exists()
        result = changer.result(timeout=12)
    assert FieldCondition.objects.get(pk=result).target_field_id == new_fields[0].pk
