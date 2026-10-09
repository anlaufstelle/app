"""Tests for hybrid statistics logic (snapshot + live query merging)."""

from datetime import date, datetime
from unittest.mock import patch

import pytest
from django.utils import timezone

from core.models import Event, StatisticsSnapshot
from core.services.dashboard import (
    _merge_sample_report_1_stats,
    _merge_stats,
    _split_into_segments,
    get_sample_report_1_statistics_hybrid,
    get_statistics_hybrid,
)
from core.services.dashboard.snapshot import SNAPSHOT_AS_OF_KEY

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
            {"name": "Kontakt", "count": total},
        ],
        "by_age_cluster": [
            {"cluster": "18_26", "label": "18–26", "count": total},
        ],
        "unique_clients": unique,
    }


def _sample_report_1_snapshot_data(total=5, unique=3):
    """Build a sample report snapshot data dict."""
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
        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            segments = _split_into_segments(date(2025, 1, 1), date(2025, 1, 31))

        assert len(segments) == 1
        seg_from, seg_to, use_snapshot = segments[0]
        assert seg_from == date(2025, 1, 1)
        assert seg_to == date(2025, 1, 31)
        assert use_snapshot is True

    def test_current_month_no_snapshot(self):
        """Current month → use_snapshot=False."""
        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            segments = _split_into_segments(date(2025, 3, 1), date(2025, 3, 31))

        assert len(segments) == 1
        assert segments[0][2] is False

    def test_partial_past_month_no_snapshot(self):
        """Partial past month (not starting on 1st) → use_snapshot=False."""
        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            segments = _split_into_segments(date(2025, 1, 5), date(2025, 1, 31))

        assert len(segments) == 1
        assert segments[0][2] is False  # not a full month

    def test_partial_end_no_snapshot(self):
        """Partial past month (not ending on last day) → use_snapshot=False."""
        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            segments = _split_into_segments(date(2025, 1, 1), date(2025, 1, 20))

        assert len(segments) == 1
        assert segments[0][2] is False

    def test_quarter_spanning_three_months(self):
        """Q1 2025 with current month March → Jan+Feb snapshot, Mar live."""
        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
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
        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
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
        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            segments = _split_into_segments(date(2025, 1, 15), date(2025, 1, 15))

        assert len(segments) == 1
        assert segments[0] == (date(2025, 1, 15), date(2025, 1, 15), False)


class TestMergeStats:
    """Tests for _merge_stats()."""

    def test_empty_list(self):
        result = _merge_stats([])
        assert result["total_contacts"] == 0
        # K4b (Refs #1770): der Merge liefert keine Personenzahl mehr — sie ist
        # nicht additiv und kommt als eigener Live-Term aus dem Hybrid-Pfad.
        assert result["unique_clients"] is None
        assert result["by_contact_stage"] == {"anonym": 0, "identifiziert": 0, "qualifiziert": 0}

    def test_single_dict(self):
        stats = _snapshot_data(total=10, anonym=2, identifiziert=5, qualifiziert=3, unique=7)
        result = _merge_stats([stats])
        assert result["total_contacts"] == 10
        assert result["unique_clients"] is None  # K4b (Refs #1770)
        assert result["by_contact_stage"]["identifiziert"] == 5

    def test_two_dicts_summed(self):
        s1 = _snapshot_data(total=3, anonym=1, identifiziert=1, qualifiziert=1, unique=2)
        s2 = _snapshot_data(total=7, anonym=2, identifiziert=3, qualifiziert=2, unique=5)
        result = _merge_stats([s1, s2])
        assert result["total_contacts"] == 10
        # Ereigniszahlen bleiben additiv, Personenzahlen nicht (K4b, Refs #1770).
        assert result["unique_clients"] is None
        assert result["by_contact_stage"]["anonym"] == 3
        assert result["by_contact_stage"]["identifiziert"] == 4
        assert result["by_contact_stage"]["qualifiziert"] == 3

    def test_by_document_type_merged_by_name(self):
        s1 = {
            "total_contacts": 3,
            "by_contact_stage": {"anonym": 0, "identifiziert": 3, "qualifiziert": 0},
            "by_document_type": [
                {"name": "Kontakt", "count": 2},
                {"name": "Notiz", "count": 1},
            ],
            "by_age_cluster": [],
            "unique_clients": 2,
        }
        s2 = {
            "total_contacts": 4,
            "by_contact_stage": {"anonym": 0, "identifiziert": 4, "qualifiziert": 0},
            "by_document_type": [
                {"name": "Kontakt", "count": 3},
                {"name": "Beratung", "count": 1},
            ],
            "by_age_cluster": [],
            "unique_clients": 3,
        }
        result = _merge_stats([s1, s2])
        # Refs #1749 (ADR-040): Merge-Schluessel ist der blosse Name — er ist
        # je Einrichtung eindeutig, seit ``category`` entfallen ist.
        doc_types = {e["name"]: e["count"] for e in result["by_document_type"]}
        assert doc_types == {"Kontakt": 5, "Notiz": 1, "Beratung": 1}

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


