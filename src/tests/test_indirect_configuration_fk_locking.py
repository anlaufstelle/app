"""Indirect FK locks must follow the protection gate before quota and audit."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event as ThreadEvent
from types import SimpleNamespace

import pytest
from django.db import connections, transaction
from django.test import RequestFactory

from core.admin.documents import DocumentTypeAdmin
from core.admin_site import anlaufstelle_admin_site
from core.models import DocumentType, ImportJob
from core.services.file_vault.quota import lock_facility_storage
from core.services.import_engine import _lock_job_fk_rows
from tests.test_admin_config_audit import _request
from tests.test_sensitivity_write_locking import run_connection, wait_for_completion_or_lock

pytestmark = pytest.mark.django_db(transaction=True)


def test_wizard_job_read_waits_for_protection_before_locking_job(facility, admin_user):
    from core.services.dynamic_protection import lock_dynamic_protection
    from core.services.import_engine import _lock_job

    job = ImportJob.objects.create(facility=facility, created_by=admin_user, target_type="client")
    started, pids = ThreadEvent(), {}

    def lock_job():
        pids["worker"] = connections["default"].connection.info.backend_pid
        started.set()
        with transaction.atomic():
            _lock_job(job)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            lock_dynamic_protection(facility.pk, exclusive=True)
            worker = pool.submit(run_connection, lock_job)
            assert started.wait(5)
            assert wait_for_completion_or_lock(worker, pids["worker"])
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT pg_stat_clear_snapshot()")
                cursor.execute("SELECT query FROM pg_stat_activity WHERE pid = %s", [pids["worker"]])
                assert "pg_advisory_xact_lock_shared(2525" in cursor.fetchone()[0]
        worker.result(timeout=12)


@pytest.mark.parametrize("first_writer", ["import", "configuration"])
def test_import_fk_locks_and_type_rename_both_commit(facility, admin_user, first_writer):
    document_type = DocumentType.objects.create(facility=facility, name="Import type")
    saved, release_writer, second_started = ThreadEvent(), ThreadEvent(), ThreadEvent()
    pids = {}

    def write_import():
        job = ImportJob(facility=facility, created_by=admin_user, target_type="event", document_type=document_type)
        _lock_job_fk_rows(job)
        lock_facility_storage(facility.pk)
        job.save()

    def rename_type():
        current = DocumentType.objects.get(pk=document_type.pk)
        current.name = "Renamed import type"
        admin = DocumentTypeAdmin(DocumentType, anlaufstelle_admin_site)
        admin.save_model(
            _request(RequestFactory(), admin_user),
            current,
            SimpleNamespace(initial={"sensitivity": current.sensitivity}, changed_data=["name"]),
            change=True,
        )

    def first():
        with transaction.atomic():
            if first_writer == "import":
                job = ImportJob(
                    facility=facility, created_by=admin_user, target_type="event", document_type=document_type
                )
                _lock_job_fk_rows(job)
                saved.set()
                assert release_writer.wait(10), "Import transaction was not released"
                lock_facility_storage(facility.pk)
                job.save()
            else:
                rename_type()
                saved.set()
                assert release_writer.wait(10), "Configuration transaction was not released"

    def second():
        pids["second"] = connections["default"].connection.info.backend_pid
        second_started.set()
        with transaction.atomic():
            if first_writer == "import":
                rename_type()
            else:
                write_import()

    errors = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(run_connection, first)
        try:
            assert saved.wait(5), "First writer did not reach its pause boundary"
            changer = pool.submit(run_connection, second)
            assert second_started.wait(5)
            assert wait_for_completion_or_lock(changer, pids["second"])
        finally:
            release_writer.set()
        for future in (writer, changer):
            try:
                future.result(timeout=12)
            except Exception as exc:
                errors.append(f"{type(exc).__name__} (SQLSTATE {getattr(exc.__cause__, 'sqlstate', None)}): {exc}")
    if errors:
        pytest.fail("\n".join(errors), pytrace=False)
    document_type.refresh_from_db()
    assert document_type.name == "Renamed import type"
    assert ImportJob.objects.filter(facility=facility, document_type=document_type).count() == 1
