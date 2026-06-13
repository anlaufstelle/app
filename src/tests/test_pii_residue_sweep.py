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
        "core_episode",
        "core_workitem",
        "core_activity",
        "core_eventhistory",
        "core_auditlog",
        "core_deletionrequest",
    }:
        assert expected in tables_hit, f"Fixture befuellt {expected} nicht: {tables_hit}"


# --------------------------------------------------------------------------
# Service-Layer-Fixture: eine vollbesetzte PII-Akte (Refs #1083)
# --------------------------------------------------------------------------
# Baut die Akte ueberwiegend ueber Service-Funktionen (nicht
# ``.objects.create`` fuer die PII-Traeger, wo ein Service existiert), damit
# abgeleitete Senken realistisch befuellt werden:
#   - create_client    -> core_client.{pseudonym,notes} + Activity "… angelegt"
#   - update_client     -> Activity "… qualifiziert" (Stufenwechsel)
#   - create_case       -> core_case.{title,description}
#   - Episode.create    -> core_episode.{title,description} (kein Service)
#   - create_workitem   -> core_workitem.{title,description} + Activity
#   - create_event      -> core_event.search_text (aus data_json/Field-Slug)
#                          + core_eventhistory.data_after (CREATE) + Activity
#   - EventHistory.create-> core_eventhistory.{data_before,data_after} (UPDATE)
#   - store_encrypted_file-> core_eventattachment (Name Fernet-verschluesselt:
#                          strukturell geprueft, NICHT per Needle-Scan)
#   - request_deletion   -> core_deletionrequest.reason
#   - approve_deletion   -> core_auditlog.detail (pseudonym + reason)
# Alle Klartext-PII-Werte tragen den ``RESIDUEPROBE-``-Praefix.

# Minimal-PDF (gueltige Magic-Bytes) fuer ``store_encrypted_file`` —
# der Vault prueft Endung + Magic-Bytes gegen den content_type.
PDF_MINIMAL = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


