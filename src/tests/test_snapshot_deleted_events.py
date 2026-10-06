"""Monthly statistics keep counting events that a retention run deleted (Refs #2425).

The monthly aggregate is the anonymous remainder of the deleted events. A
deletion run must never lower an existing aggregate because events were
deleted — neither across several runs in the same month nor for events the
run deletes while their month is still running.
"""

from datetime import datetime, time, timedelta

import pytest
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

from core.models import Event, Settings, StatisticsSnapshot


def _past_month_noon():
    """Noon on the 10th of a month that lies at least one full month back."""
    day = (timezone.localdate().replace(day=1) - timedelta(days=40)).replace(day=10)
    return timezone.make_aware(datetime.combine(day, time(12, 0)))


def _event(facility, doc_type, user, occurred_at, client=None):
    return Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=occurred_at,
        data_json={},
        is_anonymous=client is None,
        created_by=user,
    )


def _delete_and_carry(facility, event):
    """Mark ``event`` deleted and carry it, in one transaction like a retention run."""
    from core.services.dashboard import carry_deleted_event

    with transaction.atomic():
        Event.objects.filter(pk=event.pk).update(is_deleted=True)
        carry_deleted_event(facility, event)


@pytest.mark.django_db
class TestRetentionRunsKeepMonthlyCounts:
    def test_second_run_in_same_month_keeps_events_of_first_run(
        self, facility, staff_user, client_identified, doc_type_contact
    ):
        occurred_at = _past_month_noon()
        for _ in range(2):
            _event(facility, doc_type_contact, staff_user, occurred_at)
            _event(facility, doc_type_contact, staff_user, occurred_at, client=client_identified)
        settings_obj = Settings.objects.create(
            facility=facility,
            retention_anonymous_days=1,
            retention_identified_days=3650,
            retention_qualified_days=3650,
        )

        call_command("enforce_retention")
        snapshot = StatisticsSnapshot.objects.get(facility=facility, year=occurred_at.year, month=occurred_at.month)
        assert snapshot.data["total_contacts"] == 4
        assert Event.objects.filter(facility=facility, is_deleted=True).count() == 2

        settings_obj.retention_identified_days = 1
        settings_obj.save()
        call_command("enforce_retention")

        snapshot.refresh_from_db()
        assert Event.objects.filter(facility=facility, is_deleted=False).count() == 0
        assert snapshot.data["total_contacts"] == 4
        assert snapshot.data["by_contact_stage"]["anonym"] == 2
        assert snapshot.data["by_contact_stage"]["identifiziert"] == 2

    def test_events_deleted_while_month_runs_count_in_month_end_snapshot(
        self, facility, staff_user, client_identified, doc_type_contact, monkeypatch
    ):
        from core.services.dashboard import create_or_update_snapshot

        occurred_at = _past_month_noon()
        _event(facility, doc_type_contact, staff_user, occurred_at)
        _event(facility, doc_type_contact, staff_user, occurred_at, client=client_identified)
        Settings.objects.create(
            facility=facility,
            retention_anonymous_days=1,
            retention_identified_days=3650,
            retention_qualified_days=3650,
        )
        # The snapshot service sees the events' month as the running month,
        # as with a short deadline that deletes events before the month ends.
        running_day = timezone.localdate(occurred_at) + timedelta(days=5)
        monkeypatch.setattr("core.services.dashboard.snapshot.timezone.localdate", lambda *args: running_day)

        call_command("enforce_retention")
        monkeypatch.undo()
        assert Event.objects.filter(facility=facility, is_deleted=True).count() == 1

        # Month-end snapshot run, after the month has ended.
        create_or_update_snapshot(facility, occurred_at.year, occurred_at.month)

        snapshot = StatisticsSnapshot.objects.get(facility=facility, year=occurred_at.year, month=occurred_at.month)
        assert snapshot.data["total_contacts"] == 2
        assert snapshot.data["by_contact_stage"]["anonym"] == 1
        assert snapshot.data["by_contact_stage"]["identifiziert"] == 1

    def test_sample_report_payload_keeps_deleted_events(self, facility, staff_user, doc_type_contact):
        occurred_at = _past_month_noon()
        for _ in range(3):
            _event(facility, doc_type_contact, staff_user, occurred_at)
        settings_obj = Settings.objects.create(
            facility=facility,
            retention_anonymous_days=3650,
            retention_identified_days=3650,
            retention_qualified_days=3650,
        )
        call_command("create_statistics_snapshots", year=occurred_at.year, month=occurred_at.month)
        snapshot = StatisticsSnapshot.objects.get(facility=facility, year=occurred_at.year, month=occurred_at.month)
        before = snapshot.backend_data["sample-report-1"]["total"]
        assert before == 3

        settings_obj.retention_anonymous_days = 1
        settings_obj.save()
        call_command("enforce_retention")
        call_command("create_statistics_snapshots", year=occurred_at.year, month=occurred_at.month)

        snapshot.refresh_from_db()
        assert snapshot.backend_data["sample-report-1"]["total"] == before
        assert snapshot.data["total_contacts"] == 3


