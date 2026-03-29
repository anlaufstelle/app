"""Tests for hybrid statistics logic (snapshot + live query merging)."""

from datetime import date, datetime
from unittest.mock import patch

import pytest
from django.utils import timezone

from core.models import DocumentType, Event, StatisticsSnapshot
from core.services.snapshot import (
    _merge_jugendamt_stats,
    _merge_stats,
    _split_into_segments,
    get_jugendamt_statistics_hybrid,
    get_statistics_hybrid,
)

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


def _jugendamt_snapshot_data(total=5, unique=3):
    """Build a Jugendamt snapshot data dict."""
    return {
        "total": total,
        "by_category": [["Kontakte", total]],
        "by_age_cluster": [
            {"cluster": "18_26", "label": "18–26", "count": total},
        ],
        "unique_clients": unique,
    }


# ---------------------------------------------------------------------------
# Pure-logic tests (no DB)
# ---------------------------------------------------------------------------


class TestSplitIntoSegments:
    """Tests for _split_into_segments()."""

    def test_single_full_past_month(self):
        """Full past month → single segment with use_snapshot=True."""
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            segments = _split_into_segments(date(2025, 1, 1), date(2025, 1, 31))

        assert len(segments) == 1
        seg_from, seg_to, use_snapshot = segments[0]
        assert seg_from == date(2025, 1, 1)
        assert seg_to == date(2025, 1, 31)
        assert use_snapshot is True

    def test_current_month_no_snapshot(self):
        """Current month → use_snapshot=False."""
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            segments = _split_into_segments(date(2025, 3, 1), date(2025, 3, 31))

        assert len(segments) == 1
        assert segments[0][2] is False

    def test_partial_past_month_no_snapshot(self):
        """Partial past month (not starting on 1st) → use_snapshot=False."""
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            segments = _split_into_segments(date(2025, 1, 5), date(2025, 1, 31))

        assert len(segments) == 1
        assert segments[0][2] is False  # not a full month

    def test_partial_end_no_snapshot(self):
        """Partial past month (not ending on last day) → use_snapshot=False."""
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            segments = _split_into_segments(date(2025, 1, 1), date(2025, 1, 20))

        assert len(segments) == 1
        assert segments[0][2] is False

    def test_quarter_spanning_three_months(self):
        """Q1 2025 with current month March → Jan+Feb snapshot, Mar live."""
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            segments = _split_into_segments(date(2025, 1, 1), date(2025, 3, 31))

        assert len(segments) == 3
        # January: full past → snapshot
        assert segments[0] == (date(2025, 1, 1), date(2025, 1, 31), True)
        # February: full past → snapshot
        assert segments[1] == (date(2025, 2, 1), date(2025, 2, 28), True)
        # March: current month → live
        assert segments[2] == (date(2025, 3, 1), date(2025, 3, 31), False)

    def test_partial_first_and_last_month(self):
        """Mid-Jan to mid-Mar → 3 segments, all partial/current → no snapshots."""
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            segments = _split_into_segments(date(2025, 1, 10), date(2025, 3, 20))

        assert len(segments) == 3
        # January partial (starts 10th) → no snapshot
        assert segments[0] == (date(2025, 1, 10), date(2025, 1, 31), False)
        # February full past → snapshot
        assert segments[1] == (date(2025, 2, 1), date(2025, 2, 28), True)
        # March partial current → no snapshot
        assert segments[2] == (date(2025, 3, 1), date(2025, 3, 20), False)

    def test_single_day(self):
        """Single day range → one segment, no snapshot."""
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            segments = _split_into_segments(date(2025, 1, 15), date(2025, 1, 15))

        assert len(segments) == 1
        assert segments[0] == (date(2025, 1, 15), date(2025, 1, 15), False)


