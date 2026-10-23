"""Pin the reviewed family graph and every consumer format/source."""

from collections import defaultdict
from itertools import combinations

from core.reporting.registry import get_backends
from tests.reporting._disclosure_inventory import DIMENSIONS, FORMAT_PAIRS, FORMATS, PAIRS, SOURCES


def test_registered_dimensions_match_the_reviewed_inventory():
    registered = defaultdict(list)
    for backend in get_backends():
        for dimension in sorted(backend.disclosure_dimensions):
            registered[dimension].append(backend.slug)
    assert {dimension: tuple(slugs) for dimension, slugs in registered.items()} == DIMENSIONS
    pairs = {(dimension, *pair) for dimension, slugs in registered.items() for pair in combinations(slugs, 2)}
    assert pairs == set(PAIRS)
    assert len(PAIRS) == 37
    assert len(FORMAT_PAIRS) == 925
    assert len(set(FORMAT_PAIRS)) == 925
    assert sum(dimension == "event_counts" for dimension, _, _ in PAIRS) == 36
    assert sum(dimension == "person_counts" for dimension, _, _ in PAIRS) == 1


def test_every_registered_format_and_hybrid_source_is_inventoried():
    actual = {}
    for backend in get_backends():
        if not backend.disclosure_dimensions:
            continue
        sources = {format_name.lower(): source for format_name, source in backend.get_format_sources().items()}
        sources["preview"] = "live"
        assert set(sources) == set(FORMATS)
        actual.update({(backend.slug, format_name): source for format_name, source in sources.items()})
    assert actual == SOURCES
    assert sum(source == "snapshot_hybrid" for source in actual.values()) == 1
