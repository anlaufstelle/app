"""Reviewed dimension and source inventory for shared-disclosure consumers."""

from itertools import combinations, product

DIMENSIONS = {
    "event_counts": (
        "ban-overview",
        "counseling-hours",
        "example",
        "general-key-figures",
        "monthly-trend",
        "needle-exchange-balance",
        "quantity-sum",
        "sample-report-1",
        "service-distribution",
    ),
    "person_counts": ("client-metrics", "general-key-figures"),
    "case_counts": ("case-metrics",),
}
FORMATS = ("preview", "csv", "json", "xml", "pdf")
PAIRS = tuple((dimension, *pair) for dimension, slugs in DIMENSIONS.items() for pair in combinations(slugs, 2))
FORMAT_PAIRS = tuple((*pair, *formats) for pair, formats in product(PAIRS, product(FORMATS, repeat=2)))
SOURCES = {
    (slug, format_name): "snapshot_hybrid" if (slug, format_name) == ("sample-report-1", "pdf") else "live"
    for slugs in DIMENSIONS.values()
    for slug in slugs
    for format_name in FORMATS
}