@pytest.fixture
def maximal_pii_graph(facility, admin_user, lead_user):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from core.models import (
        Client,
        DocumentType,
        DocumentTypeField,
        Episode,
        EventHistory,
        FieldTemplate,
    )
    from core.services.case import create_case, create_workitem
    from core.services.client import (
        approve_client_deletion,
        create_client,
        request_client_deletion,
        update_client,
    )
    from core.services.events import create_event
    from core.services.file_vault import store_encrypted_file

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
    case = create_case(
        facility,
        admin_user,
        client=client,
        title="RESIDUEPROBE-Falltitel",
        description="RESIDUEPROBE-Fallbeschreibung",
    )

    # 3a) Episode unter dem Fall (core_episode.{title,description}). Kein
    #     Episode-Service vorhanden — der Freitext IST der Needle, daher
    #     direkter ``.objects.create`` (keine Ableitung noetig).
    Episode.objects.create(
        case=case,
        title="RESIDUEPROBE-Episodentitel",
        description="RESIDUEPROBE-Episodenbeschreibung",
        started_at=timezone.now().date(),
        created_by=admin_user,
    )

    # 3b) Arbeitsauftrag fuer die Person (core_workitem.{title,description})
    #     ueber den Service, damit abgeleitete Senken realistisch befuellt
    #     werden (Activity "Aufgabe: …").
    create_workitem(
        facility,
        admin_user,
        client=client,
        title="RESIDUEPROBE-Aufgabe",
        description="RESIDUEPROBE-Aufgabenbeschreibung",
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

    # 5a) Expliziter UPDATE-Eintrag mit Freitext im data_before UND data_after.
    #     ``create_event`` schreibt nur einen CREATE-Eintrag (data_after); der
    #     data_before-Pfad wird sonst nie befuellt. INSERT ist trotz Append-Only-
    #     Trigger erlaubt (nur UPDATE/DELETE blocken).
    EventHistory.objects.create(
        event=event,
        changed_by=admin_user,
        action=EventHistory.Action.UPDATE,
        data_before={ft_notiz.slug: "RESIDUEPROBE-Verlauf alt"},
        data_after={ft_notiz.slug: "RESIDUEPROBE-Verlauf neu"},
    )

    # 5b) Verschluesselter Dateianhang (core_eventattachment). Der Klartext-
    #     Dateiname wird Fernet-verschluesselt abgelegt — der Needle erscheint
    #     NICHT in der DB. Das Attachment wird daher strukturell (Loeschung)
    #     geprueft, nicht per Needle-Scan.
    ft_anhang = FieldTemplate.objects.create(
        facility=facility,
        name="Residue-Anhang",
        field_type=FieldTemplate.FieldType.FILE,
    )
    upload = SimpleUploadedFile("RESIDUEPROBE-Anhang.pdf", PDF_MINIMAL, content_type="application/pdf")
    attachment = store_encrypted_file(facility, upload, ft_anhang, event, admin_user)

    # 6) Vier-Augen-Loeschung: Antrag (reason -> core_deletionrequest.reason),
    #    Genehmigung (pseudonym + reason -> core_auditlog.detail). Reviewer
    #    != Antragsteller und reviewer.can_confirm_deletion erforderlich.
    dr = request_client_deletion(client, admin_user, reason="RESIDUEPROBE-Begruendung")
    approve_client_deletion(dr, lead_user)

    client.refresh_from_db()
    return SimpleNamespace(
        facility=facility,
        client=client,
        event=event,
        doc_type=doc_type,
        attachment=attachment,
    )


# --------------------------------------------------------------------------
# Pfad-Sweep: anonymize_client() (Refs #1083)
# --------------------------------------------------------------------------
# Trennt erwartete (``known_residue``) von undeklarierten Treffern und
# beweist tabellenweise, dass ``Client.anonymize()`` jede Scoped-Tabelle
# leerredigiert. ``core_event`` ist als bekanntes Leck (H2) xfail markiert.


_KNOWN = {(r.table, r.column) for r in COLUMN_CLASSIFICATION if r.kind == "known_residue"}


def undeclared_hits(facility_id) -> list[Hit]:
    return [h for h in scan_facility_for_needles(facility_id) if (h.table, h.column) not in _KNOWN]


def _fmt(hits: list[Hit]) -> str:
    return "\n".join(f"  {h.table}.{h.column}: {h.sample!r}" for h in hits)


# Bekannte Lecks von ``anonymize_client()`` — als strikte xfails markiert,
# sodass ein spaeterer Fix den Test als XPASS reisst (Erinnerung, das xfail
# zu entfernen). Pro Eintrag ein eigenes Folge-Issue:
#
#   H2 (core_event): anonymize_client() leert die Live-Event-data_json/
#       search_text NICHT (nur der Soft-Delete-Pfad tut das). Die Live-Zeile
#       bleibt mit Klartext-Needle stehen.
#   H3-1 (core_activity): _redact_activities() redigiert nur Activities mit
#       Target Client/Event. ``create_workitem`` schreibt aber eine Activity
#       mit Target=WorkItem ("Aufgabe: <titel>"), deren Titel Klienten-PII
#       tragen kann (z.B. Pseudonym) — diese bleibt unredigiert stehen.
#   H3-2 (core_deletionrequest): _redact_deletion_requests() redigiert nur
#       Antraege mit target_type="Event". Der Vier-Augen-Antrag, der die
#       Klienten-Loeschung ausloest (``request_client_deletion``), hat aber
#       target_type="Client" — sein Freitext-``reason`` bleibt stehen.
ANONYMIZE_XFAIL = {
    "core_event": "H2: Event.data_json/search_text-Residue, Fix folgt",
    "core_activity": "H3-1: WorkItem-Target-Activity-Summary unredigiert, Fix folgt",
    "core_deletionrequest": "H3-2: Client-Target-DeletionRequest.reason unredigiert, Fix folgt",
}
ANONYMIZE_TABLES = [
    pytest.param(t, marks=pytest.mark.xfail(strict=True, reason=ANONYMIZE_XFAIL[t])) if t in ANONYMIZE_XFAIL else t
    for t in SCOPED_TABLES
]


@pytest.mark.django_db
@pytest.mark.parametrize("table", ANONYMIZE_TABLES)
def test_no_residue_after_anonymize(maximal_pii_graph, table):
    g = maximal_pii_graph
    g.client.anonymize()
    hits = [h for h in undeclared_hits(g.facility.id) if h.table == table]
    assert not hits, f"PII-Residue nach anonymize() in {table}:\n{_fmt(hits)}"


@pytest.mark.django_db
def test_attachments_deleted_after_anonymize(maximal_pii_graph):
    from core.models import EventAttachment

    g = maximal_pii_graph
    assert EventAttachment.objects.filter(event=g.event).exists()  # Positiv-Kontrolle
    g.client.anonymize()
    assert EventAttachment.objects.filter(event=g.event).count() == 0
