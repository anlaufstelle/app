"""Pin the reviewed family graph and every consumer format/source."""

from collections import defaultdict
from itertools import combinations

import pytest

from core.reporting.registry import get_backends
from tests.reporting._disclosure_inventory import DIMENSIONS, FORMAT_PAIRS, FORMATS, PAIRS, SOURCES, exported_formats


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


def test_inventoried_formats_match_the_supported_export_formats():
    assert set(exported_formats()) == set(FORMATS), (
        f"Supported export formats {sorted(exported_formats())} differ from the reviewed inventory {sorted(FORMATS)}"
    )


def test_every_registered_format_and_hybrid_source_is_inventoried():
    exported = set(exported_formats())
    assert exported == set(FORMATS), f"Export formats {sorted(exported)} are not all inventoried"
    actual = {}
    for backend in get_backends():
        if not backend.disclosure_dimensions:
            continue
        declared = {format_name.lower() for format_name in backend.get_format_sources()}
        assert declared == exported - {"preview"}, (
            f"{backend.slug} declares sources for {sorted(declared)}, expected {sorted(exported - {'preview'})}"
        )
        sources = {format_name.lower(): source for format_name, source in backend.get_format_sources().items()}
        sources["preview"] = "live"
        assert set(sources) == set(FORMATS)
        actual.update({(backend.slug, format_name): source for format_name, source in sources.items()})
    assert actual == SOURCES
    assert sum(source == "snapshot_hybrid" for source in actual.values()) == 1


def _unsupported_format_inventory_checks():
    test_inventoried_formats_match_the_supported_export_formats()
    test_every_registered_format_and_hybrid_source_is_inventoried()


def test_new_supported_export_format_without_inventory_is_rejected(monkeypatch):
    from core.reporting import export

    monkeypatch.setitem(export._CONTENT_TYPES, "odt", "application/vnd.oasis.opendocument.text")
    with pytest.raises(AssertionError, match="odt"):
        _unsupported_format_inventory_checks()


def test_supported_export_format_without_source_declaration_is_rejected(monkeypatch):
    backend = next(backend for backend in get_backends() if backend.disclosure_dimensions)
    declared = {name: source for name, source in backend.get_format_sources().items() if name != "XML"}
    monkeypatch.setattr(type(backend), "get_format_sources", lambda self: declared)
    with pytest.raises(AssertionError, match="declares sources"):
        _unsupported_format_inventory_checks()


def test_source_declaration_for_an_unsupported_format_is_rejected(monkeypatch):
    backend = next(backend for backend in get_backends() if backend.disclosure_dimensions)
    declared = {**backend.get_format_sources(), "ODT": "live"}
    monkeypatch.setattr(type(backend), "get_format_sources", lambda self: declared)
    with pytest.raises(AssertionError, match="declares sources"):
        _unsupported_format_inventory_checks()
