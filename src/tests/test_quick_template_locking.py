"""Quick-template FK writers must share the configuration gate before audit."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event as ThreadEvent
from types import SimpleNamespace

import pytest
from django.db import connections, transaction
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext

from core.admin.documents import FieldTemplateAdmin, QuickTemplateAdmin
from core.admin_site import anlaufstelle_admin_site
from core.models import DocumentType, FieldTemplate, QuickTemplate
from tests._sensitivity_helpers import make_inherited_fields_explicit
from tests.test_admin_config_audit import _request
from tests.test_sensitivity_write_locking import run_connection, wait_for_completion_or_lock

pytestmark = pytest.mark.django_db(transaction=True)


def test_shared_and_exclusive_gates_do_not_lock_configuration_rows(facility):
    from django.db import connection

    from core.services.dynamic_protection import lock_dynamic_protection

    with transaction.atomic(), CaptureQueriesContext(connection) as captured:
        lock_dynamic_protection(facility.pk)
        lock_dynamic_protection(facility.pk, exclusive=True)
    queries = [query["sql"] for query in captured.captured_queries]
    assert any("pg_advisory_xact_lock_shared" in sql for sql in queries)
    assert any("pg_advisory_xact_lock(2525" in sql for sql in queries)
    assert not any("FOR SHARE" in sql or "FOR UPDATE" in sql for sql in queries), queries


@pytest.mark.parametrize("first_writer", ["quick_template", "configuration"])
def test_quick_template_and_bulk_activation_both_commit(facility, sample_event, admin_user, first_writer):
    field = FieldTemplate.objects.create(facility=facility, name="Activation", field_type=FieldTemplate.FieldType.TEXT)
    saved, release_writer, second_started = ThreadEvent(), ThreadEvent(), ThreadEvent()
    pids = {}

    def write_template():
        obj = QuickTemplate(facility=facility, document_type=sample_event.document_type, name="Quick entry")
        admin = QuickTemplateAdmin(QuickTemplate, anlaufstelle_admin_site)
        admin.save_model(
            _request(RequestFactory(), admin_user),
            obj,
            SimpleNamespace(changed_data=["name", "document_type"]),
            change=False,
        )

    def deactivate():
        admin = FieldTemplateAdmin(FieldTemplate, anlaufstelle_admin_site)
        admin._bulk_set_active(
            _request(RequestFactory(), admin_user), FieldTemplate.objects.filter(pk=field.pk), is_active=False
        )

    actions = {"quick_template": write_template, "configuration": deactivate}
    second_writer = "configuration" if first_writer == "quick_template" else "quick_template"

    def first():
        with transaction.atomic():
            actions[first_writer]()
            saved.set()
            assert release_writer.wait(10), "First transaction was not released"

    def second():
        pids["second"] = connections["default"].connection.info.backend_pid
        second_started.set()
        actions[second_writer]()

    errors = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(run_connection, first)
        try:
            assert saved.wait(5), "First writer did not reach its commit boundary"
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
    field.refresh_from_db()
    assert field.is_active is False
    assert QuickTemplate.objects.filter(facility=facility, name="Quick entry").count() == 1


def test_admin_prefilled_values_use_fresh_type_under_the_gate(facility, sample_event, admin_user):
    from core.models import DocumentTypeField

    document_type = sample_event.document_type
    field = FieldTemplate.objects.create(facility=facility, name="Prefilled", field_type=FieldTemplate.FieldType.TEXT)
    DocumentTypeField.objects.create(document_type=document_type, field_template=field)
    stale_type = DocumentType.objects.get(pk=document_type.pk)
    make_inherited_fields_explicit(document_type)
    document_type.sensitivity = DocumentType.Sensitivity.HIGH
    document_type.save()
    obj = QuickTemplate(
        facility=facility, document_type=stale_type, name="Protected", prefilled_data={field.slug: "Value"}
    )
    admin = QuickTemplateAdmin(QuickTemplate, anlaufstelle_admin_site)
    admin.save_model(
        _request(RequestFactory(), admin_user), obj, SimpleNamespace(changed_data=["prefilled_data"]), change=False
    )
    obj.refresh_from_db()
    assert obj.prefilled_data == {}


def test_model_template_write_waits_before_fk_persistence(facility, sample_event):
    from core.services.dynamic_protection import lock_dynamic_protection

    started, pids = ThreadEvent(), {}

    def write_template():
        pids["writer"] = connections["default"].connection.info.backend_pid
        started.set()
        return QuickTemplate.objects.create(
            facility=facility, document_type=sample_event.document_type, name="Model entry"
        ).pk

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            lock_dynamic_protection(facility.pk, exclusive=True)
            writer = pool.submit(run_connection, write_template)
            assert started.wait(5)
            assert wait_for_completion_or_lock(writer, pids["writer"])
            assert not QuickTemplate.objects.filter(facility=facility, name="Model entry").exists()
        template_id = writer.result(timeout=12)
    assert QuickTemplate.objects.get(pk=template_id).document_type_id == sample_event.document_type_id
