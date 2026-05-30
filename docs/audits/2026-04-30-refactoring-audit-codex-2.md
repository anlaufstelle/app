# Refactoring-Audit: Anlaufstelle

Datum: 2026-04-30 
Scope: lokale Codebasis unter `/work/anlaufstelle`; keine Produktivcode-Aenderungen. 
Hinweis: `rg` war in der Umgebung nicht verfuegbar, daher wurden `find`, `grep`, `wc` und gezielte Dateilesungen genutzt.

## 1. Executive Summary

Anlaufstelle ist fuer ein kleines Django-Projekt bereits auffallend sicherheitsbewusst strukturiert: Facility-Scoping ist in Middleware und Manager-Schicht verankert (`src/core/middleware/facility_scope.py:36-55`, `src/core/models/managers.py`), PostgreSQL-RLS ist als zweite Verteidigungslinie dokumentiert und migriert (`src/core/migrations/0047_postgres_rls_setup.py:21-91`, `0057_rls_quicktemplate.py:18-23`, `0063_rls_outcome_milestone_dtf.py:18-51`), Sensitivitaetsregeln sind zentral modelliert (`src/core/services/sensitivity.py:14-69`), Feldverschluesselung ist in `Event.save` verankert (`src/core/models/event.py:86-112`) und die Testbasis ist breit.

Die groessten Risiken liegen nicht in fehlenden Grundkonzepten, sondern in Randpfaden und Betriebsdetails:

- P0/P1: Anonymisierung und Auditlog-Pruning verwenden privilegierte PostgreSQL-Mechanismen (`src/core/models/client.py:182-191`, `src/core/services/retention.py:845-854`), waehrend die Architektur einen `NOSUPERUSER`-DB-User verlangt (`docs/adr/005-facility-scoping-and-rls.md:17-32`). Ohne Laufzeittest mit Produktionsrolle ist nicht abschliessend bewertbar, ob diese DSGVO-kritischen Pfade in Produktion funktionieren.
- P1: Der invalid-POST-Pfad der Event-Erstellung rendert dynamische Felder eines gespooften Dokumentationstyps ohne `user_can_see_document_type` und ohne `remove_restricted_fields` (`src/core/views/events.py:174-201`), waehrend der HTMX-Pfad korrekt prueft (`src/core/views/events.py:259-269`).
- P1: Der CSV-Export-Service filtert Feldwerte nach Rolle, aber nicht die Event-Query selbst per `visible_to(user)` (`src/core/services/export.py:21-32`, `158-180`). Die aktuelle View ist Lead/Admin-only (`src/core/views/statistics.py:165-180`), aber der Service ist als wiederverwendbarer Datenschutzpfad zu schwach abgesichert.
- P1: Der DSGVO-Dokumentenservice liest Vorlagen aus `docs/dsgvo-templates` (`src/core/services/dsgvo_package.py:11`, `48-49`), das Docker-Image kopiert aber nur `src/` (`Dockerfile:34-38`) und `.dockerignore` schliesst `docs/` aus (`.dockerignore:10`). Im gebauten Image ist der Downloadpfad damit wahrscheinlich defekt.

Nicht unnoetig anfassen: RLS-Grundstruktur, Sensitivity-Service, Event-Encryption-Pipeline, File-Vault-Pruefungen, CSP-Grundlinie und die bestehenden Architekturtests. Diese Bereiche sind fachlich wichtig und bereits absichtlich dokumentiert oder getestet.

## 2. Architekturueberblick

| Bereich | Zweck | Bewertung | Risiko | Refactoring-Bedarf |
|---|---|---|---|---|
| `src/anlaufstelle/settings/` | Settings fuer dev/prod/test/e2e | Prod fail-closed fuer Secrets und Hosts (`prod.py:37-44`, `94-101`) | falsche Settings-Auswahl bleibt Betriebsrisiko | P2, Doku/Healthcheck statt Umbau |
| `src/core/models/` | Facility, User, Client, Event, DocumentType, Case, WorkItem, Audit, Retention | sauber nach Dateien gesplittet; Manager/RLS-Konzept erkennbar | einzelne Modelle enthalten zu viel Domainlogik (`Client.anonymize`) | P1/P2 |
| `src/core/services/` | Export, Event-Erstellung, Retention, Suche, Offline, File Vault, Audit | richtige Richtung: Businesslogik aus Views herausgezogen | einige Services sind sehr gross oder haben mehrere Verantwortlichkeiten | P1/P2 |
| `src/core/views/` | CBV/Funktionsviews, HTMX-Partial-Endpunkte | Rollen-Mixins und zentrale Event-Loader vorhanden | Event-Create-View und Statistik/Export enthalten Randpfadlogik | P1/P2 |
| `src/core/forms/` | dynamische Event-Forms, Validierung | serverseitige Validierung vorhanden | dynamisches JSONB-Schema ueber Form/Model/Service verteilt | P2 |
| `src/templates/` | Full Pages und Partials | viele Partials vorhanden, CSP-Architekturtests | grosse Templates (`base.html` 361 LOC, `events/detail.html` 226 LOC) | P2/P3 |
| `src/static/js/` | HTMX/Offline/Autosave/Filter | Offline-Autosave faellt nicht auf Plaintext zurueck (`autosave.js:168-178`) | `sessionStorage` speichert Filter inkl. Pseudonym-Suche (`filter-persistence.js:36-64`, `templates/core/clients/list.html:19-31`) | P2 |
| `src/tests/` | Unit, Integration, RLS, E2E | sehr breite Testbasis, Architekturtests vorhanden | Randpfade fuer Export-Service, Admin-Gate und invalid POST fehlen | P1/P2 |
| Docker/Compose/Scripts | Self-Hosting, Backup, Restore, Startup | non-root Image, Healthcheck, ClamAV, Runbook | DSGVO-Vorlagen im Image fehlen wahrscheinlich; privilegierte DB-Pfade pruefen | P1 |
| `docs/` | README, ADRs, Runbook, Security Notes | umfangreich und domänennah | einzelne Aussagen passen nicht exakt zum Code/Admin-Pfad | P2 |

Zentrale Domänenkonzepte:

