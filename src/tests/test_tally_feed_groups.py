"""Hourly tally groups share the counters' visibility and origin contract."""

from datetime import UTC, datetime, time, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from django.urls import reverse
from django.utils import timezone

from core.constants import FEED_MAX_PER_TYPE
from core.models import DocumentType, Event
from core.services.events.feed import build_feed_items
from core.services.events.tally import build_tally_summary
from tests._factories import insert_event_past_save
from tests.test_a11y_board_navigation import TreeParser
from tests.test_zeitstrom_watch import _watch

pytestmark = pytest.mark.django_db


def _at(day, hour, minute=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _event(facility, user, doc_type, when, **kwargs):
    create = insert_event_past_save if doc_type.system_type == "ban" else Event.objects.create
    return create(
        facility=facility,
        document_type=doc_type,
        created_by=user,
        occurred_at=when,
        is_anonymous=True,
        created_via=Event.CreatedVia.TALLY,
        **kwargs,
    )


def _groups(facility, user, day, **kwargs):
    return [
        item["object"]
        for item in build_feed_items(facility, day, "events", user=user, **kwargs)
        if item["type"] == "tally_group"
    ]


def test_groups_are_complete_before_both_feed_caps(facility, staff_user, doc_type_contact):
    day = timezone.localdate()
    disabled = DocumentType.objects.create(facility=facility, name="Disabled", is_active=False)
    for hour, count, doc_type in [(8, FEED_MAX_PER_TYPE + 2, doc_type_contact), (9, 3, disabled), (10, 1, disabled)]:
        Event.objects.bulk_create(
            [
                Event(
                    facility=facility,
                    document_type=doc_type,
                    created_by=staff_user,
                    occurred_at=_at(day, hour),
                    is_anonymous=True,
                    created_via=Event.CreatedVia.TALLY,
                )
                for _ in range(count)
            ]
        )
    Event.objects.bulk_create(
        [
            Event(facility=facility, document_type=doc_type_contact, created_by=staff_user, occurred_at=_at(day, 23))
            for _ in range(FEED_MAX_PER_TYPE)
        ]
    )
    groups = _groups(facility, staff_user, day)
    assert len(groups) == 3
    assert sorted(g["total"] for g in groups) == [1, 3, FEED_MAX_PER_TYPE + 2]
    assert sum(g["total"] for g in groups) == FEED_MAX_PER_TYPE + 6
    assert {g["start"].hour for g in groups} == {8, 9, 10}
    assert len(build_feed_items(facility, day, "events", user=staff_user)) == FEED_MAX_PER_TYPE


def test_same_hour_has_one_group_with_type_breakdown_and_standard_contact(
    facility, staff_user, doc_type_contact, doc_type_crisis
):
    day = timezone.localdate()
    for doc_type in [doc_type_contact, doc_type_contact, doc_type_crisis]:
        _event(facility, staff_user, doc_type, _at(day, 12, 30))
    standard = Event.objects.create(
        facility=facility,
        document_type=doc_type_contact,
        created_by=staff_user,
        occurred_at=_at(day, 12, 45),
        is_anonymous=True,
    )
    groups = _groups(facility, staff_user, day)
    assert len(groups) == 1
    assert groups[0]["total"] == 3
    assert {t["name"]: t["count"] for t in groups[0]["types"]} == {
        doc_type_contact.name: 2,
        doc_type_crisis.name: 1,
    }
    feed = build_feed_items(facility, day, "events", user=staff_user)
    assert [i["object"].pk for i in feed if i["type"] == "event"] == [standard.pk]
    filtered = _groups(facility, staff_user, day, document_type_id=doc_type_contact.pk)
    assert filtered[0]["total"] == 2


def test_group_total_matches_live_summary_for_all_types_and_visibility(
    facility, assistant_user, staff_user, doc_type_contact, other_facility
):
    from core.services.events.tally import current_tally_context

    context = current_tally_context(facility)
    when = context.window_start + timedelta(minutes=1)
    disabled = DocumentType.objects.create(facility=facility, name="Disabled", is_active=False)
    high = DocumentType.objects.create(facility=facility, name="Private", sensitivity="high")
    ban = DocumentType.objects.create(facility=facility, name="Ban", system_type="ban", min_contact_stage="identified")
    for doc_type in (doc_type_contact, disabled, high, ban):
        _event(facility, staff_user, doc_type, when)
    _event(facility, staff_user, doc_type_contact, when, is_deleted=True)
    _event(facility, staff_user, doc_type_contact, context.window_start - timedelta(seconds=1))
    foreign_type = DocumentType.objects.create(facility=other_facility, name="Other")
    _event(other_facility, staff_user, foreign_type, when)
    for user in (assistant_user, staff_user):
        summary = build_tally_summary(facility, user, context=context)
        groups = _groups(facility, user, context.shift_date, time_filter=context.time_filter)
        assert sum(g["total"] for g in groups) == summary["tally_total"]
        assert sum(g["total"] for g in groups) == 3
        mixed = build_feed_items(facility, context.shift_date, "", context.time_filter, user=user)
        assert sum(i["object"]["total"] for i in mixed if i["type"] == "tally_group") == 3
        assert not [i for i in mixed if i["type"] == "ban"]


def test_group_toggle_and_list_link_render_through_the_feed(client, staff_user, facility, doc_type_contact):
    day = timezone.localdate()
    _event(facility, staff_user, doc_type_contact, _at(day, 18, 15))
    client.force_login(staff_user)
    response = client.get(reverse("core:zeitstrom_feed_partial"), {"time_filter": "all"})
    root = TreeParser(response.content.decode()).root
    toggle = next(n for n in root.iter("button") if n.get("data-testid") == "tally-group-toggle")
    assert toggle.get("aria-expanded") == "false"
    assert toggle.get(":aria-expanded") == "expanded"
    assert toggle.get("@click") == "toggle"
    details = next(n for n in root.iter() if n.get("id") == toggle.get("aria-controls"))
    assert details.get("x-show") == "expanded"
    assert any(n.get("data-count") == "1" for n in details.iter())
    assert [child.get("data-feed-column") for child in toggle] == ["edge", "time", "type", "person", "details", "by"]
    link = next(n for n in details.iter("a"))
    params = parse_qs(urlsplit(link.get("href")).query)
    assert params == {"date_from": [day.isoformat()], "date_to": [day.isoformat()], "created_via": ["tally"]}
    assert sum(g["total"] for g in _groups(facility, staff_user, day)) == 1


def test_watch_and_next_feed_refresh_follow_increment_and_undo(client, facility, staff_user, doc_type_contact):
    client.force_login(staff_user)
    params = {"time_filter": "all", "type": "events"}
    first = _watch(client, params=params)
    event = _event(facility, staff_user, doc_type_contact, timezone.now())
    second = _watch(client, params=params, etag=first["ETag"], validators=first.json()["validators"])
    assert "feed" in second.json()["components"]
    assert _groups(facility, staff_user, timezone.localdate())[0]["total"] == 1
    # Same group, a count change rather than topology alone.
    another = _event(facility, staff_user, doc_type_contact, timezone.now())
    third = _watch(client, params=params, etag=second["ETag"], validators=second.json()["validators"])
    assert "feed" in third.json()["components"]
    response = client.get(reverse("core:zeitstrom_feed_partial"), params)
    assert 'data-total="2"' in response.content.decode()
    Event.objects.filter(pk=another.pk).update(is_deleted=True)
    fourth = _watch(client, params=params, etag=third["ETag"], validators=third.json()["validators"])
    assert "feed" in fourth.json()["components"]
    response = client.get(reverse("core:zeitstrom_feed_partial"), params)
    assert 'data-total="1"' in response.content.decode()
    Event.objects.filter(pk=event.pk).update(is_deleted=True)
    assert _groups(facility, staff_user, timezone.localdate()) == []


def test_autumn_repeated_hour_keeps_both_real_hour_groups(facility, staff_user, doc_type_contact):
    for hour in (0, 1):
        _event(facility, staff_user, doc_type_contact, datetime(2026, 10, 25, hour, 15, tzinfo=UTC))
    groups = _groups(facility, staff_user, datetime(2026, 10, 25).date())
    assert len(groups) == 2
    assert len({g["key"] for g in groups}) == 2
    assert sum(g["total"] for g in groups) == 2


@pytest.mark.parametrize("feed_type", ["", "events", "bans"])
def test_watch_ignores_tally_ban_minutes_only_when_the_feed_groups_them(client, facility, staff_user, feed_type):
    day = timezone.localdate()
    ban_type = DocumentType.objects.create(
        facility=facility, name="Ban", system_type="ban", min_contact_stage="identified"
    )
    event = _event(facility, staff_user, ban_type, _at(day, 12, 15))
    client.force_login(staff_user)
    params = {"date": day.isoformat(), "time_filter": "all", "type": feed_type}
    before_feed = client.get(reverse("core:zeitstrom_feed_partial"), params)
    before_watch = _watch(client, params=params)
    Event.objects.filter(pk=event.pk).update(occurred_at=_at(day, 12, 45))
    after_feed = client.get(reverse("core:zeitstrom_feed_partial"), params)
    after_watch = _watch(client, params=params)
    assert before_feed.status_code == after_feed.status_code == 200
    assert before_watch.status_code == after_watch.status_code == 200
    feed_changed = before_feed.content != after_feed.content
    watch_changed = before_watch.json()["validators"]["feed"] != after_watch.json()["validators"]["feed"]
    assert feed_changed == (feed_type == "bans")
    assert watch_changed == feed_changed
