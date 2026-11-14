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