- Mandant: `Organization`/`Facility`, `request.current_facility`, `FacilityScopedManager`, PostgreSQL-RLS.
- Personenbezug: `Client` mit Pseudonym, Kontaktstufe, Alterscluster und Notizen (`src/core/models/client.py:40-63`).
- Dokumentation: `Event` plus dynamisches `data_json` (`src/core/models/event.py:62-63`) und konfigurierbare `DocumentType`/`FieldTemplate` (`src/core/models/document_type.py:18-145`, `225-335`).
- Sensitivitaet: Rollenmaximum und Feld-/Dokumenttyp-Sensitivitaet zentral in `src/core/services/sensitivity.py:14-69`.
- Datenschutzpfade: Anonymisierung, Retention, Export, Audit, Offline-Bundle, File Vault.

Erkennbare Architekturprinzipien:

- Defense-in-Depth: ORM-Facility-Filter plus RLS (`docs/adr/005-facility-scoping-and-rls.md:15-32`).
- Service Layer statt reiner Fat Views, aber noch nicht konsequent.
- Soft-Delete/Retention statt sofortiger physischer Loeschung fuer Events.
- Append-only-Historie/Audit mit Ausnahmen fuer DSGVO-Redaktion.
- HTMX fuer Teilaktualisierungen, mit ersten Mixins (`src/core/views/mixins.py:45-70`).

## 3. Wichtigste Refactoring-Kandidaten

### RF-001: EventCreate invalid POST kann eingeschraenkte Feld-Metadaten rendern

- Prioritaet: P1
- Bereich: Views, Forms, Sensitivity
- Fundstellen: `src/core/views/events.py:174-201`; Gegenbeispiel korrekt: `src/core/views/events.py:259-269`
- Problem: Wenn `EventMetaForm` invalid ist, wird `DocumentType.objects.get(pk=..., facility=..., is_active=True)` verwendet und danach `DynamicEventDataForm` gebaut. Es fehlt die Sensitivity-Pruefung `user_can_see_document_type` und das Entfernen eingeschraenkter Felder per `remove_restricted_fields`.
- Risiko: Niedrigere Rollen koennen durch manipuliertes POST-`document_type` Feldnamen/Labels/Help-Texte eines hoeher sensitiven Dokumentationstyps sehen. Das ist kein bestaetigter Datenwert-Leak, aber ein Metadaten-Leak in einem besonders sensiblen System.
- Empfehlung: denselben Guard wie im HTMX-Partial verwenden; bei fehlender Berechtigung `PermissionDenied` oder leeres `DynamicEventDataForm`; anschliessend immer `remove_restricted_fields`.
- Geschaetzter Aufwand: S
- Vorher notwendige Tests: Assistant postet invalides Formular mit HIGH-DocumentType; Response darf keine Feldlabels/Help-Texte des HIGH-Typs enthalten.
- Moegliche Nebenwirkungen: Validierungsfehlerseite kann weniger Felder anzeigen; Templates muessen leeres `data_form` weiter verarbeiten.
- Sichere Umsetzungsschritte: Charakterisierungstest; Guard einfuegen; Regressionstest fuer normalen invalid POST mit erlaubtem DocumentType.

### RF-002: CSV-Export-Service filtert Felder, aber nicht Events nach Rollen-Sichtbarkeit

- Prioritaet: P1
- Bereich: Exportlogik, Sensitivity
- Fundstellen: `_get_events_queryset` in `src/core/services/export.py:21-32`; `export_events_csv` in `src/core/services/export.py:158-180`; aktuelle View ist Lead/Admin-only in `src/core/views/statistics.py:165-180`.
- Problem: Der Service exportiert alle Events einer Facility im Zeitraum und laesst erst pro Feld `user_can_see_field` laufen (`src/core/services/export.py:133-145`). Wenn der Service spaeter fuer Staff/Assistant wiederverwendet wird, bleiben Zeilen hoeher sensitiver Dokumentationstypen sichtbar.
- Risiko: Zukunfts-/Wiederverwendungsrisiko mit Datenschutzwirkung; Dokumentationstypname, Datum und Pseudonym koennten fuer nicht berechtigte Rollen sichtbar werden.
- Empfehlung: Export-Service muss selbst mit `Event.objects.visible_to(user)` oder einem Export-Policy-Objekt arbeiten, nicht nur die View. Lead/Admin koennen weiterhin alles sehen.
- Geschaetzter Aufwand: S/M
- Vorher notwendige Tests: Service-Test mit Staff/Assistant und HIGH-Event; CSV darf keine HIGH-Zeile enthalten.
- Moegliche Nebenwirkungen: Falls bisher Systemexports ohne User bewusst alle Events brauchten, muss `user=None` explizit als Systemmodus dokumentiert und nur intern genutzt werden.
- Sichere Umsetzungsschritte: Test; `_get_events_queryset(..., user=None)` erweitern; View unveraendert lassen; Management-/Service-Callsites pruefen.

### RF-003: DSGVO-Dokumentenvorlagen fehlen wahrscheinlich im Docker-Image

- Prioritaet: P1
- Bereich: Deployment, DSGVO-Paket
- Fundstellen: `src/core/services/dsgvo_package.py:11`, `48-49`; `src/core/views/dsgvo.py:27-50`; `Dockerfile:34-38`; `.dockerignore:10`.
- Problem: Lokal zeigt `TEMPLATE_DIR` auf `<repo>/docs/dsgvo-templates`. Im Runtime-Image wird nur `src/` nach `/app` kopiert; `docs/` wird vom Build-Kontext ausgeschlossen. Der Service wird im Image daher voraussichtlich `FileNotFoundError` werfen.
- Risiko: DSGVO-Paketdownload faellt in produktionsnahen Deployments aus; fuer Self-Hosting und Datenschutzdokumentation ist das ein sichtbarer Betriebsfehler.
- Empfehlung: Vorlagen in ein App-Package verschieben, z.B. `src/core/dsgvo_templates/`, und via `importlib.resources` oder Django-Template-Loader laden. Alternativ `docs/dsgvo-templates` explizit ins Image kopieren und Pfad robust berechnen.
- Geschaetzter Aufwand: S/M
- Vorher notwendige Tests: Docker- oder zumindest `collectstatic`/Runtime-Test, der `render_document` in einem Image-Kontext ausfuehrt.
- Moegliche Nebenwirkungen: Pfade in Tests/Management-Command `generate_dsgvo_package` anpassen.
- Sichere Umsetzungsschritte: Test, Paketpfad einfuehren, alte Tests aktualisieren, Image-Build in CI pruefen.

