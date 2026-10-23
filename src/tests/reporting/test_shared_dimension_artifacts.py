"""Cross-report recipient attacks through the public artifact service."""

import json
from datetime import date, datetime

import pytest
from django.utils import timezone

from core.models import Client
from core.reporting.export import export_dataset
from core.reporting.registry import get_backend
from tests._factories import make_document_type, make_event


@pytest.mark.django_db
def test_category_margin_and_general_type_count_do_not_recover_a_single_event(facility, admin_user):
    for name, report_key, amount in (("Alpha", "counseling", 7), ("Bravo", "crisis", 1), ("Outside", "excluded", 4)):
        document_type = make_document_type(facility=facility, name=name, report_key=report_key)
        for _ in range(amount):
            make_event(
                facility=facility,
                document_type=document_type,
                client=None,
                created_by=admin_user,
                occurred_at=timezone.make_aware(datetime(2026, 1, 15)),
                data_json={},
            )
    payloads = {
        slug: json.loads(
            export_dataset(get_backend(slug), facility, date(2026, 1, 1), date(2026, 3, 31), format="json").content
        )
        for slug in ("general-key-figures", "sample-report-1")
    }
    general = payloads["general-key-figures"]
    alpha = next(
        row["count"] for row in general["records"] if row["table"] == "by_document_type" and row["label"] == "Alpha"
    )
    category = payloads["sample-report-1"]["records"][0]["count"]
    possibilities = {category - alpha} if category is not None and alpha is not None else set(range(1, 5))
    assert 1 in possibilities
    assert len(possibilities) >= 2, "The category margin minus Alpha must not reveal Bravo=1"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "whole_slug,prefix_slug",
    [
        ("general-key-figures", "general-key-figures"),
        ("general-key-figures", "sample-report-1"),
        ("sample-report-1", "general-key-figures"),
        ("general-key-figures", "example"),
    ],
)
def test_public_calendar_prefix_difference_does_not_recover_a_single_event(
    facility, admin_user, whole_slug, prefix_slug
):
    document_type = make_document_type(facility=facility, name="Contacts", report_key="contact")
    for month, amount in ((1, 10), (3, 1)):
        for _ in range(amount):
            make_event(
                facility=facility,
                document_type=document_type,
                client=None,
                created_by=admin_user,
                occurred_at=timezone.make_aware(datetime(2026, month, 15)),
                data_json={},
            )
    counts = []
    for slug, end in ((whole_slug, date(2026, 3, 31)), (prefix_slug, date(2026, 2, 28))):
        payload = json.loads(export_dataset(get_backend(slug), facility, date(2026, 1, 1), end, format="json").content)
        counts.append(payload["summary"]["total_contacts" if slug == "general-key-figures" else "total"])
    whole, prefix = counts
    possibilities = {whole - prefix} if whole is not None and prefix is not None else set(range(1, 5))
    assert 1 in possibilities
    assert len(possibilities) >= 2, "Jan–Mar minus Jan–Feb must not reveal the March singleton"


@pytest.mark.django_db
def test_hybrid_pdf_category_suppression_remains_a_shared_type_bound(facility, admin_user):
    from core.models import StatisticsSnapshot
    from core.services.dashboard.external_report import build_shared_document_type_counts

    for name, report_key, amount in (("Alpha", "contact", 7), ("Outside", "excluded", 6)):
        document_type = make_document_type(facility=facility, name=name, report_key=report_key)
        for _ in range(amount):
            make_event(
                facility=facility,
                document_type=document_type,
                client=None,
                created_by=admin_user,
                occurred_at=timezone.make_aware(datetime(2026, 1, 15)),
                data_json={},
            )
    StatisticsSnapshot.objects.create(
        facility=facility,
        year=2026,
        month=1,
        backend_data={
            "sample-report-1": {
                "total": 1,
                "unique_clients": 0,
                "by_category": [["Kontakte", 1]],
                "by_age_cluster": [],
            }
        },
    )
    start, end = date(2026, 1, 1), date(2026, 1, 31)
    assert build_shared_document_type_counts(facility, start, end)["Alpha"] is None
    payload = json.loads(
        export_dataset(get_backend("general-key-figures"), facility, start, end, format="json").content
    )
    counts = {row["label"]: row["count"] for row in payload["records"] if row["table"] == "by_document_type"}
    assert counts["Alpha"] is None, "Another live consumer must not undo the hybrid category's stricter type bound"


@pytest.mark.django_db
def test_canonical_family_reads_snapshot_rows_once(facility, admin_user):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    document_type = make_document_type(facility=facility, name="Contacts", report_key="contact")
    for month in (1, 2):
        for _ in range(5):
            make_event(
                facility=facility,
                document_type=document_type,
                client=None,
                created_by=admin_user,
                occurred_at=timezone.make_aware(datetime(2026, month, 15)),
                data_json={},
            )
    with CaptureQueriesContext(connection) as queries:
        response = export_dataset(
            get_backend("general-key-figures"), facility, date(2026, 1, 1), date(2026, 2, 28), format="json"
        )
    assert response.status_code == 200
    settings_reads = [query for query in queries if '"core_settings"' in query["sql"]]
    assert len(settings_reads) <= 2, "Local preparation and final solver validation must not read settings per window"
    snapshot_reads = [query for query in queries if '"core_statisticssnapshot"' in query["sql"]]
    assert len(snapshot_reads) <= 1, "Canonical windows must share one facility-scoped raw snapshot collection"


