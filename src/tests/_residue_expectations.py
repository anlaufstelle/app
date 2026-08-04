"""Deklarative Soll-Quelle fuer den PII-Residue-Sweep (Refs #1083).

Jede Freitext-/JSON-Spalte jeder facility-gescopten Tabelle wird genau einmal
klassifiziert. Das Completeness-Gate in test_pii_residue_sweep.py erzwingt, dass
keine Spalte unklassifiziert bleibt — ein neues Feld reisst den Test, bis eine
Einordnung getroffen ist (analog zu _authz_expectations.py, Refs #1055).
"""

from dataclasses import dataclass

# Single Source of Truth fuer die Tabellenmenge — kein zweiter Drift-Punkt.
from tests.test_rls import EXPECTED_TABLES as SCOPED_TABLES

# SCOPED_TABLES wird bewusst re-exportiert (vom Gate-Test importiert), daher
# in __all__ — sonst meldet Ruff F401 fuer den scheinbar ungenutzten Import.
__all__ = ["COLUMN_CLASSIFICATION", "NEEDLE_PREFIX", "SCOPED_TABLES", "ColRule"]

# Eindeutiger Praefix aller geseedeten PII-Sentinels.
NEEDLE_PREFIX = "RESIDUEPROBE-"


@dataclass(frozen=True)
class ColRule:
    table: str
    column: str
    kind: str  # "pii" | "non_pii" | "known_residue"
    reason: str = ""
    issue: str = ""  # Pflicht bei kind=known_residue mit reason "pending_fix:..."


def pii(table: str, column: str) -> ColRule:
    return ColRule(table, column, "pii")


def non_pii(table: str, column: str, reason: str) -> ColRule:
    return ColRule(table, column, "non_pii", reason=reason)


def known_residue(table: str, column: str, reason: str, issue: str = "") -> ColRule:
    return ColRule(table, column, "known_residue", reason=reason, issue=issue)


