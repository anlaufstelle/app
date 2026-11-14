"""Zeitstrom feed order follows the displayed minute, then the recording order.

Event times come from the form at minute precision (``%H:%M``), so every
contact carries ``:00`` seconds. Activities (deletions, updates) carry the
exact instant of the action. Sorting the merged feed by the raw timestamp
put a deletion above every contact recorded later in the same minute, and
contacts sharing a minute came back in arbitrary database order.

The feed now compares the minute shown on the card first and breaks ties by
the moment the entry was recorded (``created_at`` for events, bans and work
items, the activity instant for activities).
"""

from datetime import datetime, time, timedelta

import pytest
from django.utils import timezone

from core.models import Activity, Event
from core.services.events import build_feed_items
from core.services.events.crud import create_event, soft_delete_event
from core.services.zeitstrom_filters import ZeitstromState
from core.services.zeitstrom_watch import _feed_projection
from tests._factories import make_document_type

pytestmark = pytest.mark.django_db

_DAY = timezone.localdate()
_MINUTE = timezone.make_aware(datetime.combine(_DAY, time(12, 3)))


def _state(feed_type=""):
    return ZeitstromState(
        target_date=_DAY,
        time_filter=None,
        time_filters=(),
        live_context=None,
        feed_type=feed_type,
        doc_type_id="",
        view="",
        local_date=_DAY,
    )


def _contact(facility, user, doc_type, client, *, occurred_at, recorded_at):
    event = create_event(facility, user, doc_type, occurred_at, {}, client=client)
    Event.objects.filter(pk=event.pk).update(created_at=recorded_at)
    return event


def _labels(items, names):
    return [names.get(item["object"].pk, item["type"]) for item in items]


@pytest.fixture
def same_minute_feed(facility, staff_user, doc_type_contact, client_identified):
    """Contacts A, B, C entered at 12:03 with a deletion between A and B."""
    second = timedelta(seconds=1)
    old = _contact(
        facility, staff_user, doc_type_contact, client_identified, occurred_at=_MINUTE, recorded_at=_MINUTE + second
    )
    # Insert in an order that differs from the recording order, so the
    # database row order cannot produce the expected result by accident.
    c = _contact(
        facility,
        staff_user,
        doc_type_contact,
        client_identified,
        occurred_at=_MINUTE,
        recorded_at=_MINUTE + 50 * second,
    )
    a = _contact(
        facility,
        staff_user,
        doc_type_contact,
        client_identified,
        occurred_at=_MINUTE,
        recorded_at=_MINUTE + 10 * second,
    )
    b = _contact(
        facility,
        staff_user,
        doc_type_contact,
        client_identified,
        occurred_at=_MINUTE,
        recorded_at=_MINUTE + 40 * second,
    )
    soft_delete_event(old, staff_user)
    Activity.objects.filter(verb=Activity.Verb.DELETED).update(occurred_at=_MINUTE + 20 * second)
    return {a.pk: "A", b.pk: "B", c.pk: "C"}


def test_same_minute_entries_follow_recording_order(facility, staff_user, same_minute_feed):
    items = build_feed_items(facility, _DAY, "", user=staff_user)

    assert _labels(items, same_minute_feed) == ["C", "B", "activity", "A"]


def test_watch_projection_uses_the_feed_order(facility, staff_user, same_minute_feed):
    projection = _feed_projection(facility, staff_user, _state())
    names = {str(pk): label for pk, label in same_minute_feed.items()}

    assert [names.get(value[0], kind) for kind, value in projection] == ["C", "B", "activity", "A"]


def test_contact_time_still_beats_recording_time_across_minutes(
    facility, staff_user, doc_type_contact, client_identified
):
    """A back-dated contact stays at the minute it happened."""
    late_entry = _contact(
        facility,
        staff_user,
        doc_type_contact,
        client_identified,
        occurred_at=_MINUTE - timedelta(minutes=30),
        recorded_at=_MINUTE + timedelta(minutes=5),
    )
    earlier_entry = _contact(
        facility, staff_user, doc_type_contact, client_identified, occurred_at=_MINUTE, recorded_at=_MINUTE
    )

    items = build_feed_items(facility, _DAY, "events", user=staff_user)

    assert [item["object"].pk for item in items] == [earlier_entry.pk, late_entry.pk]


def _deletion(facility, user, doc_type, client, *, at):
    """Delete a fresh contact and set the exact instant of the deletion entry."""
    event = create_event(facility, user, doc_type, _MINUTE - timedelta(hours=1), {}, client=client)
    soft_delete_event(event, user)
    activity = Activity.objects.filter(verb=Activity.Verb.DELETED, target_id=event.pk).get()
    Activity.objects.filter(pk=activity.pk).update(occurred_at=at)
    return activity


