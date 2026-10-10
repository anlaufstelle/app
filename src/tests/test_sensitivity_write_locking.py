"""Real PostgreSQL races between protection changes and writes (Refs #2525)."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event as ThreadEvent
from time import monotonic

import pytest
from django.db import close_old_connections, connections
from django.db.models.signals import pre_save
from django.test import RequestFactory
from django.utils import timezone

from core.forms.config import DocumentTypeForm
from core.models import DocumentTypeField, Event
from core.services.config import save_document_type, save_field_template
from core.services.events import create_event, update_event
from core.services.file_vault import is_encrypted_value, safe_decrypt
from tests.test_sensitivity_transitions import field_form, make_record, request_for

pytestmark = pytest.mark.django_db(transaction=True)


def run_connection(function):
    """Use a dedicated connection with bounded waits and guaranteed cleanup."""
    close_old_connections()
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SET statement_timeout = 10000")
            cursor.execute("SET lock_timeout = 8000")
        return function()
    finally:
        connections["default"].close()


def wait_for_completion_or_lock(future, backend_pid):
    """Observe actual database contention before releasing the paused writer."""
    deadline = monotonic() + 5
    while monotonic() < deadline:
        if future.done():
            future.result()
            return False
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", [backend_pid])
            row = cursor.fetchone()
        if row and row[0] == "Lock":
            return True
        ThreadEvent().wait(0.01)
    raise AssertionError("Protection change neither committed nor waited on a database lock")


@pytest.mark.parametrize("path", ["create", "update", "save", "bulk"])
@pytest.mark.parametrize("change", ["type", "field"])
def test_concurrent_create_and_protection_change_commit_safe_values(facility, lead_user, change, path):
    existing, field = make_record(facility, lead_user, "event")
    target = existing.document_type
    ready, resume, changing = ThreadEvent(), ThreadEvent(), ThreadEvent()
    pids = {}

    def pause_before_insert(sender, instance, **kwargs):
        if instance.facility_id == facility.pk:
            pids["writer"] = connections["default"].connection.info.backend_pid
            ready.set()
            assert resume.wait(10), "Writer was not released"

    def write():
        if path == "update":
            return update_event(event=existing, user=lead_user, data_json={field.slug: "ConcurrentSecret"}).pk
        if path in {"bulk", "save"}:
            instance = Event(
                facility=facility,
                created_by=lead_user,
                document_type=target,
                occurred_at=timezone.now(),
                data_json={field.slug: "ConcurrentSecret"},
                search_text="ConcurrentSecret",
            )
            if path == "bulk":

                def pause_sql(execute, sql, params, many, context):
                    if sql.startswith('INSERT INTO "core_event" '):
                        pause_before_insert(Event, instance)
                    return execute(sql, params, many, context)

                with connections["default"].execute_wrapper(pause_sql):
                    Event.objects.bulk_create([instance])
            else:
                instance.save()
            return instance.pk
        return create_event(
            facility=facility,
            user=lead_user,
            document_type=target,
            occurred_at=timezone.now(),
            data_json={field.slug: "ConcurrentSecret"},
        ).pk

    def change_protection():
        pids["changer"] = connections["default"].connection.info.backend_pid
        changing.set()
        request = request_for(RequestFactory(), facility, lead_user)
        if change == "type":
            form = DocumentTypeForm(
                {"name": target.name, "sensitivity": "high", "is_active": "on"},
                instance=target,
                facility=facility,
            )
            assert form.is_valid(), form.errors
            save_document_type(request, form, creating=False)
        else:
            save_field_template(request, field_form(field, facility), creating=False, facility=facility)

    pre_save.connect(pause_before_insert, sender=Event, weak=False)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(run_connection, write)
            try:
                assert ready.wait(5), "Writer did not reach INSERT"
                changer = pool.submit(run_connection, change_protection)
                assert changing.wait(5), "Protection connection did not start"
                assert pids["writer"] != pids["changer"]
                wait_for_completion_or_lock(changer, pids["changer"])
            finally:
                resume.set()
            created_id = writer.result(timeout=12)
            changer.result(timeout=12)
    finally:
        pre_save.disconnect(pause_before_insert, sender=Event)
    created = Event.objects.for_facility(facility).get(pk=created_id)
    assert created.search_text == ""
    assert is_encrypted_value(created.data_json[field.slug]) is (change == "field")
    assert safe_decrypt(created.data_json[field.slug]) == "ConcurrentSecret"


@pytest.mark.parametrize("method", ["create", "update"])
def test_opposite_bulk_order_does_not_deadlock_and_keeps_protection(facility, lead_user, method):
    from threading import Barrier

    from core.models import DocumentType

    first, field = make_record(facility, lead_user, "event")
    second, _ = make_record(facility, lead_user, "event", field=field)
    field.sensitivity = "high"
    field.save()
    start = Barrier(2)

    def write_batch(type_ids):
        types = [DocumentType.objects.for_facility(facility).get(pk=pk) for pk in type_ids]
        objects = [
            Event(
                facility=facility,
                created_by=lead_user,
                document_type=target,
                occurred_at=timezone.now(),
                data_json={field.slug: "BatchSecret"},
                search_text="BatchSecret",
            )
            for target in types
        ]
        if method == "update":
            ids = {first.document_type_id: first.pk, second.document_type_id: second.pk}
            for obj in objects:
                obj.pk = ids[obj.document_type_id]
        start.wait(timeout=5)
        if method == "update":
            Event.objects.bulk_update(objects, ["data_json"])
        else:
            Event.objects.bulk_create(objects)
        return [obj.pk for obj in objects]

    order = [first.document_type_id, second.document_type_id]
    with ThreadPoolExecutor(max_workers=2) as pool:
        forward = pool.submit(run_connection, lambda: write_batch(order))
        backward = pool.submit(run_connection, lambda: write_batch(list(reversed(order))))
        ids = forward.result(timeout=12) + backward.result(timeout=12)
    assert len(ids) == 4
    assert len(set(ids)) == (2 if method == "update" else 4)
    for record in Event.objects.for_facility(facility).filter(pk__in=ids):
        assert record.search_text == ""
        assert is_encrypted_value(record.data_json[field.slug])
        assert safe_decrypt(record.data_json[field.slug]) == "BatchSecret"


@pytest.mark.parametrize("path", ["type-save", "field-save", "assignment-save", "bulk"])
def test_model_and_bulk_paths_preserve_current_protection(facility, lead_user, path):
    record, field = make_record(facility, lead_user, "event")
    if path == "type-save":
        record.document_type.sensitivity = "high"
        record.document_type.save()
    elif path == "field-save":
        field.sensitivity = "high"
        field.save()
    elif path == "assignment-save":
        record.document_type.fields.get(field_template=field).delete()
        field.sensitivity = "high"
        field.save()
        DocumentTypeField.objects.create(document_type=record.document_type, field_template=field)
    else:
        field.sensitivity = "high"
        field.save()
        # The caller may have prepared this batch before the protection change.
        record.pk = None
        record._state.adding = True
        record.data_json = {field.slug: "SecretToken"}
        record.search_text = "SecretToken"
        Event.objects.bulk_create([record])
    record.refresh_from_db()
    assert record.search_text == ""
    assert is_encrypted_value(record.data_json[field.slug]) is (path != "type-save")


def test_data_only_model_update_also_persists_a_safe_index(facility, lead_user):
    record, field = make_record(facility, lead_user, "event")
    field.sensitivity = "high"
    field.save()
    record.data_json = {field.slug: "NewSecret"}
    record.save(update_fields=["data_json"])
    record.refresh_from_db()
    assert record.search_text == ""
    assert is_encrypted_value(record.data_json[field.slug])


def test_update_history_does_not_restore_a_stale_plaintext_before_value(facility, lead_user, rf):
    from core.models import EventHistory

    record, field = make_record(facility, lead_user, "event")
    save_field_template(
        request_for(rf, facility, lead_user), field_form(field, facility), creating=False, facility=facility
    )
    update_event(event=record, user=lead_user, data_json={field.slug: "NextSecret"})
    history = EventHistory.objects.get(event=record, action="update")
    assert is_encrypted_value(history.data_before[field.slug])
    assert safe_decrypt(history.data_before[field.slug]) == "SecretToken"
    assert is_encrypted_value(history.data_after[field.slug])


def test_history_rotation_serializes_its_protection_decision(facility, lead_user, monkeypatch):
    from io import StringIO

    from django.core.management import call_command

    from core.models import EventHistory
    from core.services.file_vault import rotation

    record, field = make_record(facility, lead_user, "event")
    history = EventHistory.objects.create(event=record, action="update", data_after={field.slug: "HistorySecret"})
    ready, resume, changing = ThreadEvent(), ThreadEvent(), ThreadEvent()
    pids = {}
    original = rotation._reencrypt_history_payload

    def pause_after_decision(payload, encrypted_slugs):
        result = original(payload, encrypted_slugs)
        if isinstance(payload, dict) and field.slug in payload and not ready.is_set():
            ready.set()
            assert resume.wait(10), "History rotation was not released"
        return result

    def rotate():
        call_command("reencrypt_fields", stdout=StringIO())

    def change():
        pids["changer"] = connections["default"].connection.info.backend_pid
        changing.set()
        request = request_for(RequestFactory(), facility, lead_user)
        save_field_template(request, field_form(field, facility), creating=False, facility=facility)

    monkeypatch.setattr(rotation, "_reencrypt_history_payload", pause_after_decision)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(run_connection, rotate)
        try:
            assert ready.wait(5), "History rotation did not reach its decision"
            changer = pool.submit(run_connection, change)
            assert changing.wait(5)
            blocked = wait_for_completion_or_lock(changer, pids["changer"])
        finally:
            resume.set()
        writer.result(timeout=12)
        changer.result(timeout=12)
    assert blocked, "Protection changed during the historical write decision"
    call_command("reencrypt_fields", stdout=StringIO())
    history.refresh_from_db()
    assert is_encrypted_value(history.data_after[field.slug])
    assert safe_decrypt(history.data_after[field.slug]) == "HistorySecret"


def test_protection_change_does_not_deadlock_with_an_audit_commit(facility, lead_user):
    from django.db import transaction

    from core.models import AuditLog
    from core.services.audit import audit_event

    record, _ = make_record(facility, lead_user, "event")
    ready, resume, changing = ThreadEvent(), ThreadEvent(), ThreadEvent()
    pids = {}

    def audit():
        with transaction.atomic():
            audit_event(AuditLog.Action.EVENT_UPDATE, user=lead_user, facility=facility, target_obj=record)
            ready.set()
            assert resume.wait(10), "Audit commit was not released"

    def change():
        pids["changer"] = connections["default"].connection.info.backend_pid
        changing.set()
        target = record.document_type
        form = DocumentTypeForm(
            {"name": target.name, "sensitivity": "elevated", "is_active": "on"}, instance=target, facility=facility
        )
        assert form.is_valid(), form.errors
        save_document_type(request_for(RequestFactory(), facility, lead_user), form, creating=False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(run_connection, audit)
        try:
            assert ready.wait(5)
            changer = pool.submit(run_connection, change)
            assert changing.wait(5)
            wait_for_completion_or_lock(changer, pids["changer"])
        finally:
            resume.set()
        writer.result(timeout=12)
        changer.result(timeout=12)
    record.refresh_from_db()
    assert record.search_text == ""


def test_shared_protection_gate_keeps_configuration_rows_unlocked(facility):
    from django.db import connection, transaction
    from django.test.utils import CaptureQueriesContext

    from core.services.dynamic_protection import lock_dynamic_protection

    with transaction.atomic(), CaptureQueriesContext(connection) as captured:
        lock_dynamic_protection(facility.pk)
    queries = [query["sql"] for query in captured.captured_queries]
    assert any("pg_advisory_xact_lock_shared" in sql for sql in queries)
    assert not any("FOR SHARE" in sql or "FOR UPDATE" in sql for sql in queries), queries


@pytest.mark.parametrize("change", ["type", "field"])
def test_configuration_change_blocks_writer_before_record_locks(facility, lead_user, change):
    from django.db import transaction

    record, field = make_record(facility, lead_user, "event")
    ready, resume, writing = ThreadEvent(), ThreadEvent(), ThreadEvent()
    pids, lock_queries = {}, []

    def configure():
        with transaction.atomic():
            if change == "type":
                record.document_type.sensitivity = "high"
                record.document_type.save()
            else:
                field.sensitivity = "high"
                field.save()
            ready.set()
            assert resume.wait(10), "Configuration transaction was not released"

    def write():
        pids["writer"] = connections["default"].connection.info.backend_pid
        writing.set()

        def capture_locks(execute, sql, params, many, context):
            if "FOR UPDATE" in sql or "FOR SHARE" in sql:
                lock_queries.append(sql)
            return execute(sql, params, many, context)

        with connections["default"].execute_wrapper(capture_locks):
            update_event(event=record, user=lead_user, data_json={field.slug: "AfterChange"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        changer = pool.submit(run_connection, configure)
        try:
            assert ready.wait(5), "Configuration change did not reach persistence"
            writer = pool.submit(run_connection, write)
            assert writing.wait(5)
            assert wait_for_completion_or_lock(writer, pids["writer"])
            assert lock_queries == [], "Writer took a row lock before the protection gate"
        finally:
            resume.set()
        changer.result(timeout=12)
        writer.result(timeout=12)
    record.refresh_from_db()
    assert record.search_text == ""
    assert is_encrypted_value(record.data_json[field.slug]) is (change == "field")
    assert safe_decrypt(record.data_json[field.slug]) == "AfterChange"
