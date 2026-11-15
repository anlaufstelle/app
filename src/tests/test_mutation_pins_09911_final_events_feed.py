"""Mutation pins for ``core.services.events.feed`` (mutation final, part 2b).

Source: survivors of the final targeted mutation delta,
module ``core.services.events.feed``. Every test names the mutants it kills
(``core.services.events.feed.x…__mutmut_N``) and the value that differs under
the mutant.

Fixed dates only (2026-03-10 / 2026-03-14): nothing here depends on the
calendar day of the run.

Not pinned (equivalent, see the triage file ``triage-events-feed.json``):
``values_list("pk", flat=...)`` variants in ``activities_for_feed_read`` (the
queryset only feeds an ``IN`` subquery, ``flat`` changes the iterable class
and not the SQL), the ``dt_fields.get`` default in
``enrich_events_with_preview``, the ``exclude_tally`` default of
``_feed_bans`` (its only caller passes it), the document type id tiebreak of
``build_tally_feed_groups`` (names are unique per facility), and the
shift-by-constant / time zone variants of ``feed_sort_key``.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from core.models import Activity, DocumentType, Event
from core.services.events import build_feed_items
from core.services.events import feed as feed_module
from core.services.events.feed import build_tally_feed_groups, cap_feed_items, feed_sort_key

UTC = dt.UTC
FEED_DATE = dt.date(2026, 3, 10)


def _berlin(hour, minute=0):
    return timezone.make_aware(dt.datetime(2026, 3, 10, hour, minute))


@pytest.fixture
def plain_type(facility):
    return DocumentType.objects.create(facility=facility, name="Final-Plain")


@pytest.fixture
def ban_type(facility):
    return DocumentType.objects.create(
        facility=facility,
        name="Final-Ban",
        sensitivity=DocumentType.Sensitivity.ELEVATED,
        system_type=DocumentType.SystemType.BAN,
        min_contact_stage="identified",
    )


def _event(facility, doc_type, user, client, when, created_at=None):
    event = Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=when,
        data_json={},
        created_by=user,
    )
    if created_at is not None:
        # ``created_at`` is ``auto_now_add``: only settable through ``update()``.
        Event.objects.filter(pk=event.pk).update(created_at=created_at)
    return event


def _tally_event(facility, doc_type, user, when):
    return Event.objects.create(
        facility=facility,
        client=None,
        document_type=doc_type,
        occurred_at=when,
        data_json={},
        created_by=user,
        is_anonymous=True,
        created_via=Event.CreatedVia.TALLY,
    )


def _feed_pks(items, kind):
    return {item["object"].pk for item in items if item["type"] == kind}


# ============================================================================
# _feed_activities
# ============================================================================


@pytest.mark.django_db
class TestFeedActivitiesCarryThePersonLabel:
    def test_activity_on_a_client_shows_the_client_pseudonym(self, facility, staff_user, client_identified):
        """Kills ``x__feed_activities__mutmut_26`` (``feed_person = None``).

        The activity card names the person it concerns; under the mutant the
        label is ``None`` instead of ``"Test-ID-01"``.
        """
        Activity.objects.create(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target_type=ContentType.objects.get_for_model(type(client_identified)),
            target_id=client_identified.pk,
            context={},
            occurred_at=_berlin(12),
        )

        items = build_feed_items(facility, FEED_DATE, "activities", user=staff_user)

        assert [item["type"] for item in items] == ["activity"]
        assert items[0]["object"].feed_person == "Test-ID-01"


# ============================================================================
# _feed_events / _feed_bans: which rows survive the per-type cap
# ============================================================================


@pytest.mark.django_db
class TestFeedCapKeepsTheRightRows:
    def test_event_cap_keeps_the_newest_occurred_at_not_the_newest_recording(
        self, facility, staff_user, client_identified, plain_type, monkeypatch
    ):
        """Kills ``x__feed_events__mutmut_19`` (``order_by("-created_at")`` only).

        Cap of two. The event that happened last was recorded first; ordering
        by recording time would cut it and keep the oldest contact instead.
        """
        monkeypatch.setattr(feed_module, "FEED_MAX_PER_TYPE", 2)
        newest = _event(
            facility, plain_type, staff_user, client_identified, _berlin(12), dt.datetime(2026, 3, 10, 6, 0, tzinfo=UTC)
        )
        middle = _event(
            facility, plain_type, staff_user, client_identified, _berlin(11), dt.datetime(2026, 3, 10, 7, 0, tzinfo=UTC)
        )
        _event(
            facility, plain_type, staff_user, client_identified, _berlin(10), dt.datetime(2026, 3, 10, 8, 0, tzinfo=UTC)
        )

        items = build_feed_items(facility, FEED_DATE, "events", user=staff_user)

        assert _feed_pks(items, "event") == {newest.pk, middle.pk}

    def test_event_cap_among_equal_times_keeps_the_most_recently_recorded(
        self, facility, staff_user, client_identified, plain_type, monkeypatch
    ):
        """Kills ``x__feed_events__mutmut_20`` (``order_by("-occurred_at", )``).

        Five contacts share one minute; with a cap of two the two recorded
        last must survive. Without the ``-created_at`` tiebreak the database
        keeps whichever rows it meets first (or last). The rows are therefore
        inserted in an order unrelated to their recording time: neither the
        first two nor the last two inserted rows are the expected pair.
        """
        monkeypatch.setattr(feed_module, "FEED_MAX_PER_TYPE", 2)
        # Recording minutes in insertion order: the newest two (3, 4) sit in
        # the middle of the insertion sequence.
        events = {
            minute: _event(
                facility,
                plain_type,
                staff_user,
                client_identified,
                _berlin(12),
                dt.datetime(2026, 3, 10, 6, minute, tzinfo=UTC),
            )
            for minute in (0, 3, 4, 1, 2)
        }

        items = build_feed_items(facility, FEED_DATE, "events", user=staff_user)

        assert _feed_pks(items, "event") == {events[3].pk, events[4].pk}

    def test_ban_cap_keeps_the_newest_occurred_at_not_the_newest_recording(
        self, facility, staff_user, client_identified, ban_type, monkeypatch
    ):
        """Kills ``x__feed_bans__mutmut_12`` (``order_by("-created_at")`` only)."""
        monkeypatch.setattr(feed_module, "FEED_MAX_PER_TYPE", 2)
        newest = _event(
            facility, ban_type, staff_user, client_identified, _berlin(12), dt.datetime(2026, 3, 10, 6, 0, tzinfo=UTC)
        )
        middle = _event(
            facility, ban_type, staff_user, client_identified, _berlin(11), dt.datetime(2026, 3, 10, 7, 0, tzinfo=UTC)
        )
        _event(
            facility, ban_type, staff_user, client_identified, _berlin(10), dt.datetime(2026, 3, 10, 8, 0, tzinfo=UTC)
        )

        items = build_feed_items(facility, FEED_DATE, "bans", user=staff_user)

        assert _feed_pks(items, "ban") == {newest.pk, middle.pk}


# ============================================================================
# build_tally_feed_groups
# ============================================================================

WINDOW_START = dt.datetime(2026, 3, 14, 0, 0, tzinfo=UTC)
WINDOW_END = dt.datetime(2026, 3, 15, 0, 0, tzinfo=UTC)


@pytest.mark.django_db
class TestBuildTallyFeedGroups:
    def test_groups_come_back_in_hour_order_regardless_of_type_names(self, facility, staff_user):
        """Kills ``x_build_tally_feed_groups__mutmut_18`` (``"hour"`` dropped from ``order_by``).

        The earlier hour holds the type that sorts last by name. Ordered by
        name first the later hour would lead the list.
        """
        late_name = DocumentType.objects.create(facility=facility, name="Bravo")
        early_name = DocumentType.objects.create(facility=facility, name="Alpha")
        _tally_event(facility, late_name, staff_user, dt.datetime(2026, 3, 14, 9, 30, tzinfo=UTC))
        _tally_event(facility, early_name, staff_user, dt.datetime(2026, 3, 14, 10, 30, tzinfo=UTC))

        groups = build_tally_feed_groups(facility, WINDOW_START, WINDOW_END, staff_user)

        assert [group["key"] for group in groups] == ["2026031409", "2026031410"]

    def test_types_within_a_group_are_ordered_by_name(self, facility, staff_user):
        """Kills ``x_build_tally_feed_groups__mutmut_19`` (``"document_type__name"`` dropped).

        The names are assigned so that name order is the reverse of id order;
        ordering by id would list ``Zulu`` before ``Alpha``.
        """
        first = DocumentType.objects.create(facility=facility, name="tmp-one")
        second = DocumentType.objects.create(facility=facility, name="tmp-two")
        low, high = sorted((first, second), key=lambda doc_type: doc_type.pk)
        DocumentType.objects.filter(pk=low.pk).update(name="Zulu")
        DocumentType.objects.filter(pk=high.pk).update(name="Alpha")
        for doc_type in (low, high):
            _tally_event(facility, doc_type, staff_user, dt.datetime(2026, 3, 14, 9, 30, tzinfo=UTC))

        groups = build_tally_feed_groups(facility, WINDOW_START, WINDOW_END, staff_user)

        assert len(groups) == 1
        assert [row["name"] for row in groups[0]["types"]] == ["Alpha", "Zulu"]

    def test_group_end_is_one_hour_after_its_start(self, facility, staff_user, plain_type):
        """Kills ``x_build_tally_feed_groups__mutmut_57`` / ``_59`` / ``_61`` / ``_79`` / ``_80``.

        ``end = None`` (57), ``hour - 1h`` (59), ``hour + 2h`` (61) and a
        renamed ``end`` key (79, 80): the group spans exactly 10:00-11:00 UTC.
        """
        _tally_event(facility, plain_type, staff_user, dt.datetime(2026, 3, 14, 10, 30, tzinfo=UTC))

        groups = build_tally_feed_groups(facility, WINDOW_START, WINDOW_END, staff_user)

        assert len(groups) == 1
        assert groups[0]["start"] == dt.datetime(2026, 3, 14, 10, 0, tzinfo=UTC)
        assert groups[0]["end"] == dt.datetime(2026, 3, 14, 11, 0, tzinfo=UTC)

    def test_group_key_is_the_utc_hour_as_year_month_day_hour(self, facility, staff_user, plain_type):
        """Kills ``x_build_tally_feed_groups__mutmut_74`` (``"XX%Y%m%d%HXX"``) and ``_76`` (``"%Y%M%D%H"``)."""
        _tally_event(facility, plain_type, staff_user, dt.datetime(2026, 3, 14, 10, 30, tzinfo=UTC))

        groups = build_tally_feed_groups(facility, WINDOW_START, WINDOW_END, staff_user)

        assert [group["key"] for group in groups] == ["2026031410"]

    def test_type_rows_carry_the_document_type_id_as_string(self, facility, staff_user, plain_type):
        """Kills ``x_build_tally_feed_groups__mutmut_105`` / ``_106`` / ``_107``.

        The row key is ``id`` (105: ``XXidXX``, 106: ``ID``) and its value is
        the pk of the type as a string (107: ``"None"``).
        """
        _tally_event(facility, plain_type, staff_user, dt.datetime(2026, 3, 14, 10, 30, tzinfo=UTC))
        _tally_event(facility, plain_type, staff_user, dt.datetime(2026, 3, 14, 10, 40, tzinfo=UTC))

        groups = build_tally_feed_groups(facility, WINDOW_START, WINDOW_END, staff_user)

        assert groups[0]["types"] == [{"id": str(plain_type.pk), "name": "Final-Plain", "count": 2}]


# ============================================================================
# cap_feed_items / feed_sort_key (pure functions)
# ============================================================================


class TestCapFeedItems:
    def test_no_single_entry_survives_when_groups_fill_the_cap(self):
        """Kills ``x_cap_feed_items__mutmut_30`` (``max(1, ...)`` instead of ``max(0, ...)``).

        As many complete hour groups as the cap allows leave no room: the
        single entry must be dropped. The mutant keeps one and exceeds the cap.
        """
        base = dt.datetime(2026, 3, 14, 12, 0, tzinfo=UTC)
        cap = feed_module.FEED_MAX_PER_TYPE
        groups = [
            {"type": "tally_group", "occurred_at": base - dt.timedelta(hours=index), "object": {}}
            for index in range(cap)
        ]
        entry = {"type": "event", "occurred_at": base + dt.timedelta(hours=1), "recorded_at": base, "object": None}

        result = cap_feed_items([entry, *groups])

        assert [item["type"] for item in result] == ["tally_group"] * cap


class TestFeedSortKey:
    def test_default_time_key_is_occurred_at(self):
        """Kills ``x_feed_sort_key__mutmut_1`` (``"XXoccurred_atXX"``) and ``_2`` (``"OCCURRED_AT"``).

        Without ``time_key`` the key reads ``occurred_at``; under the mutants
        the call raises ``KeyError``.
        """
        moment = dt.datetime(2026, 3, 14, 10, 0, tzinfo=UTC)

        assert feed_sort_key({"occurred_at": moment}) == (moment, moment)

    def test_microseconds_do_not_decide_within_a_minute(self):
        """Kills ``x_feed_sort_key__mutmut_13`` (``replace(second=0, )``).

        Two cards share a minute. The one with the smaller microsecond part
        was recorded later and therefore comes first; keeping the microseconds
        in the key would put the other one on top.
        """
        base = dt.datetime(2026, 3, 14, 10, 0, tzinfo=UTC)
        recorded_later = {
            "type": "event",
            "occurred_at": base + dt.timedelta(microseconds=100_000),
            "recorded_at": base + dt.timedelta(seconds=5),
            "object": None,
        }
        recorded_earlier = {
            "type": "event",
            "occurred_at": base + dt.timedelta(microseconds=200_000),
            "recorded_at": base + dt.timedelta(seconds=2),
            "object": None,
        }

        assert feed_sort_key(recorded_later)[0] == feed_sort_key(recorded_earlier)[0] == base
        assert cap_feed_items([recorded_earlier, recorded_later]) == [recorded_later, recorded_earlier]