### RF-004: DSGVO-kritische Redaktionspfade nutzen privilegierte PostgreSQL-Operationen

- Prioritaet: P0, falls mit Produktionsrolle reproduzierbar; bis zur Verifikation mindestens P1
- Bereich: Anonymisierung, Retention, RLS-Betrieb
- Fundstellen: `Client.anonymize` setzt `SET LOCAL session_replication_role = replica` (`src/core/models/client.py:172-191`); `prune_auditlog` deaktiviert Trigger per `ALTER TABLE core_auditlog DISABLE TRIGGER` (`src/core/services/retention.py:845-854`); Architektur verlangt `NOSUPERUSER` (`docs/adr/005-facility-scoping-and-rls.md:17-32`).
- Problem: Die Codepfade umgehen Append-only-Trigger fuer legitime DSGVO-Redaktion. Ob diese Statements mit der empfohlenen App-DB-Rolle laufen, ist ohne PostgreSQL-Laufzeittest nicht abschliessend bewertbar.
- Risiko: Anonymisierung oder Auditlog-Aufbewahrung koennen in Produktion scheitern. Das waere ein Datenschutz-/Betroffenenrechte-Problem.
- Empfehlung: Einen produktionsnahen PostgreSQL-Test mit NOSUPERUSER-Approlle einfuehren. Falls er scheitert, Redaktionsmechanismus auf dedizierte SECURITY-DEFINER-Funktionen/Migrationen oder kontrollierte Wartungsrolle umstellen.
- Geschaetzter Aufwand: M/L
- Vorher notwendige Tests: Funktionaler Test fuer `Client.anonymize` mit EventHistory und fuer `prune_auditlog` unter RLS/NOSUPERUSER.
- Moegliche Nebenwirkungen: Trigger-/RLS-Migrationslogik ist sicherheitskritisch; keine breite Umstellung ohne Datenbanktest.
- Sichere Umsetzungsschritte: erst Test reproduzieren; dann kleinste DB-Funktion fuer exakt erlaubte Redaktion; Audittest; Runbook aktualisieren.

### RF-005: Admin-Oberflaeche ist nicht eindeutig an Rollen- und Facility-Policies gekoppelt

- Prioritaet: P1
- Bereich: Admin, RBAC, Dokumentation
- Fundstellen: Admin-URL `src/anlaufstelle/urls.py:27`; `UserAdmin` ohne sichtbaren globalen AdminSite-Gate in `src/core/admin.py:53-60`; Security Notes behaupten `/admin-mgmt/` sei Admin/Lead-only (`docs/security-notes.md:70-73`); Seed legt alle Rollen mit `is_staff=True` an (`src/core/seed/users.py:14-30`).
- Problem: Django Admin verwendet primaer `is_staff` und Modellpermissions. Die Codebasis dokumentiert zusaetzlich Admin/Lead-only, aber ein custom `AdminSite.has_permission` oder konsequente ModelAdmin-Facility-Scopes sind in den gelesenen Stellen nicht sichtbar.
- Risiko: Kein bestaetigter Datenleck-Befund, aber ein sicherheitsrelevanter Drift zwischen Doku, Seed-Rollen und Django-Admin-Mechanik. Besonders riskant, wenn spaeter Modellpermissions an Staff/Assistant vergeben werden.
- Empfehlung: Explizite AdminSite-Policy: nur Admin oder bewusst Admin+Lead; dazu ModelAdmin-`get_queryset`/Permission-Tests fuer Facility-Scope oder klare Entscheidung "Django Admin nur Superuser/Admin".
- Geschaetzter Aufwand: M
- Vorher notwendige Tests: Assistant/Staff/Lead Zugriff auf `/admin-mgmt/`; ModelAdmin-Queryset mit zwei Facilities; MFA/Sudo-Anforderung verifizieren.
- Moegliche Nebenwirkungen: Bestehende Wartungsworkflows fuer Lead koennen blockiert werden; Entscheidung vor Umsetzung klaeren.
- Sichere Umsetzungsschritte: Doku-Entscheidung; Tests; custom AdminSite; ModelAdmin-Scopes fuer Facility-Modelle.

### RF-006: Retention-Service hat duplizierte Strategielogik

- Prioritaet: P1/P2
- Bereich: Retention, Loeschlogik
- Fundstellen: Datei ist 974 LOC; `collect_doomed_events` dupliziert vier Strategien (`src/core/services/retention.py:485-551`); Enforcement dupliziert sie erneut (`612-739`); Proposal-Erzeugung ein drittes Mal (`861-974`).
- Problem: Loeschkriterien muessen an mehreren Stellen synchron gehalten werden; der Kommentar in `collect_doomed_events` weist selbst darauf hin (`retention.py:488-489`).
- Risiko: Unterschiedliche Vorschau, Proposal-Liste und tatsaechliche Loeschung. Bei Aufbewahrungsfristen ist das ein Datenschutz- und Audit-Risiko.
- Empfehlung: Kleine `RetentionStrategy`-Struktur oder Selector-Funktionen pro Kategorie, die Queryset, Due-Date und Detailbildung liefern. Keine grosse State-Machine.
- Geschaetzter Aufwand: M/L
- Vorher notwendige Tests: Charakterisierung fuer alle vier Strategien, LegalHold, Dry-Run, Proposal-Deduplizierung, Redaction.
- Moegliche Nebenwirkungen: Loeschlogik ist kritisch; Refactor nur mit unveraenderten Queryset-Erwartungen.
- Sichere Umsetzungsschritte: Tests erweitern; einen Strategy-Selector einfuehren; zuerst `collect_doomed_events` auf Selector umstellen; dann Enforcement/Proposals.

