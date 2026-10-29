"""Pins for hybrid snapshot cohort collection.

Source: mutation delta of the release-0.99.9 checkpoint, 19 mutants.
Groups: total cohort token and default (4), additive counters (3), month-start
day (1), missing optional lists (4), counter validation (6), fixed identity (2).
Follows test_mutation_pins_0998_case_handover_note.py.
"""

import re
from datetime import date

import pytest

from core.services.dashboard.shared_disclosure import TemporalPopulation, population_units
from core.services.dashboard.shared_hybrid_sources import collect_hybrid_populations

JAN = (2000, 1)
FEB = (2000, 2)
MAPPED = "hybrid-mapped-events"
INVALID_COUNT = "Invalid snapshot event cohort count"


def _collect(snapshots, populations=()):
    """Collect hybrid populations from raw month payloads and key them by identity."""
    wrapped = {month: {"backend_data": {"sample-report-1": raw}} for month, raw in snapshots.items()}
    return {p.identity: p for p in collect_hybrid_populations(populations, wrapped)}


def _tokens(population):
    """Return the set of cohort tokens, treating their naming as opaque."""
    return {token for token, _ in population.members}


def _days(population):
    """Return the set of member dates."""
    return {day for _, day in population.members}


def _weights(population):
    """Return the sorted multiset of cohort weights."""
    return sorted(weight for _, weight in population.unit_weights)


def test_inconsistent_snapshot_months_keep_distinct_total_cohorts():
    """Two inconsistent months contribute separate total cohorts with their own weights.

    Kills x_collect_hybrid_populations__mutmut_105 through collect_hybrid_populations.
    """
    result = _collect(
        {
            JAN: {"total": 10, "by_category": [["A", 4]]},
            FEB: {"total": 7, "by_category": [["A", 3]]},
        }
    )

    mapped = result[MAPPED]
    assert len(mapped.members) == 2
    assert _days(mapped) == {date(2000, 1, 1), date(2000, 2, 1)}
    assert len(_tokens(mapped)) == 2
    assert all(isinstance(token, str) for token in _tokens(mapped))
    assert {token for token, _ in mapped.unit_weights} == _tokens(mapped)
    assert _weights(mapped) == [7, 10]


def test_snapshot_without_total_invents_no_total_cohort():
    """A category-only payload leaves the mapped-events population empty.

    Kills x_collect_hybrid_populations__mutmut_120 and
    x_collect_hybrid_populations__mutmut_115 and
    x_collect_hybrid_populations__mutmut_117 through collect_hybrid_populations.
    """
    result = _collect({JAN: {"by_category": [["A", 3]]}})

    assert result[MAPPED].members == ()
    assert result[MAPPED].unit_weights == ()
    category = result["hybrid-category:A"]
    assert len(category.members) == 1
    assert _days(category) == {date(2000, 1, 1)}
    assert _weights(category) == [3]


def test_snapshot_without_total_keeps_single_count_category_out_of_mapped_events():
    """A category of one without a total is not part of the mapped-events cohort.

    Kills x_collect_hybrid_populations__mutmut_82 through collect_hybrid_populations.
    """
    result = _collect({JAN: {"by_category": [["A", 1]]}})

    assert result[MAPPED].members == ()
    assert result[MAPPED].unit_weights == ()
    category = result["hybrid-category:A"]
    assert len(category.members) == 1
    assert _weights(category) == [1]


def test_repeated_age_cluster_rows_are_summed():
    """Rows of the same age cluster add up to one cohort weight.

    Kills x_collect_hybrid_populations__mutmut_129 through collect_hybrid_populations.
    """
    result = _collect(
        {JAN: {"total": 5, "by_age_cluster": [{"cluster": "18-26", "count": 2}, {"cluster": "18-26", "count": 3}]}}
    )

    age = result["hybrid-age:18-26"]
    assert len(age.members) == 1
    assert _weights(age) == [5]


