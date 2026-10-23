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


def exported_formats():
    """Return the formats the export dispatch really accepts, plus the preview."""
    from core.reporting import export

    return ("preview", *export._CONTENT_TYPES)


PAIRS = tuple((dimension, *pair) for dimension, slugs in DIMENSIONS.items() for pair in combinations(slugs, 2))
FORMAT_PAIRS = tuple((*pair, *formats) for pair, formats in product(PAIRS, product(FORMATS, repeat=2)))
SOURCES = {
    (slug, format_name): "snapshot_hybrid" if (slug, format_name) == ("sample-report-1", "pdf") else "live"
    for slugs in DIMENSIONS.values()
    for slug in slugs
    for format_name in FORMATS
}

# Explicit review boundary: new dimensions, pairs or sources require review.
ATTACK_CLASSES = (
    ("event_counts", "ban-overview", "counseling-hours", "live", "live"),
    ("event_counts", "ban-overview", "example", "live", "live"),
    ("event_counts", "ban-overview", "general-key-figures", "live", "live"),
    ("event_counts", "ban-overview", "monthly-trend", "live", "live"),
    ("event_counts", "ban-overview", "needle-exchange-balance", "live", "live"),
    ("event_counts", "ban-overview", "quantity-sum", "live", "live"),
    ("event_counts", "ban-overview", "sample-report-1", "live", "live"),
    ("event_counts", "ban-overview", "sample-report-1", "live", "snapshot_hybrid"),
    ("event_counts", "ban-overview", "service-distribution", "live", "live"),
    ("event_counts", "counseling-hours", "example", "live", "live"),
    ("event_counts", "counseling-hours", "general-key-figures", "live", "live"),
    ("event_counts", "counseling-hours", "monthly-trend", "live", "live"),
    ("event_counts", "counseling-hours", "needle-exchange-balance", "live", "live"),
    ("event_counts", "counseling-hours", "quantity-sum", "live", "live"),
    ("event_counts", "counseling-hours", "sample-report-1", "live", "live"),
    ("event_counts", "counseling-hours", "sample-report-1", "live", "snapshot_hybrid"),
    ("event_counts", "counseling-hours", "service-distribution", "live", "live"),
    ("event_counts", "example", "general-key-figures", "live", "live"),
    ("event_counts", "example", "monthly-trend", "live", "live"),
    ("event_counts", "example", "needle-exchange-balance", "live", "live"),
    ("event_counts", "example", "quantity-sum", "live", "live"),
    ("event_counts", "example", "sample-report-1", "live", "live"),
    ("event_counts", "example", "sample-report-1", "live", "snapshot_hybrid"),
    ("event_counts", "example", "service-distribution", "live", "live"),
    ("event_counts", "general-key-figures", "monthly-trend", "live", "live"),
    ("event_counts", "general-key-figures", "needle-exchange-balance", "live", "live"),
    ("event_counts", "general-key-figures", "quantity-sum", "live", "live"),
    ("event_counts", "general-key-figures", "sample-report-1", "live", "live"),
    ("event_counts", "general-key-figures", "sample-report-1", "live", "snapshot_hybrid"),
    ("event_counts", "general-key-figures", "service-distribution", "live", "live"),
    ("event_counts", "monthly-trend", "needle-exchange-balance", "live", "live"),
    ("event_counts", "monthly-trend", "quantity-sum", "live", "live"),
    ("event_counts", "monthly-trend", "sample-report-1", "live", "live"),
    ("event_counts", "monthly-trend", "sample-report-1", "live", "snapshot_hybrid"),
    ("event_counts", "monthly-trend", "service-distribution", "live", "live"),
    ("event_counts", "needle-exchange-balance", "quantity-sum", "live", "live"),
    ("event_counts", "needle-exchange-balance", "sample-report-1", "live", "live"),
    ("event_counts", "needle-exchange-balance", "sample-report-1", "live", "snapshot_hybrid"),
    ("event_counts", "needle-exchange-balance", "service-distribution", "live", "live"),
    ("event_counts", "quantity-sum", "sample-report-1", "live", "live"),
    ("event_counts", "quantity-sum", "sample-report-1", "live", "snapshot_hybrid"),
    ("event_counts", "quantity-sum", "service-distribution", "live", "live"),
    ("event_counts", "sample-report-1", "service-distribution", "live", "live"),
    ("event_counts", "sample-report-1", "service-distribution", "snapshot_hybrid", "live"),
    ("person_counts", "client-metrics", "general-key-figures", "live", "live"),
)