### RF-007: `Client.anonymize` vermischt Domainlogik, Dateiloeschung und DB-Trigger-Bypass

- Prioritaet: P1/P2
- Bereich: Datenmodell, Anonymisierung
- Fundstellen: `src/core/models/client.py:105-204`.
- Problem: Das Model anonymisiert eigene Felder, Cases, Episodes, WorkItems, EventAttachments, EventHistory und DeletionRequests in einer Methode.
- Risiko: Schwer isoliert testbar; neue abhaengige Tabellen koennen leicht vergessen werden; DB-spezifische Triggerlogik liegt im Model.
- Empfehlung: `services/client_anonymization.py` mit klaren Schritten und Ergebnisobjekt; `Client.anonymize` bleibt als duenner Wrapper fuer Rueckwaertskompatibilitaet.
- Geschaetzter Aufwand: M
- Vorher notwendige Tests: bestehende `test_anonymize_residue.py` beibehalten; neue Tests fuer EventHistory mit Postgres und DeletionRequest.
- Moegliche Nebenwirkungen: Signal-/Audit-Verhalten kann sich aendern.
- Sichere Umsetzungsschritte: Service extrahieren ohne Verhalten zu aendern; Tests unveraendert gruen halten; danach DB-Privilegienproblem separat loesen.

### RF-008: Dynamisches JSONB-Schema ist ueber Model, Form und Service verteilt

- Prioritaet: P2
- Bereich: DocumentType, FieldTemplate, Event-Daten
- Fundstellen: `FieldTemplate.clean` validiert Defaults/Sensitivity (`src/core/models/document_type.py:236-291`), `DynamicEventDataForm` baut und validiert Felder (`src/core/forms/events.py:124-230`), `_validate_data_json` filtert Daten (`src/core/services/event.py:303-335`).
- Problem: Schema- und Feldtypregeln sind nicht an einer Stelle greifbar.
- Risiko: neue Feldtypen oder Datei-Marker koennen in einem Pfad funktionieren und in einem anderen brechen.
- Empfehlung: kleine Registry pro `FieldTemplate.FieldType` fuer Default-Casting, Formfield-Bau, CSV/Export-Rendering und JSON-Validierung. Erst ein Feldtyp als Pilot, keine Vollabstraktion auf einmal.
- Geschaetzter Aufwand: M
- Vorher notwendige Tests: Default-Werte, inaktive Select-Optionen, FILE-Marker Stage A/B, unbekannte Slugs.
- Moegliche Nebenwirkungen: dynamische Forms und Exportformat.
- Sichere Umsetzungsschritte: Registry read-only einfuehren; Tests; dann Form/Service schrittweise anschliessen.

### RF-009: Audit-Logging ist teils zentral, teils direkt verteilt

- Prioritaet: P2
- Bereich: Auditierbarkeit
- Fundstellen: vorhandener Service `src/core/services/audit.py:33-71`; direkte `AuditLog.objects.create` an vielen Stellen, z.B. `src/core/views/statistics.py:182-189`, `src/core/views/dsgvo.py:37-45`, `src/core/views/offline.py:42-53`, `src/core/services/event.py:564-619`, `src/core/services/retention.py`.
- Problem: IP-Ermittlung, Detail-Schema, System-/Request-Kontext und Fehlerverhalten sind nicht einheitlich.
- Risiko: Auditdaten bleiben vorhanden, koennen aber uneinheitlich oder schwer auswertbar werden. Bei Security-Events ist das langfristig relevant.
- Empfehlung: keine grosse Event-Sourcing-Abstraktion; stattdessen `audit.log_export`, `audit.log_mutation`, `audit.log_security` und `audit.log_system` mit stabilen Detail-Schemata.
- Geschaetzter Aufwand: M
- Vorher notwendige Tests: bestehende Audit-Coverage, Export/MFA/Auth/Retention Smoke-Tests.
- Moegliche Nebenwirkungen: Tests koennen an Detailformaten haengen.
- Sichere Umsetzungsschritte: erst neue Helper einfuehren, dann einzelne Callsite-Gruppen migrieren.

### RF-010: Offline-Bundle behandelt neues Stage-B-Dateimarkerformat nicht explizit

- Prioritaet: P2
- Bereich: Offline, Attachments
- Fundstellen: Offline serialisiert nur `__file__` speziell (`src/core/services/offline.py:80-85`); neue Events speichern `__files__` (`src/core/services/event.py:398-402`).
- Problem: `__files__` wird als normales Dict ins Offline-Bundle uebernommen.
- Risiko: vermutlich kein Dateiinhalt-Leak, aber inkonsistente Offline-UI und Metadatenexposition von Attachment-IDs/Sortierung.
- Empfehlung: `__files__` wie `__file__` normalisieren: nur "Datei vorhanden" plus sichere Anzeigenamen, keine internen IDs.
- Geschaetzter Aufwand: S
- Vorher notwendige Tests: Offline-Bundle mit Stage-B-Multiattachment; lower role darf Feld nicht sehen, berechtigte Rolle sieht nur sichere Marker.
- Moegliche Nebenwirkungen: Offline-Anzeige vorhandener Dateien.
- Sichere Umsetzungsschritte: Test; Serializer anpassen; E2E Offline-Anzeige pruefen.

### RF-011: Clientseitige Filterpersistenz speichert Pseudonym-Suchbegriffe in `sessionStorage`

- Prioritaet: P2
- Bereich: Frontend, Datenschutz-Minimierung
- Fundstellen: generische Speicherung in `src/static/js/filter-persistence.js:36-64`; Clientliste speichert `name="q"` mit Pseudonym-Suche (`src/templates/core/clients/list.html:19-31`); weitere Persistenzcontainer in `attachments/list.html`, `zeitstrom/index.html`, `workitems/inbox.html`.
- Problem: Der Mechanismus speichert alle named Inputs/Selects innerhalb `data-filter-persist`, auch Textsuchfelder.
- Risiko: Pseudonyme oder Suchbegriffe bleiben bis Sitzungsende im Browser-Speicher. Das ist kein Serverleck, aber Datenschutz-Minimierung spricht gegen Persistenz sensibler Suchtexte.
- Empfehlung: Allowlist nur fuer unkritische Selects oder `data-filter-persist-exclude` fuer Textfelder wie `q`.
- Geschaetzter Aufwand: S
- Vorher notwendige Tests: JS/Playwright-Test, dass `q` nicht persistiert, Stage/Age aber optional schon.
- Moegliche Nebenwirkungen: Komfortverlust beim Zuruecknavigieren.
- Sichere Umsetzungsschritte: Attribut einfuehren; Clientliste zuerst; danach weitere Filter pruefen.

