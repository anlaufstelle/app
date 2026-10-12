"""Pins for the public event list services.

Source: mutation delta of the release-0.99.7 checkpoint, 52 reviewed mutants;
51 receive new pins here.
Groups:

* ``event_list_options`` (10): accessible choices and preloaded case titles.
* ``resolve_event_list_filters`` (12): canonical IDs and selected presets.
* ``event_list_queryset`` (19): independent filters, fallbacks and ordering.
* ``_date_value`` (4): safe calendar limits through public normalization.
* ``_apply_shift`` (6 new pins, 1 already covered): bounded shift snapshots
  and consecutive window days through the public queryset service.
The already covered backwards window iteration mutant is excluded.
"""

from datetime import date, datetime, time, timedelta
from uuid import UUID, uuid4

import pytest
from django.db import connection
from django.utils import timezone

from core.models import Case, Client, DocumentType, Event, TimeFilter
from core.services.dashboard.search import MIN_QUERY_LENGTH
from core.services.events.list_filters import (
    event_list_options,
    event_list_queryset,
    resolve_event_list_filters,
)

pytestmark = pytest.mark.django_db
DAY = date(2026, 6, 10)


def moment(day, hour=9):
    """Build a local instant without depending on the current clock."""
    return timezone.make_aware(datetime.combine(day, time(hour)))


@pytest.fixture
def create_event(facility, staff_user, doc_type_contact):
    """Create visible events while allowing each independent filter to vary."""

    def create(day=DAY, hour=9, **overrides):
        values = {
            "facility": facility,
            "document_type": doc_type_contact,
            "created_by": staff_user,
            "occurred_at": moment(day, hour),
        }
        values.update(overrides)
        return Event.objects.create(**values)

    return create


@pytest.fixture
def day_shift(facility):
    """Supply a saved shift for the options fallback and window tests."""
    return TimeFilter.objects.create(facility=facility, label="Day shift", start_time=time(8), end_time=time(16))


def ids(queryset):
    """Evaluate the service result in its public ordering."""
    return [event.pk for event in queryset]


def test_client_options_include_only_live_active_local_clients(facility, staff_user, second_facility):
    """Client options retain eligible people and exclude deleted, inactive and foreign people.

    x_event_list_options__mutmut_23, x_event_list_options__mutmut_24,
    x_event_list_options__mutmut_25, x_event_list_options__mutmut_26,
    x_event_list_options__mutmut_27, x_event_list_options__mutmut_28,
    x_event_list_options__mutmut_29.
    """
    Client.objects.create(facility=facility, pseudonym="A deleted", is_deleted=True)
    Client.objects.create(facility=facility, pseudonym="B inactive", is_active=False)
    Client.objects.create(facility=second_facility, pseudonym="C foreign")
    live = Client.objects.create(facility=facility, pseudonym="Z live")

    assert {person.pk for person in event_list_options(facility, staff_user)["clients"]} == {live.pk}


def test_local_case_options_load_titles_without_followup_sql(facility, staff_user, client_identified, foreign_case):
    """Local live cases are selectable and their loaded titles need no later SQL.

    x_event_list_options__mutmut_40, x_event_list_options__mutmut_39.
    """
    cases = [Case.objects.create(facility=facility, client=client_identified, title=f"Case {i}") for i in range(3)]
    loaded = list(event_list_options(facility, staff_user)["cases"])
    assert {case.pk for case in loaded} == {case.pk for case in cases}
    assert foreign_case.pk not in {case.pk for case in loaded}
    followup_sql = []

    def record(execute, sql, params, many, context):
        followup_sql.append(sql)
        return execute(sql, params, many, context)

    with connection.execute_wrapper(record):
        assert {case.title for case in loaded} == {case.title for case in cases}
    assert not followup_sql


def test_order_is_newest_first_with_descending_uuid_ties(facility, staff_user, create_event):
    """The list orders timestamps before UUIDs and keeps ties deterministic.

    x_event_list_queryset__mutmut_154, x_event_list_queryset__mutmut_155.
    """
    old = create_event(day=DAY - timedelta(days=1), id=UUID(int=99))
    lower = create_event(id=UUID(int=1))
    higher = create_event(id=UUID(int=2))
    result = event_list_queryset(facility, staff_user, {})

    assert result.query.order_by == ("-occurred_at", "-pk")
    assert ids(result) == [higher.pk, lower.pk, old.pk]


