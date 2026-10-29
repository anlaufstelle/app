"""Pins for canonical calendar windows and live projection validation.

Source: mutation delta of the release-0.99.9 checkpoint, 3 mutants.
Groups: empty-source fallback (1), complete calendar boundaries (1), source diagnostic (1).
"""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from core.services.dashboard import shared_disclosure as disclosure
from core.services.dashboard.statistics import ROLLING_WINDOW_DAYS


@pytest.mark.parametrize("populated", [False, True])
def test_only_empty_sources_add_the_fallback_window(monkeypatch, populated):
    """Source months add their unions; only empty sources add a fallback: x_canonical_live_windows__mutmut_14."""
    today = date(2026, 3, 31)
    monkeypatch.setattr(timezone, "localdate", lambda: today)
    sources = (
        [
            disclosure.TemporalPopulation(
                "event_counts", "all", (("first", date(2026, 1, 15)), ("second", date(2026, 3, 15)))
            )
        ]
        if populated
        else []
    )
    # A current stock must never create a historical calendar month.
    sources.append(disclosure.TemporalPopulation("case_counts", "stock", (("case", date.min),), point_in_time=True))
    expected = {(today - timedelta(days=days - 1), today) for days in ROLLING_WINDOW_DAYS.values()}
    expected.update(
        {(date(2026, 1, 1), date(2026, 1, 31)), (date(2026, 1, 1), today), (date(2026, 3, 1), today)}
        if populated
        else {(date.min, date.min)}
    )
    assert disclosure.canonical_live_windows(sources) == tuple(sorted(expected))


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (date(2026, 1, 1), date(2026, 1, 15), False),
        (date(2026, 1, 15), date(2026, 1, 31), False),
        (date(2024, 2, 1), date(2024, 2, 29), True),
    ],
)
def test_calendar_interval_requires_both_complete_month_boundaries(start, end, expected):
    """Both calendar boundaries must be complete: x_is_calendar_interval__mutmut_1."""
    assert disclosure.is_calendar_interval(start, end) is expected


def test_non_live_dataset_projection_explains_the_invalid_source():
    """Reject another source before building a live release: x_project_shared_dataset__mutmut_6."""
    result = SimpleNamespace(disclosure_context=(date(2026, 1, 1), date(2026, 1, 31), "snapshot"))
    with pytest.raises(ValueError) as error:
        disclosure.project_shared_dataset(SimpleNamespace(pk=271), result, "client-metrics")
    assert str(error.value) == "A live population projection requires a live source"