### RF-012: `apply_attachment_changes` erzeugt N+1-Queries bei Attachment-Updates

- Prioritaet: P2
- Bereich: Performance, File Vault
- Fundstellen: `event.attachments.filter(pk=...).first` in Schleifen (`src/core/services/event.py:431-448`).
- Problem: Pro vorhandener Datei werden separate Queries ausgefuehrt.
- Risiko: Bei vielen Attachment-Eintraegen langsame Edit-Requests; unter Uploadlast unnoetige DB-Last.
- Empfehlung: Attachments vorab als Dict laden (`event.attachments.filter(pk__in=ids)`), dann in REMOVE/REPLACE wiederverwenden.
- Geschaetzter Aufwand: S
- Vorher notwendige Tests: bestehende Stage-B Attachment-Tests; Querycount-Test fuer mehrere Attachments.
- Moegliche Nebenwirkungen: Sortierung/Entry-ID-Behandlung.
- Sichere Umsetzungsschritte: Test; lokale Map; Verhalten unveraendert lassen.

## 4. Security- und Datenschutzbefunde

| ID | Prioritaet | Fundstelle | Risiko | Empfehlung | Testbedarf |
|---|---|---|---|---|---|
| | P1 | `src/core/views/events.py:174-201` | Metadaten eingeschraenkter Dokumentationstypen im invalid POST | Guard wie `EventFieldsPartialView` (`events.py:259-269`) | Assistant/Staff invalid POST mit HIGH-DocType |
| | P1 | `src/core/services/export.py:21-32`, `158-180` | Service kann bei Wiederverwendung hoeher sensitive Event-Zeilen exportieren | Service-level `visible_to(user)` | CSV-Service-Test pro Rolle |
| | P1 | `src/anlaufstelle/urls.py:27`, `src/core/admin.py:53-60`, `docs/security-notes.md:70-73`, `src/core/seed/users.py:14-30` | Admin-Policy nicht eindeutig im Code erzwungen | Custom AdminSite/ModelAdmin-Scopes oder Doku auf Admin-only korrigieren | Admin-Zugriffsmatrix |
| | P0/P1 | `src/core/models/client.py:182-191`, `src/core/services/retention.py:845-854`, `docs/adr/005-facility-scoping-and-rls.md:17-32` | DSGVO-Redaktion koennte mit Produktions-DB-Rolle scheitern | PostgreSQL-NOSUPERUSER-Test; ggf. SECURITY-DEFINER-Redaktionsfunktion | produktionsnaher DB-Test |
| | P2 | `src/static/js/filter-persistence.js:36-64`, `src/templates/core/clients/list.html:19-31` | Pseudonym-Suchbegriffe im Browser-SessionStorage | Textfelder aus Persistenz ausschliessen | Playwright/JS-Test |
| | P1 | `src/core/services/dsgvo_package.py:11`, `Dockerfile:34-38`, `.dockerignore:10` | DSGVO-Paket-Download bricht im Image | Templates paketieren/kopieren | Docker-Runtime-Test |
| | P2 | `src/core/services/offline.py:80-85`, `src/core/services/event.py:398-402` | Stage-B Attachment-Marker werden offline nicht minimiert | Marker normalisieren | Offline-Bundle-Test |
| | P2 | `src/core/signals/audit.py` plus RLS-Design in `0047_postgres_rls_setup.py:66-70` | `facility=None` AuditLogs sind absichtlich nicht facility-sichtbar; Monitoring fuer globale Login-Angriffe kann fehlen | Systemweite Security-Auswertung fuer Betreiber dokumentieren/implementieren | Failed-login/Breach-Detection-Test |

Positivbefunde:

- Facility-Isolation ist doppelt abgesichert: Middleware setzt `request.current_facility` und PostgreSQL-Sessionvariable (`src/core/middleware/facility_scope.py:36-55`), RLS ist fuer direkte und transitive Tabellen aktiv (`src/core/migrations/0047_postgres_rls_setup.py:21-91`, `0057`, `0063`).
- Sensitivity-Policy ist zentral und wird fuer Event-Detail ueber `get_visible_event_or_404` genutzt (`src/core/services/sensitivity.py:72-92`).
- Verschluesselung ist fail-closed, wenn kein Key gesetzt ist (`src/core/services/encryption.py:56-85`); Production Settings erzwingen Key (`src/anlaufstelle/settings/prod.py:94-101`).
- CSV-Injection wird neutralisiert (`src/core/services/export.py:88-108`).
- File Vault prueft Extension, Magic Bytes und Virus-Scan-Pfade (`src/core/services/file_vault.py:145-230`).

## 5. Datenmodell und Domaenenlogik

Das Datenmodell ist fuer Django-Verhaeltnisse gut segmentiert, aber an drei Stellen sollte die Domainlogik ausgeduennt werden:

1. `Client.anonymize` (`src/core/models/client.py:105-204`) ist zu gross fuer ein Model. Ziel: `ClientAnonymizationService`, der explizit die Teilbereiche `client`, `cases`, `episodes`, `workitems`, `attachments`, `event_history`, `deletion_requests` behandelt. Das Model kann als Wrapper bleiben.
2. `DocumentType`/`FieldTemplate` (`src/core/models/document_type.py:18-145`, `225-335`) enthalten wichtige Invarianten: `system_type` unveraenderbar (`116-124`), HIGH-Felder muessen verschluesselt sein (`243-248`), FILE-Felder werden erzwungen verschluesselt (`294-297`). Diese Invarianten nicht aus dem Model entfernen; aber Form-/Export-/JSON-Regeln koennen ueber eine kleine FieldType-Registry vereinheitlicht werden.
3. `Event.data_json` ist flexibel, aber schemaarm (`src/core/models/event.py:62-63`). Die Validierung ist auf Form und Service verteilt (`src/core/forms/events.py:124-230`, `src/core/services/event.py:303-335`). Ziel: zentrale Validatoren pro Feldtyp, keine starre neue Tabellenstruktur.

