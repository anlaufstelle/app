# Codebasis- und Refactoring-Audit: Anlaufstelle

Datum: 2026-05-14
Scope: aktuelle lokale Codebasis unter `/work/anlaufstelle`
Basis: statische Codeinspektion, gezielte Datei-/Strukturanalyse, keine Codeänderungen, kein vollständiger Testlauf
Hinweis: `rg` war in der Umgebung nicht verfügbar; Analyse erfolgte mit `find`, `grep`, `git ls-files`, `wc`, `nl` und gezielten Dateilesungen.

## 1. Executive Summary

Die Codebasis ist für ein vorproduktives Django-Fachsystem ungewöhnlich reif. Positiv fallen vor allem die klare Domänenorientierung, die vorhandene Service-Layer-Strategie, starke Architekturtests, viele Datenschutz-/Security-Guards und eine breite Testsuite auf. Die größten Probleme sind keine fehlende Grundarchitektur, sondern gewachsene Module, verstreute Audit- und Policy-Logik, einige konkrete Performance-/Compliance-Risiken und operative Lücken im Self-Hosting-Setup.

Gesamtbewertung: **7.5/10**

| Bereich | Bewertung | Kurzurteil |
|---|---:|---|
| Architektur | 8/10 | Gute Richtung mit CBVs, Services, Mixins und Architekturtests; einzelne Module sind zu groß. |
| Datenschutz/Security | 8/10 | Viele Schutzmechanismen vorhanden; Audit-/Settings-/Deployment-Pfade brauchen Vereinheitlichung. |
| Wartbarkeit | 6.5/10 | Fachlich sauber, aber mehrere God-Module und verstreute Regeln erhöhen Änderungskosten. |
| Tests | 8.5/10 | Sehr breite Testbasis und wertvolle Architekturtests; gezielte Regressionstests für Findings fehlen. |
| Frontend | 6.5/10 | Funktional und CSP-bewusst; Alpine-/Base-Template-Struktur ist gewachsen. |
| Betrieb/Deployment | 7/10 | Dev-Setup wirkt robust; Prod-Compose und Self-Hosting-Hardening sollten nachziehen. |
| Produkt-Fit | 7.5/10 | Domäne gut getroffen; ein Einrichtungs-/Konfigurationsassistent würde viel Komplexität entschärfen. |

Wichtigste Empfehlung: **Nicht breit refactoren, sondern gezielt konsolidieren.** Die Codebasis hat bereits tragfähige Leitplanken. Der nächste Qualitätsgewinn entsteht durch das Schließen konkreter Inkonsistenzen und durch das Zerlegen weniger zentraler Sammelmodule.

## 2. Kontext und Metriken

Aktueller Stand:

- Projektversion laut `pyproject.toml`: `0.12.0`.
- Python-Zielversion: `>=3.13`.
- Requirements verlangen `Django>=6.0.5,<6.1`; die README nennt noch `Django 5.1+` (`README.md:190`) und ist damit veraltet.
- Getrackte Python-Dateien: 468.
- Testdateien unter `src/tests`: 190.
- Nicht-Test-/Nicht-Migrations-Python: ca. 22k LOC.
- Tests: ca. 40k LOC.
- Größte produktive Python-Dateien:
 - `src/core/views/system.py` mit 877 LOC
 - `src/core/admin.py` mit 481 LOC
 - `src/core/seed/constants.py` mit 476 LOC
 - `src/core/views/events.py` mit 436 LOC
 - `src/core/retention/proposals.py` mit 425 LOC
 - `src/core/models/document_type.py` mit 399 LOC
 - `src/core/services/file_vault.py` mit 398 LOC
 - `src/core/services/clients.py` mit 376 LOC
- Größte Frontend-Datei: `src/static/js/alpine-components.js` mit 759 LOC.
- Lokal liegen viele ignorierte Artefakte (`src/staticfiles`, `src/media`, `__pycache__`). `find. -name __pycache__ -type d` fand 697 Verzeichnisse. Das ist kein Codequalitätsproblem an sich, erschwert aber lokale Analyse und Tooling.

## 3. Stärken, die erhalten bleiben sollten

### 3.1 Architekturtests als Sicherheitsnetz

`src/tests/test_architecture.py` ist ein großer Pluspunkt. Die Tests schützen Architekturregeln wie:

