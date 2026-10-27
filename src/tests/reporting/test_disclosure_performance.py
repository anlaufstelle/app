"""Exact masks and bounded solver work for report-sized population graphs."""

from cProfile import Profile
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.services.dashboard import disclosure, shared_disclosure
from tests.reporting import _disclosure_reference as reference
from tests.reporting.test_shared_dimension_disclosure import count, key


def report_sized_graph():
    """434 cells / 388 equations with internal aliases and 24 secondary victims.

    Each group models one small canonical atom beside a published larger
    population. Fifteen internal aggregate aliases share its exact equation.
    Releasing the larger atom during the final utility pass exposes the small
    atom again, exercising both hiding and re-publication of cells.
    """
    counts, partitions = [], []
    for index in range(24):
        prefix = f"group-{index:02d}"
        small, large = f"{prefix}:atom-small", f"{prefix}:atom-large"
        counts.extend([count(small, 1), count(large, 10)])
        for alias in range(16):
            margin = f"{prefix}:margin-{alias:02d}"
            counts.append(count(margin, 11, hidden=alias > 0))
            partitions.append(shared_disclosure.CountPartition(margin, key(margin), (key(small), key(large))))
    counts.extend([count("empty-event", 0), count("empty-person", 0)])
    partitions.extend(partitions[:4])
    return counts, partitions


def reference_mask(counts, partitions, *, threshold=5, facility_id=1):
    """Use the frozen solver through the unchanged public mask state machine."""
    with (
        patch.object(shared_disclosure, "shared_bounds", lambda *args, **kwargs: reference.shared_bounds(*args)),
        patch.object(
            shared_disclosure, "shared_alternatives", lambda *args, **kwargs: reference.shared_alternatives(*args)
        ),
    ):
        return shared_disclosure.protect_counts(
            SimpleNamespace(pk=facility_id), counts, partitions, threshold=threshold
        )


def test_report_sized_mask_matches_the_frozen_solver():
    counts, partitions = report_sized_graph()
    assert (len(counts), len(partitions)) == (434, 388)
    expected = reference_mask(counts, partitions)
    actual = shared_disclosure.protect_counts(SimpleNamespace(pk=1), counts, partitions, threshold=5)
    assert actual == expected
    assert not actual.dropped
    for index in range(24):
        assert actual.published[key(f"group-{index:02d}:atom-large")] is None
        assert actual.published[key(f"group-{index:02d}:margin-00")] == 11


def test_report_sized_mask_reuses_exact_bases():
    counts, partitions = report_sized_graph()
    with (
        patch.object(disclosure, "_shared_basis", wraps=disclosure._shared_basis) as calculate,
        patch.object(disclosure, "Fraction", wraps=disclosure.Fraction) as rational,
    ):
        mask = shared_disclosure.protect_counts(SimpleNamespace(pk=1), counts, partitions, threshold=5)
    assert mask.published[key("group-00:atom-large")] is None
    # The partition matrix stays fixed across all audits and utility trials.
    # Adding hidden columns must update its existing exact reduction.
    assert calculate.call_count == 1
    assert rational.call_count == 0


@pytest.mark.parametrize(
    "counts,equations,threshold",
    [
        (
            [count("all", 12), count("alpha", 7), count("bravo", 1), count("outside", 4), count("mapped", 8)],
            [("all", ("alpha", "bravo", "outside")), ("mapped", ("alpha", "bravo"))],
            5,
        ),
        (
            [
                count("x", 20, hidden=True),
                count("y", 20, hidden=True),
                count("z", 1),
                count("xy", 40),
                count("xz", 21),
                count("yz", 21),
            ],
            [("xy", ("x", "y")), ("xz", ("x", "z")), ("yz", ("y", "z"))],
            5,
        ),
        (
            [
                count("a", 3),
                count("b", 7, hidden=True),
                count("c", 4),
                count("d", 3),
                count("ab", 10),
                count("bc", 11),
                count("acd", 10),
            ],
            [("ab", ("a", "b")), ("bc", ("b", "c")), ("acd", ("a", "c", "d"))],
            5,
        ),
        (
            [count("all", 6), count("alpha", 5, hidden=True, person_sensitive=True), count("bravo", 1)],
            [("all", ("alpha", "bravo"))],
            5,
        ),
        ([count("singleton", 1, lower=1)], [], 2),
    ],
    ids=["common-margin", "fractional-cycle", "integer-world", "person-sensitive", "row-existence"],
)
def test_recipient_attack_masks_match_the_frozen_solver(counts, equations, threshold):
    partitions = [
        shared_disclosure.CountPartition(str(index), key(margin), tuple(key(cell) for cell in members))
        for index, (margin, members) in enumerate(equations)
    ]
    assert shared_disclosure.protect_counts(
        SimpleNamespace(pk=1), counts, partitions, threshold=threshold
    ) == reference_mask(counts, partitions, threshold=threshold)


