"""Mutation-Followup-Tests für ``core.services.snapshot`` — Merge & Hybrid.

Refs Welle 7 (#930). Sub-File aus ``test_mutation_followup_snapshot``;
enthält ``TestMergeStatsPerField``, ``TestMergeJugendamtStatsPerField``
und ``TestGetStatisticsHybridCutoff`` — also die Feld-für-Feld-Aggregation
und die Cutoff-Logik ``snapshot vs. live`` im hybriden Statistik-Pfad.

Constraint: Tests gegen Verify-DB (``POSTGRES_DB=anlaufstelle_verify``).
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch

import pytest

from core.models import (
    StatisticsSnapshot,
)
from core.services.snapshot import (
    _empty_jugendamt_stats,
    _empty_stats,
    _merge_jugendamt_stats,
    _merge_stats,
    get_statistics_hybrid,
)
from tests._mutation_followup_snapshot_helpers import _make_event

# ---------------------------------------------------------------------------
# _merge_stats — Feld-für-Feld + Asymmetrie für +/-
# ---------------------------------------------------------------------------


class TestMergeStatsPerField:
    """Refs Welle 7 — ``_merge_stats`` (Line 149).

    Adressierte Mutationen:
    - ``+=`` → ``-=`` per Feld (total_contacts, unique_clients, stage-keys,
      doc_type count, age_cluster count).
    - ``.get(key, 0)`` → ``.get(key)`` würde None liefern und crashen.
    - ``stats_list = []`` → ``_empty_stats()`` (early return) prüfen.
    - ``key in doc_type_map`` → ``not in`` würde Doppel-Insert produzieren.
    - ``sorted(..., reverse=True)`` → ``reverse=False`` würde Sortierung
      invertieren.
    """

    def test_empty_list_returns_empty_stats(self):
        """``if not stats_list: return _empty_stats()`` — Early-Return.

        Mutation ``not`` → identity würde immer den loop laufen lassen
        (würde aber durch leeren Loop noch _empty_stats liefern). Aber
        Mutation ``_empty_stats()`` → ``{}`` würde fehlende keys liefern.
        """
        result = _merge_stats([])
        # Strukturell identisch zu _empty_stats
        assert result == _empty_stats()

    def test_total_contacts_summed(self):
        """``merged["total_contacts"] += stats.get("total_contacts", 0)``."""
        s1 = _empty_stats()
        s1["total_contacts"] = 3
        s2 = _empty_stats()
        s2["total_contacts"] = 7
        result = _merge_stats([s1, s2])
        assert result["total_contacts"] == 10

    def test_total_contacts_asymmetric_inputs(self):
        """Asymmetrische Inputs (1+2 != 2-1 != 1-2 != -1).

        Mutmut mutiert ``+=`` → ``-=``: würde 1-2 = -1 liefern statt 3.
        Mutmut mutiert ``=`` (initial 0) → 1: würde 4 liefern statt 3.
        """
        s1 = _empty_stats()
        s1["total_contacts"] = 1
        s2 = _empty_stats()
        s2["total_contacts"] = 2
        result = _merge_stats([s1, s2])
        assert result["total_contacts"] == 3, "1 + 2 = 3 (nicht -1, nicht 1, nicht 2)"

    def test_unique_clients_summed_separately_from_total(self):
        """``unique_clients`` darf NICHT mit ``total_contacts`` getauscht sein."""
        s1 = _empty_stats()
        s1["total_contacts"] = 5
        s1["unique_clients"] = 3
        s2 = _empty_stats()
        s2["total_contacts"] = 10
        s2["unique_clients"] = 7
        result = _merge_stats([s1, s2])
        assert result["total_contacts"] == 15
        assert result["unique_clients"] == 10
        # Sanity: nicht vertauscht
        assert result["total_contacts"] != result["unique_clients"]

    def test_anonym_summed_per_key(self):
        s1 = _empty_stats()
        s1["by_contact_stage"]["anonym"] = 2
        s2 = _empty_stats()
        s2["by_contact_stage"]["anonym"] = 3
        result = _merge_stats([s1, s2])
        assert result["by_contact_stage"]["anonym"] == 5

    def test_identifiziert_summed_per_key(self):
        s1 = _empty_stats()
        s1["by_contact_stage"]["identifiziert"] = 4
        s2 = _empty_stats()
        s2["by_contact_stage"]["identifiziert"] = 1
        result = _merge_stats([s1, s2])
        assert result["by_contact_stage"]["identifiziert"] == 5

    def test_qualifiziert_summed_per_key(self):
        s1 = _empty_stats()
        s1["by_contact_stage"]["qualifiziert"] = 6
        s2 = _empty_stats()
        s2["by_contact_stage"]["qualifiziert"] = 0
        result = _merge_stats([s1, s2])
        assert result["by_contact_stage"]["qualifiziert"] == 6

    def test_three_stages_summed_independently(self):
        """Mutation in der Stage-Loop (``for key in (...)``) würde Keys droppen."""
        s1 = _empty_stats()
        s1["by_contact_stage"] = {"anonym": 1, "identifiziert": 2, "qualifiziert": 3}
        s2 = _empty_stats()
        s2["by_contact_stage"] = {"anonym": 10, "identifiziert": 20, "qualifiziert": 30}
        result = _merge_stats([s1, s2])
        assert result["by_contact_stage"]["anonym"] == 11
        assert result["by_contact_stage"]["identifiziert"] == 22
        assert result["by_contact_stage"]["qualifiziert"] == 33

    def test_doc_type_composite_key_merge(self):
        """Composite-Key ``(name, category)``. Mutation ``entry["name"],
        entry["category"]`` → nur name würde Kategorien zusammenwerfen.
        """
        s1 = _empty_stats()
        s1["by_document_type"] = [{"name": "Kontakt", "category": "contact", "count": 2}]
        s2 = _empty_stats()
        s2["by_document_type"] = [
            {"name": "Kontakt", "category": "contact", "count": 3},
            {"name": "Kontakt", "category": "service", "count": 5},  # andere category
        ]
        result = _merge_stats([s1, s2])
        # Same composite key merged
        by_dt = {(e["name"], e["category"]): e["count"] for e in result["by_document_type"]}
        assert by_dt[("Kontakt", "contact")] == 5
        # Different category-key stays separate
        assert by_dt[("Kontakt", "service")] == 5

    def test_doc_type_first_occurrence_creates_entry(self):
        """``else: doc_type_map[composite] = {**entry}`` — neue Composite-Keys
        landen mit shallow-copy im Result."""
        s1 = _empty_stats()
        s1["by_document_type"] = [{"name": "Neu", "category": "cat", "count": 7}]
        result = _merge_stats([s1])
        assert len(result["by_document_type"]) == 1
        assert result["by_document_type"][0]["count"] == 7

    def test_doc_type_sorted_desc_by_count(self):
        """``sorted(..., reverse=True)``. Mutation ``reverse=False`` würde
        die kleinsten zuerst liefern."""
        s1 = _empty_stats()
        s1["by_document_type"] = [
            {"name": "Klein", "category": "x", "count": 1},
            {"name": "Gross", "category": "y", "count": 100},
            {"name": "Mittel", "category": "z", "count": 10},
        ]
        result = _merge_stats([s1])
        counts = [e["count"] for e in result["by_document_type"]]
        assert counts == [100, 10, 1]

    def test_age_cluster_merged_by_cluster_key(self):
        """``cluster = entry["cluster"]`` — Composite ist nur ``cluster``."""
        s1 = _empty_stats()
        s1["by_age_cluster"] = [{"cluster": "18_26", "label": "18–26", "count": 2}]
        s2 = _empty_stats()
        s2["by_age_cluster"] = [{"cluster": "18_26", "label": "18–26", "count": 3}]
        result = _merge_stats([s1, s2])
        assert len(result["by_age_cluster"]) == 1
        assert result["by_age_cluster"][0]["count"] == 5

    def test_age_cluster_sorted_desc_by_count(self):
        s1 = _empty_stats()
        s1["by_age_cluster"] = [
            {"cluster": "u18", "label": "Unter 18", "count": 2},
            {"cluster": "18_26", "label": "18–26", "count": 8},
            {"cluster": "27_plus", "label": "27+", "count": 5},
        ]
        result = _merge_stats([s1])
        counts = [e["count"] for e in result["by_age_cluster"]]
        assert counts == [8, 5, 2]

    def test_get_with_default_zero_handles_missing_keys(self):
        """``.get("total_contacts", 0)`` — Default 0. Mutation ``.get(key)``
        ohne Default würde bei fehlendem Key None liefern → TypeError beim ``+=``.
        """
        s1 = {}  # völlig leeres dict — nichts da
        s2 = _empty_stats()
        s2["total_contacts"] = 5
        # darf nicht crashen
        result = _merge_stats([s1, s2])
        assert result["total_contacts"] == 5

    def test_get_by_contact_stage_default_handles_missing_subkey(self):
        """``stats.get("by_contact_stage", {}).get(key, 0)`` —
        beide Defaults essenziell."""
        s1 = {"total_contacts": 0}  # kein by_contact_stage
        s2 = _empty_stats()
        s2["by_contact_stage"]["anonym"] = 7
        result = _merge_stats([s1, s2])
        assert result["by_contact_stage"]["anonym"] == 7


# ---------------------------------------------------------------------------
# _merge_jugendamt_stats — Tupel/List-Normalisierung + Aggregation
# ---------------------------------------------------------------------------


class TestMergeJugendamtStatsPerField:
    """Refs Welle 7 — ``_merge_jugendamt_stats`` (Line 208).

    Adressierte Mutationen:
    - ``total += stats.get("total", 0)``  → ``-=``.
    - ``entry[0], entry[1]`` → ``entry[1], entry[0]`` würde Name und Count
      vertauschen.
    - ``category_map.get(name, 0) + count`` → ``- count`` würde subtrahieren.
    - List-Comprehension ``[(name, count) ...]`` würde Tupel/List-Form mutieren.
    """

    def test_empty_list_returns_empty_jugendamt_stats(self):
        result = _merge_jugendamt_stats([])
        assert result == _empty_jugendamt_stats()

    def test_total_summed(self):
        s1 = _empty_jugendamt_stats()
        s1["total"] = 3
        s2 = _empty_jugendamt_stats()
        s2["total"] = 4
        result = _merge_jugendamt_stats([s1, s2])
        assert result["total"] == 7

    def test_total_asymmetric_inputs(self):
        s1 = _empty_jugendamt_stats()
        s1["total"] = 2
        s2 = _empty_jugendamt_stats()
        s2["total"] = 5
        result = _merge_jugendamt_stats([s1, s2])
        assert result["total"] == 7, "2+5=7, nicht -3, nicht 5, nicht 2"

    def test_unique_clients_summed(self):
        s1 = _empty_jugendamt_stats()
        s1["unique_clients"] = 4
        s2 = _empty_jugendamt_stats()
        s2["unique_clients"] = 6
        result = _merge_jugendamt_stats([s1, s2])
        assert result["unique_clients"] == 10

    def test_by_category_tuple_inputs(self):
        """Tupel-Inputs: ``("Kontakte", 5)``."""
        s1 = _empty_jugendamt_stats()
        s1["by_category"] = [("Kontakte", 5)]
        result = _merge_jugendamt_stats([s1])
        # Output ist immer list[tuple]
        assert result["by_category"] == [("Kontakte", 5)]

    def test_by_category_list_inputs(self):
        """List-Inputs (so kommen sie aus JSON-Snapshots zurück)."""
        s1 = _empty_jugendamt_stats()
        s1["by_category"] = [["Beratung", 3]]
        result = _merge_jugendamt_stats([s1])
        # Output ist tuple — Mutation ``[0], [1]`` → ``[1], [0]`` würde
        # ``(3, "Beratung")`` liefern.
        assert result["by_category"] == [("Beratung", 3)]

    def test_by_category_merge_same_name_sums_counts(self):
        """Same name in zwei Snapshots → Counts summieren.

        Mutation ``category_map.get(name, 0) + count`` → ``- count`` würde
        2 - 3 = -1 liefern statt 5.
        """
        s1 = _empty_jugendamt_stats()
        s1["by_category"] = [("Kontakte", 2)]
        s2 = _empty_jugendamt_stats()
        s2["by_category"] = [("Kontakte", 3)]
        result = _merge_jugendamt_stats([s1, s2])
        cats = dict(result["by_category"])
        assert cats["Kontakte"] == 5

    def test_by_category_distinct_names_preserved(self):
        """Verschiedene Kategorien bleiben separat."""
        s1 = _empty_jugendamt_stats()
        s1["by_category"] = [("Kontakte", 2), ("Beratung", 1)]
        s2 = _empty_jugendamt_stats()
        s2["by_category"] = [("Vermittlung", 4)]
        result = _merge_jugendamt_stats([s1, s2])
        cats = dict(result["by_category"])
        assert cats == {"Kontakte": 2, "Beratung": 1, "Vermittlung": 4}

    def test_by_category_tuple_and_list_mixed_normalize_to_tuple(self):
        """Tuple + List mixed input → beide werden im Output zu tuple."""
        s1 = _empty_jugendamt_stats()
        s1["by_category"] = [("X", 1)]
        s2 = _empty_jugendamt_stats()
        s2["by_category"] = [["X", 2]]
        result = _merge_jugendamt_stats([s1, s2])
        # X-Summe 3, als tuple
        assert ("X", 3) in result["by_category"]

    def test_age_cluster_merged(self):
        s1 = _empty_jugendamt_stats()
        s1["by_age_cluster"] = [{"cluster": "18_26", "label": "18–26", "count": 2}]
        s2 = _empty_jugendamt_stats()
        s2["by_age_cluster"] = [{"cluster": "18_26", "label": "18–26", "count": 4}]
        result = _merge_jugendamt_stats([s1, s2])
        assert len(result["by_age_cluster"]) == 1
        assert result["by_age_cluster"][0]["count"] == 6

    def test_age_cluster_sorted_desc(self):
        s1 = _empty_jugendamt_stats()
        s1["by_age_cluster"] = [
            {"cluster": "a", "label": "A", "count": 1},
            {"cluster": "b", "label": "B", "count": 10},
            {"cluster": "c", "label": "C", "count": 5},
        ]
        result = _merge_jugendamt_stats([s1])
        counts = [e["count"] for e in result["by_age_cluster"]]
        assert counts == [10, 5, 1]


# ---------------------------------------------------------------------------
# get_statistics_hybrid — Cutoff snapshot vs live + top_clients-Branch
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGetStatisticsHybridCutoff:
    """Refs Welle 7 — ``get_statistics_hybrid`` (Line 248).

    Adressierte Mutationen:
    - ``if use_snapshot: stats = get_snapshot(...)`` — Conditional kippt.
    - ``if stats is None: stats = get_statistics(...)`` — Fallback bei
      fehlendem Snapshot.
    - ``stats.pop("top_clients", None)`` im Segment-Loop.
    - ``merged["top_clients"] = live_full["top_clients"]`` —
      top_clients IMMER live, nie aus Snapshot.
    """

    def test_uses_snapshot_when_use_snapshot_true_and_snapshot_exists(self, facility):
        """Snapshot-Branch greift bei use_snapshot=True UND vorhandenem Snapshot.

        Mutation ``if use_snapshot`` → ``if not use_snapshot`` würde nie
        den Snapshot nutzen.
        """
        # Snapshot mit Marker, der nicht aus Live-Query stammen kann
        StatisticsSnapshot.objects.create(
            facility=facility,
            year=2025,
            month=1,
            data={
                "total_contacts": 999,
                "by_contact_stage": {"anonym": 0, "identifiziert": 0, "qualifiziert": 0},
                "by_document_type": [],
                "by_age_cluster": [],
                "unique_clients": 999,
            },
            jugendamt_data={},
        )
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 6, 15)
            result = get_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 1, 31))
        # Live-Query hätte 0 ergeben — wir lesen 999 aus Snapshot
        assert result["total_contacts"] == 999

    def test_fallback_to_live_when_snapshot_missing(self, facility, staff_user, client_identified, doc_type_contact):
        """``if stats is None: stats = get_statistics(...)`` Fallback-Branch.

        Mutation ``if stats is None`` → ``if stats is not None`` würde
        diesen Branch kippen.
        """
        jan = datetime(2025, 1, 15, 10, 0)
        _make_event(facility, client_identified, doc_type_contact, staff_user, jan)
        # KEIN Snapshot vorhanden
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 6, 15)
            result = get_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 1, 31))
        assert result["total_contacts"] == 1

    def test_current_month_uses_live_ignoring_snapshot(self, facility, staff_user, client_identified, doc_type_contact):
        """Cutoff: aktueller Monat IMMER live, Snapshot ignoriert."""
        StatisticsSnapshot.objects.create(
            facility=facility,
            year=2025,
            month=3,
            data={
                "total_contacts": 999,
                "by_contact_stage": {"anonym": 0, "identifiziert": 0, "qualifiziert": 0},
                "by_document_type": [],
                "by_age_cluster": [],
                "unique_clients": 999,
            },
            jugendamt_data={},
        )
        mar = datetime(2025, 3, 5, 10, 0)
        _make_event(facility, client_identified, doc_type_contact, staff_user, mar)
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_statistics_hybrid(facility, date(2025, 3, 1), date(2025, 3, 31))
        assert result["total_contacts"] == 1, "Current month muss live sein, nicht 999 aus Snapshot"

    def test_top_clients_always_from_live_full_range(self, facility, staff_user, client_identified, doc_type_contact):
        """``merged["top_clients"] = live_full["top_clients"]``.

        Mutation ``live_full["top_clients"]`` → ``[]`` würde leere Liste
        liefern.
        """
        # Snapshot ohne top_clients (so wird er bei create_or_update_snapshot
        # bewusst gespeichert)
        StatisticsSnapshot.objects.create(
            facility=facility,
            year=2025,
            month=1,
            data={
                "total_contacts": 0,
                "by_contact_stage": {"anonym": 0, "identifiziert": 0, "qualifiziert": 0},
                "by_document_type": [],
                "by_age_cluster": [],
                "unique_clients": 0,
            },
            jugendamt_data={},
        )
        # Event in Januar
        jan = datetime(2025, 1, 15, 10, 0)
        _make_event(facility, client_identified, doc_type_contact, staff_user, jan)
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 6, 15)
            result = get_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 1, 31))
        # top_clients muss im Result-Dict landen (Key-Existenz)
        assert "top_clients" in result, "top_clients muss vom live_full immer in den merged-Dict gesetzt werden"

    def test_segment_pop_top_clients_does_not_crash(self, facility, staff_user, client_identified, doc_type_contact):
        """``stats.pop("top_clients", None)`` Segment-Branch.

        Mutation ``pop("top_clients", None)`` → ``pop("top_clients")``
        (ohne default) würde KeyError werfen, falls Snapshot keinen Key hat.
        """
        # Snapshot ohne top_clients (Normalfall)
        StatisticsSnapshot.objects.create(
            facility=facility,
            year=2025,
            month=1,
            data={
                "total_contacts": 5,
                "by_contact_stage": {"anonym": 0, "identifiziert": 5, "qualifiziert": 0},
                "by_document_type": [],
                "by_age_cluster": [],
                "unique_clients": 3,
                # KEIN top_clients-Key (so wie create_or_update_snapshot speichert)
            },
            jugendamt_data={},
        )
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 6, 15)
            # darf nicht crashen
            result = get_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 1, 31))
        assert result["total_contacts"] == 5

    def test_merged_combines_snapshot_and_live_month_correctly(
        self, facility, staff_user, client_identified, doc_type_contact
    ):
        """Range über mehrere Monate: Snapshot-Monat + Live-Monat sauber addiert.

        Mutation an ``segment_stats.append(stats)`` (z.B. ``= [stats]``)
        würde nur das letzte Segment behalten.
        """
        # Snapshot für Januar mit 5 Kontakten
        StatisticsSnapshot.objects.create(
            facility=facility,
            year=2025,
            month=1,
            data={
                "total_contacts": 5,
                "by_contact_stage": {"anonym": 0, "identifiziert": 5, "qualifiziert": 0},
                "by_document_type": [],
                "by_age_cluster": [],
                "unique_clients": 5,
            },
            jugendamt_data={},
        )
        # Live-Event in März (current month bei localdate=2025-03-15)
        mar = datetime(2025, 3, 10, 10, 0)
        _make_event(facility, client_identified, doc_type_contact, staff_user, mar)
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 3, 31))
        # 5 (Jan-Snapshot) + 0 (Feb live, leer) + 1 (Mar live) = 6
        assert result["total_contacts"] == 6