- keine unkontrollierten Event-Zugriffe in Views,
- keine Event-Verschlüsselungs-Bypasses,
- keine Inline-Skripte in Templates,
- keine unerwünschten Abhängigkeiten zwischen Retention und Views,
- keine unkontrollierten Service-Imports in Models.

Diese Tests sollten bei jedem Refactoring aktiv erweitert werden. Sie sind der richtige Ort, um neue Architekturentscheidungen dauerhaft abzusichern.

### 3.2 Service-Layer-Richtung

Die ADR zu CBVs und Service Layer ist in der Codebasis sichtbar umgesetzt. Businesslogik wurde bereits in Services verschoben, z. B. Events, Clients, WorkItems, Retention, File Vault und Settings. Das ist die richtige Richtung.

Wichtig: Die Service-Schicht sollte nicht neu erfunden werden. Sinnvoll ist eine **Konsolidierung bestehender Services**, nicht ein zweiter Architekturstil.

### 3.3 Datenschutz- und Security-Bewusstsein

Die Codebasis enthält viele bewusst gesetzte Schutzmechanismen:

- Sensitivity-Prüfungen für Dokumenttypen/Felder.
- Verschlüsselung für sensible Event-Felder und Dateien.
- File-Vault mit Extension-, MIME- und Virenscan-Prüfung.
- RLS-/Facility-Scoping als Defense-in-Depth.
- Retention-/Legal-Hold-/Audit-Mechaniken.
- CSP-bewusste Frontend-Organisation.

Diese Bereiche sind fachlich wertvoll. Refactorings müssen hier klein, testgetrieben und rückwärtskompatibel erfolgen.

## 4. Muss-Fixes und konkrete Findings

###: Settings-Audit deckt nicht alle relevanten Settings ab

Priorität: **P1**
Bereich: Compliance, Audit, Settings
Fundstellen: `src/core/services/settings.py:13-24`, `src/core/models/settings.py:55-135`

`_AUDIT_FIELDS` enthält aktuell:

- `facility_full_name`
- `default_document_type`
- `session_timeout_minutes`
- Retention-Basisfristen
- Datei-Upload-Policy

Im Settings-Modell existieren weitere sicherheits- und compliance-relevante Felder, die nicht in `_AUDIT_FIELDS` enthalten sind:

- `client_trash_days`
- `auditlog_retention_months`
- `mfa_enforced_facility_wide`
- `retention_auto_approve_after_defer`
- `retention_max_defer_count`
- `retention_use_k_anonymization`
- `k_anonymity_threshold`
- `search_trigram_threshold`

Risiko: Änderungen an Retention-, MFA-, Such- und Anonymisierungsverhalten können ohne Audit-Diff bleiben. In einem DSGVO-orientierten Fachsystem ist das ein relevanter Nachvollziehbarkeitsmangel.

Empfehlung:

- `_AUDIT_FIELDS` auf alle nichttechnischen, verhaltensrelevanten Settings erweitern.
- Test hinzufügen, der sicherstellt, dass jedes relevante Feld aus `Settings` entweder auditiert oder bewusst ausgeschlossen ist.
- Ausschlüsse explizit dokumentieren, z. B. `updated_at` und `facility`.

Geschätzter Aufwand: **S**

###: Audit-Logging ist trotz zentralem Helper verstreut

Priorität: **P1**
Bereich: Audit, Services, Views
Fundstellen: `src/core/services/audit.py`, zahlreiche direkte `AuditLog.objects.create(...)`

Es gibt einen zentralen Helper in `src/core/services/audit.py`, aber die Analyse fand 51 direkte `AuditLog.objects.create(...)`-Stellen außerhalb von `__pycache__`. Beispiele liegen in:

- `src/core/services/clients.py`
- `src/core/services/workitems.py`
- `src/core/services/settings.py`
- `src/core/services/events/crud.py`
- `src/core/retention/*`
- `src/core/views/system.py`
- `src/core/views/mfa.py`
- `src/core/signals/audit.py`

Nicht jeder direkte Aufruf ist falsch. Einige Low-Level-Signale oder Spezialpfade brauchen bewusst direkten Zugriff. Das Problem ist die fehlende einheitliche Audit-Payload-Policy.

Risiko:

