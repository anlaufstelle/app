"""Schluessel-Allowlist fuer den ``AuditLog.detail``-Payload (Refs #1944).

Warum ueberhaupt
================

Das AuditLog ist append-only (Trigger ``auditlog_immutable``, Migration 0024)
und HMAC-verkettet (Refs #1070) — nachtraegliche Redaktion ist strukturell
ausgeschlossen. **Minimierung beim Schreiben ist der einzige Hebel**; was nie
ins ``detail`` geschrieben wird, ueberlebt auch keine Anonymisierung
(Refs #1751, #1753).

Bis hierher war diese Minimierung reine Call-Site-Konvention: der
Architekturtest ``TestAuditLogCreationAllowlist`` prueft nur, **wo** Audit-
Zeilen entstehen duerfen, nie **was** in ihnen steht. Ein neuer Service-Aufruf
mit ``detail={"pseudonym": ...}`` lief durch kein Gate.

Warum Schluessel-Allowlist statt Werte-Blacklist
================================================

Eine Blacklist verdaechtiger Schluessel (``pseudonym``, ``notes``, ``email``,
``phone``) ist billig zu bauen, faengt aber genau den Fall **nicht**, der
wehtut: einen Freitextwert unter einem unverdaechtigen Schluessel
(``{"info": name}``, ``{"filename": ...}``). Eine Allowlist dreht die
Beweislast um — jeder neue Schluessel muss hier mit Begruendung eingetragen
werden, sonst schlaegt der Schreibvorgang im Entwicklungs- und CI-Lauf fehl.

Warum Laufzeit statt nur statischer AST-Guard
=============================================

Ein rein statischer Guard sieht nur literale ``detail={...}``-Dicts. Rund ein
Dutzend Schreibpunkte bauen ihr ``detail`` dynamisch auf (``**kwargs`` in
``core/admin/mixins.py``, Zaehler-Dicts in
``management/commands/export_facility.py``, ``**release_detail(...)`` in
``core/views/mixins.py``) — ausgerechnet dort waere die Kontrolle am
schwaechsten. Der statische Guard bleibt zusaetzlich bestehen
(``TestAuditDetailKeyInventory``), damit ein neuer literaler Schreibpunkt
schon ohne Testausfuehrung auffaellt.

Fail-Verhalten (bewusste Wahl)
==============================

**Strikt in Tests/DEBUG, meldend im Betrieb.** Ein unbekannter Schluessel
bricht den Schreibvorgang nur ab, wenn ``settings.AUDIT_DETAIL_STRICT`` gesetzt
ist (Default: ``DEBUG``; in ``settings/test.py`` hart an). Im Betrieb wird der
Verstoss auf ``ERROR`` gemeldet und die Zeile trotzdem geschrieben.

Begruendung: ein **fehlender** Audit-Eintrag ist schlimmer als ein zu breiter.
Das Log ist der Rechenschaftsnachweis (Art. 5 Abs. 2 DSGVO), und die
schaerfsten Schreibpunkte laufen genau dann, wenn ohnehin etwas schiefgeht
(``SECURITY_VIOLATION`` bei Virenfund, ``LOGIN_FAILED`` unter Angriff). Eine
Exception dort verwandelte einen Datenminimierungs-Mangel in einen
Nachweis-Ausfall — und bei ``audit_admin_view`` (dort faengt der Aufrufer
``Exception`` defensiv ab) sogar in ein stilles Loch. Umgekehrt ist der
Strikt-Modus in Tests/CI scharf genug: jeder neue Schreibpunkt wird von der
zugehoerigen Test-Abdeckung ausgefuehrt und faellt vor dem Deploy auf.

Die Meldung nennt **nur die Schluesselnamen**, nie die Werte — sonst truege sie
die PII in das Anwendungs-Log weiter, das genau diese Minimierung vermeiden
soll.

Fuer neue Schreibpunkte
=======================

Neue ``AuditLog.Action``-Werte bringen fast immer neue ``detail``-Schluessel
mit. Sie gehoeren hier hinein — mit Begruendung, warum der Wert keine
Klienten-PII traegt (oder warum der Freitext ausnahmsweise bleiben muss).
"""

