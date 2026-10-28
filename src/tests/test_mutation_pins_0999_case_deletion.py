"""Pin scoped case and request locks and processing gate behavior.

Source: mutation delta of the release-0.99.9 checkpoint, 6 mutants.
Groups: facility changes before row locks (2), processing gate locks (3),
foreign actor rejection before case reads (1).
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils.translation import gettext

from core.models import Case, DeletionRequest
from core.services.case import deletion
from core.services.client import restriction

pytestmark = pytest.mark.django_db


@pytest.mark.postgres
def test_case_moved_before_its_lock_is_not_erased(case_trashed, second_facility, admin_user):
    """Refuse a case moved after the person lock: x__lock_case_chain__mutmut_25."""
    moved = False

    def move_after_person_lock(execute, sql, params, many, context):
        nonlocal moved
        result = execute(sql, params, many, context)
        if (
            not moved
            and 'FROM "core_client"' in sql
            and any(mode in sql for mode in ("FOR UPDATE", "FOR NO KEY UPDATE"))
        ):
            moved = True
            Case.objects.filter(pk=case_trashed.pk).update(facility=second_facility)
        return result

    with connection.execute_wrapper(move_after_person_lock), pytest.raises(ValidationError) as error:
        deletion.anonymize_case_now(case_trashed, admin_user)

    assert moved
    assert error.value.messages == [gettext("Nur Fälle im Papierkorb können anonymisiert werden.")]
    case_trashed.refresh_from_db()
    assert case_trashed.anonymized_at is None


@pytest.mark.postgres
def test_request_moved_before_its_lock_is_not_loaded(case_open, second_facility, staff_user, lead_user):
    """Approval cannot load a request moved after the case lock.

    x__lock_person_and_case_then_request__mutmut_37.
    """
    request, _created = deletion.create_or_get_case_deletion_request(case_open, staff_user, "Requested erasure")
    moved = False

    def move_after_case_lock(execute, sql, params, many, context):
        nonlocal moved
        result = execute(sql, params, many, context)
        if not moved and 'FROM "core_case"' in sql and any(mode in sql for mode in ("FOR UPDATE", "FOR NO KEY UPDATE")):
            moved = True
            DeletionRequest.objects.filter(pk=request.pk).update(facility=second_facility)
        return result

    with connection.execute_wrapper(move_after_case_lock), pytest.raises(DeletionRequest.DoesNotExist):
        deletion.approve_case_deletion(request, lead_user)

    assert moved
    request.refresh_from_db()
    assert request.status == DeletionRequest.Status.PENDING
    case_open.refresh_from_db()
    assert not case_open.is_deleted


@pytest.mark.postgres
def test_case_anonymization_processing_gate_locks_its_person(monkeypatch, case_trashed, admin_user):
    """The restriction gate takes a scoped person lock within immediate erasure.

    x_anonymize_case_now__mutmut_34, x_anonymize_case_now__mutmut_36,
    x_anonymize_case_now__mutmut_37.
    """
    original = restriction.assert_processing_allowed
    statements = []

    def observe(*args, **kwargs):
        with CaptureQueriesContext(connection) as queries:
            result = original(*args, **kwargs)
        statements.extend(query["sql"] for query in queries)
        return result

    monkeypatch.setattr(restriction, "assert_processing_allowed", observe)
    deletion.anonymize_case_now(case_trashed, admin_user)

    locks = [
        sql
        for sql in statements
        if 'FROM "core_client"' in sql and any(mode in sql for mode in ("FOR UPDATE", "FOR NO KEY UPDATE"))
    ]
    assert locks
    assert any(case_trashed.client_id.hex in sql and "WHERE" in sql for sql in locks)
    case_trashed.refresh_from_db()
    assert case_trashed.anonymized_at is not None


def test_foreign_actor_is_rejected_before_reading_cases(case_open, second_facility_user):
    """Foreign actors never reach a case read: x_soft_delete_case__mutmut_9."""
    with CaptureQueriesContext(connection) as queries, pytest.raises(ValidationError) as error:
        deletion.soft_delete_case(case_open, second_facility_user)

    assert error.value.messages == [deletion.CASE_OTHER_FACILITY_MESSAGE]
    assert not any('FROM "core_case"' in query["sql"] for query in queries)
    case_open.refresh_from_db()
    assert not case_open.is_deleted
