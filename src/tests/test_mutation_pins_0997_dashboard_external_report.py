"""Pins for shared report suppression and missing breakdowns.

Source: mutation delta of the release-0.99.7 checkpoint, 17 mutants.
Groups: shared live/PDF category decisions (4), shared fail-safe fallbacks (5),
missing legacy statistics (6), single-margin guard exhaustion (2).
All scenarios enter public service APIs. The real sample backend retains its
format dispatch and suppression. The shared type decision reads real events and
snapshots; only the removed-category fallback replaces one local contract.
"""

from datetime import date, datetime

import pytest
from django.utils import timezone

from core.models import DocumentType, Settings, StatisticsSnapshot
from core.reporting.base import DatasetResult, FieldSpec
from core.reporting.registry import get_backend
from core.services.dashboard import external_report
from tests._factories import make_document_type, make_event

pytestmark = pytest.mark.django_db

_START = date(2026, 1, 1)
_END = date(2026, 12, 31)


@pytest.fixture
def shared_report(facility, monkeypatch):
    """Provide visible type counts and a real mapped sample backend."""
    names = ["Contact A", "Contact B"]
    for name in names:
        DocumentType.objects.create(facility=facility, name=name, report_key="contact")
    general = {"by_document_type": [{"name": name, "count": 10} for name in names]}
    monkeypatch.setattr(external_report, "build_external_report", lambda *args: general)
    sample = get_backend("sample-report-1")
    category = sample.typ_bindung.resolve("contact")
    visible = {"total": 20, "unique_clients": 0, "by_category": [(category, 20)]}
    hidden = {"total": 1, "unique_clients": 0, "by_category": [(category, 1)]}
    monkeypatch.setattr(sample, "build_dataset", lambda *args: DatasetResult("aggregate", [], visible, []))
    monkeypatch.setattr("core.services.dashboard.get_sample_report_1_statistics_hybrid", lambda *args: hidden)
    return sample, general, names


@pytest.fixture
def mapped_types(facility, admin_user):
    """Two event types of one sample category with ten published events each."""
    names = ["Contact A", "Contact B"]
    for name in names:
        document_type = make_document_type(facility=facility, name=name, report_key="contact")
        for _ in range(10):
            make_event(
                facility=facility,
                document_type=document_type,
                client=None,
                created_by=admin_user,
                occurred_at=timezone.make_aware(datetime(2026, 1, 15)),
                data_json={},
            )
    return names


def test_hybrid_pdf_decision_hides_all_mapped_type_counts(facility, mapped_types):
    """A category hidden only in hybrid PDF blocks every mapped shared type.

    x_build_shared_document_type_counts__mutmut_41, x_build_shared_document_type_counts__mutmut_48,
    x_build_shared_document_type_counts__mutmut_49, x_build_shared_document_type_counts__mutmut_54.
    """
    sample = get_backend("sample-report-1")
    category = sample.typ_bindung.resolve("contact")
    StatisticsSnapshot.objects.create(
        facility=facility,
        year=2026,
        month=1,
        backend_data={
            "sample-report-1": {"total": 1, "unique_clients": 0, "by_category": [[category, 1]], "by_age_cluster": []}
        },
    )
    live = external_report.suppress_sample_report_1_stats(
        facility, sample.build_format_dataset(facility, _START, _END, format="json").summary
    )
    hybrid = external_report.suppress_sample_report_1_stats(
        facility, sample.build_format_dataset(facility, _START, _END, format="pdf").summary
    )
    assert live["by_category"][0]["count"] == 20
    assert hybrid["by_category"][0]["count"] is None

    assert external_report.build_shared_document_type_counts(facility, _START, _END) == dict.fromkeys(mapped_types)


def test_missing_general_breakdown_returns_empty_shared_counts(facility, shared_report):
    """A removed general breakdown produces no shared type counts.

    x_build_shared_document_type_counts__mutmut_14, x_build_shared_document_type_counts__mutmut_16.
    """
    _, general, _ = shared_report
    general.pop("by_document_type")
    assert external_report.build_shared_document_type_counts(facility, _START, _END) == {}


def test_backend_without_binding_hides_all_existing_types(facility, mapped_types, monkeypatch):
    """Missing type bindings fail closed: no type count is published at all.

    The shared release refuses to resolve categories without a binding instead of
    publishing the names with hidden counts.

    x_build_shared_document_type_counts__mutmut_25.
    """
    monkeypatch.setattr(get_backend("sample-report-1"), "typ_bindung", None)
    with pytest.raises(ValueError, match="category binding"):
        external_report.build_shared_document_type_counts(facility, _START, _END)


def test_removed_sample_categories_hide_mapped_shared_counts(facility, mapped_types, monkeypatch):
    """Fail-safe removal of sample categories blocks their mapped types.

    x_build_shared_document_type_counts__mutmut_70, x_build_shared_document_type_counts__mutmut_72.
    """
    protect = external_report._protect_sample_statistics

    def without_categories(*args, **kwargs):
        return {key: value for key, value in protect(*args, **kwargs).items() if key != "by_category"}

    monkeypatch.setattr(external_report, "_protect_sample_statistics", without_categories)
    assert external_report.build_shared_document_type_counts(facility, _START, _END) == dict.fromkeys(mapped_types)


@pytest.mark.parametrize(
    "missing", ["by_document_type", "by_age_cluster", "by_contact_stage"], ids=["types", "ages", "stages"]
)
def test_legacy_statistics_accept_missing_breakdowns(facility, missing):
    """Missing legacy lists and stage mappings retain safe empty output semantics.

    x_suppress_report_stats__mutmut_31, x_suppress_report_stats__mutmut_33,
    x_suppress_report_stats__mutmut_40, x_suppress_report_stats__mutmut_42,
    x_suppress_report_stats__mutmut_73, x_suppress_report_stats__mutmut_75.
    """
    stats = {
        "total_contacts": 10,
        "unique_clients": 10,
        "by_document_type": [],
        "by_age_cluster": [],
        "by_contact_stage": {},
    }
    stats.pop(missing)

    protected = external_report.suppress_report_stats(facility, stats)

    assert protected["by_document_type"] == []
    assert protected["by_contact_stage"] == {}
    assert protected["by_age_cluster"] == [
        {
            "cluster": external_report.NO_PERSON_CLUSTER,
            "label": str(external_report.NO_PERSON_LABEL),
            "count": 10,
            "suppressed": False,
        }
    ]
    assert missing not in stats


def test_person_sensitive_guard_exhaustion_returns_empty_records(facility):
    """Exhausted person-sensitive guards remove the breakdown before projection.

    x_suppress_single_margin__mutmut_33, x_suppress_single_margin__mutmut_35.
    """
    Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 1})
    records = [{"name": f"row-{index}", "count": 1, "persons": int(index != 0)} for index in range(601)]
    result = DatasetResult("rows", records, {"total": 601}, [])
    breakdown = external_report._Breakdown("records", "total", external_report.ORDER_INPUT, reveals_person_gap=True)

    protected, summary = external_report.suppress_single_margin(
        facility, result, breakdown=breakdown, field_specs=[FieldSpec("name", "Name"), FieldSpec("count", "Count")]
    )

    assert protected == []
    assert summary["total"] is None
    assert summary["k_anonymity_threshold"] == 1
