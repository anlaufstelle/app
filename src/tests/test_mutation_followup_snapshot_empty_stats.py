"""Mutation-Followup-Tests für ``core.services.snapshot`` — Empty-Defaults.

Refs Welle 7 (#930). Sub-File aus ``test_mutation_followup_snapshot``;
enthält ``TestEmptyStats`` und ``TestEmptyJugendamtStats`` — also die
Felder-für-Felder-Sentinels für ``_empty_stats`` und
``_empty_jugendamt_stats``.

Constraint: Tests gegen Verify-DB (``POSTGRES_DB=anlaufstelle_verify``).
"""

from __future__ import annotations

from core.services.snapshot import (
    _empty_jugendamt_stats,
    _empty_stats,
)

# ---------------------------------------------------------------------------
# _empty_stats / _empty_jugendamt_stats — Field-by-Field
# ---------------------------------------------------------------------------


class TestEmptyStats:
    """Refs Welle 7 — ``_empty_stats`` (Line 138).

    Adressierte Mutationen: jedes Feld einzeln (Mutmut mutiert single keys/
    initial-Werte). Wir prüfen ALLE Keys + Defaults explizit.
    """

    def test_total_contacts_zero(self):
        assert _empty_stats()["total_contacts"] == 0

    def test_unique_clients_zero(self):
        assert _empty_stats()["unique_clients"] == 0

    def test_by_contact_stage_three_keys_zero(self):
        stage = _empty_stats()["by_contact_stage"]
        assert stage["anonym"] == 0
        assert stage["identifiziert"] == 0
        assert stage["qualifiziert"] == 0
        # Genau diese drei Keys, kein extra-Key
        assert set(stage.keys()) == {"anonym", "identifiziert", "qualifiziert"}

    def test_by_document_type_empty_list(self):
        assert _empty_stats()["by_document_type"] == []
        # Liste, nicht None — Aufrufer iteriert per for-loop
        assert isinstance(_empty_stats()["by_document_type"], list)

    def test_by_age_cluster_empty_list(self):
        assert _empty_stats()["by_age_cluster"] == []
        assert isinstance(_empty_stats()["by_age_cluster"], list)

    def test_returns_dict_with_exactly_five_top_level_keys(self):
        """Mutation würde einen Key droppen oder hinzufügen."""
        keys = set(_empty_stats().keys())
        assert keys == {
            "total_contacts",
            "by_contact_stage",
            "by_document_type",
            "by_age_cluster",
            "unique_clients",
        }


class TestEmptyJugendamtStats:
    """Refs Welle 7 — ``_empty_jugendamt_stats`` (Line 198)."""

    def test_total_zero(self):
        assert _empty_jugendamt_stats()["total"] == 0

    def test_unique_clients_zero(self):
        assert _empty_jugendamt_stats()["unique_clients"] == 0

    def test_by_category_empty_list(self):
        assert _empty_jugendamt_stats()["by_category"] == []
        assert isinstance(_empty_jugendamt_stats()["by_category"], list)

    def test_by_age_cluster_empty_list(self):
        assert _empty_jugendamt_stats()["by_age_cluster"] == []
        assert isinstance(_empty_jugendamt_stats()["by_age_cluster"], list)

    def test_returns_dict_with_exactly_four_top_level_keys(self):
        keys = set(_empty_jugendamt_stats().keys())
        assert keys == {"total", "by_category", "by_age_cluster", "unique_clients"}
