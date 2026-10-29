"""Pins for temporal disclosure identities, cohorts and publication metadata.

Source: mutation delta of the release-0.99.9 checkpoint, 18 mutants.
Groups: hidden atoms (3), person sensitivity (2), aliases (3), weights (3),
unknown identities (2), metadata (2), error diagnostics (3).
"""

from datetime import date
from types import SimpleNamespace

import pytest

from core.services.dashboard.shared_disclosure import CountKey, RawCount, TemporalPopulation, protect_temporal_counts

START, END = date(2026, 1, 1), date(2026, 3, 31)
WINDOWS = ((START, END),)
FACILITY = SimpleNamespace(pk=37)


def population(identity="events", *, dimension="event_counts", size=7, weights=()):
    return TemporalPopulation(
        dimension, identity, tuple((f"unit-{i}", START) for i in range(size)), unit_weights=weights
    )


def key(identity="events", end=END):
    return CountKey("event_counts", identity, START, end)


def test_internal_atoms_stay_hidden_and_metadata_survives():
    """Keep atoms private and retain scope: x_protect_temporal_counts__mutmut_110,
    x_protect_temporal_counts__mutmut_113, x_protect_temporal_counts__mutmut_114,
    x_protect_temporal_counts__mutmut_199, x_protect_temporal_counts__mutmut_200.
    """
    mask = protect_temporal_counts(FACILITY, [population()], WINDOWS, threshold=5)
    atoms = {cell: value for cell, value in mask.published.items() if cell.population.startswith("canonical:")}
    assert atoms
    assert all(value is None for value in atoms.values())
    assert mask.published[key()] == 7
    assert mask.facility_id == FACILITY.pk
    assert mask.threshold == 5


def test_person_sensitive_target_cannot_be_reconstructed_from_another_population():
    """Protect a hidden six-event population: x_protect_temporal_counts__mutmut_158,
    x_protect_temporal_counts__mutmut_163.
    """
    mask = protect_temporal_counts(
        FACILITY,
        [population("sensitive", size=6), population("public", size=6)],
        WINDOWS,
        hidden_keys={key("sensitive")},
        person_sensitive_keys={key("sensitive")},
        threshold=5,
    )
    assert mask.published[key("sensitive")] is None
    assert mask.published[key("public")] is None


def test_later_alias_transfers_local_suppression_to_the_earlier_window():
    """Honor the later alias: x_protect_temporal_counts__mutmut_179,
    x_protect_temporal_counts__mutmut_180.
    """
    earlier, later = key(end=date(2026, 2, 28)), key()
    mask = protect_temporal_counts(
        FACILITY,
        [population()],
        ((START, earlier.date_to), (START, later.date_to)),
        observations=[RawCount(later, 7, hidden=True)],
        threshold=5,
    )
    assert mask.published[earlier] is None
    assert mask.published[later] is None


def test_dropped_positive_row_disappears_in_every_window_alias():
    """Withhold row existence across aliases: x_protect_temporal_counts__mutmut_207."""
    earlier, later = key(end=date(2026, 2, 28)), key()
    mask = protect_temporal_counts(
        FACILITY,
        [population(size=1)],
        ((START, earlier.date_to), (START, later.date_to)),
        observations=[RawCount(later, 1, lower=1)],
        threshold=2,
    )
    assert {earlier, later} <= mask.dropped
    assert mask.published[earlier] is None
    assert mask.published[later] is None


@pytest.mark.parametrize("weight", [1, 2])
def test_distinct_person_weights_are_exactly_one(weight):
    """Accept unit person weights and reject inflation: x_protect_temporal_counts__mutmut_26."""
    persons = population(dimension="person_counts", size=1, weights=(("unit-0", weight),))
    if weight == 1:
        mask = protect_temporal_counts(FACILITY, [persons], WINDOWS, threshold=5)
        assert mask.published[CountKey("person_counts", "events", START, END)] is None
    else:
        with pytest.raises(ValueError) as error:
            protect_temporal_counts(FACILITY, [persons], WINDOWS, threshold=5)
        assert str(error.value) == "Distinct person populations require actual unit memberships"


@pytest.mark.parametrize("weight", [-1, 0, True])
def test_invalid_weights_cannot_hide_inside_an_aggregate(weight):
    """Reject invalid cohorts before adding: x_protect_temporal_counts__mutmut_37,
    x_protect_temporal_counts__mutmut_38, x_protect_temporal_counts__mutmut_42.
    """
    events = population(size=2, weights=(("unit-0", weight), ("unit-1", 6)))
    with pytest.raises(ValueError) as error:
        protect_temporal_counts(FACILITY, [events], WINDOWS, threshold=5)
    assert str(error.value) == "Invalid event cohort weight"


@pytest.mark.parametrize("option", ["observations", "person_sensitive_keys", "dropped_keys"])
def test_unknown_local_constraints_are_rejected(option):
    """Validate every constraint identity: x_protect_temporal_counts__mutmut_67,
    x_protect_temporal_counts__mutmut_68.
    """
    unknown = key("unknown")
    value = [RawCount(unknown, 7)] if option == "observations" else {unknown}
    with pytest.raises(ValueError) as error:
        protect_temporal_counts(FACILITY, [population()], WINDOWS, threshold=5, **{option: value})
    assert str(error.value) == "Unknown temporal suppression identity"


def test_duplicate_population_error_identifies_the_problem():
    """Keep the duplicate identity diagnostic: x_protect_temporal_counts__mutmut_13."""
    with pytest.raises(ValueError) as error:
        protect_temporal_counts(FACILITY, [population(), population()], WINDOWS, threshold=5)
    assert str(error.value) == "Duplicate temporal population identity"


def test_conflicting_cohort_error_identifies_the_problem():
    """Keep the conflicting weight diagnostic: x_protect_temporal_counts__mutmut_52."""
    first = population("first", size=1, weights=(("unit-0", 6),))
    second = population("second", size=1, weights=(("unit-0", 7),))
    with pytest.raises(ValueError) as error:
        protect_temporal_counts(FACILITY, [first, second], WINDOWS, threshold=5)
    assert str(error.value) == "Conflicting event cohort weights"