class TestMergeSampleReport1Stats:
    """Tests for _merge_sample_report_1_stats()."""

    def test_empty_list(self):
        result = _merge_sample_report_1_stats([])
        assert result["total"] == 0
        assert result["unique_clients"] is None  # K4b (Refs #1770)
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
        result = _merge_sample_report_1_stats([s1, s2])
        assert result["total"] == 7
        assert result["unique_clients"] is None  # K4b (Refs #1770)
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
        result = _merge_sample_report_1_stats([s1, s2])
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
        )

        # Soft-delete the event so live query would return 0
        event.is_deleted = True
        event.save()

        # Query January (a past month) — should use snapshot values
        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 1, 31))

        assert result["total_contacts"] == 10
        # K4b (Refs #1770): die Personenzahl kommt NICHT mehr aus dem Snapshot,
        # sondern aus einer Live-Abfrage ueber den Gesamtzeitraum — das Event ist
        # soft-deleted, live bleiben 0 Personen. Bewusst akzeptiert: der
        # Quellen-Ausweis macht die abweichende Herkunft kenntlich, ein
        # Rueckfall auf den Snapshot-Wert waere der Suppressions-Bypass.
        assert result["unique_clients"] == 0
        assert result["unique_clients_data_source"] == "live"
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
        )

        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
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
        )

        # Live event in March (current month)
        mar_dt = datetime(2025, 3, 12, 9, 0)
        _make_event(facility, client_identified, doc_type_contact, staff_user, mar_dt)

        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 3, 31))

        # Snapshot: 5 + Live March: 1 + Live Feb (empty): 0 = 6
        assert result["total_contacts"] == 6
        # K4b (Refs #1770): kein "3 + 1" mehr — DISTINCT ueber den Gesamtzeitraum
        # findet genau die eine Person mit dem Live-Event im Maerz.
        assert result["unique_clients"] == 1

    def test_snapshot_stand_is_newest_contributing_timestamp(self, facility):
        """Der Stand ist das Maximum, nicht der zuletzt iterierte Snapshot."""
        january = StatisticsSnapshot.objects.create(
            facility=facility,
            year=2025,
            month=1,
            data=_snapshot_data(total=2),
        )
        february = StatisticsSnapshot.objects.create(
            facility=facility,
            year=2025,
            month=2,
            data=_snapshot_data(total=3),
        )
        newest = timezone.make_aware(datetime(2025, 6, 2, 12, 0))
        older = timezone.make_aware(datetime(2025, 6, 1, 12, 0))
        StatisticsSnapshot.objects.filter(pk=january.pk).update(updated_at=newest)
        StatisticsSnapshot.objects.filter(pk=february.pk).update(updated_at=older)

        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 9, 15)
            result = get_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 2, 28))

        assert result[SNAPSHOT_AS_OF_KEY] == newest


@pytest.mark.django_db
class TestHybridFallbackWithoutSnapshot:
    """Past month without snapshot falls back to live query."""

    def test_hybrid_fallback_without_snapshot(self, facility, client_identified, doc_type_contact, staff_user):
        # Event in January but NO snapshot
        jan_dt = datetime(2025, 1, 20, 11, 0)
        _make_event(facility, client_identified, doc_type_contact, staff_user, jan_dt)

        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 1, 31))

        # Falls back to live query — should find the 1 event
        assert result["total_contacts"] == 1


