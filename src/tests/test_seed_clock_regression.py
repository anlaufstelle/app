"""Bulk event seeding must not shift its random stream at clock boundaries."""

import json
import random
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest

from core.models import Activity, DocumentType, Event
from core.seed import activities, events
from core.seed.users import seed_users
from tests.test_seed_event_min_contact_stage import CFG, _load_default_doc_types, _make_clients


@pytest.fixture
def seed_clock(monkeypatch):
    current = [datetime(2026, 10, 13, 8, 9, 59, tzinfo=UTC)]
    real_timezone = events.timezone

    class Clock:
        def __getattr__(self, name):
            return getattr(real_timezone, name)

        def now(self):
            return current[0]

        def localdate(self, value=None, timezone=None):
            return real_timezone.localdate(value or self.now(), timezone)

    monkeypatch.setattr(events, "timezone", Clock())
    return current


@pytest.mark.django_db
@pytest.mark.parametrize(
    "hour, minute",
    [(8, 9), (9, 59), (7, 59), (23, 59)],
    ids=["five-minute-boundary", "hour-boundary", "opening-boundary", "midnight-boundary"],
)
def test_bulk_seed_content_is_stable_across_clock_boundaries(facility, seed_clock, hour, minute):
    users = seed_users(facility, 0)
    clients = _make_clients(facility, users[0], n_identified=6, n_qualified=6)
    _load_default_doc_types(facility, keep_slugs=["beratungsgespraech", "kontakt"])
    encrypted = {
        dt.pk: {
            field.field_template.slug
            for field in dt.fields.select_related("field_template")
            if field.field_template.is_encrypted
        }
        for dt in DocumentType.objects.filter(facility=facility)
    }

    def snapshot():
        return Counter(
            (
                event.document_type_id,
                event.client_id,
                event.is_anonymous,
                event.search_text,
                json.dumps(
                    {
                        key: "<encrypted>" if key in encrypted[event.document_type_id] else value
                        for key, value in event.data_json.items()
                    },
                    sort_keys=True,
                ),
            )
            for event in Event.objects.filter(facility=facility)
        )

    seed_clock[0] = seed_clock[0].replace(hour=hour, minute=minute)
    random.seed(2270)
    assert events.seed_events_bulk(facility, users, clients, CFG) == CFG["events_per_facility"]
    first = snapshot()
    Event.objects.filter(facility=facility).delete()
    seed_clock[0] += timedelta(seconds=1)
    random.seed(2270)
    assert events.seed_events_bulk(facility, users, clients, CFG) == CFG["events_per_facility"]
    assert snapshot() == first


@pytest.mark.django_db
def test_bulk_seed_uses_supplied_clock_for_timestamps_and_data_dates(facility):
    users = seed_users(facility, 0)
    clients = _make_clients(facility, users[0], n_identified=6, n_qualified=6)
    _load_default_doc_types(facility, keep_slugs=["begleitung"])
    now = datetime(2020, 1, 2, 8, 9, 59, tzinfo=UTC)
    random.seed(2270)
    assert events.seed_events_bulk(facility, users, clients, CFG, now=now) == CFG["events_per_facility"]
    for event in Event.objects.filter(facility=facility):
        assert event.occurred_at <= now
        appointment = datetime.fromisoformat(event.data_json["datum"]).date()
        assert now.date() - timedelta(days=7) <= appointment <= now.date() + timedelta(days=14)


@pytest.mark.django_db
def test_activity_sampling_is_stable_across_cutoff_time(facility, seed_clock, monkeypatch):
    users = seed_users(facility, 0)
    clients = _make_clients(facility, users[0], n_identified=6, n_qualified=6)
    _load_default_doc_types(facility, keep_slugs=["kontakt"])
    now = seed_clock[0].replace(hour=11, minute=59, second=59)
    random.seed(2270)
    events.seed_events_bulk(facility, users, clients, CFG, now=now)
    Event.objects.filter(facility=facility).update(
        occurred_at=(now - timedelta(days=90)).replace(hour=12, minute=0, second=0)
    )
    monkeypatch.setattr(activities, "timezone", events.timezone)

    def snapshot():
        return Counter(Activity.objects.filter(facility=facility).values_list("target_type_id", "target_id", "verb"))

    seed_clock[0] = now
    random.seed(2270)
    first_count = activities.seed_activities(facility, users, CFG)
    first = snapshot()
    first_state = random.getstate()
    Activity.objects.filter(facility=facility).delete()
    seed_clock[0] += timedelta(seconds=2)
    random.seed(2270)
    assert activities.seed_activities(facility, users, CFG) == first_count
    assert snapshot() == first
    assert random.getstate() == first_state
