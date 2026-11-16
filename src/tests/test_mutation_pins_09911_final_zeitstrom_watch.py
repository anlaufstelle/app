"""Mutation regression pins for ``core.services.zeitstrom_watch`` and one feed helper.

Source: targeted mutation delta "final" (end state ``b8f08a4d2``), module
``zeitstrom_watch`` plus the ``activity_person_label`` survivor of
``core.services.events.feed``.
Refs #2717

Each test names the mutants it kills as ``x…__mutmut_N``.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime, time, timedelta

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.constants import FEED_MAX_PER_TYPE
from core.models import Activity, BanRevision, Client, DocumentType, Event, WorkItem
from core.services.events.feed import activity_person_label, get_time_range
from core.services.events.tally import TallyLiveContext
from core.services.system.ban_revision import bump_ban_revision
from core.services.zeitstrom_filters import resolve_zeitstrom_state
from core.services.zeitstrom_watch import (
    _feed_projection,
    build_zeitstrom_component_validator,
    build_zeitstrom_watch_snapshot,
)
from tests._factories import make_document_type

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

DAY = date(2026, 3, 10)


def _at(hour, minute=0):
    return timezone.make_aware(datetime.combine(DAY, time(hour, minute)))


def _state(facility, **params):
    return resolve_zeitstrom_state({"date": DAY.isoformat(), "time_filter": "all", **params}, facility, today=DAY)


def _ids(projection, kind):
    return [value[0] for item_kind, value in projection if item_kind == kind]


@pytest.fixture
def ban_type(facility):
    return make_document_type(
        facility=facility,
        name="Ban",
        system_type=DocumentType.SystemType.BAN,
        sensitivity=DocumentType.Sensitivity.NORMAL,
    )


# ---------------------------------------------------------------------------
# _feed_projection: which rows survive the per-type cap
# ---------------------------------------------------------------------------


def test_event_cap_keeps_the_newest_occurred_events_not_the_newest_recorded(facility, staff_user, doc_type_contact):
    """Pins core.services.zeitstrom_watch.x__feed_projection__mutmut_33.

    One event more than the cap exists. The oldest occurred one was recorded
    last. The cap must cut by occurrence time, so that very event is dropped and
    the newest occurred one (recorded first) stays.
    """
    events = Event.objects.bulk_create(
        [
            Event(
                facility=facility,
                document_type=doc_type_contact,
                created_by=staff_user,
                occurred_at=_at(6) + timedelta(minutes=FEED_MAX_PER_TYPE - index),
                is_anonymous=True,
            )
            for index in range(FEED_MAX_PER_TYPE + 1)
        ]
    )
    newest_occurred, oldest_occurred = events[0], events[-1]

    ids = _ids(_feed_projection(facility, staff_user, _state(facility, type="events")), "event")

    assert len(ids) == FEED_MAX_PER_TYPE
    assert str(newest_occurred.pk) in ids
    assert str(oldest_occurred.pk) not in ids


def _bulk_bans(facility, user, ban_type, client, occurred_at_for):
    return Event.objects.bulk_create(
        [
            Event(
                facility=facility,
                client=client,
                document_type=ban_type,
                created_by=user,
                occurred_at=occurred_at_for(index),
            )
            for index in range(FEED_MAX_PER_TYPE + 1)
        ]
    )


def test_ban_cap_keeps_the_newest_occurred_bans_not_the_newest_recorded(
    facility, staff_user, ban_type, client_identified
):
    """Pins core.services.zeitstrom_watch.x__feed_projection__mutmut_191.

    Same shape as the event cap: the ban query must cut by occurrence time first.
    """
    bans = _bulk_bans(
        facility,
        staff_user,
        ban_type,
        client_identified,
        lambda index: _at(6) + timedelta(minutes=FEED_MAX_PER_TYPE - index),
    )

    ids = _ids(_feed_projection(facility, staff_user, _state(facility, type="bans")), "ban")

    assert len(ids) == FEED_MAX_PER_TYPE
    assert str(bans[0].pk) in ids
    assert str(bans[-1].pk) not in ids


def test_ban_cap_breaks_ties_in_occurrence_time_by_recording_time(facility, staff_user, ban_type, client_identified):
    """Pins core.services.zeitstrom_watch.x__feed_projection__mutmut_192.

    All bans share one occurrence time, so only the recording time decides which
    one the cap drops: the one recorded first.
    """
    bans = _bulk_bans(facility, staff_user, ban_type, client_identified, lambda index: _at(9))

    ids = _ids(_feed_projection(facility, staff_user, _state(facility, type="bans")), "ban")

    assert len(ids) == FEED_MAX_PER_TYPE
    assert str(bans[-1].pk) in ids
    assert str(bans[0].pk) not in ids


def test_ban_recorded_after_a_contact_of_the_same_minute_is_listed_first(
    facility, staff_user, doc_type_contact, ban_type, client_identified
):
    """Pins core.services.zeitstrom_watch.x__feed_projection__mutmut_214 and …_215.

    A ban and a contact share the displayed minute. The ban was recorded later,
    so it comes first; without its recording time the ban would rank by the bare
    minute and fall behind the contact.
    """
    contact = Event.objects.create(
        facility=facility, document_type=doc_type_contact, created_by=staff_user, occurred_at=_at(9), is_anonymous=True
    )
    ban = Event.objects.create(
        facility=facility, client=client_identified, document_type=ban_type, created_by=staff_user, occurred_at=_at(9)
    )

    projection = _feed_projection(facility, staff_user, _state(facility))

    order = [(kind, value[0]) for kind, value in projection if kind in {"event", "ban"}]
    assert order == [("ban", str(ban.pk)), ("event", str(contact.pk))]


def test_document_type_filter_narrows_the_tally_groups(facility, staff_user, doc_type_contact, doc_type_crisis):
    """Pins core.services.zeitstrom_watch.x__feed_projection__mutmut_45 and …_50.

    With a document-type filter the hourly tally groups of other types must not
    reach the projection.
    """
    for doc_type in (doc_type_contact, doc_type_crisis):
        Event.objects.create(
            facility=facility,
            document_type=doc_type,
            created_by=staff_user,
            occurred_at=_at(8),
            is_anonymous=True,
            created_via=Event.CreatedVia.TALLY,
        )

    projection = _feed_projection(facility, staff_user, _state(facility, doc_type=str(doc_type_contact.pk)))

    groups = [value for kind, value in projection if kind == "tally_group"]
    assert len(groups) == 1
    assert [entry["id"] for entry in groups[0]["types"]] == [str(doc_type_contact.pk)]


# ---------------------------------------------------------------------------
# KPI validator
# ---------------------------------------------------------------------------


def test_kpi_validator_follows_the_ban_revision_without_a_passed_revision(facility, staff_user):
    """Pins core.services.zeitstrom_watch.x__kpi_projection__mutmut_3.

    When no revision is handed in, the KPI projection reads the ban revision of
    its own facility, so a bump moves the validator.
    """
    state = _state(facility)

    before = build_zeitstrom_component_validator(staff_user, facility, state, "kpi")
    bump_ban_revision(facility, (0, 1, 2))
    after = build_zeitstrom_component_validator(staff_user, facility, state, "kpi")

    assert BanRevision.objects.filter(facility=facility).exists()
    assert before != after


def test_kpi_validator_follows_the_tally_total_of_the_live_window(facility, staff_user, doc_type_contact):
    """Pins core.services.zeitstrom_watch.x__kpi_projection__mutmut_9, …_10, …_11, …_12, …_13, …_14, …_15.

    In the live tally context the KPI row shows the tally total of the live
    window carried by the state (not the window of the moment). A tally entry
    inside that window therefore moves the validator.
    """
    start, end = get_time_range(DAY, None)
    state = dataclasses.replace(_state(facility), live_context=TallyLiveContext(None, DAY, start, end))
    assert state.is_live_tally_context

    before = build_zeitstrom_component_validator(staff_user, facility, state, "kpi")
    Event.objects.create(
        facility=facility,
        document_type=doc_type_contact,
        created_by=staff_user,
        occurred_at=_at(12),
        is_anonymous=True,
        created_via=Event.CreatedVia.TALLY,
    )
    after = build_zeitstrom_component_validator(staff_user, facility, state, "kpi")

    assert before != after


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def _banrevision_queries(captured):
    table = BanRevision._meta.db_table
    return [query["sql"] for query in captured.captured_queries if table in query["sql"]]


def test_handover_snapshot_does_not_read_the_ban_revision(facility, staff_user):
    """Pins core.services.zeitstrom_watch.x_build_zeitstrom_watch_snapshot__mutmut_33.

    The handover view watches only the handover note; it must not spend a read on
    the ban revision, which only the feed view's ``bans`` and ``kpi`` components
    use. The feed view does read it.
    """
    handover = resolve_zeitstrom_state({"view": "uebergabe"}, facility)
    feed = resolve_zeitstrom_state({}, facility)

    with CaptureQueriesContext(connection) as handover_queries:
        build_zeitstrom_watch_snapshot(staff_user, facility, handover)
    with CaptureQueriesContext(connection) as feed_queries:
        build_zeitstrom_watch_snapshot(staff_user, facility, feed)

    assert _banrevision_queries(handover_queries) == []
    assert _banrevision_queries(feed_queries) != []


# ---------------------------------------------------------------------------
# activity_person_label
# ---------------------------------------------------------------------------


def _activity_for(facility, user, target):
    return Activity.objects.create(
        facility=facility,
        actor=user,
        verb=Activity.Verb.UPDATED,
        target_type=ContentType.objects.get_for_model(type(target)),
        target_id=target.pk,
    )


def test_activity_person_label_is_empty_for_a_target_without_a_person(facility, staff_user, doc_type_contact):
    """Pins core.services.events.feed.x_activity_person_label__mutmut_3.

    An anonymous event names no person: the label is the empty string, not a
    placeholder. A client and an event with a client show the pseudonym.
    """
    anonymous = Event.objects.create(
        facility=facility, document_type=doc_type_contact, created_by=staff_user, occurred_at=_at(10), is_anonymous=True
    )
    client = Client.objects.create(facility=facility, pseudonym="Label-01", created_by=staff_user)
    with_client = Event.objects.create(
        facility=facility, client=client, document_type=doc_type_contact, created_by=staff_user, occurred_at=_at(11)
    )

    assert activity_person_label(_activity_for(facility, staff_user, anonymous)) == ""
    assert activity_person_label(_activity_for(facility, staff_user, client)) == "Label-01"
    assert activity_person_label(_activity_for(facility, staff_user, with_client)) == "Label-01"


# ---------------------------------------------------------------------------
# focus box validator
# ---------------------------------------------------------------------------


def test_focus_box_validator_without_passed_counters_follows_the_facility_work_items(facility, staff_user):
    """Pins core.services.zeitstrom_watch.x_build_zeitstrom_component_validator__mutmut_60.

    Without handed-in counters the focus box reads the open work items of its
    own facility, so a new urgent one moves the validator.
    """
    state = _state(facility)

    before = build_zeitstrom_component_validator(staff_user, facility, state, "focus_box")
    WorkItem.objects.create(
        facility=facility,
        title="Urgent task",
        item_type=WorkItem.ItemType.TASK,
        priority=WorkItem.Priority.URGENT,
        created_by=staff_user,
    )
    after = build_zeitstrom_component_validator(staff_user, facility, state, "focus_box")

    assert before != after