@pytest.mark.django_db
def test_standard_rolling_window_keeps_a_safe_partial_month_union(facility, admin_user, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda: date(2026, 3, 31))
    document_type = make_document_type(facility=facility, name="Contacts", report_key="contact")
    for day, amount in ((1, 5), (15, 6)):
        for _ in range(amount):
            make_event(
                facility=facility,
                document_type=document_type,
                client=None,
                created_by=admin_user,
                occurred_at=timezone.make_aware(datetime(2026, 3, day)),
                data_json={},
            )
    payload = json.loads(
        export_dataset(
            get_backend("general-key-figures"),
            facility,
            date(2026, 3, 2),
            date(2026, 3, 31),
            format="json",
        ).content
    )
    assert payload["summary"]["total_contacts"] == 6, (
        "The standard last-30-days population belongs to the canonical family"
    )


@pytest.mark.django_db
def test_free_partial_month_does_not_add_a_request_dependent_subtraction_equation(facility, admin_user, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda: date(2026, 3, 31))
    document_type = make_document_type(facility=facility, name="Contacts", report_key="contact")
    for day, amount in ((1, 1), (20, 10)):
        for _ in range(amount):
            make_event(
                facility=facility,
                document_type=document_type,
                client=None,
                created_by=admin_user,
                occurred_at=timezone.make_aware(datetime(2026, 1, day)),
                data_json={},
            )
    payload = json.loads(
        export_dataset(
            get_backend("general-key-figures"), facility, date(2026, 1, 16), date(2026, 1, 31), format="json"
        ).content
    )
    assert payload["summary"]["total_contacts"] is None
    assert all(
        row["table"] in {"total_contacts", "unique_clients"} and row["count"] is None for row in payload["records"]
    ), "Only fixed summary fields may remain; data-dependent type rows must disappear"


@pytest.mark.django_db
def test_sparse_multi_year_calendar_family_keeps_the_small_final_remainder_private(facility, admin_user, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda: date(2026, 3, 31))
    document_type = make_document_type(facility=facility, name="Contacts", report_key="contact")
    for year, month, amount in ((2024, 1, 10), (2025, 1, 10), (2026, 3, 1)):
        for _ in range(amount):
            make_event(
                facility=facility,
                document_type=document_type,
                client=None,
                created_by=admin_user,
                occurred_at=timezone.make_aware(datetime(year, month, 15)),
                data_json={},
            )
    values = [
        json.loads(
            export_dataset(get_backend("general-key-figures"), facility, date(2024, 1, 1), end, format="json").content
        )["summary"]["total_contacts"]
        for end in (date(2026, 3, 31), date(2025, 12, 31))
    ]
    whole, prefix = values
    possible = {whole - prefix} if whole is not None and prefix is not None else set(range(1, 5))
    assert 1 in possible and len(possible) >= 2


@pytest.mark.django_db
def test_rows_hidden_by_the_shared_mask_keep_the_value_independent_type_order(facility, admin_user, monkeypatch):
    from core.models import DocumentType, StatisticsSnapshot

    monkeypatch.setattr(timezone, "localdate", lambda: date(2026, 4, 30))
    StatisticsSnapshot.objects.create(
        facility=facility,
        year=2026,
        month=1,
        backend_data={
            "sample-report-1": {
                "total": 1,
                "unique_clients": 0,
                "by_category": [["Kontakte", 1]],
                "by_age_cluster": [],
            }
        },
    )
    for name, report_key, amount in (("Alpha", "excluded", 6), ("Mike", "contact", 7), ("Zulu", "excluded", 8)):
        document_type = make_document_type(facility=facility, name=name, report_key=report_key)
        for _ in range(amount):
            make_event(
                facility=facility,
                document_type=document_type,
                client=None,
                created_by=admin_user,
                occurred_at=timezone.make_aware(datetime(2026, 1, 15)),
                data_json={},
            )
    make_event(
        facility=facility,
        document_type=DocumentType.objects.get(facility=facility, name="Mike"),
        client=None,
        created_by=admin_user,
        occurred_at=timezone.make_aware(datetime(2026, 3, 15)),
        data_json={},
    )
    payload = json.loads(
        export_dataset(
            get_backend("general-key-figures"), facility, date(2026, 1, 1), date(2026, 1, 31), format="json"
        ).content
    )
    rows = [row for row in payload["records"] if row["table"] == "by_document_type"]
    hidden = [row["label"] for row in rows if row["count"] is None]
    visible = [row["label"] for row in rows if row["count"] is not None]
    assert len(hidden) >= 2, "The probe needs at least two hidden types"
    assert hidden == sorted(hidden), "Hidden types must follow the value-independent type order"
    assert [row["label"] for row in rows] == [*visible, *hidden], "Hidden rows form the block at the end"


def test_projected_rows_are_returned_in_their_declared_order():
    from core.services.dashboard.external_report import ORDER_AGE_CLUSTER, ORDER_INPUT, project_protected_rows

    clusters = [Client.AgeCluster.values[2], Client.AgeCluster.values[0], Client.AgeCluster.values[1]]
    rows = [{"cluster": cluster, "count": 9 - index} for index, cluster in enumerate(clusters)]
    projected = project_protected_rows(rows, [8, None, None], order=ORDER_AGE_CLUSTER)
    assert [row["cluster"] for row in projected] == [
        clusters[0],
        *sorted(clusters[1:], key=Client.AgeCluster.values.index),
    ]
    assert [row["count"] for row in projected] == [8, None, None]
    assert [row["suppressed"] for row in projected] == [False, True, True]
    unordered = project_protected_rows(rows, [None, None, 7], order=ORDER_INPUT)
    assert [row["cluster"] for row in unordered] == clusters
