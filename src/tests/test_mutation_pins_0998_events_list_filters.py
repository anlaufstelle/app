"""Pins for public event list visibility, shift windows and loaded page data.

Source: mutation delta of the release-0.99.8 checkpoint, 8 mutants.
Groups: read visibility (2), shift bounds (3), supplied options (2),
preloaded page cases (1). Three aggregation-margin candidates are
result-equivalent because the shift windows are clamped to the requested dates.
"""

from datetime import date, datetime, time, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Case, Client, Event, TimeFilter
from core.services.events.list_filters import (
    event_list_page_contents,
    event_list_queryset,
    resolve_event_list_filters,
)

pytestmark = pytest.mark.django_db
DAY = date(2026, 6, 10)


def moment(day, hour=9):
    """Build a deterministic local event timestamp."""
    return timezone.make_aware(datetime.combine(day, time(hour)))


@pytest.fixture
def create_event(facility, staff_user, doc_type_contact):
    """Create list entries with independent timestamps and relations."""

    def create(offset=0, hour=9, **overrides):
        return Event.objects.create(
            facility=facility,
            created_by=staff_user,
            document_type=doc_type_contact,
            occurred_at=moment(DAY + timedelta(days=offset), hour),
            **overrides,
        )

    return create


@pytest.fixture
def shift(facility):
    """Provide a daytime shift with unambiguous window boundaries."""
    return TimeFilter.objects.create(facility=facility, label="Day shift", start_time=time(8), end_time=time(16))


def test_list_excludes_unreadable_people_and_deleted_events(facility, staff_user, create_event):
    """Only live events with active people or no person survive the list boundary.

    x_event_list_queryset__mutmut_4, x_event_list_queryset__mutmut_5.
    """
    for index, flags in enumerate(({"is_active": False}, {"is_deleted": True})):
        person = Client.objects.create(facility=facility, pseudonym=f"Hidden {index}", **flags)
        create_event(hour=12 + index, client=person)
    active = Client.objects.create(facility=facility, pseudonym="Visible")
    create_event(hour=11, client=active, is_deleted=True)
    linked = create_event(client=active)
    anonymous = create_event(hour=8)
    assert [event.pk for event in event_list_queryset(facility, staff_user, {})] == [linked.pk, anonymous.pk]


def test_future_shift_bounds_use_latest_event(facility, staff_user, create_event, shift, monkeypatch):
    """Future events remain visible when today's date precedes their shift windows.

    x__apply_shift__mutmut_40.
    """
    monkeypatch.setattr(timezone, "now", lambda: moment(DAY - timedelta(days=2)))
    first, last = create_event(), create_event(offset=1)
    result = event_list_queryset(facility, staff_user, {"time_filter": str(shift.pk)})
    assert [event.pk for event in result] == [last.pk, first.pk]


@pytest.mark.parametrize("boundary, outside", [("date_from", -1), ("date_to", 1)])
def test_shift_respects_requested_boundary(facility, staff_user, create_event, shift, boundary, outside):
    """Shift windows stay inside the requested start and end dates.

    x__apply_shift__mutmut_44, x__apply_shift__mutmut_51.
    """
    create_event(offset=outside)
    chosen = create_event()
    result = event_list_queryset(facility, staff_user, {"time_filter": str(shift.pk), boundary: DAY.isoformat()})
    assert [event.pk for event in result] == [chosen.pk]


def test_empty_supplied_shift_options_remain_authoritative(facility, staff_user, create_event, shift):
    """An explicitly empty shift selection does not activate a stored shift.

    x_event_list_queryset__mutmut_80.
    """
    outside, inside = create_event(hour=19), create_event()
    result = event_list_queryset(facility, staff_user, {"time_filter": str(shift.pk)}, options={"time_filters": []})
    assert [event.pk for event in result] == [outside.pk, inside.pk]


def test_resolver_honors_supplied_empty_choices(facility, staff_user, shift):
    """The resolver rejects a stored identifier excluded from supplied choices.

    x_resolve_event_list_filters__mutmut_4.
    """
    options = {key: [] for key in ("document_types", "clients", "cases", "creators", "time_filters")}
    assert (
        resolve_event_list_filters({"time_filter": str(shift.pk)}, facility, staff_user, today=DAY, options=options)
        == {}
    )


def test_page_case_titles_are_loaded_with_the_page(facility, staff_user, client_identified, create_event):
    """Page hydration preloads every case title for rendering without later SQL.

    x_event_list_page_contents__mutmut_11.
    """
    cases = [Case.objects.create(facility=facility, client=client_identified, title=f"Case {i}") for i in range(3)]
    events = [create_event(case=case) for case in cases]
    loaded = event_list_page_contents(facility, staff_user, events)
    assert [event.pk for event in loaded] == [event.pk for event in events]
    with CaptureQueriesContext(connection) as queries:
        assert [event.case.title for event in loaded] == [case.title for case in cases]
    assert not queries.captured_queries
