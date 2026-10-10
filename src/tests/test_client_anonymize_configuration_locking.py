"""Concurrent transactions preserve the dynamic protection lock order (Refs #2525)."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event as ThreadEvent

import pytest
from django.db import connection, connections
from django.test.utils import CaptureQueriesContext

from tests.test_sensitivity_write_locking import run_connection, wait_for_completion_or_lock

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("path", ["manual", "batch"])
@pytest.mark.parametrize("first_writer", ["anonymization", "configuration"])
def test_anonymization_and_type_change_both_commit(
    facility, staff_user, lead_user, _encryption_key, path, first_writer
):
    from datetime import timedelta
    from types import SimpleNamespace

    from django.utils import timezone

    from core.models import Case, Client, DocumentType
    from core.services.client.main import (
        anonymize_client_now,
        anonymize_eligible_soft_deleted_clients,
    )
    from tests.test_sensitivity_transitions import make_record

    person, _field = make_record(facility, staff_user, "client")
    document_type_id = person.document_type_id
    Case.objects.create(facility=facility, client=person, title="Linked case")
    person.is_deleted = True
    person.deleted_at = timezone.now() - timedelta(days=8)
    person.save(update_fields=["is_deleted", "deleted_at"])
    locked, resume, started = ThreadEvent(), ThreadEvent(), ThreadEvent()
    pids = {}

    def anonymize():
        if path == "manual":
            return anonymize_client_now(person, lead_user)
        return anonymize_eligible_soft_deleted_clients(facility, SimpleNamespace(client_trash_days=7))

    def change_type():
        target = DocumentType.objects.get(pk=document_type_id)
        target.sensitivity = "elevated"
        target.save()

    actions = {"anonymization": anonymize, "configuration": change_type}
    second_writer = "configuration" if first_writer == "anonymization" else "anonymization"

    def first():
        def pause_after_person_lock(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            row_locked = 'FROM "core_client"' in sql and "FOR UPDATE" in sql
            row_written = sql.startswith('UPDATE "core_client"')
            if (row_locked or row_written) and not locked.is_set():
                locked.set()
                assert resume.wait(10), "First transaction was not released after locking the person"
            return result

        with connections["default"].execute_wrapper(pause_after_person_lock):
            return actions[first_writer]()

    def second():
        pids["changer"] = connections["default"].connection.info.backend_pid
        started.set()
        return actions[second_writer]()

    errors = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(run_connection, first)
        try:
            assert locked.wait(5), "First transaction did not lock the person"
            changer = pool.submit(run_connection, second)
            assert started.wait(5)
            assert wait_for_completion_or_lock(changer, pids["changer"])
        finally:
            resume.set()
        for future in (writer, changer):
            try:
                future.result(timeout=12)
            except Exception as exc:
                errors.append(f"{type(exc).__name__} (SQLSTATE {getattr(exc.__cause__, 'sqlstate', None)}): {exc}")
    if errors:
        pytest.fail("\n".join(errors), pytrace=False)
    person = Client.objects.get(pk=person.pk)
    assert person.is_active is False
    assert person.anonymized_at is not None
    assert person.data_json == {}
    assert DocumentType.objects.get(pk=document_type_id).sensitivity == "elevated"


@pytest.mark.parametrize("path", ["manual", "batch"])
def test_anonymization_gate_precedes_the_first_data_lock(facility, staff_user, lead_user, path):
    from datetime import timedelta
    from types import SimpleNamespace

    from django.utils import timezone

    from core.models import Client
    from core.services.client.main import (
        anonymize_client_now,
        anonymize_eligible_soft_deleted_clients,
    )

    person = Client.objects.create(
        facility=facility,
        created_by=staff_user,
        pseudonym="Gate-first person",
        is_deleted=True,
        deleted_at=timezone.now() - timedelta(days=8),
    )
    with CaptureQueriesContext(connection) as captured:
        if path == "manual":
            anonymize_client_now(person, lead_user)
        else:
            anonymize_eligible_soft_deleted_clients(facility, SimpleNamespace(client_trash_days=7))
    queries = [entry["sql"] for entry in captured.captured_queries]
    gates = [index for index, sql in enumerate(queries) if "pg_advisory_xact_lock_shared(2525" in sql]
    writes = [
        index
        for index, sql in enumerate(queries)
        if sql.startswith(("INSERT", "UPDATE", "DELETE")) or "FOR UPDATE" in sql
    ]
    assert gates and writes, queries
    assert gates[0] < writes[0], queries