@pytest.mark.django_db
class TestSampleReport1Hybrid:
    """sample report hybrid works analogously to regular hybrid."""

    def test_sample_report_1_hybrid_uses_snapshot(self, facility, client_identified, doc_type_contact, staff_user):
        # Snapshot with sample report data for January
        StatisticsSnapshot.objects.create(
            facility=facility,
            year=2025,
            month=1,
            data=_snapshot_data(total=5),
            backend_data={"sample-report-1": _sample_report_1_snapshot_data(total=10, unique=6)},
        )

        # Live event in March (``doc_type_contact`` traegt den report_key fuer den Sachbericht)
        mar_dt = datetime(2025, 3, 8, 10, 0)
        _make_event(facility, client_identified, doc_type_contact, staff_user, mar_dt)

        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_sample_report_1_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 3, 31))

        # Jan from snapshot (10) + Feb live (0) + Mar live (1) = 11
        assert result["total"] == 11
        # K4b (Refs #1770): Live-DISTINCT ueber den Gesamtzeitraum statt "6 + 1".
        assert result["unique_clients"] == 1


@pytest.mark.django_db
class TestSampleReport1HybridReadsBackendData:
    """M5 P3 (Refs #1061) / #1740: ``backend_data`` ist die einzige Snapshot-Quelle.

    Das Sonderfeld des Sachberichts und sein Read-Fallback sind mit dem Ende
    der Uebergangsphase entfallen (Migrationen ``0123``/``0124``).
    """

    def test_backend_data_ist_die_quelle(self, facility):
        StatisticsSnapshot.objects.create(
            facility=facility,
            year=2025,
            month=1,
            data=_snapshot_data(total=5),
            backend_data={"sample-report-1": _sample_report_1_snapshot_data(total=10, unique=6)},
        )

        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_sample_report_1_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 1, 31))

        assert result["total"] == 10
        # K4b (Refs #1770): Live-Term — ohne Ereignisse in der DB bleiben 0.
        assert result["unique_clients"] == 0

    def test_snapshot_ohne_backend_key_faellt_auf_live_zurueck(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Snapshot mit fremdem Backend, ohne ``sample-report-1`` -> Live-Abfrage.

        Genau der Zustand eines Snapshots, dessen Aggregat nie geschrieben
        wurde: ohne Fallback-Feld darf der Lesepfad kein leeres Aggregat
        ausliefern, sondern muss live nachrechnen.
        """
        _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2025, 1, 8, 10, 0))

        StatisticsSnapshot.objects.create(
            facility=facility,
            year=2025,
            month=1,
            data=_snapshot_data(total=5),
            backend_data={"traeger": {"total": 42}},
        )

        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_sample_report_1_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 1, 31))

        assert result["total"] == 1

    def test_empty_snapshot_falls_back_to_live_query(self, facility, client_identified, doc_type_contact, staff_user):
        """Leeres ``backend_data`` -> Live-Abfrage statt eines leeren Aggregats."""
        _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2025, 1, 8, 10, 0))

        StatisticsSnapshot.objects.create(
            facility=facility,
            year=2025,
            month=1,
            data=_snapshot_data(total=5),
            backend_data={},
        )

        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_sample_report_1_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 1, 31))

        assert result["total"] == 1
        assert result[SNAPSHOT_AS_OF_KEY] is None


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
        )

        # Query partial January (10th–25th) — snapshot should NOT be used
        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_statistics_hybrid(facility, date(2025, 1, 10), date(2025, 1, 25))

        # Only event on Jan 20 falls within 10–25, event on Jan 5 does not
        assert result["total_contacts"] == 1  # live query, not 99 from snapshot


# ---------------------------------------------------------------------------
# #1311 — k-Anon-Geltungsbereich: internes Dashboard zeigt BEWUSST Roh-Kleinstzellen
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDashboardKAnonScopeRaw:
    """#1311: Das interne Statistik-Dashboard (``StatisticsView`` ->
    ``get_statistics_hybrid``) unterdrueckt Kleinstfallzahlen (< k) BEWUSST NICHT
    — anders als der External-Report-/PDF-Pfad, der die Einrichtung verlaesst.

    Begruendung (Lead/Admin-intern, RLS-Zeilen-Zugriff auf dieselben Rohdaten =>
    kein Privacy-Gewinn, nur Usability-Kosten) und der artefakt-basierte
    Geltungsbereich: ``docs/security-notes.md`` (§ K-Anonymitaet …, Geltungsbereich
    der Suppression) und ``docs/adr/023-k-anonymization-statistik.md``
    (Update 2026-07-11). Wer den internen Pfad kuenftig doch unterdrueckt, muss die
    dokumentierte Entscheidung mit aendern.
    """

    def test_hybrid_returns_raw_small_cell_counts(self, facility, client_identified, doc_type_contact, staff_user):
        today = timezone.localdate()
        # 2 Events (< Default-Schwelle k=5) im laufenden Monat.
        for _ in range(2):
            _make_event(facility, client_identified, doc_type_contact, staff_user, timezone.now())

        stats = get_statistics_hybrid(facility, today.replace(day=1), today)

        # Randsumme roh, nicht None/"unterdrueckt".
        assert stats["total_contacts"] == 2
        kontakt = next(r for r in stats["by_document_type"] if r["name"] == "Kontakt")
        assert kontakt["count"] == 2
        # Kein Suppressions-Marker im internen Dashboard-Pfad.
        assert "suppressed" not in kontakt


# ---------------------------------------------------------------------------
# K4b (Refs #1770) — ``unique_clients`` ist nicht additiv
# ---------------------------------------------------------------------------


class TestMergeSummiertPersonenzahlenNie:
    """K4b (Refs #1770): ``unique_clients`` darf nie ueber Monate summiert werden.

    ``unique_clients`` ist das einzige nicht-additive Feld: 3 Personen mit je
    einem Kontakt in 6 Monaten ergaben summiert "18" — und die k-Pruefung lief
    gegen diese aufgeblaehte Zahl, waehrend der Live-Export mit distinct = 3 < k
    unterdrueckte. Das ist ein Suppressions-Bypass, kein Genauigkeitsproblem.

    Der Merge liefert deshalb strukturell **keine** Personenzahl mehr
    (``None`` = unbekannt); den Wert setzt allein der Hybrid-Pfad als eigenen
    Live-Term ueber den Gesamtzeitraum.
    """

    def test_merge_liefert_keine_summierte_personenzahl(self):
        segmente = [_snapshot_data(total=1, unique=3) for _ in range(6)]
        result = _merge_stats(segmente)

        assert result["unique_clients"] is None, "Merge darf keine Personen-Monate ausweisen"

    def test_merge_sample_report_1_liefert_keine_summierte_personenzahl(self):
        segmente = [_sample_report_1_snapshot_data(total=1, unique=3) for _ in range(6)]
        result = _merge_sample_report_1_stats(segmente)

        assert result["unique_clients"] is None

    def test_altersgruppen_personenzahl_wird_maximiert_nicht_summiert(self):
        """Personen sind ueber Segmente nicht additiv — das Maximum ist die
        engste Untergrenze, die sich aus den Segmenten allein ableiten laesst."""
        segmente = [
            {
                **_snapshot_data(total=4),
                "by_age_cluster": [{"cluster": "u18", "label": "Unter 18", "count": 4, "persons": 3}],
            },
            {
                **_snapshot_data(total=6),
                "by_age_cluster": [{"cluster": "u18", "label": "Unter 18", "count": 6, "persons": 2}],
            },
        ]
        result = _merge_stats(segmente)

        row = result["by_age_cluster"][0]
        assert row["count"] == 10, "Ereigniszahlen bleiben additiv"
        assert row["persons"] == 3

    def test_alt_segment_ohne_personenzahl_macht_den_bucket_unbekannt(self):
        """Alt-Snapshot ohne ``persons`` ⇒ unbekannt ⇒ spaeter fail-safe unterdrueckt."""
        segmente = [
            {
                **_snapshot_data(total=4),
                "by_age_cluster": [{"cluster": "u18", "label": "Unter 18", "count": 4, "persons": 3}],
            },
            {**_snapshot_data(total=6), "by_age_cluster": [{"cluster": "u18", "label": "Unter 18", "count": 6}]},
        ]
        result = _merge_stats(segmente)

        assert result["by_age_cluster"][0]["persons"] is None

    def test_alt_segment_ohne_kontaktstufen_personen_macht_alle_stufen_unbekannt(self):
        alt = _snapshot_data(total=5)
        alt.pop("persons_by_contact_stage", None)
        result = _merge_stats([alt])

        assert result["persons_by_contact_stage"] == {"anonym": None, "identifiziert": None, "qualifiziert": None}

    def test_merge_kommt_mit_alt_snapshot_json_ohne_personen_klar(self):
        """Formatwechsel darf den Merge-Pfad nicht sprengen (Bestands-Snapshots)."""
        alt = {
            "total_contacts": 5,
            "by_contact_stage": {"anonym": 1, "identifiziert": 3, "qualifiziert": 1},
            "by_document_type": [{"name": "Kontakt", "count": 5}],
            "by_age_cluster": [{"cluster": "18_26", "label": "18–26", "count": 5}],
            "unique_clients": 3,
        }
        result = _merge_stats([alt])

        assert result["total_contacts"] == 5
        assert result["by_age_cluster"][0]["persons"] is None
        assert result["unique_clients"] is None


@pytest.mark.django_db
class TestHybridPersonenzahlIstEigenerLiveTerm:
    """K4b (Refs #1770): der Hybrid-Pfad rechnet die Personenzahl live nach."""

    def test_drei_personen_ueber_sechs_monate_bleiben_drei(
        self, facility, doc_type_contact, staff_user, client_identified
    ):
        """Der Kernfall aus der Analyse: 3 Personen x 6 Monate ⇒ nicht "18"."""
        from core.models import Client

        personen = [client_identified] + [
            Client.objects.create(
                facility=facility,
                contact_stage=Client.ContactStage.IDENTIFIED,
                pseudonym=f"K4b-{index:02d}",
                created_by=staff_user,
            )
            for index in range(2)
        ]
        for monat in range(1, 7):
            for person in personen:
                _make_event(facility, person, doc_type_contact, staff_user, datetime(2025, monat, 10, 9, 0))
            StatisticsSnapshot.objects.create(
                facility=facility,
                year=2025,
                month=monat,
                data=_snapshot_data(total=3, unique=3),
            )

        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 9, 15)
            result = get_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 6, 30))

        assert result["unique_clients"] == 3
        assert result["unique_clients_data_source"] == "live"

    def test_sample_report_1_hybrid_zaehlt_personen_live_ueber_den_gesamtzeitraum(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        for monat in (1, 2):
            _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2025, monat, 10, 9, 0))
            StatisticsSnapshot.objects.create(
                facility=facility,
                year=2025,
                month=monat,
                data=_snapshot_data(total=1),
                backend_data={"sample-report-1": _sample_report_1_snapshot_data(total=1, unique=1)},
            )

        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 9, 15)
            result = get_sample_report_1_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 2, 28))

        assert result["total"] == 2, "Ereigniszahlen bleiben additiv"
        assert result["unique_clients"] == 1, "eine Person in beiden Monaten bleibt eine Person"
        assert result["unique_clients_data_source"] == "live"


