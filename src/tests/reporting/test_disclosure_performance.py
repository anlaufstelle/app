"""Exact masks and bounded solver work for report-sized population graphs."""

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
    with patch.object(disclosure, "_shared_basis", wraps=disclosure._shared_basis) as calculate:
        mask = shared_disclosure.protect_counts(SimpleNamespace(pk=1), counts, partitions, threshold=5)
    assert mask.published[key("group-00:atom-large")] is None
    # 25 monotone audits plus 24 rejected re-publication trials. At most two
    # distinct effective published views per audit need exact elimination;
    # bounds and integer witnesses must reuse the same view's basis.
    assert calculate.call_count <= 2 * (25 + 24)


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


@pytest.mark.parametrize("seed", range(16))
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
        for index in range(5)
    ]
    counts, partitions = list(atoms), []
    for index in range(8):
        members = random.sample(atoms, random.randrange(1, len(atoms) + 1))
        margin = count(f"margin-{index}", sum(cell.value for cell in members), hidden=index % 3 == 0)
        counts.append(margin)
        partitions.append(shared_disclosure.CountPartition(str(index), margin.key, tuple(cell.key for cell in members)))
    for ordered in (partitions, list(reversed(partitions))):
        assert shared_disclosure.protect_counts(
            SimpleNamespace(pk=1), counts, ordered, threshold=threshold
        ) == reference_mask(counts, ordered, threshold=threshold)