@pytest.mark.django_db
class TestRunningMonthRow:
    def test_deletion_in_running_month_writes_its_row(self, facility, staff_user, doc_type_contact):
        event = _event(facility, doc_type_contact, staff_user, timezone.now())
        today = timezone.localdate()

        _delete_and_carry(facility, event)

        snapshot = StatisticsSnapshot.objects.get(facility=facility, year=today.year, month=today.month)
        assert snapshot.data["total_contacts"] == 1
        assert snapshot.backend_data["_deleted"]["_statistics"]["total_contacts"] == 1

    def test_lead_card_names_latest_completed_month(self, facility, lead_user):
        from core.services.dashboard import lead_dashboard_context

        today = timezone.localdate()
        completed = StatisticsSnapshot.objects.create(facility=facility, year=2024, month=6, data={})
        StatisticsSnapshot.objects.create(facility=facility, year=today.year, month=today.month, data={})

        assert lead_dashboard_context(lead_user, facility)["last_snapshot"] == completed


@pytest.mark.django_db
class TestCarryDeletedEvent:
    def test_events_of_one_month_add_up(self, facility, staff_user, doc_type_contact):
        occurred_at = _past_month_noon()
        events = [_event(facility, doc_type_contact, staff_user, occurred_at + timedelta(hours=h)) for h in range(3)]

        for event in events:
            _delete_and_carry(facility, event)

        snapshot = StatisticsSnapshot.objects.get(facility=facility)
        assert snapshot.data["total_contacts"] == 3
        assert snapshot.backend_data["_deleted"]["_statistics"]["total_contacts"] == 3

    def test_existing_row_keeps_its_counts_and_extends_the_carry(self, facility, staff_user, doc_type_contact):
        from core.services.dashboard import create_or_update_snapshot

        occurred_at = _past_month_noon()
        first = _event(facility, doc_type_contact, staff_user, occurred_at)
        _event(facility, doc_type_contact, staff_user, occurred_at)
        create_or_update_snapshot(facility, occurred_at.year, occurred_at.month)

        _delete_and_carry(facility, first)

        snapshot = StatisticsSnapshot.objects.get(facility=facility)
        assert snapshot.data["total_contacts"] == 2
        assert snapshot.backend_data["sample-report-1"]["total"] == 2
        assert snapshot.backend_data["_deleted"]["_statistics"]["total_contacts"] == 1
        assert snapshot.backend_data["_deleted"]["sample-report-1"]["total"] == 1

    def test_later_runs_add_to_carry_and_keep_persons_a_lower_bound(
        self, facility, staff_user, client_identified, doc_type_contact
    ):
        occurred_at = _past_month_noon()
        first = _event(facility, doc_type_contact, staff_user, occurred_at, client=client_identified)
        second = _event(facility, doc_type_contact, staff_user, occurred_at, client=client_identified)
        for event in (first, second):
            _delete_and_carry(facility, event)

        data = StatisticsSnapshot.objects.get(facility=facility).data
        assert data["total_contacts"] == 2
        assert data["by_contact_stage"] == {"anonym": 0, "identifiziert": 2, "qualifiziert": 0}
        # One person behind both events: never summed to 2.
        assert data["persons_by_contact_stage"]["identifiziert"] == 1
        assert [(row["count"], row["persons"]) for row in data["by_age_cluster"]] == [(2, 1)]

    def test_failing_backend_rolls_back_and_keeps_earlier_carry(
        self, facility, staff_user, doc_type_contact, monkeypatch
    ):
        from core.reporting.backends.sample_report_1 import SampleReport1Backend
        from core.services.dashboard.snapshot import SnapshotCarryError

        occurred_at = _past_month_noon()
        first = _event(facility, doc_type_contact, staff_user, occurred_at)
        second = _event(facility, doc_type_contact, staff_user, occurred_at)
        _delete_and_carry(facility, first)

        live_summary = SampleReport1Backend.summarize_events

        def broken_for_deleted(self, events):
            # The live payload (``build_dataset``) keeps working.
            if events.filter(is_deleted=True).exists():
                raise RuntimeError("boom")
            return live_summary(self, events)

        monkeypatch.setattr(SampleReport1Backend, "summarize_events", broken_for_deleted)
        with pytest.raises(SnapshotCarryError):
            _delete_and_carry(facility, second)

        backend_data = StatisticsSnapshot.objects.get(facility=facility).backend_data
        assert "_errors" not in backend_data
        assert backend_data["_deleted"]["sample-report-1"]["total"] == 1
        assert backend_data["_deleted"]["_statistics"]["total_contacts"] == 1
        assert Event.objects.get(pk=second.pk).is_deleted is False

    def test_backend_without_summary_hook_fails_the_carry(self, facility, staff_user, doc_type_contact, monkeypatch):
        from core.reporting.backends.sample_report_1 import SampleReport1Backend
        from core.reporting.base import DatasetBackend
        from core.services.dashboard.snapshot import SnapshotCarryError

        occurred_at = _past_month_noon()
        first = _event(facility, doc_type_contact, staff_user, occurred_at)
        second = _event(facility, doc_type_contact, staff_user, occurred_at)
        _delete_and_carry(facility, first)

        live_summary = SampleReport1Backend.summarize_events

        def no_summary_for_deleted(self, events):
            if events.filter(is_deleted=True).exists():
                return DatasetBackend.summarize_events(self, events)
            return live_summary(self, events)

        monkeypatch.setattr(SampleReport1Backend, "summarize_events", no_summary_for_deleted)
        with pytest.raises(SnapshotCarryError):
            _delete_and_carry(facility, second)

        backend_data = StatisticsSnapshot.objects.get(facility=facility).backend_data
        assert backend_data["_deleted"]["sample-report-1"]["total"] == 1
        assert Event.objects.get(pk=second.pk).is_deleted is False

    def test_every_snapshot_backend_can_summarize_deleted_events(self):
        from core.reporting.base import DatasetBackend
        from core.reporting.registry import get_backends
        from core.services.dashboard.snapshot import _SUMMARY_MERGERS

        snapshot_backends = [backend for backend in get_backends() if backend.snapshot_enabled]
        assert snapshot_backends
        for backend in snapshot_backends:
            assert type(backend).summarize_events is not DatasetBackend.summarize_events, backend.slug
            assert backend.slug in _SUMMARY_MERGERS, backend.slug


@pytest.mark.django_db
class TestRetentionCommandCarry:
    def _setup(self, facility, staff_user, doc_type_contact):
        occurred_at = _past_month_noon()
        _event(facility, doc_type_contact, staff_user, occurred_at)
        Settings.objects.create(
            facility=facility,
            retention_anonymous_days=1,
            retention_identified_days=3650,
            retention_qualified_days=3650,
        )
        return occurred_at

    def test_event_left_live_by_the_run_is_not_carried(self, facility, staff_user, doc_type_contact, monkeypatch):
        self._setup(facility, staff_user, doc_type_contact)
        monkeypatch.setattr(
            "core.management.commands.enforce_retention.process_facility_retention",
            lambda *args, **kwargs: {"count": 0},
        )

        call_command("enforce_retention")

        assert not StatisticsSnapshot.objects.filter(facility=facility).exists()

    def test_dry_run_writes_no_snapshot(self, facility, staff_user, doc_type_contact):
        self._setup(facility, staff_user, doc_type_contact)

        call_command("enforce_retention", "--dry-run")

        assert not StatisticsSnapshot.objects.filter(facility=facility).exists()
        assert Event.objects.filter(facility=facility, is_deleted=False).count() == 1