@pytest.mark.django_db
class TestSachberichtPdfWeistDenLiveTermAus:
    """K4b (Refs #1770): das PDF darf keine summierte Personenzahl mehr zeigen —
    und muss die abweichende Quelle dieses einen Terms ausweisen (#1743, Option c).
    """

    def _pdf_context(self, facility, date_from, date_to):
        from core.services.dashboard import get_statistics_hybrid, suppress_report_stats

        stats = get_statistics_hybrid(facility, date_from, date_to)
        return suppress_report_stats(facility, stats)

    def test_summierte_personenzahl_erscheint_nicht_im_pdf_pfad(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        for monat in range(1, 7):
            _make_event(facility, client_identified, doc_type_contact, staff_user, datetime(2025, monat, 10, 9, 0))
            StatisticsSnapshot.objects.create(
                facility=facility,
                year=2025,
                month=monat,
                data=_snapshot_data(total=1, anonym=0, identifiziert=1, qualifiziert=0, unique=3),
            )

        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 9, 15)
            stats = self._pdf_context(facility, date(2025, 1, 1), date(2025, 6, 30))

        # Live-DISTINCT = 1 Person < k ⇒ unterdrueckt statt "18".
        assert stats["unique_clients"] is None
        assert stats["unique_clients_data_source"] == "live"

    def test_pdf_template_setzt_die_fussnote_am_term(self, facility, client_identified, doc_type_contact, staff_user):
        from django.template.loader import render_to_string

        html = render_to_string(
            "core/export/sample_report_1_pdf.html",
            {
                "facility_name": facility.name,
                "date_from": date(2025, 1, 1),
                "date_to": date(2025, 6, 30),
                "stats": {
                    "total": 30,
                    "unique_clients": 9,
                    "by_category": [],
                    "by_age_cluster": [],
                    "unique_clients_data_source": "live",
                },
                "generated_at": timezone.now(),
                "data_source_note": "Monats-Aggregate",
                "data_source_explanation": "…",
            },
        )

        assert "unique-clients-note" in html