def test_separate_masks_do_not_reuse_values_bounds_partitions_or_facilities():
    counts = [count("all", 11), count("alpha", 10), count("bravo", 1)]
    relation = shared_disclosure.CountPartition("types", key("all"), (key("alpha"), key("bravo")))
    cases = [
        (counts, [relation], 5, 1),
        (counts, [relation], 1, 2),
        ([replace(cell, value=value) for cell, value in zip(counts, (13, 10, 3), strict=True)], [relation], 5, 1),
        ([replace(cell, lower=1, upper=1) if cell.key == key("bravo") else cell for cell in counts], [relation], 5, 1),
        (counts, [], 5, 1),
        (counts, [relation], 5, 1),
    ]
    for raw, partitions, threshold, facility_id in cases:
        expected = reference_mask(raw, partitions, threshold=threshold, facility_id=facility_id)
        actual = shared_disclosure.protect_counts(SimpleNamespace(pk=facility_id), raw, partitions, threshold=threshold)
        assert actual == expected


def test_shared_services_invalidate_bases_when_values_are_hidden_restored_or_changed():
    cells = {
        "total": disclosure.Cell("total", 5, lo=0),
        "a": disclosure.Cell("a", None, lo=0, hi=4),
        "b": disclosure.Cell("b", None, lo=0, hi=4),
    }
    partitions = [disclosure.Partition("parts", "total", 5, (cells["a"], cells["b"]))]
    cache = disclosure.SharedBasisCache(partitions)
    raw = {"total": 5, "a": 2, "b": 3}
    expected = reference.shared_alternatives(cells, partitions, raw)
    assert disclosure.shared_alternatives(cells, partitions, raw, basis_cache=cache) == expected
    assert len(expected["a"]) > 1 and len(expected["b"]) > 1

    for value in (2, 1):
        published = {**cells, "a": replace(cells["a"], published=value)}
        world = {"total": 5, "a": value, "b": 5 - value}
        assert disclosure.shared_bounds(published, partitions, basis_cache=cache)["b"] == (5 - value, 5 - value)
        assert disclosure.shared_alternatives(published, partitions, world, basis_cache=cache) == {"b": {5 - value}}
    assert disclosure.shared_alternatives(cells, partitions, raw, basis_cache=cache) == expected

    bounded = {**cells, "a": replace(cells["a"], lo=2, hi=2)}
    assert disclosure.shared_bounds(bounded, partitions, basis_cache=cache)["b"] == (3, 3)
    assert disclosure.shared_alternatives(bounded, partitions, raw, basis_cache=cache) == {"a": {2}, "b": {3}}

    changed_graph = [disclosure.Partition("other", "total", 5, (cells["a"],))]
    with pytest.raises(ValueError, match="same partition graph"):
        disclosure.shared_bounds(cells, changed_graph, basis_cache=cache)


@pytest.mark.parametrize("seed", range(200))
def test_generated_overlapping_masks_match_the_frozen_solver(seed):
    from random import Random

    random = Random(seed)
    threshold = (2, 5)[seed % 2]
    atoms = [
        count(
            f"atom-{index}",
            random.randrange(10),
            hidden=random.choice((False, True)),
            person_sensitive=random.choice((False, True)),
        )
        for index in range(5 if seed < 16 else 2 + seed % 15)
    ]
    counts, partitions = list(atoms), []
    for index in range(8 if seed < 16 else 3 + seed % 22):
        members = random.sample(atoms, random.randrange(1, len(atoms) + 1))
        margin = count(f"margin-{index}", sum(cell.value for cell in members), hidden=index % 3 == 0)
        counts.append(margin)
        partitions.append(shared_disclosure.CountPartition(str(index), margin.key, tuple(cell.key for cell in members)))
    for ordered in (partitions, list(reversed(partitions))):
        assert shared_disclosure.protect_counts(
            SimpleNamespace(pk=1), counts, ordered, threshold=threshold
        ) == reference_mask(counts, ordered, threshold=threshold)