def test_activities_in_one_minute_follow_their_exact_instant(facility, staff_user, doc_type_contact, client_identified):
    early = _deletion(facility, staff_user, doc_type_contact, client_identified, at=_MINUTE + timedelta(seconds=5))
    late = _deletion(facility, staff_user, doc_type_contact, client_identified, at=_MINUTE + timedelta(seconds=45))
    middle = _deletion(facility, staff_user, doc_type_contact, client_identified, at=_MINUTE + timedelta(seconds=25))

    items = build_feed_items(facility, _DAY, "activities", user=staff_user)

    deletions = [item["object"].pk for item in items if item["object"].verb == Activity.Verb.DELETED]
    assert deletions == [late.pk, middle.pk, early.pk]


def test_bans_in_one_minute_follow_recording_order(facility, staff_user, client_identified):
    ban_type = make_document_type(facility=facility, name="Hausverbot", system_type="ban")
    second = timedelta(seconds=1)
    # Insertion order (middle, first, last) differs from the expected display
    # order (last, middle, first) and from its reverse, so neither the physical
    # row order nor its reverse can pass; only the recording-time tiebreaker does.
    recorded = {}
    for label, offset in (("middle", 25), ("first", 5), ("last", 45)):
        ban = _contact(
            facility,
            staff_user,
            ban_type,
            client_identified,
            occurred_at=_MINUTE,
            recorded_at=_MINUTE + offset * second,
        )
        recorded[ban.pk] = label

    items = build_feed_items(facility, _DAY, "bans", user=staff_user)

    assert [item["type"] for item in items] == ["ban", "ban", "ban"]
    assert [recorded[item["object"].pk] for item in items] == ["last", "middle", "first"]


def test_contact_after_a_later_minute_deletion_stays_below_it(
    facility, staff_user, doc_type_contact, client_identified
):
    """The shown minute still decides first: a deletion in a later minute is on top."""
    contact = _contact(
        facility,
        staff_user,
        doc_type_contact,
        client_identified,
        occurred_at=_MINUTE,
        recorded_at=_MINUTE + timedelta(seconds=50),
    )
    deletion = _deletion(
        facility, staff_user, doc_type_contact, client_identified, at=_MINUTE + timedelta(minutes=1, seconds=1)
    )

    items = build_feed_items(facility, _DAY, "", user=staff_user)

    assert [item["object"].pk for item in items][:2] == [deletion.pk, contact.pk]


def _cap_names(items, names):
    return [names[item["object"].pk] for item in items if item["object"].pk in names]


@pytest.fixture
def three_contacts_in_one_minute(facility, staff_user, doc_type_contact, client_identified):
    second = timedelta(seconds=1)
    names = {}
    # Insert out of recording order so database row order cannot decide the cut.
    for label, offset in (("middle", 20), ("last", 50), ("first", 5)):
        event = _contact(
            facility,
            staff_user,
            doc_type_contact,
            client_identified,
            occurred_at=_MINUTE,
            recorded_at=_MINUTE + offset * second,
        )
        names[event.pk] = label
    return names


def test_cap_keeps_the_last_recorded_entries(monkeypatch, facility, staff_user, three_contacts_in_one_minute):
    monkeypatch.setattr("core.services.events.feed.FEED_MAX_PER_TYPE", 2)

    items = build_feed_items(facility, _DAY, "events", user=staff_user)

    assert _cap_names(items, three_contacts_in_one_minute) == ["last", "middle"]


def test_watch_projection_cap_keeps_the_same_entries_as_the_page(
    monkeypatch, facility, staff_user, three_contacts_in_one_minute
):
    monkeypatch.setattr("core.services.events.feed.FEED_MAX_PER_TYPE", 2)
    monkeypatch.setattr("core.services.zeitstrom_watch.FEED_MAX_PER_TYPE", 2)
    names = {str(pk): label for pk, label in three_contacts_in_one_minute.items()}

    projection = _feed_projection(facility, staff_user, _state("events"))

    assert [names[value[0]] for kind, value in projection if value[0] in names] == ["last", "middle"]


def test_cap_cuts_mixed_entries_by_the_new_order():
    from core.services.events import feed

    base = timezone.now().replace(second=0, microsecond=0)
    items = [
        {"type": "event", "occurred_at": base, "recorded_at": base + timedelta(seconds=seconds), "object": label}
        for label, seconds in (("a", 10), ("b", 30), ("c", 50))
    ]
    items.append({"type": "activity", "occurred_at": base + timedelta(seconds=20), "object": "deletion"})

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(feed, "FEED_MAX_PER_TYPE", 3)
        kept = [item["object"] for item in feed.cap_feed_items(items)]

    assert kept == ["c", "b", "deletion"]