# Jede Text-/JSON-Spalte der facility-gescopten Tabellen (test_rls.EXPECTED_TABLES)
# wird hier genau einmal eingeordnet. Reihenfolge folgt SCOPED_TABLES. Bei Unsicherheit
# konservativ ``pii`` (sichere Richtung — der Sweep validiert das Redaktions-Ziel).
COLUMN_CLASSIFICATION: tuple[ColRule, ...] = (
    # ---- core_client -----------------------------------------------------
    pii("core_client", "pseudonym"),
    pii("core_client", "notes"),
    non_pii("core_client", "contact_stage", reason="Enum (identified/qualified), kein Freitext."),
    non_pii("core_client", "age_cluster", reason="Generalisierte Altersgruppe (Enum-Bucket), kein Identifikator."),
    # M2 P4b (Refs #1058): dynamische Personen-Felder — exakt dieselbe Form wie
    # ``core_event.data_json`` (freie Feldwerte je DocumentType-Slug), daher
    # dieselbe Einordnung. Residue-Erwartung nach Anonymisierung: ``{}`` (siehe
    # ``_redact_client_identity`` und ``k_anonymize_client``).
    pii("core_client", "data_json"),
    # ``core_client.document_type_id`` ist eine UUID-Spalte und faellt damit
    # NICHT unter das Completeness-Gate (``TEXTY_TYPES`` in
    # test_pii_residue_sweep.py deckt nur Text-/JSON-Spalten ab) — ein Eintrag
    # hier wuerde ``test_classification_targets_real_columns`` als „veraltet"
    # reissen. Gleiche Lage wie ``core_event.document_type_id``, das aus
    # demselben Grund unklassifiziert ist. Fachlich: reiner Konfig-FK ohne
    # Klientenbezug; er wird bei der Anonymisierung trotzdem auf NULL gesetzt,
    # damit der PROTECT-Verweis den Typ nicht dauerhaft blockiert.
    # ---- core_event ------------------------------------------------------
    pii("core_event", "data_json"),
    pii("core_event", "search_text"),
    non_pii(
        "core_event",
        "idempotency_key",
        reason="Client-generierter Zufalls-Dedup-Key (Replay, Review R5/R6), Format-Whitelist, kein Freitext.",
    ),
    # ---- core_case -------------------------------------------------------
    pii("core_case", "title"),
    pii("core_case", "description"),
    non_pii("core_case", "status", reason="Enum (open/closed), kein Freitext."),
    # M2 P5 (Refs #1058): dynamische Fall-Felder — exakt dieselbe Form wie
    # ``core_client.data_json``/``core_event.data_json`` (freie Feldwerte je
    # DocumentType-Slug), daher dieselbe Einordnung. Residue-Erwartung nach
    # Anonymisierung: ``{}`` (siehe ``_redact_cases_and_episodes``).
    pii("core_case", "data_json"),
    # ``core_case.document_type_id`` ist eine UUID-Spalte und faellt damit
    # NICHT unter das Completeness-Gate (``TEXTY_TYPES`` in
    # test_pii_residue_sweep.py deckt nur Text-/JSON-Spalten ab) — analog zu
    # ``core_client.document_type_id``/``core_event.document_type_id``.
    # Fachlich: reiner Konfig-FK ohne Klientenbezug; er wird bei der
    # Anonymisierung trotzdem auf NULL gesetzt, damit der PROTECT-Verweis den
    # Typ nicht dauerhaft blockiert.
    # ---- core_workitem ---------------------------------------------------
    pii("core_workitem", "title"),
    pii("core_workitem", "description"),
    non_pii("core_workitem", "item_type", reason="Enum (hint/task), kein Freitext."),
    non_pii("core_workitem", "status", reason="Enum (open/in_progress/done/dismissed), kein Freitext."),
    non_pii("core_workitem", "priority", reason="Enum (normal/important/urgent), kein Freitext."),
    non_pii("core_workitem", "recurrence", reason="Enum (none/weekly/.../yearly), kein Freitext."),
    non_pii(
        "core_workitem",
        "idempotency_key",
        reason="Client-generierter Zufalls-Dedup-Key (Replay, Review R5/R6), Format-Whitelist, kein Freitext.",
    ),
    # ---- core_documenttype (Konfiguration: Dokumentationstyp-Schema) -----
    non_pii("core_documenttype", "name", reason="Admin-konfigurierter Typ-Name (Label), keine Klientendaten."),
    non_pii("core_documenttype", "category", reason="Enum (contact/service/admin/note)."),
    non_pii("core_documenttype", "sensitivity", reason="Enum (normal/elevated/high)."),
    non_pii("core_documenttype", "min_contact_stage", reason="Enum-Kontaktstufe (Marker), kein Freitext."),
    non_pii("core_documenttype", "system_type", reason="Stabile System-Kennung (Enum) fuer Bann/Export."),
    non_pii("core_documenttype", "icon", reason="UI-Icon-Name (Konfiguration)."),
    non_pii("core_documenttype", "color", reason="UI-Farbwert (Konfiguration)."),
    non_pii(
        "core_documenttype",
        "slug",
        reason="Aus dem Typ-Namen abgeleiteter stabiler Identifier (M2-Paket-Lookup-Key), keine Klientendaten.",
    ),
    non_pii("core_documenttype", "entity_type", reason="Enum (event/client/case)."),
    non_pii("core_documenttype", "description", reason="Admin-Beschreibung des Typs (Konfig-Label)."),
    # ---- core_fieldtemplate (Konfiguration: Feldvorlagen-Schema) ---------
    non_pii("core_fieldtemplate", "name", reason="Admin-konfigurierter Feld-Name (Label)."),
    non_pii("core_fieldtemplate", "slug", reason="Stabiler Feld-Identifier (data_json-Key), kein Freitext."),
    non_pii("core_fieldtemplate", "field_type", reason="Enum (text/number/file/...)."),
    non_pii("core_fieldtemplate", "sensitivity", reason="Enum-Sensitivitaet (erbt vom Typ), kein Freitext."),
    non_pii(
        "core_fieldtemplate",
        "default_value",
        reason="Admin-konfigurierter Form-Vorbelegungswert (Schema-Default), keine Klientendaten.",
    ),
    non_pii("core_fieldtemplate", "options_json", reason="Select/Multi-Select-Optionsdefinitionen (Schema-Konfig)."),
    non_pii("core_fieldtemplate", "statistics_category", reason="Statistik-Zuordnungs-Label (Konfiguration)."),
    non_pii("core_fieldtemplate", "help_text", reason="Admin-Hilfetext fuers Formular (Konfig-Label)."),
    # ---- core_fieldgroup (M0, Refs #1056: Konfiguration: Feldgruppen-Schema) -
    non_pii("core_fieldgroup", "name", reason="Admin-konfigurierter Gruppen-Name (Label)."),
    non_pii("core_fieldgroup", "slug", reason="Stabiler Gruppen-Identifier (M2-Paketformat-Key), kein Freitext."),
    # ---- core_fieldcondition (M1, Refs #1057: Konfiguration: Sichtbarkeitsregeln) -
    non_pii("core_fieldcondition", "operator", reason="Enum (equals/not_equals/contains/...), kein Freitext."),
    non_pii(
        "core_fieldcondition",
        "value",
        reason="Admin-Vergleichswert der Sichtbarkeitsregel (Konfig, analog default_value), keine Klientendaten.",
    ),
    # ---- core_auditlog ---------------------------------------------------
    # Refs #1093: Klienten-PII (Pseudonym/reason) wird write-time minimiert —
    # gar nicht erst ins detail geschrieben, statt im append-only-Log
    # nachtraeglich redigiert (was den Immutable-Trigger braeche). detail ist
    # daher 'pii' (scharfer Regressionswaechter), nicht mehr 'known_residue'.
    # LOGIN_FAILED.username bleibt bewusst als Sicherheits-Forensik (Art. 5(2)
    # + berechtigtes Interesse), traegt aber keinen Klienten-Sentinel und
    # faellt im Klienten-Sweep daher nicht an.
    pii("core_auditlog", "detail"),
    non_pii("core_auditlog", "action", reason="Enum (login/export/client_create/...)."),
    non_pii("core_auditlog", "target_type", reason="Modell-Typ-Marker (z.B. 'Client'), kein Freitext."),
    non_pii("core_auditlog", "target_id", reason="UUID/PK-String des Ziels, kein Freitext."),
    non_pii("core_auditlog", "prev_hash", reason="HMAC-Hash der Vorgaengerzeile (Kette #1070), kein Freitext."),
    non_pii("core_auditlog", "entry_hash", reason="HMAC-Hash der Zeile (Kette #1070), kein Freitext."),
    # ---- core_activity ---------------------------------------------------
    pii("core_activity", "summary"),
    non_pii("core_activity", "verb", reason="Enum (created/updated/deleted/...)."),
    # ---- core_deletionrequest --------------------------------------------
    pii("core_deletionrequest", "reason"),
    non_pii("core_deletionrequest", "status", reason="Enum (pending/approved/rejected)."),
    non_pii("core_deletionrequest", "target_type", reason="Enum (Event/Client), Typ-Marker."),
    # ---- core_retentionproposal ------------------------------------------
    # details traegt das Klienten-Pseudonym (build_proposal_details, proposals.py).
    pii("core_retentionproposal", "details"),
    non_pii(
        "core_retentionproposal",
        "retention_category",
        reason="Retention-Bucket (anonymous/identified/qualified/document_type), Marker.",
    ),
    non_pii("core_retentionproposal", "status", reason="Enum (pending/approved/held/deferred/rejected)."),
    non_pii("core_retentionproposal", "target_type", reason="Enum (Event), Typ-Marker."),
    # ---- core_settings (Einrichtungs-Konfiguration, kein Klientenbezug) --
    non_pii("core_settings", "facility_full_name", reason="Name der Einrichtung (Konfiguration), keine Klientendaten."),
    non_pii("core_settings", "allowed_file_types", reason="Kommagetrennte Dateiendungen (Konfiguration)."),
    # ---- core_timefilter -------------------------------------------------
    non_pii("core_timefilter", "label", reason="Schicht-Bezeichnung (z.B. 'Frühschicht'), Konfiguration."),
    # ---- core_legalhold --------------------------------------------------
    # reason ist freie Begruendung — kann Klientenbezug tragen → konservativ pii.
    pii("core_legalhold", "reason"),
    non_pii("core_legalhold", "target_type", reason="Modell-Typ-Marker, kein Freitext."),
    # ---- core_statisticssnapshot (aggregierte Zaehlungen) ----------------
    non_pii(
        "core_statisticssnapshot",
        "data",
        reason="Aggregierte Monats-Zaehlungen (by_document_type/by_age_cluster counts), keine Einzelpersonen.",
    ),
    # M5 P3 (Refs #1061): backend-unabhaengige Map ``slug -> Roh-Aggregat``,
    # seit #1740 Nachfolger des entfallenen Sachbericht-Sonderfelds. Die
    # Backends liefern per ``DatasetResult.summary`` ausschliesslich
    # Aggregate (Kategorie-Summen, keine Einzelpersonen). Diese pauschale
    # Einordnung traegt nur, solange der Vertrag am Attribut ``snapshot_enabled``
    # (``core/reporting/base.py``: aggregatrein, keine Pseudonyme/Personenzeilen/
    # Freitexte) eingehalten wird — wer ihn aufweicht, muss diesen Eintrag mit
    # aendern.
    non_pii(
        "core_statisticssnapshot",
        "backend_data",
        reason="Aggregierte Reporting-Zaehlungen je Backend-Slug, keine Einzelpersonen.",
    ),
    # ---- core_quicktemplate ----------------------------------------------
    non_pii("core_quicktemplate", "name", reason="Anzeigename der Vorlage (z.B. 'Beratung 30 Min'), Konfiguration."),
    # prefilled_data traegt freie Feldwerte (slug→Wert) → konservativ pii.
    pii("core_quicktemplate", "prefilled_data"),
    # ---- core_eventhistory (append-only Aenderungslog) -------------------
    pii("core_eventhistory", "data_before"),
    pii("core_eventhistory", "data_after"),
    pii("core_eventhistory", "field_metadata"),
    non_pii("core_eventhistory", "action", reason="Enum (create/update/delete), kein Freitext."),
    # ---- core_eventattachment --------------------------------------------
    pii("core_eventattachment", "original_filename_encrypted"),
    non_pii(
        "core_eventattachment", "storage_filename", reason="UUID-basierter Disk-Dateiname (abc.enc), kein Klartext."
    ),
    non_pii("core_eventattachment", "mime_type", reason="MIME-Typ (z.B. application/pdf), kein Freitext."),
    non_pii(
        "core_eventattachment",
        "detected_mime",
        reason="Verifizierter MIME-Typ (libmagic, z.B. application/pdf), kein Freitext (Refs #1274).",
    ),
    # ---- core_episode ----------------------------------------------------
    pii("core_episode", "title"),
    pii("core_episode", "description"),
    # ---- core_outcomegoal ------------------------------------------------
    pii("core_outcomegoal", "title"),
    pii("core_outcomegoal", "description"),
    # ---- core_milestone --------------------------------------------------
    pii("core_milestone", "title"),
    # ---- core_documenttypefield ------------------------------------------
    # Keine Text-/JSON-Spalten (nur UUID/FK/Integer sort_order) — daher kein Eintrag.
)