class TestMergeStats:
    """Tests for _merge_stats()."""

    def test_empty_list(self):
        result = _merge_stats([])
        assert result["total_contacts"] == 0
        assert result["unique_clients"] == 0
        assert result["by_contact_stage"] == {"anonym": 0, "identifiziert": 0, "qualifiziert": 0}

    def test_single_dict(self):
        stats = _snapshot_data(total=10, anonym=2, identifiziert=5, qualifiziert=3, unique=7)
        result = _merge_stats([stats])
        assert result["total_contacts"] == 10
        assert result["unique_clients"] == 7
        assert result["by_contact_stage"]["identifiziert"] == 5

    def test_two_dicts_summed(self):
        s1 = _snapshot_data(total=3, anonym=1, identifiziert=1, qualifiziert=1, unique=2)
        s2 = _snapshot_data(total=7, anonym=2, identifiziert=3, qualifiziert=2, unique=5)
        result = _merge_stats([s1, s2])
        assert result["total_contacts"] == 10
        assert result["unique_clients"] == 7
        assert result["by_contact_stage"]["anonym"] == 3
        assert result["by_contact_stage"]["identifiziert"] == 4
        assert result["by_contact_stage"]["qualifiziert"] == 3

    def test_by_document_type_merged_by_composite_key(self):
        s1 = {
            "total_contacts": 3,
            "by_contact_stage": {"anonym": 0, "identifiziert": 3, "qualifiziert": 0},
            "by_document_type": [
                {"name": "Kontakt", "category": "contact", "count": 2},
                {"name": "Notiz", "category": "note", "count": 1},
            ],
            "by_age_cluster": [],
            "unique_clients": 2,
        }
        s2 = {
            "total_contacts": 4,
            "by_contact_stage": {"anonym": 0, "identifiziert": 4, "qualifiziert": 0},
            "by_document_type": [
                {"name": "Kontakt", "category": "contact", "count": 3},
                {"name": "Beratung", "category": "service", "count": 1},
            ],
            "by_age_cluster": [],
            "unique_clients": 3,
        }
        result = _merge_stats([s1, s2])
        doc_types = {(e["name"], e["category"]): e["count"] for e in result["by_document_type"]}
        assert doc_types[("Kontakt", "contact")] == 5
        assert doc_types[("Notiz", "note")] == 1
        assert doc_types[("Beratung", "service")] == 1

    def test_by_age_cluster_merged(self):
        s1 = {
            "total_contacts": 2,
            "by_contact_stage": {"anonym": 0, "identifiziert": 2, "qualifiziert": 0},
            "by_document_type": [],
            "by_age_cluster": [
                {"cluster": "18_26", "label": "18–26", "count": 1},
                {"cluster": "u18", "label": "Unter 18", "count": 1},
            ],
            "unique_clients": 2,
        }
        s2 = {
            "total_contacts": 3,
            "by_contact_stage": {"anonym": 0, "identifiziert": 3, "qualifiziert": 0},
            "by_document_type": [],
            "by_age_cluster": [
                {"cluster": "18_26", "label": "18–26", "count": 2},
                {"cluster": "27_plus", "label": "27+", "count": 1},
            ],
            "unique_clients": 3,
        }
        result = _merge_stats([s1, s2])
        clusters = {e["cluster"]: e["count"] for e in result["by_age_cluster"]}
        assert clusters["18_26"] == 3
        assert clusters["u18"] == 1
        assert clusters["27_plus"] == 1


class TestMergeJugendamtStats:
    """Tests for _merge_jugendamt_stats()."""

    def test_empty_list(self):
        result = _merge_jugendamt_stats([])
        assert result["total"] == 0
        assert result["unique_clients"] == 0
        assert result["by_category"] == []

    def test_normalizes_tuples_and_lists(self):
        """Both tuples and lists in by_category should be handled."""
        s1 = {
            "total": 3,
            "by_category": [("Kontakte", 2), ("Beratung", 1)],
            "by_age_cluster": [],
            "unique_clients": 2,
        }
        s2 = {
            "total": 4,
            "by_category": [["Kontakte", 3], ["Vermittlung", 1]],
            "by_age_cluster": [],
            "unique_clients": 3,
        }
        result = _merge_jugendamt_stats([s1, s2])
        assert result["total"] == 7
        assert result["unique_clients"] == 5
        cats = dict(result["by_category"])
        assert cats["Kontakte"] == 5
        assert cats["Beratung"] == 1
        assert cats["Vermittlung"] == 1

    def test_age_cluster_merged(self):
        s1 = {
            "total": 2,
            "by_category": [],
            "by_age_cluster": [{"cluster": "18_26", "label": "18–26", "count": 2}],
            "unique_clients": 1,
        }
        s2 = {
            "total": 3,
            "by_category": [],
            "by_age_cluster": [
                {"cluster": "18_26", "label": "18–26", "count": 1},
                {"cluster": "u18", "label": "Unter 18", "count": 2},
            ],
            "unique_clients": 2,
        }
        result = _merge_jugendamt_stats([s1, s2])
        clusters = {e["cluster"]: e["count"] for e in result["by_age_cluster"]}
        assert clusters["18_26"] == 3
        assert clusters["u18"] == 2


# ---------------------------------------------------------------------------
# DB-backed tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHybridUsesSnapshotForPastMonth:
    """Snapshot is used for past months, ignoring current DB state."""

    def test_hybrid_uses_snapshot_for_past_month(self, facility, client_identified, doc_type_contact, staff_user):
        # Create an event in January 2025
        jan_dt = datetime(2025, 1, 15, 10, 0)
        event = _make_event(facility, client_identified, doc_type_contact, staff_user, jan_dt)

        # Create a snapshot for January with different (higher) values
        StatisticsSnapshot.objects.create(
            facility=facility,
            year=2025,
            month=1,
            data=_snapshot_data(total=10, anonym=2, identifiziert=5, qualifiziert=3, unique=8),
            jugendamt_data=_jugendamt_snapshot_data(total=10, unique=8),
        )

        # Soft-delete the event so live query would return 0
        event.is_deleted = True
        event.save()

        # Query January (a past month) — should use snapshot values
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 1, 31))

        assert result["total_contacts"] == 10
        assert result["unique_clients"] == 8
        assert result["by_contact_stage"]["identifiziert"] == 5


