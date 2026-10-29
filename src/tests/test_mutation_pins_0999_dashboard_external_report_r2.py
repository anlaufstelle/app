"""Pin local report contracts and public projection boundaries.

Source: mutation delta of the release-0.99.9 checkpoint, 32 mutants.
Groups: counseling events and minutes (7), age partition audit (1), quantity
contributors and missing values (11), service partitions (8), absent person
partition (1), legacy age ordering (1), projection length validation (3).
Dataset scenarios use public backend preparation without a live disclosure
context, preserving the local contract before shared population projection.
"""

from datetime import date

import pytest

from core.models import Client, Settings
from core.reporting.base import DatasetResult
from core.reporting.registry import get_backend
from core.services.dashboard import external_report

pytestmark = pytest.mark.django_db


def _counseling_result(counts, *, contributors=None):
    """Build raw category aggregates with distinct, nonzero minute sums."""
    categories = ("contact", "counseling", "crisis")
    records = [
        {
            "kategorie": category,
            "events": count,
            "beitragende": count if contributors is None else contributors,
            "minuten": count * 15 if contributors != 0 else 0,
        }
        for category, count in zip(categories, counts, strict=False)
    ]
    return DatasetResult(
        "rows",
        records,
        {
            "events_gesamt": sum(counts),
            "minuten_gesamt": sum(row["minuten"] for row in records),
        },
        [],
    )


def test_secondary_counseling_suppression_also_hides_minutes(facility):
    """Hide both large cells and their minutes in the 1/5/5 partition.

    x__protect_counseling_dataset__mutmut_16,
    x__protect_counseling_dataset__mutmut_43,
    x__protect_counseling_dataset__mutmut_44,
    x__protect_counseling_dataset__mutmut_45.
    """
    result = get_backend("counseling-hours").prepare_export(facility, _counseling_result([1, 5, 5]))

    assert {row["kategorie"] for row in result.records} == {"contact", "counseling", "crisis"}
    assert all(row["events"] is None for row in result.records)
    assert all(row["minuten"] is None for row in result.records)
    assert result.summary["minuten_gesamt"] is None


def test_counseling_guard_removes_reconstructible_total(facility):
    """Withhold a total that determines three hidden one-event categories.

    x__protect_counseling_dataset__mutmut_26.
    """
    Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 3})

    result = get_backend("counseling-hours").prepare_export(facility, _counseling_result([1, 1, 1]))

    assert result.summary["events_gesamt"] is None
    assert result.summary["minuten_gesamt"] is None


def test_counseling_without_duration_contributors_hides_zero_minutes(facility):
    """Keep missing duration sums hidden even at the minimum threshold.

    x__protect_counseling_dataset__mutmut_50.
    """
    Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 1})

    result = get_backend("counseling-hours").prepare_export(facility, _counseling_result([1], contributors=0))

    assert result.records == [{"kategorie": "contact", "events": 1, "minuten": None}]
    assert result.summary["minuten_gesamt"] is None


def test_counseling_totals_at_threshold_remain_visible(facility):
    """Publish complete event and duration totals exactly at the threshold.

    x__protect_counseling_dataset__mutmut_7.
    """
    result = get_backend("counseling-hours").prepare_export(facility, _counseling_result([5]))

    assert result.records == [{"kategorie": "contact", "events": 5, "minuten": 75}]
    assert result.summary["events_gesamt"] == 5
    assert result.summary["minuten_gesamt"] == 75


def test_external_age_partition_audits_primary_upper_bounds(facility):
    """Hide the large age cell when three small complements bound it tightly.

    The public shared mask independently hides this cell. Exercise the local
    contract directly so that its primary upper bounds remain protected.
    x__protect_external_statistics__mutmut_26.
    """
    raw = {
        "total_contacts": 22,
        "unique_clients": 18,
        "by_document_type": [{"name": "Contact", "count": 22}],
        "by_contact_stage": {},
        "by_age_cluster": [
            {"cluster": cluster, "count": count, "persons": count}
            for cluster, count in [
                (Client.AgeCluster.U18, 4),
                (Client.AgeCluster.AGE_18_26, 4),
                (Client.AgeCluster.AGE_27_PLUS, 10),
            ]
        ],
    }

    result = external_report._protect_external_statistics(
        facility, date(2026, 1, 1), date(2026, 1, 31), raw, threshold=5
    )

    rows = {row["cluster"]: row for row in result["by_age_cluster"]}
    assert rows[Client.AgeCluster.AGE_27_PLUS]["count"] is None
    assert rows[Client.AgeCluster.AGE_27_PLUS]["suppressed"] is True


def _quantity_result(events, contributors):
    """Build one raw quantity row with a published event count."""
    return DatasetResult(
        "rows",
        [{"document_type": "Counter", "events": events, "contributors": contributors, "quantity": 35}],
        {
            "document_type_counts": {"Counter": events},
            "contributors_total": contributors,
            "quantity_total": 35,
        },
        [],
    )


