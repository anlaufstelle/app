"""Retention snapshot rebuild failures roll back deletion and global success (Refs #2583)."""

from datetime import datetime, timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone

from core.models import AuditLog, StatisticsSnapshot
from core.reporting.registry import get_backends
from core.services.compliance import ComplianceStatus
from core.services.compliance.retention import _retention_checks
from core.services.dashboard.snapshot import carry_deleted_event, create_or_update_snapshot
from core.views.health import _check_stale_jobs
from tests._factories import make_event

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("custom_retention", [False, True], ids=["facility-period", "document-period"])
@pytest.mark.parametrize("snapshot_state", ["missing", "empty", "failed", "failed-with-carry"])
def test_retention_rebuild_failure_rolls_back_and_recovers(
    facility, settings_obj, doc_type_contact, staff_user, snapshot_state, custom_retention
):
    backend = next(item for item in get_backends() if item.snapshot_enabled)
    occurred_at = timezone.make_aware(datetime(2020, 1, 15, 12))
    if custom_retention:
        doc_type_contact.retention_days = 1
        doc_type_contact.save(update_fields=["retention_days"])
    if snapshot_state == "failed-with-carry":
        prior = make_event(
            facility=facility,
            client=None,
            document_type=doc_type_contact,
            created_by=staff_user,
            occurred_at=occurred_at,
            is_anonymous=True,
            is_deleted=True,
            data_json={},
        )
        with transaction.atomic():
            carry_deleted_event(facility, prior)
    event = make_event(
        facility=facility,
        client=None,
        document_type=doc_type_contact,
        created_by=staff_user,
        occurred_at=occurred_at,
        is_anonymous=True,
        data_json={"duration": 10},
    )
    if snapshot_state == "empty":
        StatisticsSnapshot.objects.create(facility=facility, year=2020, month=1)
    elif snapshot_state.startswith("failed"):
        with patch.object(backend, "build_dataset", side_effect=RuntimeError("monthly dataset unavailable")):
            create_or_update_snapshot(facility, 2020, 1)
        row = StatisticsSnapshot.objects.get(facility=facility, year=2020, month=1)
        assert row.backend_data["_errors"] == {backend.slug: "RuntimeError"}
        assert backend.slug not in row.backend_data
    monthly_rows = StatisticsSnapshot.objects.filter(facility=facility, year=2020, month=1)
    monthly_rows.update(updated_at=timezone.now() - timedelta(days=90))
    snapshot_before = list(monthly_rows.values("data", "backend_data", "updated_at"))
    event_before = (event.data_json, event.search_text, event.updated_at)
    other = make_event(
        facility=facility,
        client=None,
        document_type=doc_type_contact,
        created_by=staff_user,
        occurred_at=timezone.make_aware(datetime(2020, 2, 15, 12)),
        is_anonymous=True,
        data_json={"duration": 5},
    )
    old = AuditLog.objects.create(
        action=AuditLog.Action.RETENTION_RUN_COMPLETED,
        facility=None,
        timestamp=timezone.now() - timedelta(days=90),
        detail={"job_run": {"scope": "installation", "status": "success", "processed": 1, "skipped": 0, "failed": 0}},
    )
    marker_before = (old.pk, old.timestamp, old.detail)
    output = StringIO()
    build_dataset = backend.build_dataset

    def fail_only_in_january(current_facility, date_from, date_to):
        if (date_from.year, date_from.month) == (2020, 1):
            raise RuntimeError("monthly dataset unavailable")
        return build_dataset(current_facility, date_from, date_to)

    command_error = None
    with patch.object(backend, "build_dataset", side_effect=fail_only_in_january) as build:
        try:
            call_command("enforce_retention", stdout=output, stderr=StringIO())
        except CommandError as exc:
            command_error = exc
    assert build.called
    event.refresh_from_db()
    other.refresh_from_db()
    assert not event.is_deleted
    assert (event.data_json, event.search_text, event.updated_at) == event_before
    assert event.deleted_at is None
    assert not event.history.exists()
    assert list(monthly_rows.values("data", "backend_data", "updated_at")) == snapshot_before
    assert isinstance(command_error, CommandError)
    assert "1 event(s) kept" in str(command_error)
    assert "failed=1" in output.getvalue()
    assert "Deferred 0 event/person/case target(s)" in output.getvalue()
    assert other.is_deleted
    other_row = StatisticsSnapshot.objects.get(facility=facility, year=2020, month=2)
    assert other_row.data["total_contacts"] == other_row.backend_data[backend.slug]["total"] == 1
    assert "_errors" not in other_row.backend_data
    markers = list(AuditLog.objects.filter(action=old.action))
    assert [(row.pk, row.timestamp, row.detail) for row in markers] == [marker_before]
    assert _retention_checks()[0].status == ComplianceStatus.CRITICAL
    assert "retention_last_run" in _check_stale_jobs()

    call_command("enforce_retention", stdout=StringIO())
    event.refresh_from_db()
    assert event.is_deleted
    assert event.history.count() == 1
    row = monthly_rows.get()
    expected_total = 2 if snapshot_state == "failed-with-carry" else 1
    assert row.data["total_contacts"] == row.backend_data[backend.slug]["total"] == expected_total
    assert row.backend_data["_deleted"][backend.slug]["total"] == expected_total
    assert "_errors" not in row.backend_data
    assert AuditLog.objects.filter(action=old.action).exclude(pk=old.pk).count() == 1
    assert _retention_checks()[0].status == ComplianceStatus.OK
    assert "retention_last_run" not in _check_stale_jobs()

    create_or_update_snapshot(facility, 2020, 1)
    row.refresh_from_db()
    assert row.data["total_contacts"] == row.backend_data[backend.slug]["total"] == expected_total