- uneinheitliche `detail`-Schemas,
- schwerere spätere Auswertung,
- höhere Chance, dass neue Audit-Ereignisse PII oder unvollständige Kontextdaten enthalten,
- mehr Aufwand bei Compliance-Änderungen.

Empfehlung:

- Keine große Event-Sourcing-Abstraktion einführen.
- Stattdessen kleine, typisierte Audit-Helper je Domäne:
 - `audit_settings_changed(...)`
 - `audit_client_event(...)`
 - `audit_retention_decision(...)`
 - `audit_security_violation(...)`
 - `audit_system_view(...)`
- Bestehende Spezialpfade schrittweise migrieren.
- Architekturtest ergänzen, der neue direkte `AuditLog.objects.create(...)` nur in einer Allowlist erlaubt.

Geschätzter Aufwand: **M**

###: `system.py` ist ein zu großes Mixed-Concern-Modul

Priorität: **P1/P2**
Bereich: Views, Systemadministration
Fundstelle: `src/core/views/system.py`

`src/core/views/system.py` umfasst 877 Zeilen und bündelt sehr unterschiedliche Verantwortlichkeiten:

- System-Dashboard
- Cross-Facility-Auditlog
- Auditlog-Export
- Lockout-Verwaltung
- Maintenance-Flag
- Retention-Übersicht
- VVT
- Legal Holds
- System-Audit-Mixin

Die Datei ist nicht chaotisch, aber zu breit. Besonders kritisch ist, dass installationweiter Zugriff, Super-Admin-Logging, Exporte und Retention-Operationen nebeneinander liegen.

Empfehlung:

- Datei nach fachlichen Submodulen aufteilen:
 - `src/core/views/system/dashboard.py`
 - `src/core/views/system/audit.py`
 - `src/core/views/system/lockouts.py`
 - `src/core/views/system/maintenance.py`
 - `src/core/views/system/retention.py`
 - `src/core/views/system/legal_holds.py`
- `SystemAuditMixin` in ein eigenes Modul verschieben.
- URL-Namen und Templates unverändert lassen.
- Vorher Regressionstests für Super-Admin-Zugriff und Audit-Eintrag ergänzen oder bestehende Tests gezielt absichern.

Geschätzter Aufwand: **M**

###: Mögliches N+1 im Attachment-Kontext

Priorität: **P1/P2**
Bereich: Event-Edit-Kontext, Performance
Fundstelle: `src/core/services/events/context.py:104-138`

`build_attachment_context(event)` iteriert über File-Marker in `event.data_json` und ruft pro Attachment-Marker:

```python
att = event.attachments.filter(pk=entry["id"]).first()
```

Das erzeugt bei mehreren Datei-Feldern oder Multi-Uploads unnötige Queries. In `build_event_detail_context` existiert bereits ein besseres Muster: alle Attachments laden und per Dict nach PK mappen (`src/core/services/events/context.py:174-176`).

Empfehlung:

- In `build_attachment_context` einmalig `event.attachments.all` laden.
- `attachments_by_pk = {att.pk: att for att in...}` verwenden.
- Query-Count-Test für ein Event mit mehreren File-Markern ergänzen.

Geschätzter Aufwand: **S**

###: Produktions-DB-Rollenmodell ist nicht so klar gehärtet wie Dev

Priorität: **P1**
Bereich: Deployment, PostgreSQL, Self-Hosting
Fundstellen: `docker-compose.dev.yml:13-29`, `deploy/postgres-init/01-app-role.sh`, `docker-compose.prod.yml:1-24`

Das Dev-Deployment legt Bootstrap-, App- und Admin-DB-Rollen explizit an. Die App-Rolle ist `NOSUPERUSER`, die Admin-Rolle besitzt gezielt `BYPASSRLS`.

`docker-compose.prod.yml` nutzt dagegen direkt `env_file:.env` am offiziellen Postgres-Container. Beim offiziellen Postgres-Image ist der initiale `POSTGRES_USER` typischerweise eine Superuser-Rolle. Das muss nicht bedeuten, dass die App in Produktion unsicher läuft; aber die Konfiguration macht den sicheren Pfad nicht eindeutig und nicht automatisch.

Risiko:

- Self-Hosting-Installationen können mit zu privilegiertem App-DB-User laufen.
- RLS- und Trigger-Bypass-Annahmen werden schwer prüfbar.
- Abweichungen zwischen Dev-/Prod-Topologie erhöhen Betriebsrisiko.

