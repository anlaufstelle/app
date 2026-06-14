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
    # core_auditlog bewusst NICHT erwartet: seit #1093 schreibt der Klienten-
    # Pfad keine Klienten-PII mehr ins AuditLog.detail (write-time minimiert),
    # die Fixture streut dort also keinen Sentinel.
    for expected in {
        "core_client",
        "core_event",
        "core_case",
        "core_episode",
        "core_workitem",
        "core_activity",
        "core_eventhistory",
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
#   - approve_deletion   -> seit #1093 KEINE Klienten-PII mehr in
#                          core_auditlog.detail (nur die DeletionRequest-PK)
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
#   H2 (core_event): GEFIXT (#1089). ``anonymize_client`` redigiert jetzt auch
#       die Live-``Event``-Zeilen (data_json -> {} + search_text neu berechnet),
#       nicht nur EventHistory. Bereits soft-deletete Events werden NICHT
#       angefasst — deren search_text-Leck im Retention-Pfad ist ein eigener
#       Befund (H5, #1092, siehe RETENTION_XFAIL).
#   H3-2 (core_deletionrequest): _redact_deletion_requests() redigiert nur
#       Antraege mit target_type="Event". Der Vier-Augen-Antrag, der die
#       Klienten-Loeschung ausloest (``request_client_deletion``), hat aber
#       target_type="Client" — sein Freitext-``reason`` bleibt stehen.
ANONYMIZE_XFAIL = {
    "core_deletionrequest": "H3-2: Client-Target-DeletionRequest.reason unredigiert (Fix #1091)",
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


# --------------------------------------------------------------------------
# Pfad-Sweep: Event-Soft-Delete via Vier-Augen-Loeschung (Refs #1083)
# --------------------------------------------------------------------------
# Fokussierter Test (NICHT 22-fach): der Event-Soft-Delete redigiert nur den
# Event-Inhalt (``data_json`` -> ``{}``, Anhaenge geloescht). Client/Case/
# Episode/WorkItem/Activity/EventHistory bleiben absichtlich stehen (der
# Klient wird nicht geloescht) — daher wird nur auf ``core_event``-Senken
# geprueft, nicht ueber alle Scoped-Tabellen.
#
# Empirie zu search_text: ``soft_delete_event`` setzt zwar nur
# ``data_json = {}`` und berechnet ``search_text`` nicht explizit neu — das
# ``pre_save``-Signal ``_refresh_event_search_text`` (signals/event_search.py)
# leitet ``search_text`` aber bei JEDEM Event-Save aus ``data_json`` ab. Mit
# leerem ``data_json`` faellt ``search_text`` daher auf "" zurueck. Beide
# core_event-Senken (data_json + search_text) sind nach Soft-Delete sauber —
# KEIN search_text-Residue (H4 widerlegt).


@pytest.mark.django_db
def test_event_content_gone_after_soft_delete(maximal_pii_graph, admin_user, lead_user):
    from core.models import EventAttachment
    from core.services.events import approve_deletion, request_deletion

    g = maximal_pii_graph
    # Vier-Augen-Loeschung des Events (Soft-Delete leert data_json). Reviewer
    # (lead_user) != Antragsteller (admin_user); lead_user.can_confirm_deletion.
    dr = request_deletion(g.event, admin_user, reason="RESIDUEPROBE-EvDel")
    approve_deletion(dr, lead_user)

    # Der soft-deletete Event darf keinen Klartext-Needle mehr in seiner eigenen
    # Zeile tragen — data_json ist {} und search_text wird per Signal mit auf ""
    # zurueckgesetzt.
    event_hits = [h for h in undeclared_hits(g.facility.id) if h.table == "core_event"]
    assert not event_hits, f"Event-Inhalt nach Soft-Delete nicht entfernt:\n{_fmt(event_hits)}"

    # Anhaenge des Events geloescht.
    assert EventAttachment.objects.filter(event=g.event).count() == 0


# --------------------------------------------------------------------------
# Pfad-Sweep: Trash-Expiry via anonymize_eligible_soft_deleted_clients (Refs #1083)
# --------------------------------------------------------------------------
# Die Papierkorb-Frist-Anonymisierung ruft ``anonymize_client`` auf die ganze
# Klientenakte — gleiches Residue-Profil wie der direkte anonymize-Pfad
# (dieselben Lecks H2/H3-1/H3-2 ueber ``ANONYMIZE_TABLES``).


@pytest.mark.django_db
@pytest.mark.parametrize("table", ANONYMIZE_TABLES)
def test_no_residue_after_trash_expiry(maximal_pii_graph, table):
    from datetime import timedelta

    from core.models import Settings
    from core.services.client import anonymize_eligible_soft_deleted_clients

    g = maximal_pii_graph
    # Frist kuenstlich ueberschreiten, dann der reale Retention-Einstieg.
    g.client.deleted_at = timezone.now() - timedelta(days=9999)
    g.client.save(update_fields=["deleted_at"])
    settings_obj, _ = Settings.objects.get_or_create(facility=g.facility)
    settings_obj.client_trash_days = 30
    settings_obj.save()

    count = anonymize_eligible_soft_deleted_clients(g.facility, settings_obj)
    assert count >= 1, "Trash-Expiry hat den Client nicht erfasst"
    hits = [h for h in undeclared_hits(g.facility.id) if h.table == table]
    assert not hits, f"PII-Residue nach Trash-Expiry in {table}:\n{_fmt(hits)}"


# --------------------------------------------------------------------------
# Pfad-Sweep: enforce_retention Voll-Pipeline (Refs #1083)
# --------------------------------------------------------------------------
# Der Command orchestriert in dieser Reihenfolge:
#   1. process_facility_retention -> Event-Soft-Delete (vier Strategien)
#   2. enforce_activities         -> Activity-Hard-Delete
#   3. anonymize_clients          -> Client-Anonymisierung (alle Events deleted)
#   4. anonymize_eligible_soft_deleted_clients -> Trash-Expiry-Anonymisierung
#   5. prune_auditlog             -> AuditLog-Pruning
#
# Die Test-Config stellt ALLE Fristen scharf (0 Tage), damit jeder Schritt
# real agiert. Der Event wird ueber die document_type-Strategie soft-deletet
# (``doc_type.retention_days = 0`` + ``occurred_at`` in der Vergangenheit) —
# unabhaengig von der Kontaktstufe und ohne Case-Verknuepfung, die der Fixture-
# Event nicht hat. So laeuft Schritt 1 VOR der Anonymisierung (Schritt 3/4).
#
# EMPIRISCHES RESIDUE-PROFIL (am echten DB-Lauf beobachtet, nicht aus
# ANONYMIZE_TABLES kopiert — bewusst abweichend):
#
#   core_deletionrequest -> RESIDUE (H3-2, wie im anonymize-Pfad). Der
#       Vier-Augen-Antrag, der die Client-Loeschung ausloeste, hat
#       ``target_type="Client"``; ``_redact_deletion_requests`` redigiert nur
#       ``target_type="Event"`` -> sein ``reason`` bleibt stehen.
#
#   core_activity -> SAUBER (H3-1 reproduziert hier NICHT). ``enforce_activities``
#       mit ``retention_activities_days=0`` hard-deletet ALLE Activities (auch
#       die WorkItem-Target-Activity, die im reinen anonymize-Pfad als H3-1
#       stehenbliebe). Daher KEIN xfail fuer core_activity — sonst XPASS-strict.
#
# Eigene xfail-Liste (NICHT ANONYMIZE_TABLES): nur die empirisch roten Tabellen.
RETENTION_XFAIL = {
    "core_deletionrequest": "H3-2: Client-Target-DeletionRequest.reason unredigiert (Fix #1091)",
}
RETENTION_TABLES = [
    pytest.param(t, marks=pytest.mark.xfail(strict=True, reason=RETENTION_XFAIL[t])) if t in RETENTION_XFAIL else t
    for t in SCOPED_TABLES
]


def _sharpen_retention(facility):
    """Stellt alle Retention-Fristen der Facility auf 0/1, damit die Pipeline
    die Akte maximal abraeumt. Gibt das Settings-Objekt zurueck."""
    from core.models import Settings

    settings_obj, _ = Settings.objects.get_or_create(facility=facility)
    settings_obj.retention_anonymous_days = 0
    settings_obj.retention_identified_days = 0
    settings_obj.retention_qualified_days = 0
    settings_obj.retention_activities_days = 0
    settings_obj.client_trash_days = 0
    # 1 Monat statt 0 = Pruning aktiv; die frischen Fixture-AuditLogs (now)
    # liegen aber innerhalb der Frist und bleiben erhalten — seit #1093 traegt
    # core_auditlog.detail jedoch keine Klienten-PII mehr (nur die
    # DeletionRequest-PK), daher kein Sentinel-Residue.
    settings_obj.auditlog_retention_months = 1
    settings_obj.save()
    return settings_obj


@pytest.mark.django_db
@pytest.mark.parametrize("table", RETENTION_TABLES)
def test_no_residue_after_enforce_retention(maximal_pii_graph, table):
    from datetime import timedelta

    from django.core.management import call_command

    from core.models import Event

    g = maximal_pii_graph
    _sharpen_retention(g.facility)

    # Event ueber die document_type-Strategie loeschbar machen: Custom-Retention
    # 0 Tage + occurred_at in der Vergangenheit. So feuert Schritt 1 (Soft-Delete)
    # VOR der Anonymisierung — unabhaengig von Kontaktstufe/Case-Verknuepfung.
    g.doc_type.retention_days = 0
    g.doc_type.save(update_fields=["retention_days"])
    Event.objects.filter(pk=g.event.pk).update(occurred_at=timezone.now() - timedelta(days=10))
    # Papierkorb-Frist sicher ueberschritten (Trash-Expiry-Schritt).
    g.client.deleted_at = timezone.now() - timedelta(days=10)
    g.client.save(update_fields=["deleted_at"])

    call_command("enforce_retention", "--facility", g.facility.name)

    hits = [h for h in undeclared_hits(g.facility.id) if h.table == table]
    assert not hits, f"PII-Residue nach enforce_retention in {table}:\n{_fmt(hits)}"


@pytest.mark.django_db
def test_enforce_retention_acts_on_the_graph(maximal_pii_graph):
    """Positiv-Kontrolle: die Pipeline agiert real (Event soft-deletet, Client
    anonymisiert, Activities hart geloescht) — sonst waeren die Residue-Tests
    wertlos (gruen, weil nichts passiert)."""
    from datetime import timedelta

    from django.core.management import call_command

    from core.models import Activity, Event

    g = maximal_pii_graph
    _sharpen_retention(g.facility)
    g.doc_type.retention_days = 0
    g.doc_type.save(update_fields=["retention_days"])
    Event.objects.filter(pk=g.event.pk).update(occurred_at=timezone.now() - timedelta(days=10))
    g.client.deleted_at = timezone.now() - timedelta(days=10)
    g.client.save(update_fields=["deleted_at"])

    assert Activity.objects.filter(facility=g.facility).exists()  # vor dem Lauf

    call_command("enforce_retention", "--facility", g.facility.name)

    g.client.refresh_from_db()
    ev = Event.objects.get(pk=g.event.pk)
    # Event soft-deletet (data_json geleert) ...
    assert ev.is_deleted is True
    assert ev.data_json == {}
    # ... Client anonymisiert (Pseudonym-Marker) ...
    assert g.client.pseudonym.startswith("Gelöscht-")
    assert g.client.notes == ""
    # ... Activities komplett hart geloescht.
    assert not Activity.objects.filter(facility=g.facility).exists()


# --------------------------------------------------------------------------
# Pfad-Sweep: k_anonymize_client (Refs #1083)
# --------------------------------------------------------------------------
# ``k_anonymize_client`` ist BEWUSST client-only (Docstring
# k_anonymization.py:45 — „Linked cases/episodes/workitems are *not*
# modified"). Ein 22-Tabellen-Sweep waere der falsche Vertrag; geprueft wird
# gezielt die Client-Zeile selbst + die dokumentierte Nicht-Kaskade.


@pytest.mark.django_db
def test_client_record_clean_after_k_anonymization(maximal_pii_graph):
    """Die Client-Zeile selbst ist nach k-Anonymisierung needle-frei
    (pseudonym -> Hash-Bucket, notes -> "")."""
    from core.services.compliance.k_anonymization import k_anonymize_client

    g = maximal_pii_graph
    k_anonymize_client(g.client)
    hits = [h for h in undeclared_hits(g.facility.id) if h.table == "core_client"]
    assert not hits, f"PII-Residue in core_client nach k-Anonymisierung:\n{_fmt(hits)}"


@pytest.mark.django_db
def test_k_anonymization_does_not_cascade_to_cases(maximal_pii_graph):
    """k_anonymize_client kaskadiert NICHT (dokumentierte Grenze, k_anonymization.py:45).

    Festschreibung des AKTUELLEN Verhaltens: nach ``k_anonymize_client`` ALLEIN
    tragen ``core_case``/``core_episode``/``core_workitem`` weiterhin Needles —
    k-Anon redigiert nur die Client-Zeile (pseudonym/notes).

    Relevanz: wird k-Anon als Erfuellungs-Modus der Retention statt
    Hard-Anonymisierung genutzt (``Settings.retention_use_k_anonymization``),
    bleibt Fall-/Episoden-/Aufgaben-Freitext stehen — potenzielle DSGVO-Luecke
    (Befund "H6 — k-Anon kaskadiert nicht", #1094). Klassifikation/Fix entscheidet
    der Maintainer (Folge-Issue), nicht dieser Test.
    """
    from core.services.compliance.k_anonymization import k_anonymize_client

    g = maximal_pii_graph
    k_anonymize_client(g.client)
    residual = {h.table for h in undeclared_hits(g.facility.id)}
    # Bewusste Festschreibung der fehlenden Kaskade — alle drei verknuepften
    # Aggregate behalten ihren Klartext-Freitext:
    assert "core_case" in residual
    assert "core_episode" in residual
    assert "core_workitem" in residual
