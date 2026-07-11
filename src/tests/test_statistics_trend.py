"""Tests for get_statistics_trend() and ChartDataView."""

from datetime import date, datetime
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Event, StatisticsSnapshot
from core.services.dashboard import get_statistics_trend

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
        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_statistics_trend(facility, date(2025, 3, 1), date(2025, 3, 31))

        assert len(result) == 1
        assert result[0]["label"] == "2025-03"
        assert result[0]["source"] == "live"
        assert "total_contacts" in result[0]
        assert "by_contact_stage" in result[0]

    @pytest.mark.django_db
    def test_quarter_returns_three_segments(self, facility):
        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
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

        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
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

        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_statistics_trend(facility, date(2025, 3, 1), date(2025, 3, 31))

        assert result[0]["source"] == "live"
        assert result[0]["total_contacts"] == 1  # not 99

    @pytest.mark.django_db
    def test_source_live_fallback_without_snapshot(self, facility, client_identified, doc_type_contact, staff_user):
        _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2025, 1, 15, 10, 0))

        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_statistics_trend(facility, date(2025, 1, 1), date(2025, 1, 31))

        assert result[0]["source"] == "live"
        assert result[0]["total_contacts"] == 1

    @pytest.mark.django_db
    def test_label_format(self, facility):
        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
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


# ---------------------------------------------------------------------------
# #1311 — k-Anon-Geltungsbereich: Trend-Pfad zeigt BEWUSST Roh-Kleinstzellen
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTrendKAnonScopeRaw:
    """#1311: Der Trend-Pfad (``get_statistics_trend`` -> ``ChartDataView``)
    unterdrueckt Kleinstfallzahlen (< k) BEWUSST NICHT — anders als der
    External-Report-/PDF-Pfad, der die Einrichtung verlaesst.

    Begruendung (Lead/Admin-intern, RLS-Zeilen-Zugriff auf dieselben Rohdaten =>
    kein Privacy-Gewinn, nur Usability-Kosten) und der artefakt-basierte
    Geltungsbereich stehen in ``docs/security-notes.md`` (§ K-Anonymitaet …,
    Geltungsbereich der Suppression) und ``docs/adr/023-k-anonymization-statistik.md``
    (Update 2026-07-11). Dieser Test friert die Entscheidung ein: wer den internen
    Trend-Pfad kuenftig doch unterdrueckt, muss die dokumentierte Entscheidung mit
    aendern.
    """

    def test_trend_segment_keeps_raw_small_cell(self, facility, client_identified, doc_type_contact, staff_user):
        today = timezone.localdate()
        # 2 Events (< Default-Schwelle k=5) im laufenden Monat.
        for _ in range(2):
            _make_event(facility, client_identified, doc_type_contact, staff_user, timezone.now())

        result = get_statistics_trend(facility, today.replace(day=1), today)

        current = result[-1]
        # Randsumme roh, nicht None/"unterdrueckt".
        assert current["total_contacts"] == 2
        kontakt = next(r for r in current["by_document_type"] if r["name"] == "Kontakt")
        assert kontakt["count"] == 2
        # Kein Suppressions-Marker im internen Trend-Pfad.
        assert "suppressed" not in kontakt

    def test_chart_api_exposes_raw_small_cell(
        self, client, admin_user, facility, client_identified, doc_type_contact, staff_user
    ):
        for _ in range(2):
            _make_event(facility, client_identified, doc_type_contact, staff_user, timezone.now())

        client.force_login(admin_user)
        resp = client.get(reverse("core:statistics_chart_data"))
        data = resp.json()

        kontakt = next((d for d in data["document_types"] if d.get("name") == "Kontakt"), None)
        assert kontakt is not None
        # 2 Kontakte (< k=5) erscheinen roh in der Chart-JSON, nicht unterdrueckt.
        assert kontakt["count"] == 2
        assert "suppressed" not in kontakt
