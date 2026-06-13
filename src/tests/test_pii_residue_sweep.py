"""DB-weiter PII-Residue-Sweep (Refs #1083).

Beweist, dass nach Loesch-/Anonymisierungs-/Retention-Pfaden keine
``RESIDUEPROBE-``-Sentinels in undeklarierten Text-/JSON-Spalten der
facility-gescopten Tabellen verbleiben.
"""

import pytest
from django.db import connection

from tests._residue_expectations import COLUMN_CLASSIFICATION, SCOPED_TABLES

# information_schema.data_type-Werte, die Freitext/JSON tragen koennen.
TEXTY_TYPES = frozenset({"character varying", "text", "character", '"char"', "json", "jsonb"})


def texty_columns(table: str) -> list[str]:
    """Alle Freitext-/JSON-Spalten einer Tabelle (introspektiv)."""
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            [table],
        )
        return [name for name, dtype in cur.fetchall() if dtype in TEXTY_TYPES]


@pytest.mark.django_db
class TestResidueCompletenessGate:
    """Erzwingt: jede Text-/JSON-Spalte ist genau einmal klassifiziert."""

    def test_every_texty_column_classified(self):
        declared = {(r.table, r.column) for r in COLUMN_CLASSIFICATION}
        missing = [
            f"{table}.{col}" for table in SCOPED_TABLES for col in texty_columns(table) if (table, col) not in declared
        ]
        assert not missing, (
            "Unklassifizierte Text-/JSON-Spalten — in _residue_expectations.py "
            f"als pii/non_pii/known_residue einordnen:\n{sorted(missing)}"
        )

    def test_classification_targets_real_columns(self):
        # Spalten je Tabelle einmal introspizieren, dann gegen die Regeln pruefen
        # (ein DB-Roundtrip pro Tabelle statt pro Regel).
        columns_by_table = {t: frozenset(texty_columns(t)) for t in SCOPED_TABLES}
        stale = [
            f"{r.table}.{r.column}"
            for r in COLUMN_CLASSIFICATION
            if r.column not in columns_by_table.get(r.table, frozenset())
        ]
        assert not stale, f"Veraltete Klassifikations-Eintraege: {sorted(stale)}"

    def test_no_duplicate_classifications(self):
        keys = [(r.table, r.column) for r in COLUMN_CLASSIFICATION]
        dupes = sorted({f"{t}.{c}" for t, c in keys if keys.count((t, c)) > 1})
        assert not dupes, f"Doppelt klassifizierte Spalten: {dupes}"

    def test_known_residue_with_pending_fix_has_issue(self):
        bad = [
            f"{r.table}.{r.column}"
            for r in COLUMN_CLASSIFICATION
            if r.kind == "known_residue" and "pending_fix" in r.reason and not r.issue
        ]
        assert not bad, f"pending_fix ohne Issue-Referenz: {bad}"
