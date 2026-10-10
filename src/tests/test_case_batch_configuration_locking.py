"""Automatic case erasure respects concurrent protection changes (Refs #2525)."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event as ThreadEvent
from types import SimpleNamespace

import pytest
from django.db import connection, connections
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tests.test_sensitivity_write_locking import run_connection, wait_for_completion_or_lock

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("change", ["type", "field"])
@pytest.mark.parametrize("first_writer", ["anonymization", "configuration"])
def test_case_batch_and_protection_change_are_serialized(
    facility, staff_user, lead_user, _encryption_key, change, first_writer
):
    from core.models import AuditLog, Case, DocumentType, FieldTemplate
    from core.services.case.deletion import anonymize_eligible_soft_deleted_cases
    from tests.test_sensitivity_transitions import make_record

    case, field = make_record(facility, staff_user, "case")
    document_type_id = case.document_type_id
    case.is_deleted = True
    case.deleted_at = timezone.now() - timedelta(days=8)
    case.save(update_fields=["is_deleted", "deleted_at"])
    locked, resume, started = ThreadEvent(), ThreadEvent(), ThreadEvent()
    pids = {}

    def anonymize():
        return anonymize_eligible_soft_deleted_cases(facility, SimpleNamespace(client_trash_days=7))

    def configure():
        if change == "type":
            target = DocumentType.objects.get(pk=document_type_id)
            target.sensitivity = "elevated"
        else:
            target = FieldTemplate.objects.get(pk=field.pk)
            target.sensitivity = "high"
            target.is_encrypted = True
        target.save()

    actions = {"anonymization": anonymize, "configuration": configure}
    second_writer = "configuration" if first_writer == "anonymization" else "anonymization"

    def first():
        def pause_after_case_lock(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            first_gate = first_writer == "configuration" and "pg_advisory_xact_lock(2525" in sql
            first_row = first_writer == "anonymization" and 'FROM "core_case"' in sql and "FOR UPDATE" in sql
            if (first_gate or first_row) and not locked.is_set():
                locked.set()
                assert resume.wait(10), "First transaction was not released after taking its lock"
            return result

        with connections["default"].execute_wrapper(pause_after_case_lock):
            return actions[first_writer]()

    def second():
        pids["changer"] = connections["default"].connection.info.backend_pid
        started.set()
        return actions[second_writer]()

    errors = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(run_connection, first)
        try:
            assert locked.wait(5), "First transaction did not take its lock"
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
    case = Case.objects.get(pk=case.pk)
    assert case.is_deleted is True
    assert case.anonymized_at is not None
    assert case.document_type_id is None
    assert case.data_json == {}
    assert AuditLog.objects.filter(action=AuditLog.Action.CASE_ANONYMIZED, target_id=str(case.pk)).count() == 1
    if change == "type":
        assert DocumentType.objects.get(pk=document_type_id).sensitivity == "elevated"
    else:
        field.refresh_from_db()
        assert field.sensitivity == "high"
        assert field.is_encrypted is True


def test_case_batch_gate_precedes_the_first_data_lock(facility, staff_user, lead_user):
    from core.models import Case, Client
    from core.services.case.deletion import anonymize_eligible_soft_deleted_cases

    person = Client.objects.create(facility=facility, created_by=staff_user, pseudonym="Gate-first person")
    Case.objects.create(
        facility=facility,
        client=person,
        title="Gate-first case",
        is_deleted=True,
        deleted_at=timezone.now() - timedelta(days=8),
    )
    with CaptureQueriesContext(connection) as captured:
        anonymize_eligible_soft_deleted_cases(facility, SimpleNamespace(client_trash_days=7))
    queries = [entry["sql"] for entry in captured.captured_queries]
    gates = [index for index, sql in enumerate(queries) if "pg_advisory_xact_lock_shared(2525" in sql]
    writes = [
        index
        for index, sql in enumerate(queries)
        if sql.startswith(("INSERT", "UPDATE", "DELETE")) or "FOR UPDATE" in sql
    ]
    assert gates and writes, queries
    assert gates[0] < writes[0], queries
