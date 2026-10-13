"""Mutation pins for the snapshot carry-over of deleted events (Refs #2425).

Replace the former ``ensure_snapshots_for_months`` pins: that function left the
running month out and recomputed past months from the surviving events only.
``carry_deleted_event`` carries each deleted event into its local month, the
running one included, in the deleting transaction; ``create_or_update_snapshot``
adds the carry-over to the live counts; ``_soft_delete_events`` keeps an event
whose carry-over failed and goes on with the next one.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from django.db import transaction
from django.utils import timezone

from core.models import Event, StatisticsSnapshot
from core.services.dashboard import carry_deleted_event, create_or_update_snapshot, get_statistics_for_events
from tests._mutation_followup_snapshot_helpers import _make_event

BERLIN = ZoneInfo("Europe/Berlin")


def _current_month_dt():
    today = timezone.localdate()
    return timezone.make_aware(datetime(today.year, today.month, max(1, min(today.day, 28)), 10, 0))


def _delete_and_carry(facility, event):
    with transaction.atomic():
        Event.objects.filter(pk=event.pk).update(is_deleted=True)
        carry_deleted_event(facility, event)


@pytest.mark.django_db
class TestCarryDeletedEventMonth:
    def test_current_month_is_carried(self, facility, staff_user, client_identified, doc_type_contact):
        today = timezone.localdate()
        event = _make_event(facility, client_identified, doc_type_contact, staff_user, _current_month_dt())

        _delete_and_carry(facility, event)

        snapshot = StatisticsSnapshot.objects.get(facility=facility, year=today.year, month=today.month)
        assert snapshot.data["total_contacts"] == 1

    def test_each_month_carries_only_its_own_events(self, facility, staff_user, client_identified, doc_type_contact):
        for day in ((6, 15), (7, 15), (7, 16)):
            event = _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2024, *day, 10, 0))
            _delete_and_carry(facility, event)

        june = StatisticsSnapshot.objects.get(facility=facility, year=2024, month=6)
        july = StatisticsSnapshot.objects.get(facility=facility, year=2024, month=7)
        assert june.backend_data["_deleted"]["_statistics"]["total_contacts"] == 1
        assert july.backend_data["_deleted"]["_statistics"]["total_contacts"] == 2

    def test_month_is_the_local_month(self, facility, staff_user, client_identified, doc_type_contact):
        # 00:30 on 1 July in Berlin is still 30 June in UTC.
        occurred_at = datetime(2024, 7, 1, 0, 30, tzinfo=BERLIN)
        event = _make_event(facility, client_identified, doc_type_contact, staff_user, occurred_at)

        with timezone.override(BERLIN):
            _delete_and_carry(facility, event)

        months = set(StatisticsSnapshot.objects.filter(facility=facility).values_list("year", "month"))
        assert months == {(2024, 7)}

    def test_new_row_counts_live_events_and_carry(self, facility, staff_user, client_identified, doc_type_contact):
        first = _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2024, 6, 15, 10, 0))
        _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2024, 6, 16, 10, 0))

        _delete_and_carry(facility, first)

        snapshot = StatisticsSnapshot.objects.get(facility=facility, year=2024, month=6)
        assert snapshot.data["total_contacts"] == 2
        assert snapshot.backend_data["sample-report-1"]["total"] == 2
        assert snapshot.backend_data["_deleted"]["_statistics"]["total_contacts"] == 1


@pytest.mark.django_db
class TestCarryOverSurvivesRecompute:
    def test_recompute_without_new_deletions_keeps_carry(
        self, facility, staff_user, client_identified, doc_type_contact
    ):
        first = _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2024, 6, 15, 10, 0))
        _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2024, 6, 16, 10, 0))
        _delete_and_carry(facility, first)

        create_or_update_snapshot(facility, 2024, 6)

        snapshot = StatisticsSnapshot.objects.get(facility=facility, year=2024, month=6)
        assert snapshot.data["total_contacts"] == 2
        assert snapshot.backend_data["_deleted"]["_statistics"]["total_contacts"] == 1
        assert snapshot.backend_data["sample-report-1"]["total"] == 2

    def test_snapshot_without_deletions_has_no_carry_key(
        self, facility, staff_user, client_identified, doc_type_contact
    ):
        _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2024, 6, 15, 10, 0))

        create_or_update_snapshot(facility, 2024, 6)

        snapshot = StatisticsSnapshot.objects.get(facility=facility, year=2024, month=6)
        assert "_deleted" not in snapshot.backend_data
        assert snapshot.data["unique_clients"] == 1


@pytest.mark.django_db
class TestFailedCarryKeepsTheEvent:
    def test_failed_event_is_kept_and_the_next_one_deleted(
        self, facility, staff_user, client_identified, doc_type_contact, monkeypatch
    ):
        import core.services.dashboard.snapshot as snapshots
        from core.retention.enforcement import _soft_delete_events

        failing = _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2024, 6, 15, 10, 0))
        other = _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2024, 7, 15, 10, 0))
        summarize = snapshots.get_statistics_for_events

        def fail_for_one(events):
            if events.filter(pk=failing.pk).exists():
                raise RuntimeError("carry failed")
            return summarize(events)

        monkeypatch.setattr(snapshots, "get_statistics_for_events", fail_for_one)

        count, deleted_ids = _soft_delete_events(
            Event.objects.filter(facility=facility), facility=facility, category="identified", retention_days=1
        )

        assert (count, deleted_ids) == (1, [other.pk])
        assert set(Event.objects.filter(is_deleted=True).values_list("pk", flat=True)) == {other.pk}
        assert not StatisticsSnapshot.objects.filter(facility=facility, year=2024, month=6).exists()

    def test_other_errors_still_stop_the_run(
        self, facility, staff_user, client_identified, doc_type_contact, monkeypatch
    ):
        from core.retention.enforcement import _soft_delete_events

        _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2024, 6, 15, 10, 0))

        def crash(event, **kwargs):
            raise RuntimeError("attachment cleanup crashed")

        monkeypatch.setattr("core.retention.enforcement.delete_attachments_for", crash)

        with pytest.raises(RuntimeError, match="crashed"):
            _soft_delete_events(
                Event.objects.filter(facility=facility), facility=facility, category="identified", retention_days=1
            )
        assert not Event.objects.filter(is_deleted=True).exists()
        assert not StatisticsSnapshot.objects.exists()


@pytest.mark.django_db
class TestStatisticsForEvents:
    def test_counts_exactly_the_given_events_regardless_of_deletion(
        self, facility, staff_user, client_identified, doc_type_contact
    ):
        _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2024, 6, 15, 10, 0))
        _make_event(facility, None, doc_type_contact, staff_user, datetime(2024, 6, 15, 11, 0), anonymous=True)
        _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2024, 7, 1, 10, 0))
        events = Event.objects.filter(facility=facility, occurred_at__month=6)
        events.update(is_deleted=True)

        stats = get_statistics_for_events(events)

        assert stats == {
            "total_contacts": 2,
            "by_contact_stage": {"anonym": 1, "identifiziert": 1, "qualifiziert": 0},
            "persons_by_contact_stage": {"anonym": 1, "identifiziert": 1, "qualifiziert": 0},
            "by_document_type": [{"name": doc_type_contact.name, "count": 2}],
            "by_age_cluster": [
                {
                    "cluster": client_identified.age_cluster,
                    "label": stats["by_age_cluster"][0]["label"],
                    "count": 1,
                    "persons": 1,
                }
            ],
        }