from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger(__name__)


class UnknownAuditDetailKeyError(ValueError):
    """Ein ``detail``-Schluessel ist nicht in :data:`AUDIT_DETAIL_KEYS` deklariert."""


# ---------------------------------------------------------------------------
# Die Allowlist: Schluessel -> Begruendung, warum er PII-frei (oder bewusst
# freitextfuehrend) ist. Sortiert nach Bedeutung, nicht alphabetisch.
# ---------------------------------------------------------------------------

AUDIT_DETAIL_KEYS: dict[str, str] = {
    # -- Zeit- und Fristangaben ------------------------------------------
    "date_from": "Berichtszeitraum-Beginn eines Exports — Zeitangabe, kein Personenbezug",
    "date_to": "Berichtszeitraum-Ende eines Exports — Zeitangabe, kein Personenbezug",
    "occurred_at": "Ereigniszeitpunkt beim Loeschen eines Ereignisses — Zeitangabe",
    "deleted_at": "Zeitpunkt der Papierkorb-Verschiebung/Loeschung",
    "deletion_due_at": "faelliger Loeschzeitpunkt eines Retention-Vorschlags",
    "deferred_until": "Wiedervorlage eines zurueckgestellten Retention-Vorschlags",
    "expires_at": "Ablauf eines Legal Hold",
    # Refs #2065 (ADR-050 D2): Trifft ein Event-Hold auf eine bereits redigierte
    # Historie, wird er nicht abgelehnt, aber als verspaetet gekennzeichnet —
    # die Spoliationsfrage muss spaeter beantwortbar sein. Boolean.
    "history_already_redacted": "ob die Ereignis-Historie bei der Hold-Anlage schon redigiert war",
    "days": "Zahl der Tage einer Zurueckstellung",
    "retention_days": "konfigurierte Aufbewahrungsfrist der Kategorie",
    "window_minutes": "Zeitfenster der Breach-Detection-Regel",
    # -- Zaehler und Mengen ----------------------------------------------
    "count": "Anzahl betroffener Zeilen eines Bulk-/Cron-Laufs",
    "rows": "Zeilenzahl eines Imports",
    "created": "Zahl neu angelegter Zeilen eines Imports",
    "columns": "Spaltennamen der Importdatei — Schema, kein Inhalt",
    "remaining": "verbleibende MFA-Wiederherstellungscodes",
    "filter_count": "Zeilenzahl eines gefilterten AuditLog-Exports",
    "event_count": "Zahl der Ereignisse eines Offline-Bundles",
    "document_type_count": "Zahl der Dokumentationstypen eines Offline-Bundles",
    "defer_count": "wie oft ein Retention-Vorschlag bereits zurueckgestellt wurde",
    "max_defer_count": "konfiguriertes Maximum der Zurueckstellungen",
    "pruned_count": "Zahl der beim Audit-Pruning entfernten Zeilen",
    # Refs #2065 (ADR-050 D5.3): Nachweis je Charge des EventHistory-Sweeps.
    # Alles Mengen-/Zeit-/UUID-Angaben ueber die Charge selbst — keine
    # Zeileninhalte, kein Personenbezug. ``batch_first``/``batch_last`` sind die
    # Chargengrenzen in pk-Ordnung und machen die Charge in der Tabelle
    # wiederauffindbar, ohne ihren Inhalt zu nennen.
    "cutoff": "Fristgrenze eines Retention-Laufs — Zeitangabe",
    "deferred": "Zahl der wegen Legal Hold zurueckgestellten Ereignisse einer Charge",
    "skipped": "Zahl der wegen fremder Zeilensperre uebersprungenen Ereignisse einer Charge",
    "batch_first": "erste Historien-UUID einer Redaktions-Charge — Chargengrenze",
    "batch_last": "letzte Historien-UUID einer Redaktions-Charge — Chargengrenze",
    "redacted_history_rows": "Zaehler des Retention-Laufs (ADR-050)",
    "deleted_history_rows": "Zaehler des Retention-Laufs (ADR-050, Stufe 2)",
    "deferred_history_events": "Zaehler zurueckgestellter Ereignisse des Retention-Laufs (ADR-050)",
    "snapshots": "Zahl erzeugter Statistik-Snapshots",
    "findings": "Zahl der Breach-Detection-Treffer eines Laufs",
    "threshold": "Schwellwert der Breach-Detection-Regel",
    "k_threshold": "k-Anonymitaets-Schwelle des Berichts",
    "passkeys_removed": "Zahl der beim MFA-Reset entfernten Passkeys",
    "facilities": "Zahl der in einem Cron-Lauf verarbeiteten Einrichtungen",
    # Refs #1872: Ergebniszahlen des verify_audit_chain-Laufs. Beides sind reine
    # Mengenangaben ueber die Kette selbst — keine Zeileninhalte, kein Bezug auf
    # eine Person.
    "chains": "Zahl der geprueften Ketten eines verify_audit_chain-Laufs",
    "rows_checked": "Zahl der dabei geprueften AuditLog-Zeilen",
    "soft_deleted_events": "Zaehler des Retention-Laufs",
    "anonymized_clients": "Zaehler des Retention-Laufs",
    "anonymized_trash_clients": "Zaehler des Retention-Laufs",
    "anonymized_trash_cases": "Zaehler des Retention-Laufs (Refs #1818)",
    "hard_deleted_activities": "Zaehler des Retention-Laufs",
    "pruned_auditlog": "Zaehler des Retention-Laufs",
    "pruned_offline_revocations": "Zaehler des Retention-Laufs",
    "clients": "Entitaeten-Zaehler des Einrichtungs-Exports",
    "events": "Entitaeten-Zaehler des Einrichtungs-Exports",
    "cases": "Entitaeten-Zaehler des Einrichtungs-Exports",
    "episodes": "Entitaeten-Zaehler des Einrichtungs-Exports",
    "goals": "Entitaeten-Zaehler des Einrichtungs-Exports",
    "work_items": "Entitaeten-Zaehler des Einrichtungs-Exports",
    # -- Technische Referenzen (IDs, Hashes) -----------------------------
    "client_id": "Client-UUID statt Klartext-Pseudonym (Refs #1375) — aufloesbar nur solange die Zeile lebt",
    "event_id": "Ereignis-UUID (Alt-Schema der Anhang-Zeilen, Refs #1738)",
    "event_pk": "Ereignis-UUID eines Offline-Sync-Vorgangs",
    "credential_pk": "Passkey-Datensatz-UUID",
    "hold_id": "Legal-Hold-UUID",
    "case_id": "Fall-UUID — traegt den Falltitel bewusst nicht mit",
    # Refs #1959: Wird ein Ereignis von einem Fall auf einen anderen umgehaengt,
    # entsteht nur eine ``CASE_EVENT_ASSIGNED``-Zeile — der Abgang aus dem alten
    # Fall haette sonst keinen Beleg. UUID wie ``case_id``, kein Falltitel.
    "previous_case_id": "Fall-UUID des vorherigen Falls einer Ereignis-Zuordnung",
    "owner_type": "Modellname des Anhang-Eigentuemers (ADR-039 §5) — Schema, kein Inhalt",
    "owner_id": "UUID des Anhang-Eigentuemers (ADR-039 §5)",
    "attachment_id": "UUID der neu erzeugten Anhang-Version (Refs #1960)",
    "entry_id": "UUID der logischen Anhang-Versionskette (Refs #1960)",
    "target_client": "Client-UUID des Ziels einer Loesch-/Berichtigungs-Entscheidung",
    "target_event": "Ereignis-UUID des Ziels einer Loeschentscheidung",
    "target_case": "Fall-UUID des Ziels einer Loeschentscheidung (Refs #1818) — nie der Falltitel",
    "target_type": "Modellname des Import-/Audit-Ziels — Schema, kein Inhalt",
    "deletion_request": "UUID des Loeschantrags",
    # -- Duplikat-Merge (Refs #1876, ADR-045) ----------------------------
    # Die ``moved_*``-Listen sind zugleich das Undo-Inventar von
    # ``unmerge_client``: nur mit ihnen ist bestimmbar, welche Zeilen des
    # Ziels urspruenglich der Quelle gehoerten. UUIDs sind opake Bezeichner
    # ohne Personenbezug (dieselbe Einordnung wie ``target_client``).
    "merged_into": "Client-UUID des Merge-Ziels einer Zusammenfuehrung — UUID, kein Pseudonym",
    "merge_into": "Client-UUID des beantragten Merge-Ziels eines Vier-Augen-Antrags",
    "unmerged_from": "Client-UUID des bisherigen Merge-Ziels beim Aufheben einer Zusammenfuehrung",
    "moved_events": "Ereignis-UUIDs, die zum Merge-Ziel wanderten — UUIDs, keine Inhalte; Undo-Inventar",
    "moved_cases": "Fall-UUIDs, die zum Merge-Ziel wanderten — Undo-Inventar",
    "moved_workitems": "Aufgaben-UUIDs, die zum Merge-Ziel wanderten — Undo-Inventar",
    "moved_attachments": "Anhang-UUIDs uebernommener Datei-Felder — Undo-Inventar, keine Dateinamen",
    "adopted_slugs": (
        "Feld-Slugs, deren Werte aus der Merge-Quelle uebernommen wurden — Namen, keine Werte (Muster changed_fields)"
    ),
    "document_type_adopted": "ob das Merge-Ziel den Dokumentationstyp der Quelle uebernahm — Boolean",
    "target_stage_before": "Kontaktstufe des Merge-Ziels vor der Zusammenfuehrung — Enum-Wert (Undo-Inventar)",
    "actor_id": "UUID des handelnden Admins (Refs #1369)",
    "boundary_hash": "Ketten-Grenzhash des Audit-Prunings",
    "boundary_hashes": "Ketten-Grenzhashes des Audit-Prunings",
    "email_hash": "HMAC der E-Mail statt Klartext (Refs #1751)",
    "filename_hash": "HMAC des Dateinamens statt Klartext (Refs #1751)",
    "filename_ext": "gekappter Typmarker der Datei (Refs #1755) — kein Namensrest",
    # Refs #1874: ``ATTACHMENT_DELETE`` (ersatzloses Loeschen einer Anhang-Kette).
    # Reine Mengenangabe, wie viele Versionen der Kette physisch entfernt wurden —
    # kein Bezug auf Inhalt oder Person. Der Dateiname steht wie bei den
    # File-Vault-Rejects nur als ``filename_hash``/``filename_ext`` drin.
    "versions": "Zahl der physisch entfernten Versionen einer Anhang-Kette (Refs #1874)",
    "file_size": "Groesse eines erfolgreich gespeicherten Anhangs in Bytes (Refs #1960)",
    "mime": "verifizierter MIME-Typ eines Anhangs — technischer Typmarker (Refs #1960)",
    # Refs #2129: ``ATTACHMENT_QUOTA_EXCEEDED`` (Upload am Speicherkontingent der
    # Einrichtung gescheitert). Drei reine Mengen-/Konfigurationsangaben ueber die
    # Einrichtung als Ganzes — kein Bezug auf eine Person, kein Dateiname (der
    # steht hier bewusst nicht einmal als Hash, siehe ``audit_attachment_quota_exceeded``).
    "used_mb": "belegter Speicher der Einrichtung in MB — Mengenangabe (Refs #2129)",
    "limit_mb": "geltendes Speicherkontingent der Einrichtung in MB — Konfiguration (Refs #2129)",
    "requested_mb": "Groesse des abgewiesenen Uploads in MB (Refs #2129)",
    "source_ip": "IP-Adresse eines Breach-Detection-Treffers — Sicherheits-Forensik",
    # -- File-Vault-Rejects (``_reject`` in services/file_vault/policy.py) ---
    # Der Klartext-Dateiname wird von ``log_attachment_violation`` zentral in
    # ``filename_hash``/``filename_ext`` umgewandelt und erreicht das ``detail``
    # nie; was hier steht, ist ausschliesslich der technische Tatbestand.
    "size": "Groesse des abgewiesenen Uploads in Bytes",
    "max_bytes": "geltende Groessengrenze in Bytes — Konfiguration",
    "pixels": "Pixelzahl eines abgewiesenen Bildes",
    "max_pixels": "geltende Pixelgrenze — Konfiguration",
    "entries": "Zahl der Eintraege eines abgewiesenen Archivs",
    "max_entries": "geltende Eintragsgrenze — Konfiguration",
    "compressed": "komprimierte Groesse eines abgewiesenen Archivs in Bytes",
    "uncompressed": "entpackte Groesse eines abgewiesenen Archivs in Bytes",
    "ratio": "Expansionsverhaeltnis eines abgewiesenen Archivs",
    "max_ratio": "geltende Expansionsgrenze — Konfiguration",
    "extension": "Endung der abgewiesenen Datei (nur die Endung, nicht der Name)",
    "allowed": "erlaubte Endungen bzw. MIME-Typen — Konfiguration der Einrichtung",
    "declared": "vom Browser gemeldeter MIME-Typ",
    "detected": "per libmagic erkannter MIME-Typ",
    "signature": "Virensignatur des Scanner-Treffers — kein Dateibezug",
    "error": (
        "Fehlermeldung eines nicht erreichbaren Virenscanners (Verbindungsfehler). "
        "Technische Diagnose; sie stammt aus der Scanner-Anbindung, nicht aus Nutzereingaben. "
        "Ohne sie ist ein Fail-Closed-Ausfall im Betrieb nicht auswertbar."
    ),
    # -- Konfigurations- und Schemabezeichner -----------------------------
    "document_type": "Name des Dokumentationstyps — Konfiguration der Einrichtung, keine Klienten-PII",
    "document_type_id": "UUID des Dokumentationstyp-Filters eines Exports",
    "field": "Slug der Feldvorlage eines Anhangs — Schema, kein Inhalt",
    "field_path": "Feldpfad eines Berichtigungsantrags — Schema, kein Inhalt",
    "target_slug": "Slug des Zielfelds einer Feldbedingung (Refs #1849 E18)",
    "source_slug": "Slug des Quellfelds einer Feldbedingung (Refs #1849 E18)",
    "operator": "Vergleichsoperator einer Feldbedingung",
    "group_index": "Gruppenindex einer Feldbedingung",
    "schema_version": "Schemaversion eines Offline-Bundles",
    "view": "Name der aktualisierten Datenbank-Sicht",
    "backend": "Slug der Berichts-Pipeline (Refs #1754) — Identitaet der Herausgabe",
    "format": "Ausgabeformat eines Exports (CSV/PDF/JSON)",
    # -- Zustands- und Verlaufsangaben ------------------------------------
    "changed_fields": "Liste geaenderter Feldnamen — Namen, keine Werte",
    "changes": "Alt/Neu-Werte NUR value-sicherer Settings-Felder (Refs #1445, _AUDIT_VALUE_FIELDS)",
    "old_stage": "Vorheriger Vertraulichkeitsgrad einer Person — Enum-Wert",
    "new_stage": "Neuer Vertraulichkeitsgrad einer Person — Enum-Wert",
    "old_role": "Vorherige Rolle eines Kontos — Enum-Wert",
    "new_role": "Neue Rolle eines Kontos — Enum-Wert",
    "old_value": "Vorheriger Wert einer booleschen Zugangs- oder Loeschbestaetigungs-Einstellung",
    "new_value": "Neuer Wert einer booleschen Zugangs- oder Loeschbestaetigungs-Einstellung",
    "from": "Vorheriger Status eines Arbeitsauftrags — Enum-Wert",
    "to": "Neuer Status eines Arbeitsauftrags — Enum-Wert",
    "bulk": "Kennzeichen einer Sammelaenderung",
    "is_anonymous": "Kennzeichen eines personenlosen Ereignisses",
    "retention_category": "Aufbewahrungskategorie eines Vorschlags — Enum-Wert",
    "category": "semantische Differenzierung einer Retention-Entscheidung (Refs #901) — Enum-Wert",
    # -- Ablauf- und Herkunftskennzeichen ---------------------------------
    "event": "Unterart eines Auth-/MFA-Vorgangs — feste Slugs",
    "mode": "MFA-Verfahren oder Anhang-Modus (add/replace) — feste Slugs",
    "flow": "Zweig der Lockout-Wiederherstellung — feste Slugs",
    "factor": "Zweiter Faktor der Sudo-Bestaetigung — feste Slugs",
    "next": "Kennzeichen des Folgeschritts im Sudo-Flow",
    "code": "Fehlerschluessel eines WebAuthn-Vorgangs — feste Slugs",
    "command": "Name des ausloesenden Management-Commands",
    "operation": "Art einer Konfigurations-Umsortierung — feste Slugs",
    "trigger": "Ausloeser eines Loesch-/Entsperr-Vorgangs — feste Slugs",
    "unlocked_by": "Art der Lockout-Aufhebung — feste Slugs",
    "kind": "Art eines Breach-Detection-Treffers — feste Slugs",
    "admin_view": "Django-Admin-Sicht des protokollierten Lesezugriffs (Refs #1341) — feste Slugs",
    "source": "Herkunft einer Ereignis-Loeschung — feste Slugs",
    "document": "Slug des abgerufenen DSGVO-Dokuments — feste Liste",
    "filters": "gesetzte Filter eines AuditLog-Exports (Aktion, User-/Facility-ID, Datumsgrenzen) — kein Freitextfeld",
    "artifact_released": "Kennzeichen, dass ein Artefakt das Haus verlassen hat (Refs #1770)",
    "system": "Kennzeichen eines nicht request-getriebenen Vorgangs (Refs #1369)",
    "reason": (
        "Grund eines Sicherheitsverstosses/Legal Holds, Ausloeser eines "
        "Credential-Widerrufs (Refs #1965: password_change/password_reset/invite_setup) "
        "und Grund einer Kontingent-Ablehnung (Refs #2129: quota_exceeded) "
        "— feste Slugs"
    ),
    # -- Bewusst zugelassener Freitext ------------------------------------
    # Diese Schluessel tragen echten Freitext. Sie zu verbieten waere falsch —
    # sie unkommentiert zu erlauben auch. Deshalb je eine Begruendung, warum
    # der Wert **keine Klienten**-PII ist bzw. warum er bleiben muss.
    "name": (
        "Name eines Dokumentationstyps, einer Feldvorlage oder eines DSGVO-Dokuments. "
        "Das ist Konfiguration der Einrichtung, keine Klienten-PII — und ohne ihn waere "
        "eine CONFIG_DELETE-Zeile nicht mehr zuordenbar, weil das Ziel dann nicht mehr existiert."
    ),
    "username": (
        "Kontokennung bei fehlgeschlagenem Login und bei Rollenwechsel/Deaktivierung. "
        "Beschaeftigten-, keine Klientendaten; bei Angriffen existiert das Konto gar nicht "
        "(AuditLog.user ist dann NULL), der Wert ist der einzige Nachweis, was versucht wurde (Refs #1751)."
    ),
    "actor_username": (
        "Kennung des handelnden Admins. Wegen AuditLog.actor = SET_NULL die einzige dauerhafte "
        "Zuschreibung einer Rechte-Eskalation (Refs #1369) — Beschaeftigten-, keine Klientendaten."
    ),
    "note": (
        "Freie Betriebsnotiz zum Wartungsmodus bzw. zum Restore-Test. Von der Anwendungsbetreuung "
        "selbst eingegeben, ueber einen Klientenbezug gibt es dort keinen Anlass; die Minimierung "
        "ist hier Betreiber-Obliegenheit (Admin-Handbuch), keine technische Zusicherung."
    ),
    "recipient": (
        "Empfaenger einer Berichts-Freigabe (Refs #1770). Organisation, nicht Person des Falls; "
        "der Wert ist der Zweck der Freigabe-Uebersicht. Freitext-Minimierung ist Betreiber-Obliegenheit "
        "(dokumentiert in docs/security-notes.md § Audit-Log-Restdaten)."
    ),
    "purpose": (
        "Zweck einer Berichts-Freigabe (Refs #1770). Wie recipient: fuer die Rechenschaft ueber die "
        "Herausgabe unverzichtbar, Minimierung ist Betreiber-Obliegenheit."
    ),
    "message": (
        "Feste deutsche Klartextmeldung der Login-Signale ('Fehlgeschlagener Login-Versuch', "
        "'Login blockiert durch Account-Lockout'). Konstante aus dem Code, kein Nutzereingabe-Kanal."
    ),
}