def test_incremental_publications_preserve_pins_and_integer_witnesses():
    cells = {key: disclosure.Cell(key, None, lo=0, hi=20) for key in ("a", "b", "c", "ab", "bc", "ac")}
    raw = {"a": 2, "b": 3, "c": 4, "ab": 5, "bc": 7, "ac": 6}
    partitions = [
        disclosure.Partition(margin, margin, None, tuple(cells[key] for key in members))
        for margin, members in (("ab", ("a", "b")), ("bc", ("b", "c")), ("ac", ("a", "c")))
    ]
    cache = disclosure.SharedBasisCache(partitions)
    for published in ((), ("ab",), ("ab", "bc"), ("ab", "bc", "ac"), ("bc",), ()):
        current = {key: replace(cell, published=raw[key] if key in published else None) for key, cell in cells.items()}
        assert disclosure.shared_bounds(current, partitions, basis_cache=cache) == reference.shared_bounds(
            current, partitions
        )
        assert disclosure.shared_alternatives(
            current, partitions, raw, basis_cache=cache
        ) == reference.shared_alternatives(current, partitions, raw)


@pytest.mark.parametrize("coupled", [False, True], ids=["independent", "shared-pivot"])
def test_independent_integer_witnesses_skip_disjoint_pairs(coupled):
    cells = {f"cell-{index:03d}": disclosure.Cell(f"cell-{index:03d}", None, lo=0) for index in range(256)}
    partitions = []
    if coupled:
        partitions.append(disclosure.Partition("wide", "total", 2560, tuple(cells.values())))
        cells["total"] = disclosure.Cell("total", 2560)
    cells["fixed"] = disclosure.Cell("fixed", None, lo=1, hi=1)
    raw = dict.fromkeys(cells, 10) | {"fixed": 1}
    if coupled:
        raw["total"] = 2560
    expected = reference.shared_alternatives(cells, partitions, raw)
    with Profile() as profile:
        actual = disclosure.shared_alternatives(cells, partitions, raw)
    assert actual == expected
    # Count executed calls, not elapsed time or implementation line numbers.
    assert sum(entry.callcount for entry in profile.getstats()) <= 100 * len(cells)


@pytest.mark.parametrize("copies", [1, 16])
def test_wide_partition_bound_propagation_has_linear_work(copies):
    cells = {f"cell-{index:03d}": disclosure.Cell(f"cell-{index:03d}", None, lo=0) for index in range(128)}
    partition = disclosure.Partition("wide", "total", 1280, tuple(cells.values()))
    cells["total"] = disclosure.Cell("total", 1280)
    partitions = [partition] * copies
    expected = reference.shared_bounds(cells, partitions)
    with Profile() as profile:
        actual = disclosure.shared_bounds(cells, partitions)
    assert actual == expected
    assert sum(entry.callcount for entry in profile.getstats()) <= 100 * len(cells)


def seed_shaped_graph():
    """2166 cells / 324 overlapping equations across six population cohorts."""
    from random import Random

    random = Random(2639)
    counts, partitions = [], []
    for cohort in range(6):
        atoms = [
            count(
                f"cohort-{cohort}:atom-{index:03d}", 1 if index < 6 else random.randrange(5, 21), hidden=index % 5 == 0
            )
            for index in range(307)
        ]
        counts.extend(atoms)
        for index in range(54):
            start = (index * 5) % 277
            members = atoms[start : start + 10 + index % 21]
            margin = count(
                f"cohort-{cohort}:window-{index:02d}", sum(cell.value for cell in members), hidden=index % 3 == 0
            )
            counts.append(margin)
            partitions.append(
                shared_disclosure.CountPartition(str((cohort, index)), margin.key, tuple(cell.key for cell in members))
            )
    return counts, partitions


def test_seed_shaped_mask_matches_the_frozen_solver():
    counts, partitions = seed_shaped_graph()
    assert (len(counts), len(partitions)) == (2166, 324)
    expected = reference_mask(counts, partitions)
    assert shared_disclosure.protect_counts(SimpleNamespace(pk=1), counts, partitions, threshold=5) == expected