def test_repeated_category_rows_are_summed_and_stay_consistent_with_total():
    """Rows of the same category add up and still partition the total cohort.

    Kills x_collect_hybrid_populations__mutmut_64 through collect_hybrid_populations.
    """
    result = _collect({JAN: {"total": 5, "by_category": [["A", 2], ["A", 3]]}})

    category = result["hybrid-category:A"]
    mapped = result[MAPPED]
    assert _weights(category) == [5]
    assert len(mapped.members) == 1
    assert _days(mapped) == {date(2000, 1, 1)}
    assert _tokens(mapped) == _tokens(category)
    assert _weights(mapped) == [5]


def test_snapshot_cohorts_are_dated_on_the_first_of_the_month():
    """A past month's cohort is a member on the month's first day.

    Kills x_collect_hybrid_populations__mutmut_55 through collect_hybrid_populations.
    """
    result = _collect({JAN: {"total": 3, "by_category": [["A", 3]]}})

    first = date(2000, 1, 1)
    mapped = result[MAPPED]
    assert len(mapped.members) == 1
    assert _days(mapped) == {first}
    assert population_units(mapped, first, first) == _tokens(mapped)
    assert len(population_units(mapped, first, first)) == 1


def test_snapshot_without_age_clusters_still_yields_category_and_total_cohorts():
    """A payload without by_age_cluster is collected without error.

    Kills x_collect_hybrid_populations__mutmut_124 and
    x_collect_hybrid_populations__mutmut_126 through collect_hybrid_populations.
    """
    result = _collect({JAN: {"total": 3, "by_category": [["A", 3]]}})

    category = result["hybrid-category:A"]
    assert _weights(category) == [3]
    assert _weights(result[MAPPED]) == [3]
    assert _tokens(result[MAPPED]) == _tokens(category)
    assert not any(identity.startswith("hybrid-age:") for identity in result)


def test_snapshot_without_categories_still_yields_total_and_age_cohorts():
    """A payload without by_category is collected without error.

    Kills x_collect_hybrid_populations__mutmut_59 and
    x_collect_hybrid_populations__mutmut_61 through collect_hybrid_populations.
    """
    result = _collect({JAN: {"total": 4, "by_age_cluster": [{"cluster": "27+", "count": 4}]}})

    assert _weights(result["hybrid-age:27+"]) == [4]
    assert _weights(result[MAPPED]) == [4]
    assert len(result[MAPPED].members) == 1


@pytest.mark.parametrize("amount", [-1, 1.5])
def test_invalid_snapshot_counters_are_rejected(amount):
    """Negative and fractional counters raise a ValueError with the fixed message.

    Kills x_collect_hybrid_populations__mutmut_37, x_collect_hybrid_populations__mutmut_38
    and x_collect_hybrid_populations__mutmut_42 through collect_hybrid_populations.
    """
    with pytest.raises(ValueError, match=re.escape(INVALID_COUNT)):
        _collect({JAN: {"total": 3, "by_category": [["A", amount]]}})


def test_zero_counters_are_valid_and_retain_fixed_empty_cells():
    """Zero-count categories and age clusters are accepted and kept as empty populations.

    Kills x_collect_hybrid_populations__mutmut_40 and
    x_collect_hybrid_populations__mutmut_41 through collect_hybrid_populations.
    """
    result = _collect(
        {JAN: {"total": 0, "by_category": [["A", 0]], "by_age_cluster": [{"cluster": "18-26", "count": 0}]}}
    )

    assert result["hybrid-category:A"] == TemporalPopulation("event_counts", "hybrid-category:A", ())
    assert result["hybrid-age:18-26"] == TemporalPopulation("event_counts", "hybrid-age:18-26", ())
    assert result[MAPPED] == TemporalPopulation("event_counts", MAPPED, ())


def test_empty_inputs_yield_only_the_fixed_mapped_events_population():
    """Without live members or snapshots the sole population is the lowercase mapped-events cell.

    Kills x_collect_hybrid_populations__mutmut_6 and
    x_collect_hybrid_populations__mutmut_7 through collect_hybrid_populations.
    """
    assert collect_hybrid_populations((), {}) == (TemporalPopulation("event_counts", "hybrid-mapped-events", ()),)