def test_shift_filter_loads_its_own_options(facility, staff_user, create_event, day_shift):
    """A service caller can filter a saved shift without preloading options.

    x_event_list_queryset__mutmut_78, x_event_list_queryset__mutmut_77,
    x_event_list_queryset__mutmut_79, x_event_list_queryset__mutmut_80,
    x_event_list_queryset__mutmut_81.
    """
    create_event(hour=19)
    inside = create_event()
    assert ids(event_list_queryset(facility, staff_user, {"time_filter": str(day_shift.pk)})) == [inside.pk]


def test_resolver_loads_accessible_ids_without_options(facility, staff_user, client_identified, day_shift):
    """The resolver keeps accessible IDs when its caller omits options.

    x_resolve_event_list_filters__mutmut_5, x_resolve_event_list_filters__mutmut_4.
    """
    params = {"client": str(client_identified.pk), "time_filter": str(day_shift.pk)}
    assert resolve_event_list_filters(params, facility, staff_user, today=DAY) == params


def test_document_type_options_exclude_other_entity_types(facility, staff_user, doc_type_contact):
    """The event type selector excludes person and case types.

    x_event_list_options__mutmut_9.
    """
    for entity in (DocumentType.EntityType.CLIENT, DocumentType.EntityType.CASE):
        DocumentType.objects.create(facility=facility, name=f"A {entity}", entity_type=entity)
    assert {item.pk for item in event_list_options(facility, staff_user)["document_types"]} == {doc_type_contact.pk}


@pytest.mark.parametrize(
    "preset, start, end",
    [
        ("this_week", "2026-06-08", "2026-06-14"),
        ("last_month", "2026-05-01", "2026-05-31"),
    ],
)
def test_presets_preserve_the_selected_range(facility, staff_user, preset, start, end):
    """Preset dates retain the canonical selection for replay by the filter form.

    x_resolve_event_list_filters__mutmut_33, x_resolve_event_list_filters__mutmut_34,
    x_resolve_event_list_filters__mutmut_35, x_resolve_event_list_filters__mutmut_48,
    x_resolve_event_list_filters__mutmut_49, x_resolve_event_list_filters__mutmut_50.
    """
    state = resolve_event_list_filters({"range": preset}, facility, staff_user, today=DAY)
    assert state == {"range": preset, "date_from": start, "date_to": end}


@pytest.mark.parametrize("value, expected", [("0002-01-01", "0002-01-01"), ("0001-06-10", None), ("9999-06-10", None)])
def test_resolver_accepts_safe_years_only(facility, staff_user, value, expected):
    """Canonical dates leave room for shift arithmetic at both calendar limits.

    x__date_value__mutmut_3, x__date_value__mutmut_4,
    x__date_value__mutmut_5, x__date_value__mutmut_6.
    """
    params = {"date_from": value, "date_to": value}
    state = resolve_event_list_filters(params, facility, staff_user, today=DAY)
    assert state == (params if expected else {})


def test_document_type_filter_excludes_other_visible_types(facility, staff_user, create_event, doc_type_contact):
    """Selecting a type alone excludes events of other visible types.

    x_event_list_queryset__mutmut_27, x_event_list_queryset__mutmut_28.
    """
    other = DocumentType.objects.create(facility=facility, name="Other event")
    create_event(hour=10, document_type=other)
    chosen = create_event()
    assert ids(event_list_queryset(facility, staff_user, {"doc_type": str(doc_type_contact.pk)})) == [chosen.pk]


def test_client_filter_survives_resolution_and_excludes_other_clients(facility, staff_user, create_event):
    """A canonical person filter excludes events linked to another person.

    x_resolve_event_list_filters__mutmut_66, x_resolve_event_list_filters__mutmut_67,
    x_event_list_queryset__mutmut_31, x_event_list_queryset__mutmut_32.
    """
    other = Client.objects.create(facility=facility, pseudonym="A other")
    selected = Client.objects.create(facility=facility, pseudonym="Z selected")
    create_event(hour=10, client=other)
    chosen = create_event(client=selected)
    params = {"client": str(selected.pk)}
    state = resolve_event_list_filters(params, facility, staff_user, today=DAY)
    assert state == params
    assert ids(event_list_queryset(facility, staff_user, state)) == [chosen.pk]


