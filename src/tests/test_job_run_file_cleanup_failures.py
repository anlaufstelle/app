"""Retention file failures preserve retry targets and global job evidence (Refs #2583)."""

from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from core.models import Attachment, AuditLog, Event, ImportJob, StatisticsSnapshot
from core.services.compliance import ComplianceStatus
from core.services.compliance.retention import _retention_checks
from core.services.file_vault.storage import get_attachment_path
from core.services.import_engine import import_file_path
from core.views.health import _check_stale_jobs

pytestmark = pytest.mark.django_db


def _old_success():
    return AuditLog.objects.create(
        action=AuditLog.Action.RETENTION_RUN_COMPLETED,
        facility=None,
        timestamp=timezone.now() - timedelta(days=90),
        detail={"job_run": {"scope": "installation", "status": "success", "processed": 1, "skipped": 0, "failed": 0}},
    )


def _stale_upload(facility, filename):
    job = ImportJob.objects.create(
        facility=facility, target_type=ImportJob.TargetType.CLIENT, storage_filename=filename
    )
    ImportJob.objects.filter(pk=job.pk).update(created_at=timezone.now() - timedelta(days=90))
    path = import_file_path(job)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"synthetic encrypted-upload placeholder")
    return job, path


def _expired_event(facility, settings_obj, doc_type_contact, staff_user):
    return Event.objects.create(
        facility=facility,
        document_type=doc_type_contact,
        created_by=staff_user,
        occurred_at=timezone.now() - timedelta(days=settings_obj.retention_anonymous_days + 10),
        is_anonymous=True,
        data_json={"duration": 10},
    )


def _run_with_file_failure(path, unlink_fails):
    unlink = Path.unlink

    def remove(candidate, *args, **kwargs):
        if unlink_fails and candidate == path:
            raise PermissionError("file removal denied")
        return unlink(candidate, *args, **kwargs)

    output = StringIO()
    command_error = None
    with patch.object(Path, "unlink", new=remove):
        try:
            call_command("enforce_retention", stdout=output, stderr=StringIO())
        except CommandError as exc:
            command_error = exc
    return command_error, output.getvalue()


def _assert_failed_evidence(old, marker_before, command_error, output):
    assert isinstance(command_error, CommandError)
    assert "failed=1" in output
    assert list(AuditLog.objects.filter(action=old.action).values_list("pk", "timestamp", "detail")) == [marker_before]
    assert _retention_checks()[0].status == ComplianceStatus.CRITICAL
    assert "retention_last_run" in _check_stale_jobs()


def _assert_recovered(old):
    assert AuditLog.objects.filter(action=old.action).exclude(pk=old.pk).count() == 1
    assert _retention_checks()[0].status == ComplianceStatus.OK
    assert "retention_last_run" not in _check_stale_jobs()


@pytest.mark.parametrize("unlink_fails", [False, True], ids=["healthy", "permission-error"])
def test_stale_import_file_failure_is_counted_and_retried(
    facility, settings_obj, settings, tmp_path, doc_type_contact, staff_user, unlink_fails
):
    settings.MEDIA_ROOT = tmp_path
    job, upload = _stale_upload(facility, "stale-upload.enc")
    other, other_upload = _stale_upload(facility, "other-upload.enc")
    event = _expired_event(facility, settings_obj, doc_type_contact, staff_user)
    job.refresh_from_db()
    job_before = (job.status, job.storage_filename, job.original_filename_encrypted, job.updated_at)
    old = _old_success()
    marker_before = (old.pk, old.timestamp, old.detail)

    command_error, output = _run_with_file_failure(upload, unlink_fails)
    job.refresh_from_db()
    other.refresh_from_db()
    event.refresh_from_db()
    assert other.status == ImportJob.Status.ABORTED
    assert other.storage_filename == ""
    assert not other_upload.exists()
    assert event.is_deleted
    if unlink_fails:
        _assert_failed_evidence(old, marker_before, command_error, output)
        assert upload.exists()
        assert (job.status, job.storage_filename, job.original_filename_encrypted, job.updated_at) == job_before
        assert not AuditLog.objects.filter(action=AuditLog.Action.IMPORT_ABORTED, target_id=job.pk).exists()
        assert "Cleaned up 1 stale import job(s)" in output
        # Repeated failures retain the same retry reference and do not count success.
        error, repeated_output = _run_with_file_failure(upload, True)
        _assert_failed_evidence(old, marker_before, error, repeated_output)
        job.refresh_from_db()
        assert (job.status, job.storage_filename, job.original_filename_encrypted, job.updated_at) == job_before
        assert "Cleaned up" not in repeated_output
        call_command("enforce_retention", stdout=StringIO())
        job.refresh_from_db()
    else:
        assert command_error is None
        assert "failed=0" in output
    assert not upload.exists()
    assert job.status == ImportJob.Status.ABORTED
    assert job.storage_filename == ""
    assert AuditLog.objects.filter(action=AuditLog.Action.IMPORT_ABORTED, target_id=job.pk).count() == 1
    _assert_recovered(old)


