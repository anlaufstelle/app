"""Service failures and historical repairs preserve overdue operational job status."""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from core.models import AuditLog, StatisticsSnapshot
from core.services.compliance import ComplianceStatus
from core.services.compliance.cron import _snapshot_checks
from core.views.health import _check_stale_jobs

pytestmark = pytest.mark.django_db


def _old_success():
    return AuditLog.objects.create(
        action=AuditLog.Action.SNAPSHOT_RUN_COMPLETED,
        facility=None,
        timestamp=timezone.now() - timedelta(days=90),
        detail={"job_run": {"scope": "installation", "status": "success", "processed": 1, "skipped": 0, "failed": 0}},
    )


def _assert_success_not_refreshed(old):
    entries = list(AuditLog.objects.filter(action=AuditLog.Action.SNAPSHOT_RUN_COMPLETED))
    status = _snapshot_checks()[0].status
    stale = _check_stale_jobs()
    assert ([entry.pk for entry in entries], status, "snapshot_last_run" in stale) == (
        [old.pk],
        ComplianceStatus.CRITICAL,
        True,
    ), {"markers": [entry.detail for entry in entries], "status": status, "stale_jobs": stale}


def test_backend_failure_does_not_refresh_global_success(facility, settings_obj):
    from core.reporting.registry import get_backends

    backend = next(backend for backend in get_backends() if backend.snapshot_enabled)
    old = _old_success()
    output = StringIO()
    errors = StringIO()
    with patch.object(backend, "build_dataset", side_effect=RuntimeError("backend unavailable")) as build:
        with pytest.raises(CommandError, match="1 snapshot failure"):
            call_command("create_statistics_snapshots", stdout=output, stderr=errors)
    assert build.called
    assert "failed=1" in output.getvalue()
    assert "Snapshot backend failed:" in errors.getvalue()
    row = StatisticsSnapshot.objects.get(facility=facility)
    assert row.backend_data["_errors"][backend.slug] == "RuntimeError"
    _assert_success_not_refreshed(old)


def test_historical_month_does_not_hide_missing_scheduled_month(facility, settings_obj):
    old = _old_success()
    call_command("create_statistics_snapshots", year=2020, month=1, stdout=StringIO())
    today = timezone.localdate()
    previous_month = today.replace(day=1) - timedelta(days=1)
    assert StatisticsSnapshot.objects.filter(facility=facility, year=2020, month=1).exists()
    assert not StatisticsSnapshot.objects.filter(
        facility=facility, year=previous_month.year, month=previous_month.month
    ).exists()
    _assert_success_not_refreshed(old)


@pytest.mark.parametrize("custom_retention", [False, True])
def test_retention_carry_failure_does_not_refresh_global_success(
    facility, settings_obj, doc_type_contact, staff_user, custom_retention
):
    from core.models import Event
    from core.services.compliance.retention import _retention_checks
    from core.services.dashboard.snapshot import SnapshotCarryError, carry_deleted_event

    old = AuditLog.objects.create(
        action=AuditLog.Action.RETENTION_RUN_COMPLETED,
        facility=None,
        timestamp=timezone.now() - timedelta(days=90),
        detail={"job_run": {"scope": "installation", "status": "success", "processed": 1, "skipped": 0, "failed": 0}},
    )
    if custom_retention:
        doc_type_contact.retention_days = 1
        doc_type_contact.save(update_fields=["retention_days"])
    event = Event.objects.create(
        facility=facility,
        client=None,
        document_type=doc_type_contact,
        occurred_at=timezone.now() - timedelta(days=settings_obj.retention_anonymous_days + 10),
        data_json={"duration": 10},
        is_anonymous=True,
        created_by=staff_user,
    )
    other = Event.objects.create(
        facility=facility,
        document_type=doc_type_contact,
        occurred_at=event.occurred_at,
        data_json={"duration": 5},
        is_anonymous=True,
        created_by=staff_user,
    )
    output = StringIO()

    def carry_or_fail(current_facility, current_event):
        if current_event.pk == event.pk:
            raise SnapshotCarryError("carry unavailable")
        return carry_deleted_event(current_facility, current_event)

    with patch("core.retention.enforcement.carry_deleted_event", side_effect=carry_or_fail) as carry:
        with pytest.raises(CommandError, match=r"1 event\(s\) kept"):
            call_command("enforce_retention", stdout=output, stderr=StringIO())
    assert carry.call_count == (3 if custom_retention else 2)
    assert "failed=1" in output.getvalue()
    assert "Deferred 0 event/person/case target(s)" in output.getvalue()
    event.refresh_from_db()
    other.refresh_from_db()
    assert not event.is_deleted
    assert event.data_json == {"duration": 10}
    assert not event.history.exists()
    assert other.is_deleted
    entries = list(AuditLog.objects.filter(action=AuditLog.Action.RETENTION_RUN_COMPLETED))
    status = _retention_checks()[0].status
    stale = _check_stale_jobs()
    assert ([entry.pk for entry in entries], status, "retention_last_run" in stale) == (
        [old.pk],
        ComplianceStatus.CRITICAL,
        True,
    ), {"markers": [entry.detail for entry in entries], "status": status, "stale_jobs": stale}


@pytest.mark.parametrize("options", [{}, {"explicit_due": True}, {"backfill": True}])
def test_due_month_success_refreshes_global_status(facility, settings_obj, doc_type_contact, staff_user, options):
    from core.models import Event

    old = _old_success()
    previous_month = timezone.localdate().replace(day=1) - timedelta(days=1)
    Event.objects.create(
        facility=facility,
        document_type=doc_type_contact,
        occurred_at=timezone.make_aware(timezone.datetime(previous_month.year, previous_month.month, 1)),
        data_json={"duration": 10},
        is_anonymous=True,
        created_by=staff_user,
    )
    if options.get("explicit_due"):
        options = {"year": previous_month.year, "month": previous_month.month}
    call_command("create_statistics_snapshots", stdout=StringIO(), **options)
    assert StatisticsSnapshot.objects.filter(
        facility=facility, year=previous_month.year, month=previous_month.month
    ).exists()
    assert AuditLog.objects.filter(action=AuditLog.Action.SNAPSHOT_RUN_COMPLETED).exclude(pk=old.pk).count() == 1
    assert _snapshot_checks()[0].status == ComplianceStatus.OK
    assert "snapshot_last_run" not in _check_stale_jobs()


def test_backfill_without_due_month_does_not_refresh_success(facility, settings_obj):
    old = _old_success()
    call_command("create_statistics_snapshots", backfill=True, stdout=StringIO())
    _assert_success_not_refreshed(old)
