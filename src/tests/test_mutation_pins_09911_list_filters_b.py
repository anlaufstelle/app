"""Pins for the period segments above the event list.

Source: targeted mutation delta ZS0, batch ``list_filters-b`` (49 mutants of
``event_list_period_segments``).
Refs #2712
Refs #1997

The segments Heute | Woche | Monat | Zeitraum plus the extra "Letzter Monat"
link are compared as complete dictionaries (key, text, active flag, target
URL, htmx flag) under the German locale.
"""

from datetime import date

import pytest
from django.utils import translation

from core.services.events.list_filters import event_list_period_segments

BASE_URL = "/events/"
TODAY = date(2026, 6, 10)  # a Wednesday


def segments_for(filters):
    with translation.override("de"):
        return event_list_period_segments(filters, base_url=BASE_URL, today=TODAY)


def preset(key, label, url, active=False):
    return {"key": key, "label": label, "active": active, "url": url, "htmx": True}


def test_explicit_dates_are_replaced_by_preset_links_and_kept_by_the_period_link():
    """Pins x_event_list_period_segments__mutmut_6, x_event_list_period_segments__mutmut_7,
    x_event_list_period_segments__mutmut_10, x_event_list_period_segments__mutmut_11,
    x_event_list_period_segments__mutmut_15, x_event_list_period_segments__mutmut_16,
    x_event_list_period_segments__mutmut_17, x_event_list_period_segments__mutmut_18,
    x_event_list_period_segments__mutmut_27, x_event_list_period_segments__mutmut_28,
    x_event_list_period_segments__mutmut_33, x_event_list_period_segments__mutmut_41,
    x_event_list_period_segments__mutmut_42, x_event_list_period_segments__mutmut_43,
    x_event_list_period_segments__mutmut_46, x_event_list_period_segments__mutmut_52,
    x_event_list_period_segments__mutmut_53, x_event_list_period_segments__mutmut_54,
    x_event_list_period_segments__mutmut_56, x_event_list_period_segments__mutmut_62,
    x_event_list_period_segments__mutmut_63, x_event_list_period_segments__mutmut_64,
    x_event_list_period_segments__mutmut_66, x_event_list_period_segments__mutmut_72,
    x_event_list_period_segments__mutmut_73, x_event_list_period_segments__mutmut_74,
    x_event_list_period_segments__mutmut_90, x_event_list_period_segments__mutmut_91,
    x_event_list_period_segments__mutmut_92, x_event_list_period_segments__mutmut_93,
    x_event_list_period_segments__mutmut_95, x_event_list_period_segments__mutmut_96,
    x_event_list_period_segments__mutmut_97, x_event_list_period_segments__mutmut_105,
    x_event_list_period_segments__mutmut_106, x_event_list_period_segments__mutmut_108,
    x_event_list_period_segments__mutmut_109, x_event_list_period_segments__mutmut_110,
    x_event_list_period_segments__mutmut_128, x_event_list_period_segments__mutmut_134,
    x_event_list_period_segments__mutmut_135, x_event_list_period_segments__mutmut_136.
    """
    filters = {"date_from": "2026-06-01", "date_to": "2026-06-10", "doc_type": "abc"}

    segments, extra = segments_for(filters)

    assert segments == [
        preset("today", "Heute", "/events/?doc_type=abc&range=today"),
        preset("this_week", "Woche", "/events/?doc_type=abc&range=this_week"),
        preset("this_month", "Monat", "/events/?doc_type=abc&range=this_month"),
        {
            "key": "custom",
            "label": "Zeitraum",
            "active": True,
            "url": "/events/?doc_type=abc&date_from=2026-06-01&date_to=2026-06-10#event-date_from",
            "htmx": False,
        },
    ]
    assert extra == preset("last_month", "Letzter Monat", "/events/?doc_type=abc&range=last_month")


def test_active_preset_link_switches_it_off_and_period_link_turns_it_into_dates():
    """Pins x_event_list_period_segments__mutmut_37, x_event_list_period_segments__mutmut_81."""
    segments, extra = segments_for({"range": "this_week", "doc_type": "abc"})

    assert segments == [
        preset("today", "Heute", "/events/?doc_type=abc&range=today"),
        preset("this_week", "Woche", "/events/?doc_type=abc", active=True),
        preset("this_month", "Monat", "/events/?doc_type=abc&range=this_month"),
        {
            "key": "custom",
            "label": "Zeitraum",
            "active": False,
            "url": "/events/?doc_type=abc&date_from=2026-06-08&date_to=2026-06-14",
            "htmx": True,
        },
    ]
    assert extra["active"] is False


@pytest.mark.parametrize("bound", ["date_from", "date_to"])
def test_period_segment_is_active_with_a_single_date_bound(bound):
    """Pins x_event_list_period_segments__mutmut_115, x_event_list_period_segments__mutmut_116,
    x_event_list_period_segments__mutmut_117, x_event_list_period_segments__mutmut_119,
    x_event_list_period_segments__mutmut_120.
    """
    segments, _extra = segments_for({bound: "2026-06-05"})

    custom = segments[-1]
    assert custom["key"] == "custom"
    assert custom["active"] is True
    assert custom["url"] == f"/events/?{bound}=2026-06-05#event-date_from"
