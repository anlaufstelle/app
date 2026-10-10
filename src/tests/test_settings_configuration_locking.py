"""Settings writers take the protection gate before rows, persistence and audit.

The default document type FK is checked at commit, after the audit lock. A
gated type change holds that row and then waits for its own audit lock, so an
ungated settings writer closes a deadlock (Refs #2525).
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event as ThreadEvent
from types import SimpleNamespace

import pytest
from django.db import connections, transaction
from django.test import RequestFactory

from core.admin.documents import DocumentTypeAdmin
from core.admin.system import SettingsAdmin
from core.admin_site import anlaufstelle_admin_site
from core.models import DocumentType, Settings
from core.services.system.settings import update_settings
from tests.test_admin_config_audit import _request
from tests.test_sensitivity_write_locking import run_connection, wait_for_completion_or_lock

pytestmark = pytest.mark.django_db(transaction=True)


def _write_via_service(settings_obj, user, document_type):
    update_settings(settings_obj, user, default_document_type=document_type)


def _write_via_admin(settings_obj, user, document_type):
    obj = Settings.objects.get(pk=settings_obj.pk)
    obj.default_document_type = document_type
    SettingsAdmin(Settings, anlaufstelle_admin_site).save_model(_request(RequestFactory(), user), obj, None, True)


def _rename_type(document_type, user):
    current = DocumentType.objects.get(pk=document_type.pk)
    current.name = "Renamed default type"
    DocumentTypeAdmin(DocumentType, anlaufstelle_admin_site).save_model(
        _request(RequestFactory(), user),
        current,
        SimpleNamespace(initial={"sensitivity": current.sensitivity}, changed_data=["name"]),
        change=True,
    )


def _backend_query(pid):
    with connections["default"].cursor() as cursor:
        cursor.execute("SELECT pg_stat_clear_snapshot()")
        cursor.execute("SELECT query FROM pg_stat_activity WHERE pid = %s", [pid])
        return cursor.fetchone()[0]


@pytest.mark.parametrize("write_settings", [_write_via_service, _write_via_admin], ids=["service", "admin"])
@pytest.mark.parametrize("first_writer", ["settings", "configuration"])
def test_settings_default_type_and_type_rename_both_commit(facility, admin_user, write_settings, first_writer):
    document_type = DocumentType.objects.create(facility=facility, name="Default type")
    settings_obj, _created = Settings.objects.get_or_create(facility=facility)
    paused, release_first, second_started = ThreadEvent(), ThreadEvent(), ThreadEvent()
    pids = {}

    def first():
        with transaction.atomic():
            if first_writer == "settings":
                write_settings(settings_obj, admin_user, document_type)
            else:
                _rename_type(document_type, admin_user)
            paused.set()
            assert release_first.wait(10), "First transaction was not released"

    def second():
        pids["second"] = connections["default"].connection.info.backend_pid
        second_started.set()
        with transaction.atomic():
            if first_writer == "settings":
                _rename_type(document_type, admin_user)
            else:
                write_settings(settings_obj, admin_user, document_type)

    errors = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(run_connection, first)
        try:
            assert paused.wait(5), "First writer did not reach its pause boundary"
            waiter = pool.submit(run_connection, second)
            assert second_started.wait(5)
            assert wait_for_completion_or_lock(waiter, pids["second"])
            if first_writer == "configuration":
                # The settings writer must wait at its gate, before any row or audit lock.
                assert "pg_advisory_xact_lock_shared(2525" in _backend_query(pids["second"])
        finally:
            release_first.set()
        for future in (writer, waiter):
            try:
                future.result(timeout=12)
            except Exception as exc:
                errors.append(f"{type(exc).__name__} (SQLSTATE {getattr(exc.__cause__, 'sqlstate', None)}): {exc}")
    if errors:
        pytest.fail("\n".join(errors), pytrace=False)
    document_type.refresh_from_db()
    settings_obj.refresh_from_db()
    assert document_type.name == "Renamed default type"
    assert settings_obj.default_document_type_id == document_type.pk
