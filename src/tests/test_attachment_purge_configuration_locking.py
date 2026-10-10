"""Attachment purges take the protection gate before owner rows (Refs #2525)."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event as ThreadEvent

import pytest
from django.db import connections

from tests.test_attachment_purge import _attach, _file_template
from tests.test_sensitivity_transitions import make_record
from tests.test_sensitivity_write_locking import run_connection, wait_for_completion_or_lock

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("change", ["type", "field"])
@pytest.mark.parametrize("first_writer", ["purge", "configuration"])
def test_anonymous_event_purge_and_protection_change_both_commit(
    facility, staff_user, _encryption_key, change, first_writer
):
    from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate
    from core.models.attachment import Attachment
    from core.services.attachments import purge_attachment_chain
    from core.services.file_vault import get_attachment_path, is_encrypted_value, safe_decrypt

    event, text_field = make_record(facility, staff_user, "event")
    event.is_anonymous = True
    event.save(update_fields=["is_anonymous"])
    document_type_id = event.document_type_id
    file_field = _file_template(facility, "Attached file", sensitivity="normal")
    DocumentTypeField.objects.create(document_type=event.document_type, field_template=file_field)
    attachment = _attach(facility, event, file_field, staff_user, "purged.pdf")
    file_path = get_attachment_path(attachment)
    assert file_path.exists()
    locked, resume, started = ThreadEvent(), ThreadEvent(), ThreadEvent()
    pids = {}

    def purge():
        return purge_attachment_chain(event, attachment.entry_id)

    def configure():
        if change == "type":
            target = DocumentType.objects.get(pk=document_type_id)
            target.sensitivity = "elevated"
        else:
            target = FieldTemplate.objects.get(pk=text_field.pk)
            target.sensitivity = "high"
            target.is_encrypted = True
        target.save()

    actions = {"purge": purge, "configuration": configure}
    second_writer = "configuration" if first_writer == "purge" else "purge"

    def first():
        def pause_after_owner_lock(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if 'FROM "core_event"' in sql and "FOR UPDATE" in sql and not locked.is_set():
                locked.set()
                assert resume.wait(10), "First transaction was not released after locking the event"
            return result

        with connections["default"].execute_wrapper(pause_after_owner_lock):
            return actions[first_writer]()

    def second():
        pids["changer"] = connections["default"].connection.info.backend_pid
        started.set()
        return actions[second_writer]()

    errors = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(run_connection, first)
        try:
            assert locked.wait(5), "First transaction did not lock the event"
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
    event = Event.objects.get(pk=event.pk)
    assert not Attachment.objects.filter(pk=attachment.pk).exists()
    assert not file_path.exists()
    assert file_field.slug not in event.data_json
    assert safe_decrypt(event.data_json[text_field.slug]) == "SecretToken"
    assert event.search_text == ""
    assert is_encrypted_value(event.data_json[text_field.slug]) is (change == "field")
    if change == "type":
        assert DocumentType.objects.get(pk=document_type_id).sensitivity == "elevated"
    else:
        text_field.refresh_from_db()
        assert text_field.sensitivity == "high"
        assert text_field.is_encrypted is True