def _strict() -> bool:
    """Bricht ein Verstoss den Schreibvorgang ab?

    Default ist ``DEBUG`` — Entwicklung und CI laut, Betrieb meldend. Ein
    Betreiber kann per ``DJANGO_AUDIT_DETAIL_STRICT`` scharf stellen.
    """
    return bool(getattr(settings, "AUDIT_DETAIL_STRICT", False))


def validate_audit_detail(detail, *, source: str):
    """Prueft die obersten ``detail``-Schluessel gegen :data:`AUDIT_DETAIL_KEYS`.

    Gibt ``detail`` unveraendert zurueck, damit der Aufruf inline um das
    Payload gelegt werden kann (``detail=validate_audit_detail({...},
    source=...)``) und die Schreibstelle selbst nicht verschoben wird.

    ``source`` benennt den Schreibpunkt fuer die Fehlermeldung — bei den
    generischen Helpern ist der Aufrufer sonst nicht ablesbar.

    Die Pruefung bleibt bewusst **flach**: verschachtelte Ebenen (``changes``,
    ``filters``) tragen Modellfeld- bzw. Filternamen, die dort eigenen
    Allowlists unterliegen.
    """
    if not detail:
        return detail

    unknown = sorted(str(key) for key in detail if key not in AUDIT_DETAIL_KEYS)
    if not unknown:
        return detail

    # Nur die Schluesselnamen — niemals die Werte. Sonst truege ausgerechnet
    # die Datenminimierungs-Meldung die PII ins Anwendungs-Log weiter.
    meldung = (
        f"Nicht deklarierte AuditLog-detail-Schluessel aus {source}: {unknown}. "
        "Jeder detail-Schluessel muss in core.services.audit.detail_policy.AUDIT_DETAIL_KEYS "
        "mit Begruendung stehen (Datenminimierung im append-only Log, Refs #1944). "
        "Klienten-Freitext gehoert nicht hinein — statt Klartext einen HMAC "
        "(hmac_hash_value) oder die target_id verwenden."
    )
    if _strict():
        raise UnknownAuditDetailKeyError(meldung)
    logger.error(meldung)
    return detail
