"""Pins for URL parameters, shift windows, date wording and option names of the event list.

Source: targeted mutation delta ZS0, batch ``list_filters-d`` (40 mutants).
Of these, 14 are result-equivalent and not pinned (3 aggregation-margin
mutants of ``_apply_shift``, 7 default-value mutants of
``resolve_event_list_filters`` and 4 ``only()`` mutants of
``event_list_options``); the other 26 are pinned here.
Refs #2712
Refs #1997
"""

from datetime import date, datetime, time

import pytest
from django.utils import timezone, translation

from core.models import Event, TimeFilter
from core.services.events.list_filters import (
    event_list_active_filters,
    event_list_queryset,
    event_list_url_params,
)


def test_preset_replaces_the_computed_dates_in_url_parameters():
    """Pins x_event_list_url_params__mutmut_3, x_event_list_url_params__mutmut_4,
    x_event_list_url_params__mutmut_6, x_event_list_url_params__mutmut_9,
    x_event_list_url_params__mutmut_10, x_event_list_url_params__mutmut_11,
    x_event_list_url_params__mutmut_14, x_event_list_url_params__mutmut_15.

    A resolved preset carries its computed days as well; links must keep only
    the preset, so the link keeps meaning the preset when it is opened later.
    """
    filters = {"range": "this_week", "date_from": "2026-06-08", "date_to": "2026-06-14", "doc_type": "abc"}

    assert event_list_url_params(filters) == {"range": "this_week", "doc_type": "abc"}
    assert filters["date_from"] == "2026-06-08", "the input is not modified"


def test_without_a_preset_explicit_dates_stay_in_url_parameters():
    """Companion to the preset case: explicit dates are part of the link."""
    filters = {"date_from": "2026-06-08", "date_to": "2026-06-14"}

    assert event_list_url_params(filters) == filters


def test_preset_without_dates_does_not_fail_when_dropping_them():
    """Pins x_event_list_url_params__mutmut_8, x_event_list_url_params__mutmut_13."""
    assert event_list_url_params({"range": "today"}) == {"range": "today"}


@pytest.fixture
def day_shift(facility):
    return TimeFilter.objects.create(facility=facility, label="Day", start_time=time(8), end_time=time(16))


@pytest.mark.django_db
def test_shift_filter_returns_events_inside_the_window_including_the_far_future(
    facility, staff_user, doc_type_contact, day_shift
):
    """Pins x__apply_shift__mutmut_40, x__apply_shift__mutmut_68, x__apply_shift__mutmut_76,
    x__apply_shift__mutmut_86, x__apply_shift__mutmut_87.

    Without date bounds the shift windows reach up to the latest event, so a
    later event is found regardless of the current date; events outside the
    daily window stay out.
    """

    def create(year, month, day, hour):
        return Event.objects.create(
            facility=facility,
            created_by=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.make_aware(datetime(year, month, day, hour)),
        )

    inside = create(2026, 6, 10, 9)
    create(2026, 6, 10, 19)
    future = create(2099, 6, 10, 9)

    result = event_list_queryset(facility, staff_user, {"time_filter": str(day_shift.pk)})

    assert [event.pk for event in result] == [future.pk, inside.pk]


@pytest.mark.parametrize(
    ("filters", "text"),
    [
        ({"date_from": "2026-06-05"}, "Zeitraum: ab 05.06."),
        ({"date_from": "2025-12-31"}, "Zeitraum: ab 31.12.2025"),
    ],
)
def test_period_dates_drop_the_year_only_inside_the_current_year(filters, text):
    """Pins x__short_date__mutmut_2, x__short_date__mutmut_3, x__short_date__mutmut_5,
    x__short_date__mutmut_7, x__short_date__mutmut_8, x__short_date__mutmut_9,
    x__short_date__mutmut_10, x__short_date__mutmut_11.
    """
    with translation.override("de"):
        chips = event_list_active_filters(filters, {}, base_url="/events/", today=date(2026, 6, 10))

    assert [chip["text"] for chip in chips] == [text]


@pytest.mark.django_db
def test_unknown_identifier_keeps_the_raw_value_and_known_one_shows_the_plain_name(
    facility, staff_user, doc_type_contact
):
    """Pins x__option_text__mutmut_4, x__option_text__mutmut_8, x__option_text__mutmut_10."""
    from core.services.events.list_filters import event_list_options

    options = event_list_options(facility, staff_user)
    unknown = "00000000-0000-0000-0000-000000000000"
    with translation.override("de"):
        chips = event_list_active_filters(
            {"doc_type": str(doc_type_contact.pk), "client": unknown}, options, base_url="/events/"
        )

    assert [chip["text"] for chip in chips] == [f"Typ: {doc_type_contact.name}", f"Person: {unknown}"]
