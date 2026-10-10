"""Concurrent transactions preserve the dynamic protection lock order (Refs #2525)."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event as ThreadEvent

import pytest
from django.db import connection, connections, transaction
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext

from core.admin.documents import FieldTemplateAdmin
from core.admin_site import anlaufstelle_admin_site
from core.models import FieldTemplate, QuickTemplate
from tests.test_admin_config_audit import _request
from tests.test_sensitivity_write_locking import run_connection, wait_for_completion_or_lock

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("first_writer", ["setup", "configuration"])
def test_setup_facility_and_bulk_activation_both_commit(facility, admin_user, monkeypatch, first_writer):
    """A setup rerun must enter the new quick-template gate before its audit lock."""
    from io import StringIO

    from django.core.management import call_command

    from core.models import DocumentType

    DocumentType.objects.create(facility=facility, name="Review contact", slug="kontakt")
    field = FieldTemplate.objects.create(facility=facility, name="Unrelated activation", sensitivity="normal")
    answers = iter([facility.organization.name, facility.name, "review-new-bootstrap-admin"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr("getpass.getpass", lambda prompt: "Review-only-strong-password-42")
    audited, resume, started = ThreadEvent(), ThreadEvent(), ThreadEvent()
    pids = {}

    def setup():
        call_command("setup_facility", stdout=StringIO())

    def deactivate():
        admin = FieldTemplateAdmin(FieldTemplate, anlaufstelle_admin_site)
        return admin._bulk_set_active(
            _request(RequestFactory(), admin_user), FieldTemplate.objects.filter(pk=field.pk), is_active=False
        )

    actions = {"setup": setup, "configuration": deactivate}
    second_writer = "configuration" if first_writer == "setup" else "setup"

    def first():
        def pause_after_audit(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if sql.startswith('INSERT INTO "core_auditlog"') and not audited.is_set():
                audited.set()
                assert resume.wait(10), "First transaction was not released after its audit insert"
            return result

        with transaction.atomic(), connections["default"].execute_wrapper(pause_after_audit):
            return actions[first_writer]()

    def second():
        pids["changer"] = connections["default"].connection.info.backend_pid
        started.set()
        return actions[second_writer]()

    errors = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(run_connection, first)
        try:
            assert audited.wait(5), "First transaction did not reach its audit insert"
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
    field.refresh_from_db()
    assert field.is_active is False
    assert QuickTemplate.objects.for_facility(facility).exists()


@pytest.mark.parametrize("existing_facility", [False, True])
@pytest.mark.parametrize("package", [False, True])
def test_setup_gate_precedes_the_first_write(facility, monkeypatch, existing_facility, package):
    from io import StringIO

    from django.core.management import call_command

    name = facility.name if existing_facility else "New setup facility"
    answers = iter([facility.organization.name, name, "early-gate-admin"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr("getpass.getpass", lambda prompt: "Test-only-strong-password-42")
    with CaptureQueriesContext(connection) as captured:
        call_command("setup_facility", no_default_package=not package, stdout=StringIO())
    queries = [entry["sql"] for entry in captured.captured_queries]
    gate_sql = "pg_advisory_xact_lock(2525" if package else "pg_advisory_xact_lock_shared(2525"
    gates = [index for index, sql in enumerate(queries) if gate_sql in sql]
    writes = [index for index, sql in enumerate(queries) if sql.startswith(("INSERT", "UPDATE", "DELETE"))]
    assert gates and writes, queries
    assert gates[0] < writes[0], queries
    if not package:
        assert not any("pg_advisory_xact_lock(2525" in sql for sql in queries), queries


def test_setup_retries_when_another_transaction_creates_the_facility(facility, monkeypatch):
    from io import StringIO

    from django.core.management import call_command

    from core.models import AuditLog, Facility, User

    name = "Concurrently created facility"
    answers = iter([facility.organization.name, name, "concurrent-setup-admin"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr("getpass.getpass", lambda prompt: "Test-only-strong-password-42")
    gate_ids = []
    with ThreadPoolExecutor(max_workers=1) as pool:

        def create_after_first_gate(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if "pg_advisory_xact_lock(2525" in sql:
                gate_ids.append(params[0])
                if len(gate_ids) == 1:
                    pool.submit(
                        run_connection,
                        lambda: Facility.objects.create(organization=facility.organization, name=name),
                    ).result(timeout=12)
            return result

        with connection.execute_wrapper(create_after_first_gate):
            call_command("setup_facility", stdout=StringIO())
    created = Facility.objects.get(name=name)
    assert gate_ids[0] != str(created.pk)
    assert gate_ids[1] == str(created.pk)
    assert User.objects.get(username="concurrent-setup-admin").facility_id == created.pk
    assert AuditLog.objects.filter(facility=created, action=AuditLog.Action.USER_CREATED).count() == 1
    assert QuickTemplate.objects.for_facility(created).exists()
