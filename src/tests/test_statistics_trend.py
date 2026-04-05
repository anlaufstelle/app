"""Tests for get_statistics_trend() and ChartDataView."""

from datetime import date, datetime
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Event, StatisticsSnapshot
from core.services.snapshot import get_statistics_trend

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(facility, client, doc_type, user, dt):
    """Create an event at a specific aware datetime."""
    aware_dt = timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    return Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=aware_dt,
        data_json={},
        created_by=user,
    )


def _snapshot_data(total=5, anonym=1, identifiziert=3, qualifiziert=1, unique=3):
    """Build a statistics snapshot data dict."""
    return {
        "total_contacts": total,
        "by_contact_stage": {
            "anonym": anonym,
            "identifiziert": identifiziert,
            "qualifiziert": qualifiziert,
        },
        "by_document_type": [
            {"name": "Kontakt", "category": "contact", "count": total},
        ],
        "by_age_cluster": [
            {"cluster": "18_26", "label": "18–26", "count": total},
        ],
        "unique_clients": unique,
    }


# ---------------------------------------------------------------------------
# get_statistics_trend() — pure logic
# ---------------------------------------------------------------------------


class TestTrendReturnsPerSegmentData:
    """get_statistics_trend returns one entry per monthly segment."""

    @pytest.mark.django_db
    def test_single_month(self, facility):
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_statistics_trend(facility, date(2025, 3, 1), date(2025, 3, 31))

        assert len(result) == 1
        assert result[0]["label"] == "2025-03"
        assert result[0]["source"] == "live"
        assert "total_contacts" in result[0]
        assert "by_contact_stage" in result[0]

    @pytest.mark.django_db
    def test_quarter_returns_three_segments(self, facility):
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_statistics_trend(facility, date(2025, 1, 1), date(2025, 3, 31))

        assert len(result) == 3
        assert result[0]["label"] == "2025-01"
        assert result[1]["label"] == "2025-02"
        assert result[2]["label"] == "2025-03"

    @pytest.mark.django_db
    def test_source_snapshot_for_past_with_snapshot(self, facility):
        StatisticsSnapshot.objects.create(
            facility=facility,
            year=2025,
            month=1,
            data=_snapshot_data(total=10),
            jugendamt_data={},
        )

        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_statistics_trend(facility, date(2025, 1, 1), date(2025, 1, 31))

        assert result[0]["source"] == "snapshot"
        assert result[0]["total_contacts"] == 10

    @pytest.mark.django_db
    def test_source_live_for_current_month(self, facility, client_identified, doc_type_contact, staff_user):
        _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2025, 3, 10, 10, 0))

        # Even with snapshot for current month, source should be "live"
        StatisticsSnapshot.objects.create(
            facility=facility,
            year=2025,
            month=3,
            data=_snapshot_data(total=99),
            jugendamt_data={},
        )

        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_statistics_trend(facility, date(2025, 3, 1), date(2025, 3, 31))

        assert result[0]["source"] == "live"
        assert result[0]["total_contacts"] == 1  # not 99

    @pytest.mark.django_db
    def test_source_live_fallback_without_snapshot(self, facility, client_identified, doc_type_contact, staff_user):
        _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2025, 1, 15, 10, 0))

        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_statistics_trend(facility, date(2025, 1, 1), date(2025, 1, 31))

        assert result[0]["source"] == "live"
        assert result[0]["total_contacts"] == 1

    @pytest.mark.django_db
    def test_label_format(self, facility):
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 12, 15)
            result = get_statistics_trend(facility, date(2025, 1, 1), date(2025, 3, 31))

        for seg in result:
            year, month = seg["label"].split("-")
            assert len(year) == 4
            assert len(month) == 2


# ---------------------------------------------------------------------------
# ChartDataView — API tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestChartDataView:
    """Tests for the chart data JSON API endpoint."""

    def test_returns_json(self, client, admin_user):
        client.force_login(admin_user)
        resp = client.get(reverse("core:statistics_chart_data"))
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/json"

    def test_requires_login(self, client):
        resp = client.get(reverse("core:statistics_chart_data"))
        assert resp.status_code == 302

    def test_requires_lead_or_admin(self, client, staff_user):
        client.force_login(staff_user)
        resp = client.get(reverse("core:statistics_chart_data"))
        assert resp.status_code == 403

    def test_lead_can_access(self, client, lead_user):
        client.force_login(lead_user)
        resp = client.get(reverse("core:statistics_chart_data"))
        assert resp.status_code == 200

    def test_default_period_returns_data(self, client, admin_user):
        client.force_login(admin_user)
        resp = client.get(reverse("core:statistics_chart_data"))
        data = resp.json()

        assert "labels" in data
        assert "contacts" in data
        assert "document_types" in data
        assert "age_clusters" in data
        assert "sources" in data

    def test_contacts_arrays_aligned(self, client, admin_user):
        client.force_login(admin_user)
        resp = client.get(reverse("core:statistics_chart_data"), {"period": "year"})
        data = resp.json()

        num_labels = len(data["labels"])
        assert len(data["contacts"]["total"]) == num_labels
        assert len(data["contacts"]["anonym"]) == num_labels
        assert len(data["contacts"]["identifiziert"]) == num_labels
        assert len(data["contacts"]["qualifiziert"]) == num_labels
        assert len(data["sources"]) == num_labels

    def test_year_period_returns_monthly_labels(self, client, admin_user):
        client.force_login(admin_user)
        resp = client.get(reverse("core:statistics_chart_data"), {"period": "year"})
        data = resp.json()

        # Year period: Jan to current month
        assert len(data["labels"]) >= 1
        assert data["labels"][0].startswith("Jan")

    def test_custom_period(self, client, admin_user):
        client.force_login(admin_user)
        resp = client.get(
            reverse("core:statistics_chart_data"),
            {"period": "custom", "date_from": "2026-01-01", "date_to": "2026-03-31"},
        )
        data = resp.json()
        assert resp.status_code == 200
        assert len(data["labels"]) == 3