def test_case_filter_survives_resolution_and_excludes_other_cases(
    facility, staff_user, client_identified, create_event
):
    """A staff case selection excludes other cases and unassigned events.

    x_resolve_event_list_filters__mutmut_79, x_resolve_event_list_filters__mutmut_80,
    x_event_list_queryset__mutmut_42, x_event_list_queryset__mutmut_43,
    x_event_list_queryset__mutmut_44.
    """
    other = Case.objects.create(facility=facility, client=client_identified, title="A other")
    selected = Case.objects.create(facility=facility, client=client_identified, title="Z selected")
    create_event(hour=11)
    create_event(hour=10, case=other)
    chosen = create_event(case=selected)
    params = {"case": str(selected.pk)}
    state = resolve_event_list_filters(params, facility, staff_user, today=DAY)
    assert state == params
    assert ids(event_list_queryset(facility, staff_user, state)) == [chosen.pk]


def test_mine_alone_excludes_other_authors(facility, staff_user, admin_user, create_event):
    """The own-events toggle works independently of the creator filter.

    x_event_list_queryset__mutmut_49, x_event_list_queryset__mutmut_50,
    x_event_list_queryset__mutmut_51.
    """
    create_event(hour=10, created_by=admin_user)
    own = create_event()
    assert ids(event_list_queryset(facility, staff_user, {"mine": "1"})) == [own.pk]


def test_search_accepts_exact_minimum_length(facility, staff_user, create_event):
    """An indexed term at the minimum search length returns its matching event.

    x_event_list_queryset__mutmut_14.
    """
    term = "x" * MIN_QUERY_LENGTH
    other = create_event(hour=10)
    chosen = create_event()
    Event.objects.filter(pk=other.pk).update(search_text="unrelated")
    Event.objects.filter(pk=chosen.pk).update(search_text=term)
    assert ids(event_list_queryset(facility, staff_user, {"q": term})) == [chosen.pk]


def test_unknown_shift_leaves_the_visible_result_unfiltered(facility, staff_user, create_event):
    """An unknown shift passed directly to the service does not raise or discard events.

    x_event_list_queryset__mutmut_85.
    """
    event = create_event()
    assert ids(
        event_list_queryset(facility, staff_user, {"time_filter": str(uuid4())}, options={"time_filters": []})
    ) == [event.pk]


def test_shift_includes_every_day_of_a_three_day_period(facility, staff_user, day_shift, create_event):
    """Consecutive matching shift days are all returned.

    x__apply_shift__mutmut_71.
    """
    events = [create_event(day=DAY + timedelta(days=i)) for i in range(3)]
    result = event_list_queryset(facility, staff_user, {"time_filter": str(day_shift.pk)})
    assert ids(result) == [event.pk for event in reversed(events)]


@pytest.mark.parametrize(
    "anchor_offset, late_offset, boundary, included",
    [
        (-1, 0, "date_from", True),
        (-2, 0, "date_from", False),
        (2, 0, "date_to", False),
    ],
    ids=["previous-day-margin", "exclude-two-days-before", "exclude-two-days-after"],
)
def test_shift_snapshot_uses_one_day_margins(
    facility, staff_user, day_shift, create_event, anchor_offset, late_offset, boundary, included
):
    """A lazily evaluated list keeps windows derived from the one-day aggregate margin.

    x__apply_shift__mutmut_6, x__apply_shift__mutmut_8, x__apply_shift__mutmut_15.
    """
    create_event(day=DAY + timedelta(days=anchor_offset))
    stable_day = DAY + timedelta(days=3 if boundary == "date_from" else -3)
    stable = create_event(day=stable_day)
    result = event_list_queryset(facility, staff_user, {"time_filter": str(day_shift.pk), boundary: DAY.isoformat()})
    late = create_event(day=DAY + timedelta(days=late_offset))
    expected = {stable.pk, late.pk} if included else {stable.pk}
    assert set(ids(result)) == expected


@pytest.mark.parametrize(
    "start, end, late_offset, late_hour",
    [
        (time(9), time(9), -1, 9),
        (time(22), time(8), -2, 23),
    ],
    ids=["equal-times", "overnight"],
)
def test_shift_snapshot_excludes_extra_previous_windows(
    facility, staff_user, create_event, start, end, late_offset, late_hour
):
    """Equal-time and overnight shifts do not gain an extra earlier start day.

    x__apply_shift__mutmut_34, x__apply_shift__mutmut_38.
    """
    shift = TimeFilter.objects.create(facility=facility, label="Boundary shift", start_time=start, end_time=end)
    anchor = create_event(hour=9 if start == end else 23)
    result = event_list_queryset(facility, staff_user, {"time_filter": str(shift.pk)})
    create_event(day=DAY + timedelta(days=late_offset), hour=late_hour)
    assert ids(result) == [anchor.pk]