Empfehlung:

- Prod-Compose an das Dev-Rollenmodell angleichen.
- Init-Script auch für Produktion verwenden oder ein eigenes Prod-Init-Script bereitstellen.
- Healthcheck ergänzen, der App-Rolle, `NOSUPERUSER`, `NOBYPASSRLS` und erwartete Admin-Rolle prüft.
- Dokumentation und `.env.example` auf diese Rollen hin ausrichten.

Geschätzter Aufwand: **M**

###: FieldTemplate-, Default- und Form-Logik ist verteilt

Priorität: **P2**
Bereich: Dynamic Forms, DocumentType, Validierung
Fundstellen: `src/core/models/document_type.py:236-360`, `src/core/forms/events.py:112-240`

Die Feldtyp-Logik ist über Model und Form verteilt:

- Default-Validierung in `FieldTemplate._validate_default_value`.
- Default-Casting in `FieldTemplate.get_default_initial`.
- Form-Field-Zuordnung in `DynamicEventDataForm.FIELD_TYPE_MAP`.
- Select-/Multi-Select-Sonderlogik in `DynamicEventDataForm`.
- Datei-Validierung zusätzlich in Form und Service.

Das ist aktuell beherrschbar, wird aber bei jedem neuen Feldtyp teuer.

Empfehlung:

- Eine kleine Field-Type-Registry einführen, keine große Schema-Engine.
- Pro Feldtyp zentral definieren:
 - Django-Form-Field
 - Widget
 - Default-Parsing
 - Default-Validierung
 - Anzeige-/Normalisierungslogik
- Datei-Security weiterhin im Service lassen; die Form darf nur UX-Validierung liefern.

Geschätzter Aufwand: **M**

###: `file_vault.py` ist fachlich kohärent, aber zu breit

Priorität: **P2**
Bereich: File Upload, Security, Storage
Fundstelle: `src/core/services/file_vault.py`

`file_vault.py` enthält:

- Orphan-Cleanup
- ClamAV-Anbindung
- Security-Violation-Audit
- Extension-Allowlist
- MIME-Äquivalenzen
- Magic-Byte-Prüfung
- Verschlüsseltes Speichern
- Attachment-Versionierung
- Soft-/Hard-Delete-Operationen

Das ist kein akuter Fehler. Die Datei ist aber ein guter Kandidat für spätere Aufteilung, sobald File-Funktionen weiter wachsen.

Empfehlung:

- Kurzfristig nicht refactoren, solange keine konkrete Änderung ansteht.
- Bei nächster File-Vault-Erweiterung aufteilen in:
 - `file_policy.py` für Extension/MIME/Scan-Policy
 - `file_storage.py` für Verschlüsselung und Disk-I/O
 - `file_audit.py` oder zentrale Audit-Helper für Security-Violations
 - `file_cleanup.py` für Orphans und Delete-Operationen

Geschätzter Aufwand: **M**, aber nur bei Anlass durchführen.

###: Client-Anonymisierung ist eine God-Funktion

Priorität: **P2**
Bereich: DSGVO, Client-Aggregate
Fundstelle: `src/core/services/clients.py:23-96`

`anonymize_client` ist richtigerweise aus dem Model in den Service-Layer gewandert, enthält aber viele Aggregat-Schritte:

- Client-Felder redigieren
- Cases redigieren
- Episodes redigieren
- WorkItems redigieren
- EventAttachments löschen
- EventHistory mit Trigger-Bypass redigieren
- DeletionRequests redigieren

Das ist fachlich eine Transaktion, aber als eine Funktion schwer zu erweitern und schwer isoliert zu testen.

Empfehlung:

- Public API `anonymize_client(client, user=None)` beibehalten.
- Interne Schritte extrahieren:
 - `_redact_client_identity`
 - `_redact_cases_and_episodes`
 - `_redact_workitems`
 - `_delete_event_attachments`
 - `_redact_event_history`
 - `_redact_deletion_requests`
- Tests je Schritt ergänzen, aber weiterhin eine End-to-End-Anonymisierung testen.

Geschätzter Aufwand: **S/M**

###: WorkItem-Statuslogik ist doppelt vorhanden

Priorität: **P2**
Bereich: WorkItems, Statusübergänge
Fundstellen: `src/core/services/workitems.py:123-183`, `src/core/services/workitems.py:250-287`

