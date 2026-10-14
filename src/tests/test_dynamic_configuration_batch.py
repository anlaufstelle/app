"""Explicit package batching preserves protection and standalone writes (Refs #2598)."""

from collections import Counter
from contextlib import contextmanager, nullcontext
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone

from core.models import Case, Client, DocumentType, DocumentTypeField, Event, FieldCondition, FieldGroup, FieldTemplate
from core.packages.catalog import parse_catalog_package
from core.packages.loader import PackageLoadError, load_package
from core.packages.schema import parse_package
from core.services import dynamic_protection
from core.services.file_vault import is_encrypted_value, safe_decrypt
from tests._factories import make_client, make_document_type, make_event
from tests.test_sensitivity_transitions import make_record

pytestmark = pytest.mark.django_db


@contextmanager
def sql_counts():
    counts = Counter()
    statements = []

    def observe(execute, sql, params, many, context):
        statements.append(sql)
        if "pg_advisory_xact_lock(" in sql:
            counts["exclusive_gate"] += 1
        if "pg_advisory_xact_lock_shared(" in sql:
            counts["shared_gate"] += 1
        for model in (Client, Case, Event):
            if f'FROM "{model._meta.db_table}"' in sql and 'FOR UPDATE OF "' in sql and 'document_type_id" IN' in sql:
                counts[model.__name__] += 1
        return execute(sql, params, many, context)

    with connection.execute_wrapper(observe):
        yield counts, statements


def batch(facility):
    return dynamic_protection.configuration_batch(facility.pk)


def package_for(records, field, *, sensitivity="high", encrypted=True, type_sensitivity="normal"):
    return parse_package(
        {
            "schema_version": 1,
            "field_templates": [
                {
                    "slug": field.slug,
                    "name": field.name,
                    "field_type": field.field_type,
                    "sensitivity": sensitivity,
                    "is_encrypted": encrypted,
                },
                {"slug": "show-secret", "name": "Show secret", "field_type": "boolean"},
            ],
            "document_types": [
                {
                    "slug": record.document_type.slug,
                    "name": record.document_type.name,
                    "entity_type": record.document_type.entity_type,
                    "sensitivity": type_sensitivity,
                    "groups": [{"slug": "details", "name": "Details", "sort_order": 0}],
                    "field_refs": [
                        {"template_slug": "show-secret", "sort_order": 1},
                        {"template_slug": field.slug, "group_slug": "details", "sort_order": 2},
                    ],
                    "conditions": [
                        {"target": field.slug, "depends_on": "show-secret", "operator": "equals", "value": True}
                    ],
                }
                for record in records
            ],
        }
    )


@pytest.mark.parametrize("name", ["default", "jugendarbeit"])
def test_catalog_load_takes_one_gate_and_one_ordered_rewrite(facility, name):
    saves = Counter()
    with sql_counts() as (counts, statements):
        with patch.object(
            dynamic_protection, "configuration_write", wraps=dynamic_protection.configuration_write
        ) as writes:
            with patch.object(
                dynamic_protection, "rewrite_dynamic_records", wraps=dynamic_protection.rewrite_dynamic_records
            ) as rewrites:
                originals = {
                    model: model.save
                    for model in (FieldTemplate, DocumentType, DocumentTypeField, FieldGroup, FieldCondition)
                }
                with pytest.MonkeyPatch.context() as monkeypatch:
                    for model, original in originals.items():

                        def counted(self, *args, _original=original, _name=model.__name__, **kwargs):
                            saves[_name] += 1
                            return _original(self, *args, **kwargs)

                        monkeypatch.setattr(model, "save", counted)
                    report = load_package(facility, parse_catalog_package(name))
                print(
                    f"PACKAGE={name} SAVES={dict(saves)} WRITES={writes.call_count} "
                    f"REQUESTS={rewrites.call_count} SQL={dict(counts)}"
                )
    assert report.errors == []
    assert counts["exclusive_gate"] == 1
    assert counts["shared_gate"] == 0
    assert [counts[model.__name__] for model in (Client, Case, Event)] == [1, 1, 1]
    gate = next(i for i, sql in enumerate(statements) if "pg_advisory_xact_lock(" in sql)
    row_locks = [i for i, sql in enumerate(statements) if "FOR UPDATE" in sql or "FOR NO KEY UPDATE" in sql]
    assert gate < min(row_locks)
    scans = [
        model.__name__
        for sql in statements
        for model in (Client, Case, Event)
        if f'FROM "{model._meta.db_table}"' in sql and 'FOR UPDATE OF "' in sql and 'document_type_id" IN' in sql
    ]
    assert scans == ["Client", "Case", "Event"]


