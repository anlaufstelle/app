"""Pins for facility release history and overlap warnings.

Source: mutation delta of the release-0.99.7 checkpoint, 31 mutants.
Groups: incomplete periods (3), duplicate boundaries (2), legacy artifact
fallbacks (14), pagination ties (1), overlap exclusions (5), eager fields (6).
The overlap exclusions need direct helper calls: the public overview removes
these candidates in SQL before the predicate can observe them. Eager fields
are inspected during public overview construction, without query-count limits.
"""

from datetime import date
from uuid import UUID

import pytest
from django.utils import timezone

from core.models import AuditLog
from core.services.audit.chain import compute_entry_hash
from core.services.dashboard import release_log

pytestmark = pytest.mark.django_db


def _export(facility, start, end, *, target="DatasetExport", **detail):
    """Create a marked historical artifact with explicit period metadata."""
    return AuditLog.objects.create(
        facility=facility,
        action=AuditLog.Action.EXPORT,
        target_type=target,
        detail={release_log.RELEASE_ARTIFACT_KEY: True, "date_from": start, "date_to": end, **detail},
    )


@pytest.mark.parametrize("missing", ["date_from", "date_to"], ids=["missing-start", "missing-end"])
def test_incomplete_period_keeps_older_warnings(facility, missing):
    """Incomplete newest rows preserve older warnings and their own release objects.

    x_build_release_overview__mutmut_25, x_build_release_overview__mutmut_21,
    x_build_release_overview__mutmut_24.
    """
    _export(facility, "2026-01-01", "2026-12-31", backend="monthly-trend")
    _export(facility, "2026-02-01", "2026-02-28", backend="monthly-trend")
    bounds = {"date_from": "2026-03-01", "date_to": "2026-03-31", missing: None}
    newest = _export(facility, bounds["date_from"], bounds["date_to"], backend="monthly-trend")

    releases = release_log.build_release_overview(facility)

    assert len(releases) == 3
    assert releases[0].timestamp == newest.timestamp
    assert releases[0].artifact == "monthly-trend"
    assert releases[0].has_period is False
    assert releases[0].overlaps == ()
    assert releases[1].overlaps == ("monthly-trend 2026-01-01 – 2026-12-31",)
    assert releases[2].overlaps == ("monthly-trend 2026-02-01 – 2026-02-28",)


@pytest.mark.parametrize(
    "start,end", [("2026-02-01", "2026-12-31"), ("2026-01-01", "2026-02-28")], ids=["same-end", "same-start"]
)
def test_one_shared_boundary_is_not_a_duplicate(facility, start, end):
    """Both boundaries must match to exclude a repeated artifact.

    x_build_release_overview__mutmut_39, x_build_release_overview__mutmut_40.
    """
    _export(facility, "2026-01-01", "2026-12-31", backend="monthly-trend")
    _export(facility, start, end, backend="monthly-trend")

    releases = release_log.build_release_overview(facility)

    assert releases[0].overlaps == ("monthly-trend 2026-01-01 – 2026-12-31",)
    assert releases[1].overlaps == (f"monthly-trend {start} – {end}",)


@pytest.mark.parametrize(
    "metadata,artifact",
    [
        ({"format": "csv"}, "csv"),
        ({}, "DatasetExport"),
        ({"backend": "", "format": "csv"}, "csv"),
        ({"format": ""}, "DatasetExport"),
    ],
    ids=["format", "target", "empty-backend", "empty-format"],
)
def test_legacy_artifact_fallbacks_keep_warnings(facility, metadata, artifact):
    """SQL candidate identity agrees with displayed legacy artifact identity.

    x_build_release_overview__mutmut_49, x_build_release_overview__mutmut_50,
    x_build_release_overview__mutmut_53, x_build_release_overview__mutmut_54,
    x_build_release_overview__mutmut_57, x_build_release_overview__mutmut_68,
    x_build_release_overview__mutmut_69, x_build_release_overview__mutmut_70,
    x_build_release_overview__mutmut_71, x_build_release_overview__mutmut_74,
    x_build_release_overview__mutmut_78, x_build_release_overview__mutmut_79,
    x_build_release_overview__mutmut_82, x_build_release_overview__mutmut_83.
    """
    _export(facility, "2026-01-01", "2026-12-31", **metadata)
    _export(facility, "2026-02-01", "2026-02-28", **metadata)

    releases = release_log.build_release_overview(facility)

    assert [release.artifact for release in releases] == [artifact, artifact]
    assert releases[0].overlaps == (f"{artifact} 2026-01-01 – 2026-12-31",)
    assert releases[1].overlaps == (f"{artifact} 2026-02-01 – 2026-02-28",)