@pytest.mark.django_db
class TestHybridLiveForCurrentMonth:
    """Current month always uses live query, not snapshots."""

    def test_hybrid_live_for_current_month(self, facility, client_identified, doc_type_contact, staff_user):
        # Create event in "current" month (March 2025)
        mar_dt = datetime(2025, 3, 10, 14, 0)
        _make_event(facility, client_identified, doc_type_contact, staff_user, mar_dt)

        # Even if a snapshot exists for March, it should be ignored
        StatisticsSnapshot.objects.create(
            facility=facility,
            year=2025,
            month=3,
            data=_snapshot_data(total=99),
            jugendamt_data=_jugendamt_snapshot_data(total=99),
        )

        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_statistics_hybrid(facility, date(2025, 3, 1), date(2025, 3, 31))

        # Should be 1 (from the live event), not 99 (from snapshot)
        assert result["total_contacts"] == 1


@pytest.mark.django_db
class TestHybridMergesSnapshotAndLive:
    """Period spanning snapshot month + current month sums correctly."""

    def test_hybrid_merges_snapshot_and_live(self, facility, client_identified, doc_type_contact, staff_user):
        # Snapshot for January
        StatisticsSnapshot.objects.create(
            facility=facility,
            year=2025,
            month=1,
            data=_snapshot_data(total=5, anonym=1, identifiziert=3, qualifiziert=1, unique=3),
            jugendamt_data=_jugendamt_snapshot_data(total=5, unique=3),
        )

        # Live event in March (current month)
        mar_dt = datetime(2025, 3, 12, 9, 0)
        _make_event(facility, client_identified, doc_type_contact, staff_user, mar_dt)

        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 3, 31))

        # Snapshot: 5 + Live March: 1 + Live Feb (empty): 0 = 6
        assert result["total_contacts"] == 6
        assert result["unique_clients"] == 3 + 1  # sum approximation


@pytest.mark.django_db
class TestHybridFallbackWithoutSnapshot:
    """Past month without snapshot falls back to live query."""

    def test_hybrid_fallback_without_snapshot(self, facility, client_identified, doc_type_contact, staff_user):
        # Event in January but NO snapshot
        jan_dt = datetime(2025, 1, 20, 11, 0)
        _make_event(facility, client_identified, doc_type_contact, staff_user, jan_dt)

        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 1, 31))

        # Falls back to live query — should find the 1 event
        assert result["total_contacts"] == 1


@pytest.mark.django_db
class TestJugendamtHybrid:
    """Jugendamt hybrid works analogously to regular hybrid."""

    def test_jugendamt_hybrid_uses_snapshot(self, facility, client_identified, doc_type_contact, staff_user):
        # Snapshot with Jugendamt data for January
        StatisticsSnapshot.objects.create(
            facility=facility,
            year=2025,
            month=1,
            data=_snapshot_data(total=5),
            jugendamt_data=_jugendamt_snapshot_data(total=10, unique=6),
        )

        # Live event in March (needs doc_type with system_type=contact for Jugendamt)
        doc_type_contact.system_type = DocumentType.SystemType.CONTACT
        doc_type_contact.save()
        mar_dt = datetime(2025, 3, 8, 10, 0)
        _make_event(facility, client_identified, doc_type_contact, staff_user, mar_dt)

        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_jugendamt_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 3, 31))

        # Jan from snapshot (10) + Feb live (0) + Mar live (1) = 11
        assert result["total"] == 11
        assert result["unique_clients"] == 6 + 1


@pytest.mark.django_db
class TestPartialEdgeMonthsUseLive:
    """Partial first/last months always use live query even if past."""

    def test_partial_edge_months_use_live(self, facility, client_identified, doc_type_contact, staff_user):
        # Create events in January
        _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            datetime(2025, 1, 5, 10, 0),
        )
        _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            datetime(2025, 1, 20, 10, 0),
        )

        # Snapshot for January with inflated values
        StatisticsSnapshot.objects.create(
            facility=facility,
            year=2025,
            month=1,
            data=_snapshot_data(total=99),
            jugendamt_data=_jugendamt_snapshot_data(total=99),
        )

        # Query partial January (10th–25th) — snapshot should NOT be used
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_statistics_hybrid(facility, date(2025, 1, 10), date(2025, 1, 25))

        # Only event on Jan 20 falls within 10–25, event on Jan 5 does not
        assert result["total_contacts"] == 1  # live query, not 99 from snapshot