def test_entering_hidden_column_keeps_later_specializations_reduced():
    width = 24
    cells = {"a": disclosure.Cell("a", 1, lo=0)}
    for index in range(width):
        cells[f"b-{index:02d}"] = disclosure.Cell(f"b-{index:02d}", 9, lo=0)
        cells[f"c-{index:02d}"] = disclosure.Cell(f"c-{index:02d}", None, lo=0)
    partitions = [
        disclosure.Partition(str(index), f"c-{index:02d}", None, (cells["a"], cells[f"b-{index:02d}"]))
        for index in range(width)
    ]
    cache = disclosure.SharedBasisCache(partitions)
    disclosure.shared_bounds(cells, partitions, basis_cache=cache)
    with patch.object(disclosure, "_primitive_row", wraps=disclosure._primitive_row) as normalize:
        for step in range(8):
            current = {
                key: replace(cell, published=None if key == "a" or key.startswith("c-") else 9 + step)
                for key, cell in cells.items()
            }
            raw = {key: 1 if key == "a" else 9 + step if key.startswith("b-") else 10 + step for key in cells}
            actual = disclosure.shared_alternatives(current, partitions, raw, basis_cache=cache)
            assert actual == reference.shared_alternatives(current, partitions, raw)
    # One column insertion affects at most width rows. Each later value view
    # substitutes width already reduced rows, without repeated elimination.
    assert normalize.call_count <= 9 * width


def test_published_cells_do_not_repeat_bound_materialization():
    cells = {f"cell-{index}": disclosure.Cell(f"cell-{index}", index) for index in range(1000)}
    with Profile() as profile:
        actual = disclosure.shared_bounds(cells, [])
    assert actual == {key: (cell.published, cell.published) for key, cell in cells.items()}
    assert sum(entry.callcount for entry in profile.getstats()) <= 8 * len(cells)


def test_failed_coupled_witnesses_do_not_rescan_all_unresolved_cells():
    width = 128
    cells = {f"cell-{index:03d}": disclosure.Cell(f"cell-{index:03d}", None, lo=0) for index in range(width)}
    partition = disclosure.Partition("wide", "total", width * 10, tuple(cells.values()))
    cells["total"] = disclosure.Cell("total", width * 10)
    cells.update(
        {f"fixed-{index:03d}": disclosure.Cell(f"fixed-{index:03d}", None, lo=1, hi=1) for index in range(width)}
    )
    raw = {key: 1 if key.startswith("fixed-") else width * 10 if key == "total" else 10 for key in cells}
    with Profile() as profile:
        actual = disclosure.shared_alternatives(cells, [partition], raw)
    assert actual == reference.shared_alternatives(cells, [partition], raw)
    assert sum(entry.callcount for entry in profile.getstats()) <= 100 * len(cells)


def test_inferred_pins_are_materialized_once_across_bound_iterations():
    cells = {key: disclosure.Cell(key, None, lo=0, hi=10) for key in ("a", "b", "d")}
    cells.update({"root": disclosure.Cell("root", 0, lo=0), "c": disclosure.Cell("c", 5)})
    partitions = [
        disclosure.Partition("cross", "c", 5, (cells["a"], cells["d"])),
        disclosure.Partition("zero", "root", 0, (cells["a"], cells["b"])),
    ]
    with patch.object(disclosure.dataclasses, "replace", wraps=replace) as materialize:
        actual = disclosure.shared_bounds(cells, partitions)
    assert actual == {"root": (0, 0), "c": (5, 5), "a": (0, 0), "b": (0, 0), "d": (5, 5)}
    assert materialize.call_count <= 3


def test_local_partition_spans_share_aggregate_work():
    cells = tuple(disclosure.Cell(f"cell-{index}", None, lo=0, hi=20) for index in range(128))
    partition = disclosure.Partition("wide", "total", 1280, cells)
    with Profile() as profile:
        spans = disclosure.feasible_spans([partition])
    assert spans == {("wide", cell.key): 21 for cell in cells}
    assert disclosure.findings([partition], threshold=5) == []
    assert sum(entry.callcount for entry in profile.getstats()) <= 50 * len(cells)