Geeignete Zielmuster:

- QuerySet/Manager-Methoden fuer Sichtbarkeit (`visible_to`, `for_facility`) weiter ausbauen statt ad-hoc-Filter.
- Policy-Funktionen fuer Rollen/Sensitivitaet beibehalten; keine schwere Policy-Klassenhierarchie.
- Service Layer fuer irreversible oder side-effect-lastige Workflows: Anonymisierung, Retention, Export, File Vault.
- Explizite kleine Value Objects/Dataclasses fuer Retention-Strategien und Export-Kontext.
- Keine breite State-Machine fuer alle Modelle; nur bei Retention/DeletionRequest sinnvoll, falls Statusuebergaenge weiter wachsen.

## 6. Views, Forms, Templates und HTMX

Bewertung:

- Rollen-Mixins sind einfach und lesbar (`src/core/views/mixins.py:10-35`).
- HTMX-Partial-Mixin existiert (`src/core/views/mixins.py:61-70`), wird aber noch nicht flaechig genutzt.
- `EventCreateView` ist mit GET, Quick Templates, Client-Prefill, Dynamic Forms, Audit und Attachment-Flows ein typischer Refactoring-Kandidat (`src/core/views/events.py:93-248`).
- `EventFieldsPartialView` ist ein gutes Beispiel fuer einen klaren HTMX-Endpunkt mit serverseitiger Berechtigungspruefung (`src/core/views/events.py:251-269`).
- Templates enthalten grosse UI- und Darstellungseinheiten (`src/templates/base.html` 361 LOC, `src/templates/core/events/detail.html` 226 LOC, `src/templates/core/statistics/partials/dashboard_content.html` 224 LOC).

Zielstruktur, pragmatisch fuer dieses Projekt:

```text
core/views/
  events.py              # schlanke View-Orchestrierung
core/services/
  event.py               # Transaktionen, Attachments, History
  event_create_context.py# Quick-Template/Form-Kontext, falls weiter wachsend
core/selectors/
  events.py              # sichtbare Listen/Detail-Querysets, erst bei Bedarf
core/policies/
  sensitivity.py         # nur wenn services/sensitivity.py zu breit wird
core/forms/
  events.py
core/presenters/
  event_detail.py        # nur fuer komplexe Templates
templates/core/.../partials/
```

Das ist keine Aufforderung zum grossen Ordner-Rewrite. Sinnvoll waere zuerst:

1. RF-001 fixen.
2. `EventCreateView` durch eine kleine Context-Builder-Funktion entlasten.
3. Nur dort `selectors/` einfuehren, wo Querysets mehrfach genutzt werden, z.B. Export/Timeline/Retention.

Barrierefreiheit/UI-Zustaende sind ohne Playwright/a11y-Lauf nicht abschliessend bewertbar. Vorhandene E2E-Tests decken viele Workflows ab, aber nicht alle Fehlerpfade von HTMX und invalid POST.

## 7. Tests und Testluecken

Vorhandene Staerken:

- Facility-Scope-Tests fuer zentrale Views (`src/tests/test_scope.py:14-200` und weitere Klassen).
- Architekturtests gegen ungescopte `.objects.all` in Views und direkte Event-Lader (`src/tests/test_architecture.py:9-50`).
- Guard gegen Event-Encryption-Bypass via `bulk_create`/`update(data_json=...)` (`src/tests/test_architecture.py:53-127`).
- RLS-Tests (`src/tests/test_rls.py`, `src/tests/test_rls_functional.py`).
- Breite Tests fuer Export, CSV-Injection, Verschluesselung, File Vault, Retention, Anonymisierung, MFA, Offline und E2E.

Test-Gap-Matrix:

| Bereich | Risiko | Vorhandene Tests | Fehlende Tests | Empfohlene neue Tests | Prioritaet |
|---|---|---|---|---|---|
| EventCreate invalid POST | Metadaten-Leak | Event-/Sensitivity-Tests vorhanden | manipulierter invalid POST mit HIGH-DocType | Assistant darf HIGH-Feldlabels nicht sehen | P1 |
| CSV Export Service | Wiederverwendungs-Leak | `test_export.py`, CSV-Injection | Service mit Staff/Assistant und HIGH-Event | `export_events_csv(..., user=staff)` ohne HIGH-Zeile | P1 |
| Admin-Gate | Rollen-/Doku-Drift | Admin-Actions-Tests | `/admin-mgmt/` Zugriffsmatrix, Facility-Querysets | Staff/Assistant/Lead/Admin je nach Entscheidung | P1 |
| Anonymisierung unter Prod-DB-Rolle | DSGVO-Pfad kann scheitern | `test_anonymize_residue.py` | PostgreSQL NOSUPERUSER + EventHistory | `Client.anonymize` mit RLS/Triggern | P0/P1 |
| Auditlog-Pruning | Retention kann scheitern | Retention-Auditlog-Tests | PostgreSQL Trigger-Disable mit Approlle | `prune_auditlog` produktionsnah | P1 |
| DSGVO Package im Image | Download 500 | Unit/E2E lokal | Docker-Runtime-Pfad | Image starten, Dokument laden | P1 |
| Offline Stage-B Attachments | Marker-Metadaten | Offline/API/E2E vorhanden | `__files__` im Bundle | nur minimierter Marker, keine IDs | P2 |
| Filterpersistenz | Suchtext im SessionStorage | E2E Filter | Pseudonym-`q` nicht persistieren | Playwright Storage-Assert | P2 |
| Retention-Strategien | Vorschau/Enforce/Proposals divergieren | viele Retention-Tests | gleiche Query-Quelle fuer drei Pfade | Golden Tests pro Strategie | P1/P2 |
| JSONB Field Registry | Feldtypdrift | FieldTemplate-/Default-Tests | einheitliche Registry-Coverage | parametrisiert pro Feldtyp | P2 |

