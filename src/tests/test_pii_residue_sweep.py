"""DB-weiter PII-Residue-Sweep (Refs #1083).

Beweist, dass nach Loesch-/Anonymisierungs-/Retention-Pfaden keine
``RESIDUEPROBE-``-Sentinels in undeklarierten Text-/JSON-Spalten der
facility-gescopten Tabellen verbleiben.
"""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from django.db import connection
from django.utils import timezone

from tests._residue_expectations import COLUMN_CLASSIFICATION, NEEDLE_PREFIX, SCOPED_TABLES

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


# --------------------------------------------------------------------------
# Needle-Scanner (Refs #1083)
# --------------------------------------------------------------------------
# Durchsucht jede Freitext-/JSON-Spalte jeder Scoped-Tabelle nach den
# ``RESIDUEPROBE-``-Sentinels. Bewusst OHNE _KNOWN-Filterung — der Scanner
# meldet ALLE Treffer (inkl. ``core_auditlog.detail``); die Trennung in
# erwartete vs. unerwartete Residuen erfolgt erst im Pfad-Sweep (Task 3).


@dataclass(frozen=True)
class Hit:
    table: str
    column: str
    sample: str


def all_columns(table: str) -> list[str]:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",
            [table],
        )
        return [r[0] for r in cur.fetchall()]


def scan_facility_for_needles(facility_id) -> list[Hit]:
    """Scannt jede Freitext-/JSON-Spalte jeder Scoped-Tabelle nach Sentinels.

    Tabellen-/Spaltennamen stammen aus ``SCOPED_TABLES`` bzw. ``information_schema``
    (kein User-Input) — die f-String-Interpolation ist im Testkontext unbedenklich.
    Tabellen ohne ``facility_id`` (transitiv gescopt, z. B. ``core_eventhistory``)
    werden ungefiltert gelesen; sicher, weil jeder Test in einer isolierten
    Transaktion laeuft und nur die selbst geseedeten Zeilen sichtbar sind.
    """
    hits: list[Hit] = []
    with connection.cursor() as cur:
        for table in SCOPED_TABLES:
            cols = texty_columns(table)
            if not cols:
                continue
            scoped = "facility_id" in all_columns(table)
            where = "WHERE facility_id = %s" if scoped else ""
            params = [str(facility_id)] if scoped else []
            for col in cols:
                cur.execute(f'SELECT "{col}"::text FROM "{table}" {where}', params)
                for (val,) in cur.fetchall():
                    if val and NEEDLE_PREFIX in val:
                        hits.append(Hit(table, col, val[:80]))
    return hits


@pytest.mark.django_db
def test_fixture_seeds_needles_everywhere(maximal_pii_graph):
    """Positiv-Kontrolle: vor Redaktion sind Sentinels breit gestreut."""
    hits = scan_facility_for_needles(maximal_pii_graph.facility.id)
    tables_hit = {h.table for h in hits}
    for expected in {
        "core_client",
        "core_event",
        "core_case",
        "core_activity",
        "core_eventhistory",
        "core_auditlog",
        "core_deletionrequest",
    }:
        assert expected in tables_hit, f"Fixture befuellt {expected} nicht: {tables_hit}"


# --------------------------------------------------------------------------
# Service-Layer-Fixture: eine vollbesetzte PII-Akte (Refs #1083)
# --------------------------------------------------------------------------
# Baut die Akte AUSSCHLIESSLICH ueber Service-Funktionen (nicht
# ``.objects.create`` fuer die PII-Traeger), damit abgeleitete Senken
# realistisch befuellt werden:
#   - create_client    -> core_client.{pseudonym,notes} + Activity "… angelegt"
#   - update_client     -> Activity "… qualifiziert" (Stufenwechsel)
#   - create_case       -> core_case.{title,description}
#   - create_event      -> core_event.search_text (aus data_json/Field-Slug)
#                          + core_eventhistory.data_after + Activity "… für …"
#   - request_deletion   -> core_deletionrequest.reason
#   - approve_deletion   -> core_auditlog.detail (pseudonym + reason)
# Alle PII-Werte tragen den ``RESIDUEPROBE-``-Praefix.


@pytest.fixture
def maximal_pii_graph(facility, admin_user, lead_user):
    from core.models import Client, DocumentType, DocumentTypeField, FieldTemplate
    from core.services.case import create_case
    from core.services.client import (
        approve_client_deletion,
        create_client,
        request_client_deletion,
        update_client,
    )
    from core.services.events import create_event

    # 1) Person anlegen (Service schreibt Activity "Person … angelegt").
    client = create_client(
        facility,
        admin_user,
        pseudonym="RESIDUEPROBE-Pseudonym",
        notes="RESIDUEPROBE-Notiz Hausarzt",
        contact_stage=Client.ContactStage.IDENTIFIED,
    )

    # 2) Stufenwechsel IDENTIFIED -> QUALIFIED (Service schreibt Activity
    #    "<pseudonym> qualifiziert").
    update_client(
        client,
        admin_user,
        old_stage=Client.ContactStage.IDENTIFIED,
        contact_stage=Client.ContactStage.QUALIFIED,
    )

    # 3) Fall mit Freitext (core_case.{title,description}).
    create_case(
        facility,
        admin_user,
        client=client,
        title="RESIDUEPROBE-Falltitel",
        description="RESIDUEPROBE-Fallbeschreibung",
    )

    # 4) DocumentType + unverschluesseltes NORMAL-Textfeld, verknuepft wie
    #    conftest.doc_type_contact. NORMAL + unverschluesselt ist Pflicht,
    #    damit ``compute_event_search_text`` den Wert in ``search_text``
    #    aufnimmt (erhoehte/encrypted Felder werden absichtlich uebersprungen).
    doc_type = DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.CONTACT,
        name="Residue-Kontakt",
    )
    ft_notiz = FieldTemplate.objects.create(
        facility=facility,
        name="Residue-Notiz",
        field_type=FieldTemplate.FieldType.TEXTAREA,
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_notiz, sort_order=0)

    # 5) Event mit Freitext im data_json -> search_text + EventHistory.data_after.
    #    Key MUSS dem Field-Slug entsprechen, sonst verwirft ``_validate_data_json``
    #    den Wert.
    event = create_event(
        facility,
        admin_user,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json={ft_notiz.slug: "RESIDUEPROBE-Freitext Krise"},
        client=client,
    )

    # 6) Vier-Augen-Loeschung: Antrag (reason -> core_deletionrequest.reason),
    #    Genehmigung (pseudonym + reason -> core_auditlog.detail). Reviewer
    #    != Antragsteller und reviewer.can_confirm_deletion erforderlich.
    dr = request_client_deletion(client, admin_user, reason="RESIDUEPROBE-Begruendung")
    approve_client_deletion(dr, lead_user)

    client.refresh_from_db()
    return SimpleNamespace(facility=facility, client=client, event=event, doc_type=doc_type)