@pytest.mark.parametrize("mode", ["batch", "standalone"])
def test_assignment_moves_and_deletes_rewrite_both_types(facility, lead_user, mode):
    first, field = make_record(facility, lead_user, "event")
    second, _ = make_record(facility, lead_user, "event", field=field)
    assignment = first.document_type.fields.get(field_template=field)
    second.document_type.fields.get(field_template=field).delete()
    field.is_encrypted = True
    field.save()
    Event.objects.filter(pk__in=[first.pk, second.pk]).update(
        data_json={field.slug: "Retained"}, search_text="Retained"
    )
    for change in ("move", "delete"):
        with transaction.atomic():
            dynamic_protection.lock_dynamic_protection(facility.pk, exclusive=True)
            with batch(facility) if mode == "batch" else nullcontext():
                if change == "move":
                    assignment.document_type = second.document_type
                    assignment.save()
                else:
                    assignment.delete()
            for record in (first, second):
                record.refresh_from_db()
                assert record.search_text == ""
                assert safe_decrypt(record.data_json[field.slug]) == "Retained"
                assert is_encrypted_value(record.data_json[field.slug]) is (record.pk == second.pk)
    with sql_counts() as (counts, _):
        first.document_type.sensitivity = "elevated"
        first.document_type.save()
        second.document_type.sensitivity = "elevated"
        second.document_type.save()
    assert counts["exclusive_gate"] == 2
    assert [counts[model.__name__] for model in (Client, Case, Event)] == [2, 2, 2]


def configuration_snapshot(facility):
    fields = list(FieldTemplate.objects.for_facility(facility).order_by("slug").values())
    types = list(DocumentType.objects.for_facility(facility).order_by("slug").values())
    for row in fields + types:
        for key in ("id", "facility_id", "created_at", "updated_at"):
            row.pop(key, None)
    groups = list(
        FieldGroup.objects.filter(document_type__facility=facility)
        .order_by("document_type__slug", "slug")
        .values("document_type__slug", "slug", "name", "sort_order", "is_collapsible")
    )
    assignments = list(
        DocumentTypeField.objects.filter(document_type__facility=facility)
        .order_by("document_type__slug", "field_template__slug")
        .values("document_type__slug", "field_template__slug", "field_group__slug", "sort_order")
    )
    conditions = list(
        FieldCondition.objects.filter(target_field__document_type__facility=facility)
        .order_by("target_field__document_type__slug", "target_field__field_template__slug", "group_index")
        .values(
            "target_field__document_type__slug",
            "target_field__field_template__slug",
            "depends_on_field__field_template__slug",
            "operator",
            "value",
            "group_index",
        )
    )
    return fields, types, groups, assignments, conditions