`update_workitem_status` und `bulk_update_workitem_status` verwalten beide:

- Statuswechsel
- `completed_at`
- Wiederkehrende Folgeaufgaben bei `DONE`
- Idempotenz
- Logging

Die Single-Update-Variante ist stärker abgesichert (`select_for_update`, klarer alter Status, Activity-Log). Die Bulk-Variante dupliziert relevante Statusregeln.

Empfehlung:

- Eine interne Transition-Funktion extrahieren:
 - berechnet Statusänderung,
 - setzt `completed_at`,
 - entscheidet über Recurrence-Duplizierung,
 - liefert geänderte Felder zurück.
- Single- und Bulk-Pfad nutzen dieselbe Transition-Logik.
- Bulk-Pfad muss weiter performant und idempotent bleiben.

Geschätzter Aufwand: **S/M**

###: Frontend-Sammeldateien sind gewachsen

Priorität: **P2/P3**
Bereich: Frontend, Alpine, Templates
Fundstellen: `src/static/js/alpine-components.js`, `src/templates/base.html`

`alpine-components.js` ist mit 759 LOC ein Sammelpunkt für mehrere unabhängige UI-Komponenten. `base.html` enthält viel Navigation, Icon-Markup und Rollenlogik direkt im Template.

Risiko:

- Änderungen an kleinen UI-Komponenten führen zu großen Diffs.
- Komponenten sind schwer isoliert zu testen.
- Navigation und Rollenanzeige sind schwer zu überblicken.

Empfehlung:

- Alpine-Registrierungsmuster beibehalten, aber Komponenten featureweise splitten.
- Navigation in eine kleine Template-Partial- oder Python-Konfiguration auslagern.
- Inline-SVG-Wiederholungen nach Möglichkeit durch vorhandene Icon-Partials oder dedizierte Komponenten ersetzen.

Geschätzter Aufwand: **M**, niedrige fachliche Dringlichkeit.

###: Dokumentation driftet punktuell vom Code weg

Priorität: **P2/P3**
Bereich: Docs, Onboarding
Fundstelle: `README.md:190`, `requirements.in:1`

Die README nennt `Django 5.1+`, während `requirements.in` `Django>=6.0.5,<6.1` verlangt. Solche kleinen Drifts sind für ein Self-Hosting-Projekt relevant, weil Betreiber oft README und Compose-Dateien zuerst lesen.

Empfehlung:

- README-Tech-Stack aktualisieren.
- In CI optional einen sehr kleinen Docs-Consistency-Test für zentrale Versionsangaben einführen.

Geschätzter Aufwand: **XS**

###: Lokale generierte Artefakte erschweren Analyse

Priorität: **P3**
Bereich: Repo-Hygiene, Developer Experience
Fundstellen: lokale Verzeichnisse `src/staticfiles`, `src/media`, viele `__pycache__`

Die Dateien sind ignoriert und nicht getrackt. Trotzdem führen sie bei einfachen Suchbefehlen zu Rauschen, z. B. Binary-Matches aus `__pycache__`.

Empfehlung:

- `make clean` oder `scripts/clean-generated` bereitstellen.
- Dokumentieren, welche generierten Verzeichnisse lokal gefahrlos gelöscht werden können.
- Suchbefehle in Docs mit `--exclude-dir=__pycache__` oder `git grep` empfehlen.

Geschätzter Aufwand: **XS**

## 5. Überkomplexe Bereiche und Vereinfachungspotenzial

### 5.1 Nicht jede Wiederholung sofort abstrahieren

Es gibt ca. 98 `request.current_facility`-Referenzen. `FacilityScopedViewMixin` existiert bereits (`src/core/views/mixins.py:66-79`), und der Kommentar im Code nennt eine systematische Migration selbst kosmetisch.

Empfehlung: **Nicht als eigenes Refactoring-Projekt starten.** Neue Views sollten das Mixin verwenden; bestehende Views nur anfassen, wenn sie ohnehin geändert werden.

### 5.2 Retention ist fachlich komplex, aber die Modularisierung ist richtig

Retention-Code wurde bereits in ein Subpackage ausgelagert. Das ist besser als ein einzelner Riesendienst. Hier sollte man nicht zurück in eine zentrale Service-Datei refactoren.

Sinnvoll ist nur:

- gemeinsame Audit-Helper,
- klare Strategy-/Policy-Grenzen,
- Tests für Sonderfälle wie Legal Hold, Defer-Limits und Auto-Approve.

### 5.3 Admin- und Systembereich sauber trennen

Der Systembereich ist installationsweit und Super-Admin-bezogen. Facility-Admin-Flows sind ein anderer Kontext. Diese Trennung ist richtig, aber im Code noch nicht durchgehend strukturell sichtbar, weil `system.py` sehr breit ist.

Empfehlung: Super-Admin-Systemviews als eigene Sub-App/Modulgruppe behandeln, ohne die bestehenden URLs nach außen zu ändern.

### 5.4 Dynamic Document Types brauchen eine kleine interne Plattform

Das dynamische Dokumentationsmodell ist ein Kernfeature. Je mehr Feldtypen, Exporte, Offline-Sync, Retention und Anzeigevarianten hinzukommen, desto teurer wird verstreute Feldtyp-Logik.

Empfehlung: Kein generisches JSON-Schema-Framework einführen. Eine kleine lokale Registry reicht.

## 6. Was bewusst nicht priorisiert werden sollte

Folgende Dinge wirken zwar unordentlich oder repetitiv, sind aber aktuell nicht die besten Hebel:

- Pauschale Migration aller `request.current_facility`-Zugriffe auf `self.facility`.
- Große Umbenennung von Services ohne fachliche Änderung.
- Vollständige Neuarchitektur des Audit-Systems.
- Austausch von HTMX/Alpine.
- Breites Template-Refactoring ohne konkreten UI-Anlass.
- Retention-Subsystem wieder zusammenziehen.
- File-Vault splitten, solange keine neue File-Funktion ansteht.

Die Codebasis profitiert stärker von gezielten, testbaren Refactorings als von großen kosmetischen Aufräumrunden.

## 7. Sinnvolle neue Funktionen

### NF-001: Einrichtungs- und Dokumentationsassistent

Priorität: **hoch**

Die wichtigste fehlende Funktion ist ein geführter Setup-Assistent für Einrichtungen. Viele Komplexitäten der Codebasis entstehen aus konfigurierbaren Dokumenttypen, Feldvorlagen, Rollen, Retention-Regeln und Datenschutzoptionen. Aktuell hängt viel davon an Seed-Daten und Django-Admin-/Systembereichen.

Vorschlag:

- Wizard für neue Einrichtung:
 - Grunddaten
 - Rollen/Benutzer
 - Standard-Dokumentationstypen
 - Feldvorlagen
 - Retention-Defaults
 - Datei-Upload-Policy
 - MFA-Empfehlungen
- Template-Bibliothek für typische soziale Einrichtungen.
- Preview, welche Felder verschlüsselt sind und welche Rollen welche Inhalte sehen.

Nutzen:

- weniger Admin-Fehler,
- weniger Support,
- bessere Produktreife für Self-Hosting,
- weniger Bedarf, Fachkonfiguration direkt im Django Admin zu ändern.

### NF-002: Datenschutz-Review für Freitextfelder

Priorität: **hoch/mittel**

Das System erlaubt weiterhin unverschlüsselte Freitextfelder wie Notizen/Beschreibungen in bestimmten Bereichen. Das ist fachlich oft nötig, aber riskant.

Vorschlag:

- Review-Ansicht für potenziell sensible Inhalte in unverschlüsselten Freitextfeldern.
- Keine automatische KI-Entscheidung als Wahrheit; nur Hinweise und Workflows.
- Empfehlungen:
 - Inhalt in verschlüsselte Dokumentationsfelder verschieben,
 - Feldtyp/Sensitivity erhöhen,
 - Pseudonymisierung verbessern,
 - Eintrag zur Review-Wiedervorlage markieren.

Nutzen:

- reduziert Art.-9-/Klarnamen-Risiko,
- stärkt Datenschutzposition,
- passt sehr gut zur Domäne.

### NF-003: Betriebsbereitschafts-Dashboard für Self-Hosting

Priorität: **mittel**

Ein Teil der Risiken liegt im Betrieb: DB-Rollen, Backups, ClamAV, Retention-Cron, Restore-Fähigkeit, Versionen.

Vorschlag:

- System-Dashboard um konkrete Betriebschecks erweitern:
 - App-DB-Rolle ist `NOSUPERUSER`
 - App-DB-Rolle hat kein `BYPASSRLS`
 - Admin-/Maintenance-Rolle vorhanden
 - letzter erfolgreicher Backup-Zeitpunkt
 - Restore-Test dokumentiert
 - ClamAV aktuell
 - Retention-Job zuletzt gelaufen
 - App-Version entspricht Image-Tag

Nutzen:

- reduziert Self-Hosting-Fehlkonfiguration,
- macht Security-Anspruch überprüfbar,
- entlastet Betreiber.

### NF-004: Datenschutzfreundliche externe Berichte

Priorität: **mittel/niedrig**

Für Fördermittelgeber, Träger oder-ähnliche Kontexte kann ein Exportmodus sinnvoll sein, der aggregiert, k-anonymisiert und ohne Pseudonym-Rankings arbeitet.

Vorschlag:

- Berichtsvorlagen mit explizitem Datenschutzprofil.
- Schwellenwerte für kleine Gruppen.
- Kein Export von Top-Clients/Pseudonymen in externen Berichten.
- Klare Trennung zwischen interner Statistik und externer Veröffentlichung.

## 8. Empfohlene Umsetzungsreihenfolge

###: Kleine Risiken mit hoher Wirkung

1. Settings-Audit vollständig machen.
2. README-Tech-Stack aktualisieren.
3. N+1 in `build_attachment_context` beheben.
4. Query-Count- und Settings-Audit-Regressionstests ergänzen.

Erwarteter Aufwand: **1-2 Tage**

###: Struktur ohne Verhaltensänderung

1. `system.py` fachlich aufteilen.
2. URL-Namen, Templates und Permissions unverändert lassen.
3. Bestehende System-View-Tests laufen lassen und ggf. gezielt erweitern.
4. `anonymize_client` intern in Schritte zerlegen.

Erwarteter Aufwand: **2-4 Tage**

###: Audit- und Policy-Konsolidierung

1. Kleine domänenspezifische Audit-Helper einführen.
2. Neue direkte `AuditLog.objects.create(...)` per Architekturtest begrenzen.
3. Field-Type-Registry entwerfen und schrittweise einführen.
4. WorkItem-Statusübergänge zentralisieren.

Erwarteter Aufwand: **4-7 Tage**

###: Betrieb und Produktreife

1. Prod-Compose auf explizites DB-Rollenmodell bringen.
2. Betriebschecks im Systembereich ergänzen.
3. Einrichtungs-/Konfigurationsassistent konzipieren.
4. Datenschutz-Review für Freitextfelder als neues Produktfeature planen.

Erwarteter Aufwand: **1-3 Wochen**, abhängig vom gewünschten Funktionsumfang.

## 9. Akzeptanzkriterien für die nächsten Refactorings

Für jedes Refactoring sollte gelten:

- Keine Änderung der bestehenden URLs ohne bewusste Entscheidung.
- Keine Änderung von Permission-Semantik ohne explizite Tests.
- Keine Reduktion der Audit-Informationen.
- Keine neuen direkten Event-Query-Pfade ohne `visible_to`/Facility-Scope-Prüfung.
- Keine neue Feldtyp-Logik außerhalb der geplanten Registry.
- Bei Security-/DSGVO-Pfaden zuerst Regressionstest, dann Codeänderung.
- Architekturtests werden erweitert, wenn eine neue Regel eingeführt wird.

## 10. Zusammenfassung der wichtigsten Maßnahmen

Kurzfristig:

- Settings-Audit vervollständigen.
- Attachment-Kontext performanter machen.
- README-Version korrigieren.
- Generated-Artifact-Cleanup dokumentieren.

Mittelfristig:

- `system.py` splitten.
- Audit-Helper vereinheitlichen.
- Client-Anonymisierung und WorkItem-Statuslogik intern vereinfachen.
- Field-Type-Registry einführen.

Langfristig:

- Prod-DB-Rollenmodell und Betriebschecks härten.
- Einrichtungsassistent bauen.
- Datenschutz-Review für Freitextfelder ergänzen.

Die Codebasis muss nicht neu gebaut werden. Sie braucht gezielte Konsolidierung an den Stellen, an denen Fachlogik, Auditpflicht und Betriebsannahmen aktuell noch zu breit oder zu verteilt sind.