@pytest.mark.parametrize("events, contributors", [(7, 2), (6, 5)], ids=["small-contributors", "one-missing-value"])
def test_quantity_suppression_hides_contributors_values_and_totals(facility, events, contributors):
    """Protect small contributor counts and small missing-value complements.

    x__protect_quantity_dataset__mutmut_28,
    x__protect_quantity_dataset__mutmut_33,
    x__protect_quantity_dataset__mutmut_39,
    x__protect_quantity_dataset__mutmut_40,
    x__protect_quantity_dataset__mutmut_41,
    x__protect_quantity_dataset__mutmut_42,
    x__protect_quantity_dataset__mutmut_48,
    x__protect_quantity_dataset__mutmut_49,
    x__protect_quantity_dataset__mutmut_50,
    x__protect_quantity_dataset__mutmut_95.
    """
    result = get_backend("quantity-sum").prepare_export(facility, _quantity_result(events, contributors))

    assert result.records == [{"document_type": "Counter", "contributors": None, "quantity": None}]
    assert result.summary["contributors_total"] is None
    assert result.summary["quantity_total"] is None


def test_missing_quantity_complement_at_threshold_remains_visible(facility):
    """Publish safe contributions when the missing-value complement equals k.

    x__protect_quantity_dataset__mutmut_44.
    """
    result = get_backend("quantity-sum").prepare_export(facility, _quantity_result(10, 5))

    assert result.records == [{"document_type": "Counter", "contributors": 5, "quantity": 35}]
    assert result.summary["contributors_total"] == 5
    assert result.summary["quantity_total"] == 35


def _service_result(counts):
    """Build the declared single-choice crisis partition."""
    return DatasetResult(
        "rows",
        [{"feld": "art-der-krise", "option": f"option-{index}", "count": count} for index, count in enumerate(counts)],
        {"events_art_der_krise": sum(counts)},
        [],
    )


def test_service_group_without_options_hides_zero_total(facility):
    """Withhold the zero margin of an active group without options.

    x__protect_service_dataset__mutmut_10.
    """
    result = get_backend("service-distribution").prepare_export(facility, _service_result([]))

    assert result.records == []
    assert result.summary["events_art_der_krise"] is None


def test_service_group_total_at_threshold_remains_visible(facility):
    """Publish a complete single-choice group exactly at the threshold.

    x__protect_service_dataset__mutmut_11.
    """
    result = get_backend("service-distribution").prepare_export(facility, _service_result([5]))

    assert result.records == [{"feld": "art-der-krise", "option": "option-0", "count": 5}]
    assert result.summary["events_art_der_krise"] == 5


def test_service_partition_audit_hides_both_large_cells(facility):
    """Retain local secondary protection for the single-choice 1/5/5 partition.

    x__protect_service_dataset__mutmut_12,
    x__protect_service_dataset__mutmut_14,
    x__protect_service_dataset__mutmut_23.
    """
    result = get_backend("service-distribution").prepare_export(facility, _service_result([1, 5, 5]))

    assert {row["option"] for row in result.records} == {"option-0", "option-1", "option-2"}
    assert all(row["count"] is None for row in result.records)


def test_service_guard_removes_total_of_hidden_single_events(facility):
    """Remove a margin that reconstructs five hidden one-event options.

    x__protect_service_dataset__mutmut_34,
    x__protect_service_dataset__mutmut_35,
    x__protect_service_dataset__mutmut_41.
    """
    result = get_backend("service-distribution").prepare_export(facility, _service_result([1] * 5))

    assert result.summary["events_art_der_krise"] is None


def test_residual_guard_ignores_unpublished_person_partition():
    """Raw age inputs cannot suppress a person total without a published table.

    x_apply_residual_guard__mutmut_3.
    """
    report = {"unique_clients": 5}
    age_rows = [{"cluster": f"age-{index}", "count": 1, "persons": None} for index in range(5)]

    external_report.apply_residual_guard(report, 5, (), age_rows=age_rows)

    assert report == {"unique_clients": 5}


def test_age_projection_accepts_unhashable_legacy_cluster():
    """Place a non-string legacy cluster after known ages without hashing it.

    x__age_cluster_key__mutmut_8.
    """
    legacy = {"cluster": ["legacy"]}
    known = {"cluster": Client.AgeCluster.U18}

    result = external_report.project_protected_rows(
        [legacy, known], [None, None], order=external_report.ORDER_AGE_CLUSTER
    )

    assert result == [
        {**known, "count": None, "suppressed": True},
        {**legacy, "count": None, "suppressed": True},
    ]


@pytest.mark.parametrize("counts", [[], [5, 6]], ids=["missing-count", "extra-count"])
def test_projection_rejects_mismatched_row_and_count_lengths(counts):
    """Reject either direction of a row/count length mismatch instead of truncating.

    x_project_protected_rows__mutmut_9,
    x_project_protected_rows__mutmut_12,
    x_project_protected_rows__mutmut_13.
    """
    with pytest.raises(ValueError):
        external_report.project_protected_rows([{"name": "Counter"}], counts)
