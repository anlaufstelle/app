"""Pins for public bounds, invalid partitions and incomplete shared audits.

Source: mutation delta of the release-0.99.9 checkpoint, 26 mutants.
Groups: bounded aliases (5), feasible spans (5), victim release (1),
partition validation (4), incomplete audits (10), monotonicity diagnostic (1).
"""

from datetime import date
from types import SimpleNamespace

import pytest

from core.services.dashboard import shared_disclosure as disclosure

START = date(2026, 1, 1)
END = date(2026, 1, 31)
FACILITY = SimpleNamespace(pk=271)


def key(name, dimension="event_counts"):
    return disclosure.CountKey(dimension, name, START, END)


def count(name, value, **kwargs):
    return disclosure.RawCount(key(name), value, **kwargs)


@pytest.mark.parametrize("reverse", [False, True])
def test_bounded_aliases_drop_a_publicly_fixed_singleton(reverse):
    """The tightest alias bound removes row existence.

    x_protect_counts__mutmut_14, x_protect_counts__mutmut_21,
    x_protect_counts__mutmut_29, x_protect_counts__mutmut_38,
    x_protect_counts__mutmut_40.
    """
    counts = [count("singleton", 1, lower=1, upper=1), count("singleton", 1, upper=4)]
    mask = disclosure.protect_counts(FACILITY, counts[::-1] if reverse else counts, [], threshold=3)
    assert mask.published == {key("singleton"): None}
    assert mask.dropped == frozenset({key("singleton")})


@pytest.mark.parametrize(
    ("value", "lower", "upper", "person_sensitive", "dropped"),
    [(1, 1, 1, False, True), (2, 2, 4, False, True), (1, 1, 2, False, False), (3, 2, 4, True, False)],
)
def test_public_bounds_preserve_exactly_the_allowed_alternatives(value, lower, upper, person_sensitive, dropped):
    """Finite bounds retain two alternatives and remove a fixed secret.

    x_protect_counts__mutmut_87, x_protect_counts__mutmut_93,
    x_protect_counts__mutmut_94, x_protect_counts__mutmut_96,
    x_protect_counts__mutmut_168.
    """
    cell = count("bounded", value, lower=lower, upper=upper, hidden=True, person_sensitive=person_sensitive)
    mask = disclosure.protect_counts(FACILITY, [cell], [], threshold=3)
    assert mask.published == {cell.key: None}
    assert (cell.key in mask.dropped) is dropped


def test_dropped_early_victims_do_not_prevent_later_safe_cells_from_returning():
    """Independent dropped victims do not block re-publication: x_protect_counts__mutmut_191."""
    counts = [
        count("fixed-margin", 6, suppression_rank=-10),
        count("fixed-small", 1, lower=1, upper=1),
        count("fixed-large", 5, suppression_rank=-10),
        count("first-margin", 16),
        count("small-first", 2),
        count("left", 6),
        count("shared", 8),
        count("second-margin", 20),
        count("small-second", 3),
        count("right", 9),
    ]
    partitions = [
        disclosure.CountPartition(name, key(margin), tuple(map(key, cells)))
        for name, margin, cells in (
            ("fixed", "fixed-margin", ("fixed-small", "fixed-large")),
            ("first", "first-margin", ("small-first", "left", "shared")),
            ("second", "second-margin", ("small-second", "shared", "right")),
        )
    ]
    mask = disclosure.protect_counts(FACILITY, counts, partitions, threshold=5)
    assert {key(name) for name in ("fixed-margin", "fixed-small", "fixed-large")} <= mask.dropped
    assert mask.published[key("shared")] is None
    assert mask.published[key("left")] == 6
    assert mask.published[key("right")] == 9


@pytest.mark.parametrize("invalid", ["dimension", "duplicate", "missing-child", "missing-margin"])
def test_invalid_partition_structure_is_rejected_before_solving(invalid):
    """Reject independently invalid equations with the partition diagnostic.

    x_protect_counts__mutmut_49, x_protect_counts__mutmut_50,
    x_protect_counts__mutmut_51, x_protect_counts__mutmut_52.
    """
    margin, child = key("margin"), key("child")
    if invalid == "dimension":
        child = key("child", "person_counts")
    counts = [disclosure.RawCount(margin, 10 if invalid == "duplicate" else 5), disclosure.RawCount(child, 5)]
    cells = (child, child) if invalid == "duplicate" else (child,)
    if invalid == "missing-child":
        counts = counts[:1]
    if invalid == "missing-margin":
        counts = counts[1:]
    with pytest.raises(ValueError) as error:
        disclosure.protect_counts(FACILITY, counts, [disclosure.CountPartition("invalid", margin, cells)], threshold=3)
    assert str(error.value) == "Invalid raw partition: invalid"


def test_incomplete_audit_returns_a_usable_complete_fail_closed_mask(monkeypatch):
    """Solver failure preserves metadata and withholds all cells and rows.

    x_protect_counts__mutmut_178, x_protect_counts__mutmut_179,
    x_protect_counts__mutmut_180, x_protect_counts__mutmut_181,
    x_protect_counts__mutmut_182, x_protect_counts__mutmut_183,
    x_protect_counts__mutmut_184, x_protect_counts__mutmut_185,
    x_protect_counts__mutmut_186, x_protect_counts__mutmut_187.
    """

    def incomplete(*args, **kwargs):
        raise RuntimeError("Incomplete feasibility audit")

    monkeypatch.setattr(disclosure, "shared_bounds", incomplete)
    counts = [count("small", 1), count("large", 8)]
    mask = disclosure.protect_counts(FACILITY, counts, [], threshold=3)
    assert mask.facility_id == FACILITY.pk
    assert mask.threshold == 3
    assert mask.published == {cell.key: None for cell in counts}
    assert mask.dropped == frozenset(cell.key for cell in counts)


def test_exhausted_iteration_budget_retains_the_monotonicity_diagnostic(monkeypatch):
    """An exhausted budget explains the failed invariant: x_protect_counts__mutmut_230."""
    # Fault injection reaches the defensive invariant without corrupting a solver result.
    monkeypatch.setattr(disclosure, "range", lambda stop: (), raising=False)
    with pytest.raises(RuntimeError) as error:
        disclosure.protect_counts(FACILITY, [count("safe", 8)], [], threshold=3)
    assert str(error.value) == "Shared suppression lost monotonicity"