@pytest.mark.parametrize("existing,change", [(False, "field"), (True, "field"), (True, "type")])
def test_loader_matches_standalone_configuration_and_protection(facility, other_facility, lead_user, existing, change):
    if existing:
        records, counterparts = [], []
        for target_facility, destinations in ((facility, records), (other_facility, counterparts)):
            field = FieldTemplate.objects.create(
                facility=target_facility, name="Batch secret", slug="batch-secret", is_encrypted=(change == "field")
            )
            for index, entity in enumerate(("client", "client", "case", "case", "event", "event")):
                target = make_document_type(
                    facility=target_facility, name=f"Batch type {index}", slug=f"batch-type-{index}", entity_type=entity
                )
                DocumentTypeField.objects.create(document_type=target, field_template=field)
                kwargs = {
                    "document_type": target,
                    "data_json": {field.slug: "SecretToken"},
                    "is_deleted": bool(index % 2),
                }
                if entity == "client":
                    record = make_client(
                        facility=target_facility, created_by=lead_user, pseudonym=f"Person-{index}", **kwargs
                    )
                elif entity == "event":
                    record = make_event(
                        facility=target_facility,
                        created_by=lead_user,
                        client=None,
                        occurred_at=timezone.now(),
                        **kwargs,
                    )
                else:
                    owner = make_client(facility=target_facility, created_by=lead_user, pseudonym=f"Owner-{index}")
                    record = Case.objects.create(
                        facility=target_facility, created_by=lead_user, client=owner, title="Batch case", **kwargs
                    )
                if change == "field":
                    # Imported retained plaintext is protected by the package's sensitivity change.
                    type(record).objects.filter(pk=record.pk).update(data_json={field.slug: "SecretToken"})
                destinations.append(record)
        field = FieldTemplate.objects.for_facility(facility).get(slug="batch-secret")
        package = (
            package_for(records, field)
            if change == "field"
            else package_for(records, field, sensitivity="normal", encrypted=False, type_sensitivity="elevated")
        )
    else:
        package = parse_catalog_package("jugendarbeit")
    with patch.object(dynamic_protection, "configuration_batch", side_effect=lambda facility_id: nullcontext()):
        standalone = load_package(other_facility, package)
    with sql_counts() as (counts, _):
        batched = load_package(facility, package)
    assert counts["exclusive_gate"] == 1
    assert counts["shared_gate"] == 0
    assert [counts[model.__name__] for model in (Client, Case, Event)] == [1, 1, 1]
    assert standalone.errors == batched.errors == []
    assert configuration_snapshot(facility) == configuration_snapshot(other_facility)
    if existing:
        for record, counterpart in zip(records, counterparts, strict=True):
            record.refresh_from_db()
            counterpart.refresh_from_db()
            assert record.is_deleted == counterpart.is_deleted
            assert record.document_type.sensitivity == counterpart.document_type.sensitivity
            assert record.data_json.keys() == counterpart.data_json.keys()
            for slug, stored in record.data_json.items():
                expected = counterpart.data_json[slug]
                assert is_encrypted_value(stored) is is_encrypted_value(expected) is (change == "field")
                assert safe_decrypt(stored) == safe_decrypt(expected) == "SecretToken"
            assert getattr(record, "search_text", "") == getattr(counterpart, "search_text", "") == ""


@pytest.mark.parametrize("outcome", ["dry_run", "validation", "integrity", "rewrite"])
def test_loader_rollback_clears_batch_and_keeps_savepoints(facility, lead_user, monkeypatch, outcome):
    record, field = make_record(facility, lead_user, "event")
    # The loader preserves its existing immutable-encryption contract.
    FieldTemplate.objects.filter(pk=field.pk).update(is_encrypted=True)
    field.refresh_from_db()
    package = package_for([record], field)
    before = configuration_snapshot(facility)
    original_save = FieldGroup.save
    if outcome == "validation":

        def fail_group(self, *args, **kwargs):
            raise ValidationError("Injected package row failure")

        monkeypatch.setattr(FieldGroup, "save", fail_group)
    elif outcome == "integrity":
        # PostgreSQL must abort the row savepoint; a Python-only stub cannot prove recovery.
        def fail_group(self, *args, **kwargs):
            original_save(self, *args, **kwargs)
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO core_fieldgroup (id) VALUES (%s)", [self.pk])

        monkeypatch.setattr(FieldGroup, "save", fail_group)
    if outcome == "rewrite":

        def fail_record(*args, **kwargs):
            raise RuntimeError("Injected rewrite failure")

        monkeypatch.setattr(Event, "save", fail_record)
    if outcome == "dry_run":
        load_package(facility, package, dry_run=True)
    else:
        expected = RuntimeError if outcome == "rewrite" else PackageLoadError
        with pytest.raises(expected):
            load_package(facility, package)
    assert configuration_snapshot(facility) == before
    record.refresh_from_db()
    assert record.data_json == {field.slug: "SecretToken"}
    assert record.search_text == "SecretToken"
    monkeypatch.undo()
    field.sensitivity = "elevated"
    with sql_counts() as (counts, _):
        field.save()
    assert counts["exclusive_gate"] == 1
    assert counts["Event"] >= 1