## 8. Performance

Konkrete Befunde:

- Attachment-Update-N+1: `apply_attachment_changes` laedt Attachments pro Entry einzeln (`src/core/services/event.py:431-448`). Optimierung: einmalige Map per `pk__in`.
- Export-Streaming streamt CSV-Zeilen, aber iteriert das QuerySet normal (`src/core/services/export.py:164-180`). Bei grossen Zeitraeumen kann Django QuerySet-Caching Speicher binden. Optimierung: `.iterator(chunk_size=500)` nach `select_related`.
- Suche ueber `data_json__icontains` (`src/core/services/search.py:58-66`) ist funktional, aber bei wachsender Event-Tabelle teuer und nicht sauber indexierbar. Event-Model-Indizes decken Facility/Deleted/Occurred/Client/DocumentType ab (`src/core/models/event.py:73-80`), nicht JSONB-Volltext. Empfehlung: vorerst Query-Limit und Rate-Limit beibehalten; bei realer Last dedizierten Search-Index oder materialisierte Suchspalte fuer nicht-verschluesselte, freigegebene Felder.
- Retention-Proposals bauen mehrere Querysets mit aehnlichen Kriterien (`src/core/services/retention.py:861-974`). Das ist eher Wartungs- als Laufzeitproblem, kann aber bei grossen Installationen mehrfach dieselben Tabellen scannen. Selector-Extraktion ermoeglicht spaeter gezielte Indizes.
- Statistiken nutzen offenbar Materialized-View-Pfade und Tests (`src/tests/test_statistics_mv.py`, `test_statistics_hybrid.py`); nicht ohne Laufzeitprofil refaktorieren.

Nicht abschliessend bewertbar ohne Profiling:

- N+1 in komplexen Templates (`clients/detail.html`, `events/detail.html`, Statistik-Dashboard).
- PDF-Export unter Last mit WeasyPrint.
- E2E-Testlaufzeit.

## 9. Deployment und Betrieb

Gut geloest:

- Prod Settings fail-closed fuer `DJANGO_SECRET_KEY`, `ALLOWED_HOSTS` und Encryption Keys (`src/anlaufstelle/settings/prod.py:37-44`, `94-101`).
- Sichere Cookie-/HSTS-/CSRF-Defaults in Produktion (`src/anlaufstelle/settings/prod.py:48-75`).
- Docker laeuft als non-root User und bereitet `/data/media` fuer Uploads vor (`Dockerfile:53-62`).
- Healthcheck ist im Image vorhanden (`Dockerfile:66-72`).
- Runbook beschreibt Deploy, Healthcheck, Migrationsstatus und Rollback (`docs/ops-runbook.md:25-84`, `88-122`).

Riskant oder zu pruefen:

- DSGVO-Vorlagen fehlen wahrscheinlich im Image: siehe RF-003.
- RLS verlangt `NOSUPERUSER`; Redaktionspfade muessen mit dieser Rolle laufen: siehe RF-004.
- Seed-User haben alle `is_staff=True` und Standardpasswort `anlaufstelle2026` (`src/core/seed/users.py:14-30`). Das ist fuer Demo/Dev plausibel, muss aber in Produktionsdoku und Seed-Command klar als nicht-produktiv markiert bleiben.
- `TRUSTED_PROXY_HOPS`/X-Forwarded-For-Parsing wurde nicht tief geprueft; bei direkter Exponierung ohne Proxy koennen XFF-Defaults relevant werden. Nicht abschliessend ohne Deployment-Topologie.
- Backup-Key-Handling wurde nur oberflaechlich geprueft; vor oeffentlichem Betrieb sollte ein Check verhindern, dass Beispielwerte aus `.env.example` produktiv bleiben.

## 10. Dokumentation

Stark:

- README benennt Pre-Release-Status klar (`README.md:5-8`) und beschreibt Datenschutzfeatures (`README.md:103-115`).
- ADR fuer Facility/RLS ist konkret und operativ hilfreich (`docs/adr/005-facility-scoping-and-rls.md:7-32`).
- Security Notes dokumentieren bewusst akzeptierte Risiken wie RLS-freies `core_user` und Admin-CSP-Ausnahme (`docs/security-notes.md:7-32`, `52-95`).
- Ops-Runbook enthaelt Deploy-/Rollback-/Healthcheck-Prozeduren (`docs/ops-runbook.md:25-84`, `88-122`).

Verbesserungsbedarf:

- Admin-Doku/Security Notes behaupten `/admin-mgmt/` sei Admin/Lead-only (`docs/security-notes.md:70-73`), die gelesenen Codepfade zeigen aber nur die Standard-Django-Admin-URL (`src/anlaufstelle/urls.py:27`). Entscheidung und Code angleichen.
- DSGVO-Vorlagenpfad im Docker-Betrieb dokumentieren oder fixen.
- Runbook sollte einen Abschnitt "Produktions-Readiness vor Pilotbetrieb" enthalten: Encryption Key, Backup Key, Seed-Daten verboten, `check --deploy`, RLS-Funktionstest, DSGVO-Paket-Download, Anonymisierungstest.
- Eine kurze Architekturkarte fuer neue Contributor waere hilfreich: Models/Services/Views/Tests + "wo gehoert neue Facility-gescopte Tabelle hin".

## 11. Empfohlene Refactoring-Roadmap

###: Absichern vor Umbau

- Test fuer RF-001 invalid EventCreate POST.
- Test fuer RF-002 CSV-Service-Sichtbarkeit.
- PostgreSQL-NOSUPERUSER-Test fuer RF-004.
- Docker-Runtime-Test fuer DSGVO-Paket.
- Admin-Zugriffsmatrix als Entscheidungstest.

###: Kritische Zentralisierung

- EventCreate invalid POST fixen.
- Export-Service auf Event-Sichtbarkeit umstellen.
- AdminSite-Policy explizit machen.
- DSGVO-Templates paketieren.
- DB-Redaktionspfad fuer Anonymisierung/Pruning stabilisieren, falls Tests fehlschlagen.

