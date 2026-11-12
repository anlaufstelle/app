"""Pins for the period text of the event list period chip.

Source: targeted mutation delta ZS0, batch ``list_filters-c`` (44 mutants of
``_period_text``, reached through the public chip builder).
Refs #2712
Refs #1997

The dates are formatted without the year inside the current year and with the
year otherwise; the text is checked under the German locale.
"""

from datetime import date

import pytest
from django.utils import translation

from core.services.events.list_filters import event_list_active_filters

TODAY = date(2026, 6, 10)


def period_text(filters):
    with translation.override("de"):
        chips = event_list_active_filters(filters, {}, base_url="/events/", today=TODAY)
    assert [chip["key"] for chip in chips] == ["period"]
    return chips[0]["text"]


@pytest.mark.parametrize(
    ("preset", "label"),
    [
        ("today", "Heute"),
        ("this_week", "Diese Woche"),
        ("this_month", "Dieser Monat"),
        ("last_month", "Letzter Monat"),
    ],
)
def test_preset_period_uses_its_label(preset, label):
    """Pins x__period_text__mutmut_2, x__period_text__mutmut_3, x__period_text__mutmut_5,
    x__period_text__mutmut_6, x__period_text__mutmut_7, x__period_text__mutmut_8,
    x__period_text__mutmut_9, x__period_text__mutmut_11, x__period_text__mutmut_12,
    x__period_text__mutmut_13, x__period_text__mutmut_14, x__period_text__mutmut_15,
    x__period_text__mutmut_17, x__period_text__mutmut_18, x__period_text__mutmut_19,
    x__period_text__mutmut_20, x__period_text__mutmut_21, x__period_text__mutmut_23,
    x__period_text__mutmut_24, x__period_text__mutmut_25, x__period_text__mutmut_26,
    x__period_text__mutmut_27, x__period_text__mutmut_28.
    """
    assert period_text({"range": preset}) == f"Zeitraum: {label}"


def test_period_with_both_bounds_shows_a_range_without_year_in_the_current_year():
    """Pins x__period_text__mutmut_41 (a single bound must not take the range branch)."""
    assert period_text({"date_from": "2026-06-05", "date_to": "2026-06-07"}) == "Zeitraum: 05.06.–07.06."


def test_period_with_only_a_start_reads_from_that_day():
    """Pins x__period_text__mutmut_41, x__period_text__mutmut_50, x__period_text__mutmut_51,
    x__period_text__mutmut_52, x__period_text__mutmut_53, x__period_text__mutmut_54,
    x__period_text__mutmut_55, x__period_text__mutmut_56, x__period_text__mutmut_57,
    x__period_text__mutmut_58, x__period_text__mutmut_59.
    """
    assert period_text({"date_from": "2026-06-05"}) == "Zeitraum: ab 05.06."


def test_period_with_only_an_end_reads_until_that_day_with_year_outside_the_current_year():
    """Pins x__period_text__mutmut_41, x__period_text__mutmut_60, x__period_text__mutmut_61,
    x__period_text__mutmut_62, x__period_text__mutmut_63, x__period_text__mutmut_64,
    x__period_text__mutmut_65, x__period_text__mutmut_66, x__period_text__mutmut_67,
    x__period_text__mutmut_68, x__period_text__mutmut_69.
    """
    assert period_text({"date_to": "2025-12-31"}) == "Zeitraum: bis 31.12.2025"