@pytest.mark.parametrize("gate", ["none", "shared"])
def test_batch_requires_preexisting_exclusive_gate(facility, gate):
    with transaction.atomic():
        if gate == "shared":
            dynamic_protection.lock_dynamic_protection(facility.pk)
        with pytest.raises(transaction.TransactionManagementError, match="exclusive"):
            with batch(facility):
                pytest.fail("Batch accepted an absent exclusive gate")


def test_batch_never_reuses_another_facility_or_nested_context(facility, other_facility):
    with transaction.atomic():
        dynamic_protection.lock_dynamic_protection(facility.pk, exclusive=True)
        with batch(facility):
            with sql_counts() as (counts, _):
                FieldTemplate.objects.create(facility=other_facility, name="Foreign template")
            assert counts["exclusive_gate"] == 1
            with pytest.raises(transaction.TransactionManagementError, match="nested"):
                with batch(other_facility):
                    pytest.fail("Nested batch accepted")


@pytest.mark.django_db(transaction=True)
def test_batch_rejects_autocommit_and_expired_transaction(facility):
    with pytest.raises(transaction.TransactionManagementError):
        with batch(facility):
            pytest.fail("Autocommit batch accepted")
    from contextvars import copy_context

    with transaction.atomic():
        dynamic_protection.lock_dynamic_protection(facility.pk, exclusive=True)
        with batch(facility):
            expired = copy_context()
    with transaction.atomic():
        with pytest.raises(transaction.TransactionManagementError):
            expired.run(lambda: FieldTemplate.objects.create(facility=facility, name="Expired batch"))
    assert not FieldTemplate.objects.for_facility(facility).filter(name="Expired batch").exists()
    with sql_counts() as (counts, _):
        FieldTemplate.objects.create(facility=facility, name="Fresh standalone")
    assert counts["exclusive_gate"] == 1


@pytest.mark.parametrize("invalid", ["deferred", "facility", "type", "high", "range", "report_key", "options"])
def test_batch_keeps_each_row_validation_and_message(facility, other_facility, lead_user, invalid):
    record, field = make_record(facility, lead_user, "event")
    if invalid == "deferred":
        field = FieldTemplate.objects.only("pk").get(pk=field.pk)
    elif invalid == "facility":
        field.facility = other_facility
    elif invalid == "type":
        field.field_type = "boolean"
    elif invalid == "high":
        FieldTemplate.objects.filter(pk=field.pk).update(sensitivity="high", is_encrypted=True)
        record.document_type.sensitivity = "high"
        record.document_type.save()
        field.sensitivity = ""
    elif invalid == "report_key":
        from tests.test_config_reactivation_conflicts import binding_pair

        _, field, _ = binding_pair(facility)
        field.is_active = True
    elif invalid == "options":
        field = FieldTemplate.objects.create(
            facility=facility,
            name="Used option",
            field_type="multi_select",
            options_json=[{"slug": "used", "label": "Used"}],
        )
        record, _ = make_record(facility, lead_user, "event", field=field, value=["used"])
        field.options_json = []
    else:
        field = FieldTemplate.objects.create(facility=facility, name="Bounded", field_type="number")
        field.min_value = 5
        field.max_value = 2
    expected = ValueError if invalid == "deferred" else ValidationError
    with pytest.raises(expected) as standalone:
        field.save()
    with transaction.atomic():
        dynamic_protection.lock_dynamic_protection(facility.pk, exclusive=True)
        with batch(facility):
            with pytest.raises(expected) as batched:
                field.save()
    assert str(batched.value) == str(standalone.value)