###: Strukturverbesserung

- Retention-Strategy-Selector extrahieren.
- `ClientAnonymizationService` einfuehren.
- `EventCreateView` mit Context-Builder entlasten.
- Stage-B Offline-Marker normalisieren.
- Attachment-Update-N+1 beheben.

###: Aufraeumen und Dokumentieren

- Audit-Helper schrittweise auf direkte `AuditLog.objects.create`-Callsites anwenden.
- FieldType-Registry pilotieren.
- Runbook/ADR fuer Admin-Policy, DSGVO-Template-Paketierung und Redaktions-DB-Rechte aktualisieren.
- Optional: Querycount-/Profiling-Tests fuer Export, Suche, Retention.

## 12. Konkrete erste Pull Requests

1. Titel: `EventCreate invalid POST sensitivity guard`
 - Ziel: RF-001 schliessen.
 - Umfang: `src/core/views/events.py`, neuer Test in `src/tests/test_events.py` oder `test_field_sensitivity.py`.
 - Tests: invalid POST mit HIGH-DocType als Assistant.
 - Risiko: gering.
 - Warum zuerst: kleiner Patch mit direkter Datenschutzwirkung.

2. Titel: `Apply event visibility policy in CSV export service`
 - Ziel: RF-002 absichern.
 - Umfang: `src/core/services/export.py`, Export-Test.
 - Tests: Staff/Assistant sieht keine HIGH-Event-Zeile.
 - Risiko: mittel, falls Systemexports `user=None` nutzen.
 - Warum zuerst: verhindert zukuenftige Export-Regressionen.

3. Titel: `Package DSGVO templates for Docker runtime`
 - Ziel: RF-003 beheben.
 - Umfang: Template-Pfad, Docker/Test, ggf. Management-Command.
 - Tests: `render_document` im paketierten Pfad; optional Image smoke.
 - Risiko: gering/mittel.
 - Warum zuerst: sichtbarer Self-Hosting-Fehler.

4. Titel: `Verify anonymization and audit pruning with production DB role`
 - Ziel: RF-004 reproduzierbar machen.
 - Umfang: Test/CI-DB-Setup, keine Logik-Aenderung.
 - Tests: PostgreSQL NOSUPERUSER, RLS aktiv.
 - Risiko: gering.
 - Warum zuerst/nicht zuerst: zuerst als Test-PR, weil Implementierung ohne Reproduktion riskant ist.

5. Titel: `Make Django admin access policy explicit`
 - Ziel: RF-005 entscheiden und durchsetzen.
 - Umfang: custom AdminSite oder Doku-Korrektur plus Tests.
 - Tests: Rollenmatrix fuer `/admin-mgmt/`.
 - Risiko: mittel wegen Admin-Workflows.
 - Warum nicht als allererstes: braucht fachliche Entscheidung Admin-only vs Lead+Admin.

6. Titel: `Normalize Stage-B attachment markers in offline bundle`
 - Ziel: RF-010.
 - Umfang: `src/core/services/offline.py`, Offline-Bundle-Test.
 - Tests: Stage-B Attachment im Offline-Bundle.
 - Risiko: gering.
 - Warum: kleine Datenschutz-Minimierung.

7. Titel: `Do not persist sensitive text filters in sessionStorage`
 - Ziel: RF-011.
 - Umfang: `filter-persistence.js`, Templates mit `data-filter-persist`.
 - Tests: Playwright/JS-Storage-Test.
 - Risiko: gering.
 - Warum: Datenschutz-Minimierung ohne Backend-Risiko.

8. Titel: `Extract retention strategy selectors`
 - Ziel: RF-006.
 - Umfang: `src/core/services/retention.py`, neue kleine Selector-Helfer.
 - Tests: Retention-Charakterisierung.
 - Risiko: mittel/hoch.
 - Warum nicht zuerst: erst nach Absicherung, weil Loeschlogik kritisch ist.

9. Titel: `Introduce client anonymization service wrapper`
 - Ziel: RF-007.
 - Umfang: neuer Service, `Client.anonymize` delegiert.
 - Tests: bestehende Anonymize-/Residue-Tests unveraendert.
 - Risiko: mittel.
 - Warum nicht zuerst: erst DB-Rollenfrage klaeren.

10. Titel: `Reduce attachment edit queries`
 - Ziel: RF-012.
 - Umfang: `src/core/services/event.py`.
 - Tests: Stage-B Attachment-Tests plus Querycount.
 - Risiko: gering.
 - Warum: Performance-Patch ohne fachliche Semantik, aber nach Security-Patches.

## 13. Nicht anfassen / Vorsicht

- RLS-Grunddesign nicht ersetzen. Es ist dokumentiert und getestet (`docs/adr/005-facility-scoping-and-rls.md`, `src/core/migrations/0047...`, `src/tests/test_rls.py`, `test_rls_functional.py`). Neue Tabellen nur sauber einhaengen.
- Sensitivity-Service nicht in eine grosse Policy-Klassenhierarchie umbauen. Die aktuelle zentrale Funktionsebene ist fuer das Projekt angemessen (`src/core/services/sensitivity.py:14-106`).
- Event-Encryption-Pipeline nicht verschieben, bevor die Architekturtests erweitert sind. `Event.save` verschluesselt sensible Felder (`src/core/models/event.py:86-112`), und Architekturtests verhindern bekannte Bypass-Pfade (`src/tests/test_architecture.py:53-127`).
- File-Vault-Pruefungen nicht vereinfachen. Extension-/Magic-Bytes-/Virus-Scan-Pfade sind sicherheitsrelevant (`src/core/services/file_vault.py:145-230`).
- Statistik-/Materialized-View-Pfade nicht auf Verdacht refaktorieren. Erst Profiling und bestehende `test_statistics_*`-Tests auswerten.
- Admin-CSP-Ausnahme nicht nebenbei entfernen. Sie ist dokumentiert (`docs/security-notes.md:52-95`); die Admin-Rollenpolicy ist das dringlichere Thema.
- Retention und Anonymisierung nicht breit umbauen, bevor die neuen Charakterisierungs- und PostgreSQL-Rollentests stehen.
