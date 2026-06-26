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


# Jede Text-/JSON-Spalte der 22 facility-gescopten Tabellen (test_rls.EXPECTED_TABLES)
# wird hier genau einmal eingeordnet. Reihenfolge folgt SCOPED_TABLES. Bei Unsicherheit
# konservativ ``pii`` (sichere Richtung — der Sweep validiert das Redaktions-Ziel).
COLUMN_CLASSIFICATION: tuple[ColRule, ...] = (
    # ---- core_client -----------------------------------------------------
    pii("core_client", "pseudonym"),
    pii("core_client", "notes"),
    non_pii("core_client", "contact_stage", reason="Enum (identified/qualified), kein Freitext."),
    non_pii("core_client", "age_cluster", reason="Generalisierte Altersgruppe (Enum-Bucket), kein Identifikator."),
    # ---- core_event ------------------------------------------------------
    pii("core_event", "data_json"),
    pii("core_event", "search_text"),
    # ---- core_case -------------------------------------------------------
    pii("core_case", "title"),
    pii("core_case", "description"),
    non_pii("core_case", "status", reason="Enum (open/closed), kein Freitext."),
    # ---- core_workitem ---------------------------------------------------
    pii("core_workitem", "title"),
    pii("core_workitem", "description"),
    non_pii("core_workitem", "item_type", reason="Enum (hint/task), kein Freitext."),
    non_pii("core_workitem", "status", reason="Enum (open/in_progress/done/dismissed), kein Freitext."),
    non_pii("core_workitem", "priority", reason="Enum (normal/important/urgent), kein Freitext."),
    non_pii("core_workitem", "recurrence", reason="Enum (none/weekly/.../yearly), kein Freitext."),
    # ---- core_documenttype (Konfiguration: Dokumentationstyp-Schema) -----
    non_pii("core_documenttype", "name", reason="Admin-konfigurierter Typ-Name (Label), keine Klientendaten."),
    non_pii("core_documenttype", "category", reason="Enum (contact/service/admin/note)."),
    non_pii("core_documenttype", "sensitivity", reason="Enum (normal/elevated/high)."),
    non_pii("core_documenttype", "min_contact_stage", reason="Enum-Kontaktstufe (Marker), kein Freitext."),
    non_pii("core_documenttype", "system_type", reason="Stabile System-Kennung (Enum) fuer Bann/Export."),
    non_pii("core_documenttype", "icon", reason="UI-Icon-Name (Konfiguration)."),
    non_pii("core_documenttype", "color", reason="UI-Farbwert (Konfiguration)."),
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
    non_pii(
        "core_statisticssnapshot",
        "jugendamt_data",
        reason="Aggregierte Jugendamt-Zaehlungen (Kategorie-Summen), keine Einzelpersonen.",
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
