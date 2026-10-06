"""Mutation pins for the snapshot carry-over of deleted events (Refs #2425).

Replace the former ``ensure_snapshots_for_months`` pins: that function left the
running month out and recomputed past months from the surviving events only.
``record_deleted_events`` records every month of the deleted events, the
running one included, and ``create_or_update_snapshot`` adds the carry-over
to the live counts.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from django.utils import timezone

from core.models import Event, StatisticsSnapshot
from core.services.dashboard import create_or_update_snapshot, get_statistics_for_events, record_deleted_events
from tests._mutation_followup_snapshot_helpers import _make_event


def _current_month_dt():
    today = timezone.localdate()
    return timezone.make_aware(datetime(today.year, today.month, max(1, min(today.day, 28)), 10, 0))


def _delete_all(facility):
    events = Event.objects.filter(facility=facility)
    events.update(is_deleted=True)
    return events


@pytest.mark.django_db
class TestRecordDeletedEventsMonths:
    def test_current_month_is_recorded(self, facility, staff_user, client_identified, doc_type_contact):
        today = timezone.localdate()
        _make_event(facility, client_identified, doc_type_contact, staff_user, _current_month_dt())

        record_deleted_events(facility, _delete_all(facility))

        snapshot = StatisticsSnapshot.objects.get(facility=facility, year=today.year, month=today.month)
        assert snapshot.data["total_contacts"] == 1

    def test_past_and_current_month_are_both_recorded(self, facility, staff_user, client_identified, doc_type_contact):
        today = timezone.localdate()
        _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2024, 6, 15, 10, 0))
        _make_event(facility, client_identified, doc_type_contact, staff_user, _current_month_dt())

        record_deleted_events(facility, _delete_all(facility))

        months = set(StatisticsSnapshot.objects.filter(facility=facility).values_list("year", "month"))
        assert months == {(2024, 6), (today.year, today.month)}

    def test_each_month_carries_only_its_own_events(self, facility, staff_user, client_identified, doc_type_contact):
        _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2024, 6, 15, 10, 0))
        _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2024, 7, 15, 10, 0))
        _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2024, 7, 16, 10, 0))

        record_deleted_events(facility, _delete_all(facility))

        june = StatisticsSnapshot.objects.get(facility=facility, year=2024, month=6)
        july = StatisticsSnapshot.objects.get(facility=facility, year=2024, month=7)
        assert june.backend_data["_deleted"]["_statistics"]["total_contacts"] == 1
        assert july.backend_data["_deleted"]["_statistics"]["total_contacts"] == 2


@pytest.mark.django_db
class TestCarryOverSurvivesRecompute:
    def test_recompute_without_new_deletions_keeps_carry(
        self, facility, staff_user, client_identified, doc_type_contact
    ):
        _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2024, 6, 15, 10, 0))
        _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2024, 6, 16, 10, 0))
        first = Event.objects.filter(facility=facility).order_by("occurred_at").first()
        Event.objects.filter(pk=first.pk).update(is_deleted=True)
        record_deleted_events(facility, Event.objects.filter(pk=first.pk))

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