def test_equal_timestamps_have_stable_page_order(facility):
    """Timestamp ties use descending primary keys across page boundaries.

    x_release_queryset__mutmut_4.
    """
    timestamp = timezone.now()
    # Historical rows can share timestamps; current chain writes cannot.
    # Insert ascending IDs so an unsorted tie would put the wrong row first.
    entries = [
        AuditLog(
            facility=facility,
            pk=UUID(int=index + 1),
            timestamp=timestamp,
            action=AuditLog.Action.EXPORT,
            target_type="DatasetExport",
            detail={release_log.RELEASE_ARTIFACT_KEY: True, "date_from": None, "recipient": f"recipient-{index}"},
        )
        for index in range(4)
    ]
    previous_hash = ""
    for entry in entries:
        entry.prev_hash = previous_hash
        entry.entry_hash = compute_entry_hash(entry, previous_hash)
        previous_hash = entry.entry_hash
    AuditLog.objects.bulk_create(entries)
    assert {entry.timestamp for entry in entries} == {timestamp}
    expected = [entry.detail["recipient"] for entry in sorted(entries, key=lambda entry: entry.pk, reverse=True)]

    first = release_log.build_release_overview(facility, limit=2)
    second = release_log.build_release_overview(facility, limit=2, offset=2)

    assert [release.recipient for release in [*first, *second]] == expected


@pytest.mark.parametrize("complete", [False, True], ids=["display-fields", "candidate-fields"])
def test_overview_materializes_fields_before_conversion(facility, monkeypatch, complete):
    """Display rows and legacy candidates carry every field needed by conversion.

    x_build_release_overview__mutmut_8, x_build_release_overview__mutmut_9,
    x_build_release_overview__mutmut_10, x_build_release_overview__mutmut_32,
    x_build_release_overview__mutmut_33, x_build_release_overview__mutmut_34.
    """
    for month in (1, 2, 3):
        _export(facility, f"2026-0{month}-01" if complete else None, "2026-12-31" if complete else None)
    original = release_log._as_release
    converted = []

    def convert(entry):
        """Observe ORM loading at the conversion boundary without issuing SQL."""
        assert {"timestamp", "target_type", "detail"}.isdisjoint(entry.get_deferred_fields())
        converted.append(entry.pk)
        return original(entry)

    monkeypatch.setattr(release_log, "_as_release", convert)
    releases = release_log.build_release_overview(facility, limit=1 if complete else 3)

    assert converted
    assert all(release.artifact == "DatasetExport" for release in releases)
    assert bool(releases[0].overlaps) is complete


def _release(artifact, start, end, *, backend=""):
    """Build a period for predicate cases excluded by the public SQL filter."""
    return release_log.Release(None, artifact, "csv", backend, start, end, "", "")


def test_unrelated_backends_never_share_a_warning():
    """Intersecting periods of unrelated populations remain separate.

    x__overlaps__mutmut_3, x__overlaps__mutmut_4, x__overlaps__mutmut_10.
    """
    first = _release("case-metrics", date(2026, 1, 1), date(2026, 12, 31), backend="case-metrics")
    second = _release("quantity-sum", date(2026, 2, 1), date(2026, 2, 28), backend="quantity-sum")
    assert release_log._overlaps(first, second) is False
    assert release_log._overlaps(second, first) is False


def test_identical_download_periods_are_not_overlaps():
    """Repeated downloads of one artifact do not produce overlap warnings.

    x__overlaps__mutmut_26.
    """
    first = _release("csv", date(2026, 1, 1), date(2026, 1, 31))
    second = _release("csv", date(2026, 1, 1), date(2026, 1, 31))
    assert release_log._overlaps(first, second) is False


def test_disjoint_periods_are_not_overlaps_in_either_order():
    """Both interval bounds are required, regardless of chronological order.

    x__overlaps__mutmut_27.
    """
    first = _release("csv", date(2026, 1, 1), date(2026, 1, 31))
    second = _release("csv", date(2026, 2, 1), date(2026, 2, 28))
    assert release_log._overlaps(first, second) is False
    assert release_log._overlaps(second, first) is False
