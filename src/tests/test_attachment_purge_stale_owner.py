"""Purges preserve committed owner edits across PostgreSQL connections."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event as ThreadEvent

import pytest
from django.db import connections

from core.models import DocumentTypeField
from core.models.attachment import Attachment
from core.services.attachments import purge_attachment_chain
from tests.test_attachment_purge import _attach, _file_template
from tests.test_sensitivity_transitions import make_record
from tests.test_sensitivity_write_locking import run_connection

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.postgres]


@pytest.mark.parametrize("entity", ["client", "case", "event"])
@pytest.mark.parametrize("second_action", ["save", "purge"])
def test_purge_preserves_committed_changes_after_owner_was_loaded(facility, staff_user, entity, second_action):
    owner, text_field = make_record(facility, staff_user, entity, value="Original text")
    file_field = _file_template(facility, "Attached documents")
    DocumentTypeField.objects.create(document_type=owner.document_type, field_template=file_field)
    first = _attach(facility, owner, file_field, staff_user, "first.pdf")
    second = _attach(facility, owner, file_field, staff_user, "second.pdf")
    stale_owner = type(owner).objects.get(pk=owner.pk)
    ready, resume = ThreadEvent(), ThreadEvent()

    def delayed_purge():
        def pause_before_first_lock(execute, sql, params, many, context):
            # Pause the real service before its first lock, after loading its chain.
            if ("pg_advisory_xact_lock" in sql or "FOR UPDATE" in sql) and not ready.is_set():
                ready.set()
                assert resume.wait(10), "The competing writer did not release the purge"
            return execute(sql, params, many, context)

        with connections["default"].execute_wrapper(pause_before_first_lock):
            return purge_attachment_chain(stale_owner, first.entry_id)

    def committed_change():
        current = type(owner).objects.get(pk=owner.pk)
        if second_action == "purge":
            return purge_attachment_chain(current, second.entry_id)
        current.data_json[text_field.slug] = "Committed text"
        third = _attach(facility, current, file_field, staff_user, "third.pdf")
        return str(third.pk)

    with ThreadPoolExecutor(max_workers=2) as pool:
        purge = pool.submit(run_connection, delayed_purge)
        try:
            assert ready.wait(5), "The purge did not reach its first lock"
            changed = pool.submit(run_connection, committed_change).result(timeout=12)
        finally:
            resume.set()
        assert purge.result(timeout=12) == 1

    owner.refresh_from_db()
    assert not Attachment.objects.filter(pk=first.pk).exists()
    if second_action == "save":
        assert owner.data_json[text_field.slug] == "Committed text"
        assert {entry["id"] for entry in owner.data_json[file_field.slug]["entries"]} == {str(second.pk), changed}
    else:
        assert not Attachment.objects.filter(pk=second.pk).exists()
        assert file_field.slug not in owner.data_json
