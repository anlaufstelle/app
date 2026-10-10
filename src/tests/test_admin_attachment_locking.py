"""Upload and admin configuration races must commit safely (Refs #2525)."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event as ThreadEvent
from types import SimpleNamespace

import pytest
from django.db import connection, connections, transaction
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext

from core.admin.documents import DocumentTypeAdmin, FieldTemplateAdmin
from core.admin.organization import FacilityAdmin, OrganizationAdmin
from core.admin_site import anlaufstelle_admin_site
from core.models import Attachment, AuditLog, DocumentType, DocumentTypeField, Facility, FieldTemplate, Organization
from core.services.file_vault.storage import commit_staged_upload
from tests.test_admin_config_audit import _request
from tests.test_sensitivity_write_locking import run_connection, wait_for_completion_or_lock

pytestmark = pytest.mark.django_db(transaction=True)


def test_upload_and_admin_bulk_activation_both_commit(facility, sample_event, admin_user, tmp_path, _encryption_key):
    field = FieldTemplate.objects.create(facility=facility, name="Upload file", field_type=FieldTemplate.FieldType.FILE)
    DocumentTypeField.objects.create(document_type=sample_event.document_type, field_template=field)
    staged = SimpleNamespace(
        storage_name="upload.enc",
        original_name="upload.pdf",
        file_size=1,
        content_type="application/pdf",
        detected_mime="application/pdf",
        output_path=tmp_path / "upload.enc",
    )
    uploaded, release_upload, admin_started = ThreadEvent(), ThreadEvent(), ThreadEvent()
    pids = {}

    def upload():
        with transaction.atomic():
            commit_staged_upload(sample_event, field, staged, admin_user)
            uploaded.set()
            assert release_upload.wait(10), "Upload transaction was not released"

    def deactivate():
        pids["admin"] = connections["default"].connection.info.backend_pid
        admin_started.set()
        admin = FieldTemplateAdmin(FieldTemplate, anlaufstelle_admin_site)
        return admin._bulk_set_active(
            _request(RequestFactory(), admin_user), FieldTemplate.objects.filter(pk=field.pk), is_active=False
        )

    errors = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(run_connection, upload)
        try:
            assert uploaded.wait(5), "Upload failed before reaching its commit boundary"
            changer = pool.submit(run_connection, deactivate)
            assert admin_started.wait(5)
            assert wait_for_completion_or_lock(changer, pids["admin"])
        finally:
            release_upload.set()
        for future in (writer, changer):
            try:
                future.result(timeout=12)
            except Exception as exc:
                errors.append(f"{type(exc).__name__} (SQLSTATE {getattr(exc.__cause__, 'sqlstate', None)}): {exc}")
    if errors:
        pytest.fail("\n".join(errors), pytrace=False)

    field.refresh_from_db()
    assert field.is_active is False
    assert Attachment.objects.filter(event=sample_event, field_template=field).count() == 1
    assert (
        AuditLog.objects.filter(
            action=AuditLog.Action.CONFIG_UPDATE, target_id=str(field.pk), detail__bulk=True
        ).count()
        == 1
    )


@pytest.mark.parametrize(
    "operation, model", [("activate", FieldTemplate), ("delete", FieldTemplate), ("delete", DocumentType)]
)
def test_admin_bulk_configuration_takes_all_gates_before_rows(facility, admin_user, operation, model):
    second = Facility.objects.create(organization=facility.organization, name="Second gate facility")
    objects = [model.objects.create(facility=scope, name="Gate selection") for scope in (facility, second)]
    admin_class = FieldTemplateAdmin if model is FieldTemplate else DocumentTypeAdmin
    model_admin = admin_class(model, anlaufstelle_admin_site)
    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM core_facility ORDER BY hashtext(id::text), id")
        expected = [str(row[0]) for row in cursor.fetchall() if row[0] in {facility.pk, second.pk}]
    with CaptureQueriesContext(connection) as captured:
        queryset = model.objects.filter(pk__in=[obj.pk for obj in objects])
        if operation == "activate":
            assert model_admin._bulk_set_active(_request(RequestFactory(), admin_user), queryset, is_active=False) == 2
        else:
            model_admin.delete_queryset(_request(RequestFactory(), admin_user), queryset)
    queries = [query["sql"] for query in captured.captured_queries]
    first_row_lock = next(
        index for index, sql in enumerate(queries) if "FOR UPDATE" in sql or "FOR NO KEY UPDATE" in sql
    )
    # Toggles change no key column; deletions keep the full row lock.
    assert ("FOR NO KEY UPDATE" in queries[first_row_lock]) is (operation == "activate")
    gates = [sql for sql in queries[:first_row_lock] if "pg_advisory_xact_lock(2525," in sql]
    assert len(gates) == 2
    assert [next(fid for fid in expected if fid in sql) for sql in gates] == expected


def test_admin_bulk_change_reads_fresh_state_after_waiting(facility, admin_user):
    field = FieldTemplate.objects.create(facility=facility, name="Before gate")
    started = ThreadEvent()
    pids = {}

    def deactivate():
        pids["admin"] = connections["default"].connection.info.backend_pid
        started.set()
        return FieldTemplateAdmin(FieldTemplate, anlaufstelle_admin_site)._bulk_set_active(
            _request(RequestFactory(), admin_user), FieldTemplate.objects.filter(pk=field.pk), is_active=False
        )

    from core.services.dynamic_protection import lock_dynamic_protection

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            lock_dynamic_protection(facility.pk, exclusive=True)
            changer = pool.submit(run_connection, deactivate)
            assert started.wait(5)
            assert wait_for_completion_or_lock(changer, pids["admin"])
            FieldTemplate.objects.filter(pk=field.pk).update(name="After gate", is_active=False)
        assert changer.result(timeout=12) == 0
    assert not AuditLog.objects.filter(action=AuditLog.Action.CONFIG_UPDATE, target_id=str(field.pk)).exists()
    field.refresh_from_db()
    assert field.name == "After gate"
    assert field.is_active is False


@pytest.mark.parametrize("model,admin_class", [(DocumentType, DocumentTypeAdmin), (FieldTemplate, FieldTemplateAdmin)])
def test_admin_deletion_locks_only_its_selected_configuration_rows(facility, admin_user, model, admin_class):
    selected = model.objects.create(facility=facility, name="Selected deletion")
    remaining = model.objects.create(facility=facility, name="Unselected configuration")
    with CaptureQueriesContext(connection) as captured:
        admin_class(model, anlaufstelle_admin_site).delete_queryset(
            _request(RequestFactory(), admin_user), model.objects.filter(pk=selected.pk)
        )
    locks = [query["sql"] for query in captured.captured_queries if "FOR UPDATE" in query["sql"]]
    assert len(locks) == 1
    assert selected.pk.hex in locks[0].replace("-", "")
    assert remaining.pk.hex not in locks[0].replace("-", "")
    assert "WHERE" in locks[0] and '"id" IN' in locks[0]
    assert model.objects.filter(pk=remaining.pk).exists()
    assert not model.objects.filter(pk=selected.pk).exists()


@pytest.mark.parametrize("model_admin_class", [FacilityAdmin, OrganizationAdmin])
def test_admin_cascade_deletion_waits_before_config_rows(facility, admin_user, model_admin_class):
    organization = Organization.objects.create(name="Cascade gate organization")
    scope = Facility.objects.create(organization=organization, name="Cascade gate facility")
    field = FieldTemplate.objects.create(facility=scope, name="Cascade gate field")
    target = scope if model_admin_class is FacilityAdmin else organization
    started = ThreadEvent()
    pids = {}

    def delete():
        pids["admin"] = connections["default"].connection.info.backend_pid
        started.set()
        model_admin_class(type(target), anlaufstelle_admin_site).delete_queryset(
            _request(RequestFactory(), admin_user), type(target).objects.filter(pk=target.pk)
        )

    from core.services.dynamic_protection import lock_dynamic_protection

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            lock_dynamic_protection(scope.pk)
            changer = pool.submit(run_connection, delete)
            assert started.wait(5)
            assert wait_for_completion_or_lock(changer, pids["admin"]), "Cascade bypassed the configuration gate"
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_stat_clear_snapshot()")
                cursor.execute("SELECT query FROM pg_stat_activity WHERE pid = %s", [pids["admin"]])
                assert "pg_advisory_xact_lock(2525" in cursor.fetchone()[0]
            assert FieldTemplate.objects.filter(pk=field.pk).exists()
        changer.result(timeout=12)
    assert not FieldTemplate.objects.filter(pk=field.pk).exists()
    assert not type(target).objects.filter(pk=target.pk).exists()