@pytest.mark.django_db(transaction=True)
def test_copied_batch_context_cannot_cross_threads(facility):
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context

    from tests.test_sensitivity_write_locking import run_connection

    with transaction.atomic():
        dynamic_protection.lock_dynamic_protection(facility.pk, exclusive=True)
        with batch(facility):
            copied = copy_context()
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    run_connection,
                    lambda: copied.run(lambda: FieldTemplate.objects.create(facility=facility, name="Copied thread")),
                )
                with pytest.raises(transaction.TransactionManagementError):
                    future.result(timeout=5)
    assert not FieldTemplate.objects.for_facility(facility).filter(name="Copied thread").exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_writer_waits_until_package_rewrite_and_commit(facility, lead_user):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event as ThreadEvent

    from django.db import connections
    from django.db.models.signals import pre_save

    from tests.test_sensitivity_write_locking import run_connection, wait_for_completion_or_lock

    record, field = make_record(facility, lead_user, "event")
    # The loader preserves its existing immutable-encryption contract.
    FieldTemplate.objects.filter(pk=field.pk).update(is_encrypted=True)
    field.refresh_from_db()
    package = package_for([record], field)
    paused, resume, started = ThreadEvent(), ThreadEvent(), ThreadEvent()
    pids = {}

    def pause_package(sender, instance, **kwargs):
        paused.set()
        assert resume.wait(10)

    def write():
        pids["writer"] = connections["default"].connection.info.backend_pid
        started.set()
        record.data_json = {field.slug: "ConcurrentSecret"}
        record.save()

    pre_save.connect(pause_package, sender=FieldGroup, weak=False)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            importer = pool.submit(run_connection, lambda: load_package(facility, package))
            try:
                assert paused.wait(5)
                writer = pool.submit(run_connection, write)
                assert started.wait(5)
                assert wait_for_completion_or_lock(writer, pids["writer"])
            finally:
                resume.set()
            importer.result(timeout=12)
            writer.result(timeout=12)
    finally:
        pre_save.disconnect(pause_package, sender=FieldGroup)
    record.refresh_from_db()
    assert is_encrypted_value(record.data_json[field.slug])
    assert safe_decrypt(record.data_json[field.slug]) == "ConcurrentSecret"
    assert record.search_text == ""
    with sql_counts() as (counts, _):
        field.refresh_from_db()
        field.save()
    assert counts["exclusive_gate"] == 1


def test_caught_batch_rewrite_failure_rolls_back_before_outer_commit(facility, lead_user, monkeypatch):
    record, field = make_record(facility, lead_user, "event")

    def fail_record(*args, **kwargs):
        raise RuntimeError("Injected flush failure")

    with transaction.atomic():
        dynamic_protection.lock_dynamic_protection(facility.pk, exclusive=True)
        monkeypatch.setattr(Event, "save", fail_record)
        with pytest.raises(RuntimeError, match="Injected flush failure"):
            with batch(facility):
                field.is_encrypted = True
                field.save()
        field.refresh_from_db()
        assert field.is_encrypted is False
        record.refresh_from_db()
        assert record.data_json == {field.slug: "SecretToken"}
        assert record.search_text == "SecretToken"


@pytest.mark.parametrize("failure", ["validation", "integrity"])
def test_deferred_rewrite_errors_keep_the_package_error_contract(facility, lead_user, monkeypatch, failure):
    record, field = make_record(facility, lead_user, "event")
    FieldTemplate.objects.filter(pk=field.pk).update(is_encrypted=True)
    field.refresh_from_db()
    package = package_for([record], field)
    before = configuration_snapshot(facility)

    def fail_record(self, *args, **kwargs):
        if failure == "validation":
            raise ValidationError("Injected deferred rewrite validation")
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO core_event (id) VALUES (%s)", [self.pk])

    monkeypatch.setattr(Event, "save", fail_record)
    with pytest.raises(PackageLoadError) as error:
        load_package(facility, package)
    assert error.value.report.errors
    if failure == "validation":
        assert error.value.report.errors[-1][1] == "Injected deferred rewrite validation"
    assert configuration_snapshot(facility) == before
    record.refresh_from_db()
    assert record.data_json == {field.slug: "SecretToken"}
    assert record.search_text == "SecretToken"
    monkeypatch.undo()
    field.sensitivity = "elevated"
    with sql_counts() as (counts, _):
        field.save()
    assert counts["exclusive_gate"] == 1
    assert counts["Event"] == 1
