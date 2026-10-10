"""Concurrent transactions preserve the dynamic protection lock order (Refs #2525)."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event as ThreadEvent

import pytest
from django.db import connections

from tests.test_sensitivity_write_locking import run_connection, wait_for_completion_or_lock

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("decision", ["approve", "reject"])
@pytest.mark.parametrize("change", ["type", "field"])
@pytest.mark.parametrize("first_writer", ["rectification", "configuration"])
def test_rectification_and_type_change_both_commit(
    facility, staff_user, lead_user, _encryption_key, decision, change, first_writer
):
    """An outer row lock must not invert a delegated writer's protection gate."""
    from core.models import Client, DocumentType, RectificationRequest
    from core.services.client.rectification import approve_rectification, reject_rectification, request_rectification
    from tests.test_sensitivity_transitions import make_record

    person, field = make_record(facility, staff_user, "client")
    rr = request_rectification(person, staff_user, "pseudonym", "Review-corrected-person", "Review reason")
    locked, resume, started = ThreadEvent(), ThreadEvent(), ThreadEvent()
    pids = {}

    def decide():
        if decision == "approve":
            return approve_rectification(rr, lead_user)
        return reject_rectification(rr, lead_user, "Evidence insufficient")

    def change_protection():
        if change == "type":
            target = DocumentType.objects.get(pk=person.document_type_id)
            target.sensitivity = "elevated"
        else:
            target = type(field).objects.get(pk=field.pk)
            target.sensitivity = "high"
            target.is_encrypted = True
        target.save()

    actions = {"rectification": decide, "configuration": change_protection}
    second_writer = "configuration" if first_writer == "rectification" else "rectification"

    def first():
        def pause_after_person_lock(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if 'FROM "core_client"' in sql and "FOR UPDATE" in sql and not locked.is_set():
                locked.set()
                assert resume.wait(10), "First transaction was not released after the person lock"
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
            assert locked.wait(5), "First transaction did not acquire the person lock"
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
    expected_name = "Review-corrected-person" if decision == "approve" else person.pseudonym
    assert Client.objects.get(pk=person.pk).pseudonym == expected_name
    expected_status = (
        RectificationRequest.Status.APPROVED if decision == "approve" else RectificationRequest.Status.REJECTED
    )
    assert RectificationRequest.objects.get(pk=rr.pk).status == expected_status
    if change == "type":
        assert DocumentType.objects.get(pk=person.document_type_id).sensitivity == "elevated"
    else:
        field.refresh_from_db()
        assert field.sensitivity == "high"
        assert field.is_encrypted is True