@pytest.mark.parametrize("custom_retention", [False, True], ids=["facility-period", "document-period"])
@pytest.mark.parametrize("unlink_fails", [False, True], ids=["healthy", "permission-error"])
def test_attachment_file_failure_rolls_back_and_retries(
    facility,
    settings_obj,
    settings,
    tmp_path,
    doc_type_contact,
    staff_user,
    field_template,
    unlink_fails,
    custom_retention,
):
    settings.MEDIA_ROOT = tmp_path
    if custom_retention:
        doc_type_contact.retention_days = 1
        doc_type_contact.save(update_fields=["retention_days"])
    event = _expired_event(facility, settings_obj, doc_type_contact, staff_user)
    other = _expired_event(facility, settings_obj, doc_type_contact, staff_user)
    attachment = Attachment.objects.create(
        facility=facility,
        event=event,
        field_template=field_template,
        storage_filename="expired-attachment.enc",
        original_filename_encrypted={},
        file_size=10,
        mime_type="application/octet-stream",
        created_by=staff_user,
    )
    upload = get_attachment_path(attachment)
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"synthetic encrypted-attachment placeholder")
    job, job_upload = _stale_upload(facility, "healthy-upload.enc")
    event_before = (event.data_json, event.search_text, event.updated_at)
    old = _old_success()
    marker_before = (old.pk, old.timestamp, old.detail)

    command_error, output = _run_with_file_failure(upload, unlink_fails)
    event.refresh_from_db()
    other.refresh_from_db()
    job.refresh_from_db()
    assert other.is_deleted
    assert other.history.count() == 1
    assert job.status == ImportJob.Status.ABORTED
    assert not job_upload.exists()
    if unlink_fails:
        _assert_failed_evidence(old, marker_before, command_error, output)
        assert upload.exists()
        assert Attachment.objects.filter(pk=attachment.pk, storage_filename=attachment.storage_filename).exists()
        assert not event.is_deleted
        assert event.deleted_at is None
        assert (event.data_json, event.search_text, event.updated_at) == event_before
        assert not event.history.exists()
        snapshot = StatisticsSnapshot.objects.get(facility=facility)
        assert snapshot.backend_data["_deleted"]["_statistics"]["total_contacts"] == 1
        error, repeated_output = _run_with_file_failure(upload, True)
        _assert_failed_evidence(old, marker_before, error, repeated_output)
        assert Attachment.objects.filter(pk=attachment.pk).exists()
        call_command("enforce_retention", stdout=StringIO())
        event.refresh_from_db()
    else:
        assert command_error is None
        assert "failed=0" in output
    assert not upload.exists()
    assert not Attachment.objects.filter(pk=attachment.pk).exists()
    assert event.is_deleted
    assert event.history.count() == 1
    snapshot = StatisticsSnapshot.objects.get(facility=facility)
    assert snapshot.backend_data["_deleted"]["_statistics"]["total_contacts"] == 2
    _assert_recovered(old)
