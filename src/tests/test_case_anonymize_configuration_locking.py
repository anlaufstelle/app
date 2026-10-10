"""Case erasure keeps the protection gate ahead of data rows (Refs #2525)."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event as ThreadEvent

import pytest
from django.db import connection, connections
from django.test.utils import CaptureQueriesContext

from tests.test_sensitivity_write_locking import run_connection, wait_for_completion_or_lock

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("change", ["type", "field"])
@pytest.mark.parametrize("first_writer", ["anonymization", "configuration"])
def test_case_anonymization_and_protection_change_both_commit(
    facility, staff_user, lead_user, _encryption_key, change, first_writer
):
    from core.models import AuditLog, Case, DocumentType, FieldTemplate
    from core.services.case.deletion import anonymize_case_now
    from tests.test_sensitivity_transitions import make_record

    case, field = make_record(facility, staff_user, "case")
    document_type_id = case.document_type_id
    case.is_deleted = True
    case.save(update_fields=["is_deleted"])
    locked, resume, started = ThreadEvent(), ThreadEvent(), ThreadEvent()
    pids = {}

    def anonymize():
        return anonymize_case_now(case, lead_user)

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
            if 'FROM "core_case"' in sql and "FOR UPDATE" in sql and not locked.is_set():
                locked.set()
                assert resume.wait(10), "First transaction was not released after locking the case"
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
            assert locked.wait(5), "First transaction did not lock the case"
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


def test_case_anonymization_gate_precedes_the_first_data_lock(facility, staff_user, lead_user):
    from core.models import Case, Client
    from core.services.case.deletion import anonymize_case_now

    person = Client.objects.create(facility=facility, created_by=staff_user, pseudonym="Gate-first person")
    case = Case.objects.create(facility=facility, client=person, title="Gate-first case", is_deleted=True)
    with CaptureQueriesContext(connection) as captured:
        anonymize_case_now(case, lead_user)
    queries = [entry["sql"] for entry in captured.captured_queries]
    gates = [index for index, sql in enumerate(queries) if "pg_advisory_xact_lock_shared(2525" in sql]
    writes = [
        index
        for index, sql in enumerate(queries)
        if sql.startswith(("INSERT", "UPDATE", "DELETE")) or "FOR UPDATE" in sql
    ]
    assert gates and writes, queries
    assert gates[0] < writes[0], queries


def test_missing_case_keeps_the_trash_error_when_facility_reference_is_stale(facility, staff_user, lead_user):
    from copy import copy
    from uuid import uuid4

    from django.core.exceptions import ValidationError
    from django.utils.translation import gettext as _

    from core.models import AuditLog, Case, Client
    from core.services.case.deletion import anonymize_case_now

    person = Client.objects.create(facility=facility, created_by=staff_user, pseudonym="Existing person")
    case = Case.objects.create(facility=facility, client=person, title="Existing case", is_deleted=True)
    stale_case, stale_user = copy(case), copy(lead_user)
    stale_case.facility_id = stale_user.facility_id = uuid4()
    audit_count = AuditLog.objects.count()
    with pytest.raises(ValidationError) as caught:
        anonymize_case_now(stale_case, stale_user)
    assert caught.value.messages == [_("Nur Fälle im Papierkorb können anonymisiert werden.")]
    case.refresh_from_db()
    assert case.is_deleted is True
    assert case.anonymized_at is None
    assert AuditLog.objects.count() == audit_count
