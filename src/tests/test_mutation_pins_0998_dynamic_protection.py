"""Transaction-context and dynamic rewrite preservation pins.

Source: mutation delta of the release-0.99.8 checkpoint, 6 pinned mutants.
Groups: invalid batch contexts and diagnostics (4), eager type loading (1),
unchanged modification timestamps (1). Three candidates already have coverage;
two iterator-size candidates impose no public semantic requirement.
Related pattern: test_mutation_pins_0997_services_dynamic_protection.py.
"""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

import pytest
from django.db import connection, connections, transaction
from django.utils import timezone

from core.models import Event, FieldTemplate
from core.services import dynamic_protection as protection
from core.services.file_vault import is_encrypted_value, safe_decrypt
from tests.test_sensitivity_transitions import make_record

pytestmark = [pytest.mark.django_db, pytest.mark.postgres]

BATCH_ERROR = "Configuration batch has left its originating transaction or thread"


def test_rollback_only_batch_cannot_be_reused(facility):
    """A rollback-only transaction cannot reuse its configuration batch.

    x__batch_for__mutmut_4
    """
    with pytest.raises(transaction.TransactionManagementError, match=f"^{BATCH_ERROR}$"):
        with transaction.atomic():
            protection.lock_dynamic_protection(facility.pk, exclusive=True)
            with protection.configuration_batch(facility.pk):
                transaction.set_rollback(True)
                protection.lock_dynamic_protection_many([facility.pk])


def test_shared_connection_does_not_allow_cross_thread_batch_reuse(facility, monkeypatch):
    """A copied context cannot reuse a batch even with its original active connection.

    x__batch_for__mutmut_6
    """
    monkeypatch.setattr(protection, "connection", connections["default"])
    with transaction.atomic():
        protection.lock_dynamic_protection(facility.pk, exclusive=True)
        with protection.configuration_batch(facility.pk):
            copied = copy_context()
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(copied.run, protection.lock_dynamic_protection_many, [facility.pk])
                with pytest.raises(transaction.TransactionManagementError, match=f"^{BATCH_ERROR}$"):
                    future.result(timeout=5)


def test_expired_batch_reports_originating_context_error(facility):
    """A captured batch explains why reuse after its atomic block is invalid.

    x__batch_for__mutmut_13
    """
    with transaction.atomic():
        protection.lock_dynamic_protection(facility.pk, exclusive=True)
        with protection.configuration_batch(facility.pk):
            expired = copy_context()
    with pytest.raises(transaction.TransactionManagementError) as error:
        expired.run(protection.lock_dynamic_protection_many, [facility.pk])
    assert str(error.value) == BATCH_ERROR


@pytest.mark.django_db(transaction=True)
def test_autocommit_lock_explains_required_transaction():
    """The public multi-facility gate explains its enclosing-transaction requirement.

    x_lock_dynamic_protection_many__mutmut_3
    """
    with pytest.raises(transaction.TransactionManagementError) as error:
        protection.lock_dynamic_protection_many([])
    assert str(error.value) == "Dynamic protection requires an enclosing transaction"


def test_rewrite_loads_document_types_without_per_record_hydration(facility, lead_user):
    """Rewriting nonempty records does not reload each related document-type object.

    x_rewrite_dynamic_records__mutmut_21
    """
    records = [make_record(facility, lead_user, "event")[0] for _ in range(3)]
    targets = [record.document_type for record in records]
    statements = []

    def observe(execute, sql, params, many, context):
        statements.append(sql)
        return execute(sql, params, many, context)

    with transaction.atomic(), connection.execute_wrapper(observe):
        protection.lock_dynamic_protection(facility.pk, exclusive=True)
        protection.rewrite_dynamic_records(targets)
    assert not any(
        'FROM "core_documenttype"' in sql
        and '"core_documenttype"."name"' in sql
        and '"core_documenttype"."id" = %s' in sql
        for sql in statements
    )
    for record in records:
        record.refresh_from_db()
        assert record.data_json


@pytest.mark.parametrize("entity", ["client", "case", "event"])
def test_rewrite_updates_protection_without_changing_modification_time(facility, lead_user, entity):
    """Protection rewrites preserve user modification times while securing values.

    x_rewrite_dynamic_records__mutmut_33
    """
    record, field = make_record(facility, lead_user, entity)
    original_time = timezone.now() - timezone.timedelta(days=2)
    type(record).objects.filter(pk=record.pk).update(updated_at=original_time)
    FieldTemplate.objects.filter(pk=field.pk).update(is_encrypted=True, sensitivity="high")
    with transaction.atomic():
        protection.lock_dynamic_protection(facility.pk, exclusive=True)
        protection.rewrite_dynamic_records([record.document_type])
    record.refresh_from_db()
    assert record.updated_at == original_time
    assert is_encrypted_value(record.data_json[field.slug])
    assert safe_decrypt(record.data_json[field.slug]) == "SecretToken"
    if isinstance(record, Event):
        assert record.search_text == ""
