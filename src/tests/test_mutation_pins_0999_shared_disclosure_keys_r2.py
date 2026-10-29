"""Pins for count identity validation and public raw-count bounds.

Source: mutation delta of the release-0.99.9 checkpoint, 8 mutants.
Groups: raw count validation and diagnostic (5), identity validation and diagnostic (3).
"""

from datetime import date

import pytest

from core.services.dashboard.shared_disclosure import CountKey, RawCount

START = date(2026, 1, 1)
END = date(2026, 1, 31)


@pytest.mark.parametrize(
    ("value", "bounds"),
    [(1, {"lower": 2}), (2, {"upper": 1}), (1, {"lower": -1}), (1.5, {}), (True, {}), (False, {})],
)
def test_raw_count_rejects_each_invalid_value_or_bound_independently(value, bounds):
    """Invalid counts explain the rejected public bound or type.

    xǁRawCountǁ__post_init____mutmut_1, xǁRawCountǁ__post_init____mutmut_2,
    xǁRawCountǁ__post_init____mutmut_3, xǁRawCountǁ__post_init____mutmut_4,
    xǁRawCountǁ__post_init____mutmut_12.
    """
    with pytest.raises(ValueError) as error:
        RawCount(CountKey("event_counts", "all", START, END), value, **bounds)
    assert str(error.value) == "Invalid raw count or public bounds"


@pytest.mark.parametrize(
    ("dimension", "population", "start", "end"),
    [("", "all", START, END), ("event_counts", "", START, END), ("event_counts", "all", END, START)],
)
def test_count_identity_rejects_each_missing_component_or_reversed_interval(dimension, population, start, end):
    """Invalid identities retain their actionable diagnostic.

    xǁCountKeyǁ__post_init____mutmut_1, xǁCountKeyǁ__post_init____mutmut_2,
    xǁCountKeyǁ__post_init____mutmut_6.
    """
    with pytest.raises(ValueError) as error:
        CountKey(dimension, population, start, end)
    assert str(error.value) == "Invalid count identity or interval"
