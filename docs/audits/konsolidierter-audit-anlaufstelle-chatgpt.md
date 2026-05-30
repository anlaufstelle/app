# Konsolidierter Audit: `anlaufstelle/app`

**Stand der zusammengeführten Audits:** v0.10.2 / Commit `35e0f5b31e972c7345940433c572372d6b1736ee`, Release vom 28.04.2026. 
**Quellenbasis:** alle acht hochgeladenen Audit-Dateien, konsolidiert und gegeneinander abgeglichen. 
**Kurzurteil:** fachlich stark, architektonisch ernst gemeint, für Pilotbetrieb mit IT-Begleitung plausibel — aber **noch nicht produktionsreif für sensible Echtdaten ohne gezielte Härtung**.

Die Audits kommen im Kern zu demselben Bild: Anlaufstelle ist kein CRUD-Prototyp, sondern ein fortgeschrittenes Pre-Release-System mit echtem Domänenverständnis, Service-Layer, PostgreSQL-RLS, Audit/Retention, Feld-/Dateiverschlüsselung, MFA, CI und Tests. Gleichzeitig blockieren mehrere Datenschutz-, Lösch-, Betriebs- und Governance-Themen eine unkritische Produktivfreigabe. Besonders schwer wiegen Retention/EventHistory, unvollständige Anonymisierung, Klartext-Freitexte außerhalb des geschützten Event-Feldmodells, RLS-Testlücken, CSV-Injection und fehlende Betriebsreife für kleine NGOs.

---

## 1. Gesamtbewertung

| Dimension | Konsolidierte Bewertung |
|---|---:|
| Fachliches Konzept | **8/10** |
| Architektur | **7/10** |
| Codequalität | **7–8/10** |
| Sicherheit / DSGVO-Design | **7/10** |
| tatsächliche Datenschutz-Härte | **5–6/10** |
| Betrieb / Self-Hosting | **5/10** |
| Open-Source-/Governance-Reife | **5–6/10** |
| Produktreife für Pilot | **ja, mit Aufsicht** |
| Produktreife für sensible Echtdaten | **noch nein** |

**Konsolidiertes Urteil:** 
Anlaufstelle ist ein sehr gutes, fachlich fundiertes Pre-Production-System. Es ist **förderwürdig, pilotfähig und technisch substanziell**. Es sollte aber erst dann mit echten sensiblen Klientendaten betrieben werden, wenn die Lösch-/Historienlogik, Anonymisierung, Exporthärtung, RLS-Tests, Betriebsdokumentation und Governance-Fragen abgearbeitet sind.

---

## 2. Was das System richtig gut macht

### 2.1 Fachliches Modell ist deutlich über Durchschnitt

Das System versteht die Zielgruppe sichtbar: anonyme Kontakte, pseudonymisierte Klienten, niedrigschwellige Dokumentation, Zeitstrom, Fälle/Episoden, WorkItems/Übergaben, Jugendamts-/Statistiklogik, Retention und Offline-Nutzung sind keine generischen SaaS-Bausteine, sondern passen zur Praxis niedrigschwelliger Sozialarbeit.

Besonders stark:

- `Client` ohne Klarnamenfeld.
- `Event` als zentrale Dokumentationseinheit.
- `DocumentType`/`FieldTemplate` als konfigurierbares Schema.
- Sensitivität auf Dokumenttyp- und Feldebene.
- Retention/LegalHold als echte Domänenobjekte.
- WorkItems als operative Teamkommunikation.
- K-Anonymisierung als Alternative zu blindem Löschen.

### 2.2 Architektur ist pragmatisch und passend

Django + PostgreSQL + serverseitiges Rendering + HTMX ist für kleine Einrichtungen deutlich realistischer als Microservices oder eine schwere SPA/API-Plattform. Die Entscheidung für einen modularen Monolithen ist richtig. Die Audits bestätigen auch, dass der Service-Layer real existiert und nicht nur kosmetisch ist: Events, Retention, Export, File Vault, Offline, Statistik, Sensitivität, Login-Lockout und Audit sind in Services ausgelagert.

### 2.3 Security-Design ist ambitioniert

Vorhanden sind unter anderem:

- PostgreSQL Row Level Security.
- `FacilityScopeMiddleware`.
- Facility-scoped Manager.
- Rollen- und Sensitivitätslogik.
- Feldverschlüsselung mit Fernet/MultiFernet.
- verschlüsselte Dateiablage.
- MFA.
- Rate-Limits und Account-Lockout.
- AuditLog.
- EventHistory.
- ClamAV.
- produktive Fail-Closed-Settings für Secrets/Hosts/Encryption.
- CI mit Tests, Deploy-Checks, Ruff, pip-audit und E2E-Ansatz.

Das ist für ein NGO-Open-Source-Projekt ungewöhnlich stark.

---

## 3. Kritische Blocker vor produktiver Echtdaten-Nutzung

### Blocker 1: Retention löscht nicht wirklich, weil `EventHistory` Daten behalten kann

Der wichtigste konsolidierte Befund: automatische Retention löscht zwar Event-Nutzdaten aus `Event.data_json`, kopiert dieselben Werte aber vorher in `EventHistory.data_before`. Damit ist die fachliche Aussage „gelöscht“ irreführend, solange sensible Inhalte in unveränderlicher Historie weiter existieren. Ein Audit hebt zusätzlich hervor, dass manuelles Soft-Delete und Retention unterschiedliche Historien-Semantiken haben: manuelles Löschen redaktiert, Retention kann vollständige Daten historisieren.

**Priorität:** kritisch 
**Maßnahme:** ein einziger zentraler Löschpfad, der History-Einträge bei Löschung grundsätzlich redaktiert oder datenschutzkonform minimiert. Bestehende nicht-redaktierte DELETE-History muss migriert/anonymisiert werden.

---

### Blocker 2: Anonymisierung ist nicht vollständig aggregatweit

Mehrere Audits nennen denselben Kern: `Client.anonymize` räumt Client-nahe Daten auf, erfasst aber nicht konsequent alle Restspuren, insbesondere EventHistory, DeletionRequests, Attachments, Statistik-Snapshots und eventuell Audit-/Activity-Spuren. Dadurch bleibt Re-Identifikation über Historie, Anhänge oder Nebenmodelle möglich.

**Priorität:** kritisch 
**Maßnahme:** Anonymisierung als Aggregat-Operation definieren: Client, Events, Cases, Episodes, WorkItems, Attachments, History, DeletionRequests, Snapshots, Exporte, Search-Indizes und Activity-Spuren. Dazu eine testbare „Restdaten nach Anonymisierung“-Matrix.

---

### Blocker 3: Sensible Freitexte liegen außerhalb des geschützten Event-Modells

Das Event-System kann Felder sensitiv markieren und verschlüsseln. Daneben existieren aber klassische Klartextbereiche wie `Client.notes`, `Case.description`, `Episode.description` und `WorkItem.description`. Genau dort werden Sozialarbeitende in der Praxis sensible Informationen notieren. Diese Felder folgen nicht zwingend demselben Sensitivitäts-, Verschlüsselungs-, Retention- und Exportmodell wie Event-Felder.

**Priorität:** hoch 
**Maßnahme:** Freitextfelder entweder in das gleiche FieldTemplate-/Sensitivity-Modell überführen oder pro Modell Sensitivität, Verschlüsselung und Retention erzwingen.

---

### Blocker 4: Verschlüsselung ist teilweise optional

Die Audits bewerten die Verschlüsselungsarchitektur als grundsätzlich gut, kritisieren aber, dass `FieldTemplate.is_encrypted` konfigurierbar ist und Art.-9-nahe Daten dadurch nicht zwingend verschlüsselt werden. Außerdem ist `Client.pseudonym` selbst im Klartext und such-/indexierbar, was bei Backup-Leaks relevant ist.

**Priorität:** hoch 
**Maßnahme:** `Sensitivity=HIGH` muss `is_encrypted=True` erzwingen. Für Pseudonyme sollte ein Modell aus verschlüsseltem Wert plus separatem HMAC-/Hash-Lookup geprüft werden.

---

### Blocker 5: RLS ist stark, aber nicht ausreichend realitätsgetestet

RLS ist vorhanden und als Defense-in-Depth wichtig. Kritisch ist aber, dass die Audits fehlende oder unvollständige Non-Superuser-Integrationstests nennen. Wenn Tests mit Superuser-/Owner-Rechten laufen, beweisen sie nicht, dass RLS im echten Produktivbetrieb korrekt greift. Zusätzlich werden Statistik-Materialized-Views und einzelne Tabellen/Modelle als Sonderfälle genannt.

**Priorität:** hoch 
**Maßnahme:** CI-Test mit echter produktionsähnlicher DB-Rolle ohne Superuser-/Owner-Bypass. Tests müssen facility-übergreifende Zugriffe, Raw SQL, Materialized Views, Search, Export und Management Commands abdecken.

---

### Blocker 6: CSV-Export ist gegen Formula Injection zu härten

Ein Audit nennt konkret, dass CSV-Export Pseudonyme und dynamische Feldwerte direkt in CSV-Zellen schreibt, ohne Spreadsheet-Formula-Escaping. Das ist bei Öffnung in Excel/LibreOffice ein klassischer CSV-Injection-Pfad.

**Priorität:** hoch 
**Maßnahme:** zentraler CSV-Sanitizer: Werte, die mit `=`, `+`, `-`, `@`, Tab oder CR/LF beginnen, müssen sicher escaped werden. Regressionstests für Export aller dynamischen Felder.

---

### Blocker 7: Produktionsbetrieb speichert Medien/Dateien nicht ausreichend abgesichert

Ein Audit nennt fehlendes persistentes `MEDIA_ROOT` im Production-Compose; andere nennen Backup-/Restore-Lücken für Medien, Trigger, RLS und fachliche Tabellen. Für ein System mit verschlüsselten Anhängen ist das ein echter Betriebsblocker.

**Priorität:** hoch 
**Maßnahme:** persistentes Medienvolume, dokumentierter Backup-/Restore-Drill für DB + Medien + RLS + Trigger + Schlüssel, Healthcheck nach Restore, regelmäßiger Testlauf.

---

## 4. Weitere wichtige Befunde nach Bereich

### 4.1 Architektur

**Stärken**

- Modularer Monolith ist passend.
- Service-Layer existiert.
- Keine unnötige Microservice-Komplexität.
- RLS + Middleware + Manager bilden echte Defense-in-Depth.
- HTMX/SSR passt zur Zielgruppe.

**Schwächen**

- Eine einzige App `core` enthält praktisch alles: Auth, Clients, Events, Cases, WorkItems, Retention, Audit, Statistik, Offline, MFA, Export, Admin.
- `services/event.py` und `services/retention.py` wirken zu groß.
- Service-zu-Service-Abhängigkeiten sind teilweise verfilzt.
- Middleware-Reihenfolge ist ein impliziter Vertrag.
- Reporting/Statistics hat noch kein klares Read-Model/CQRS-Konzept.
- Multi-Facility-/Trägerstruktur ist nur schwach modelliert.

**Konsolidierte Empfehlung:** 
Nicht sofort alles in Apps splitten. Erst Architekturgrenzen dokumentieren, Import-Linter einführen, Service-Boundaries festziehen und neue Domänen wie `statistics`, `audit`, `retention`, `clients`, `events` langfristig separieren.

---

### 4.2 Domänenmodell

**Stärken**

- `Event` als zentrale Dokumentationseinheit ist richtig.
- `DocumentType`/`FieldTemplate` ist der zentrale Produktwert.
- Pseudonym-first ist fachlich stark.
- Anonyme Kontakte ohne Client sind sinnvoll.
- Retention/LegalHold passt zur Domäne.
- WorkItems/Hinweise bilden reale Übergaben ab.

**Schwächen**

- Doku spricht teils von drei Kontaktstufen inklusive anonym; im Code ist anonym eher Event-Level, nicht Client-Stage.
- `Case`, `Episode`, `WorkItem`, `Activity`, `AuditLog`, `EventHistory`, `RecentClientVisit` erzeugen konzeptionelle Überlappung.
- `Event.is_anonymous` plus `client=NULL` kann langfristig Inkonsistenzen erzeugen.
- JSONB-Flexibilität wird bei Reporting, Migration und Datenschutz teuer.
- `DocumentTypeField`/`FieldTemplate`-Änderungen können historische Anzeige semantisch verändern.

**Konsolidierte Empfehlung:** 
Fachmodell als ADR dokumentieren: Was ist Client-Level, was Event-Level, was Case/Episode, was WorkItem, was Audit/History? Zusätzlich FieldTemplate-Versionierung oder historische Felddefinitionen prüfen.

---

### 4.3 Sicherheit / DSGVO

**Stärken**

- Kein Klarnamenfeld im Client.
- MFA, Rate-Limit, Lockout.
- RLS.
- AuditLog.
- File Vault.
- Verschlüsselte Felder.
- ClamAV.
- Prod-Fail-Closed.
- K-Anonymisierung.
- Retention/LegalHold.

**Schwächen und Risiken**

- Retention/EventHistory.
- unvollständige Anonymisierung.
- Klartext-Pseudonym.
- Verschlüsselung für hochsensible Felder nicht zwingend.
- Klartext-Freitexte außerhalb Event-Feldmodell.
- Offline-Modus hält entschlüsselte Daten im Browserkontext.
- CSV-Injection.
- Export- und Reporting-Suppression für kleine Zellen/K-Anonymität unklar.
- WorkItem-Rechte teils inkonsistent.
- Facility-Scoping teils Konvention statt harte API.
- RLS nicht ausreichend als reale DB-Rolle getestet.
- AuditLog/EventHistory-Retention fachlich ungeklärt.
- PII-Scrubber/Logging teils zu schmal.

**Konsolidierte Empfehlung:** 
Eine zentrale Datenschutz-Policy-Schicht definieren: Sensitivity, Encryption, Retention, Export, Search, Offline, History und Audit dürfen nicht pro Feature neu interpretiert werden.

---

### 4.4 Offline/PWA

**Stärken**

- Offline-Fähigkeit ist für Streetwork fachlich wertvoll.
- Offline-Krypto und Konfliktlogik zeigen ernsthafte Produktambition.

**Risiken**

- Offline ist eine eigene Produktlinie im Produkt.
- Entschlüsselte Daten müssen für Offline-Nutzung serverseitig aufbereitet und im Browserkontext nutzbar werden.
- Geräteverlust, Session-/Key-Handling und Konfliktauflösung sind reale Risiken.
- Sync-Konflikte können fachlich kritische Daten überschreiben.
- UX bei „Treffer sichtbar/nicht sichtbar“ in verschlüsselten Feldern kann verwirren.

**Konsolidierte Empfehlung:** 
Offline erst nach Security-Härtung als eingeschränkten Pilotmodus freigeben: klare Gerätepolicy, kurze Offline-Leases, Remote-Wipe-Konzept, serverseitige Konfliktprüfung, Auditierung aller Sync-Konflikte.

---

### 4.5 Codequalität / Tests

**Stärken**

- Viele Tests.
- Playwright/E2E vorhanden.
- CI mit pytest, Ruff, pip-audit.
- Migration-Checks.
- Architekturtests.
- Service-Layer gut testbar.

**Schwächen**

- Kein mypy/pyright in CI.
- Ruff-Regeln ohne Security-/Bugbear-Tiefe in einigen Audits genannt.
- Kein Bandit/Semgrep o. ä.
- N+1-Risiken im Zeitstrom/Attachments.
- Type-Hints nicht konsequent.
- JS-Glue-Code/Offline-Sync riskanter als Python-Core.
- Manche lokale Testläufe waren wegen Umgebung nicht vollständig möglich.

**Konsolidierte Empfehlung:** 
Zuerst nicht „mehr Tests allgemein“, sondern gezielte Regressionstests für die echten Risiken: Retention-History, Anonymisierung, RLS mit Non-Superuser, CSV-Injection, Export-Suppression, Offline-Konflikte.

---

### 4.6 Betrieb / Deployment

**Stärken**

- Dockerfile.
- Production-Settings.
- Caddy.
- Healthcheck.
- ClamAV-Service.
- Non-root Runtime.
- Advisory Lock bei Migrationen.
- Backup-Skripte existieren.

**Schwächen**

- Self-Hosting ist für kleine NGOs ohne IT-Begleitung zu schwer.
- Migrationen beim Containerstart sind nicht zero-downtime.
- Backup-Verifikation ist zu flach.
- Medien/Attachments müssen sicher persistent und gesichert werden.
- Key-Rotation-Runbook fehlt oder ist nicht ausreichend.
- Monitoring für Cronjobs, Backup, ClamAV, RLS, Retention fehlt.
- Image-Namespace wirkt uneinheitlich (`anlaufstelle/app` vs. `ghcr.io/anlaufstelle/app`).

**Konsolidierte Empfehlung:** 
Für die Zielgruppe ist ein Managed-Hosting- oder Betreiber-Modell fast wichtiger als weitere Features. Ohne Betriebsunterstützung verfehlt das Projekt viele kleine Einrichtungen.

---

### 4.7 Lizenz / Governance / Nachhaltigkeit

**Stärken**

- AGPL ist klar eingebunden.
- CONTRIBUTING existiert.
- Dokumentation/Fachkonzept existiert.
- Open-Source-Positionierung ist plausibel.

**Schwächen**

- AGPL-Netzwerkhinweis/Source-Link fehlt laut Audits in der Weboberfläche.
- `SECURITY.md` ist teils stale bzw. verweist auf alten Namespace.
- Kein Code of Conduct.
- Kein klarer DCO/CLA-Prozess.
- Bus-Factor offenbar sehr niedrig.
- Release-Workflow ohne sichtbare SBOM/Signierung/Provenance.
- Co-Maintainer-/Trägerstruktur fehlt.

**Konsolidierte Empfehlung:** 
Vor externer Verbreitung: AGPL-Footer/Source-Link, Security Policy aktualisieren, Governance-Minimum ergänzen, Co-Maintainer suchen, ADRs schreiben, SBOM/Cosign/Provenance für Releases.

---

## 5. Widersprüche zwischen den Audits und konsolidierte Einordnung

### CSP / `unsafe-eval`

Einige Audits nennen `unsafe-eval` als bestehende CSP-Schuld, andere verweisen darauf, dass v0.10.2 Alpine-CSP-Migration und härtere CSP enthält. Das muss im aktuellen Code final verifiziert werden. Konsolidiert gilt: **nicht als Top-Blocker bewerten, aber als Sicherheits-Check in die Roadmap aufnehmen.**

### AuditLog-Immutable

Ein Audit kritisiert, AuditLog sei nur im Python-Modell immutable; andere nennen eine DB-Trigger-Migration. Konsolidiert gilt: **verifizieren, ob DB-Trigger für AuditLog im aktuellen Head wirklich aktiv, getestet und bei Restore erhalten sind.** EventHistory ist davon unabhängig trotzdem ein kritisches Datenschutzthema.

### Gesamtbewertung 6.5 vs. 7.5

Die Abweichung erklärt sich aus Perspektive: technisch/architektonisch ist das Projekt eher 7–8/10; produktionsrechtlich/operativ für sensible Sozialdaten eher 5–6/10. Konsolidiert: **starkes Pre-Release, aber kein unbegleiteter Produktivbetrieb.**

---

## 6. Priorisierte Maßnahmenliste

### — Produktivblocker schließen

| Prio | Maßnahme | Aufwand | Impact |
|---:|---|---:|---:|
| 1 | Retention/EventHistory redaktieren; bestehende nicht-redaktierte Delete-History migrieren | M | sehr hoch |
| 2 | Regressionstests für alle Löschpfade | S | sehr hoch |
| 3 | vollständige Anonymisierungs-Matrix für Client/Event/Case/Episode/WorkItem/Attachment/History/Snapshot | L | sehr hoch |
| 4 | CSV-Formula-Injection zentral escapen | S | hoch |
| 5 | RLS-Test mit echter Non-Superuser-Produktionsrolle in CI | M | hoch |
| 6 | `Sensitivity=HIGH => encrypted=True` erzwingen | S/M | hoch |
| 7 | Klartext-Freitexte absichern oder in geschütztes Field-Modell überführen | M/L | hoch |
| 8 | persistentes Medienvolume + DB/Media/Key-Restore-Drill | M | hoch |

### — Pilotfähig mit Echtdaten unter Aufsicht

| Prio | Maßnahme | Aufwand | Impact |
|---:|---|---:|---:|
| 9 | Export-/Reporting-Suppression für kleine Zellen/K-Anonymität | M | hoch |
| 10 | WorkItem-Rechte konsolidieren | S/M | mittel |
| 11 | Facility-scoped API erzwingen: `get_for_facility`, Lint gegen unsichere Queries | M | hoch |
| 12 | Offline-Modus begrenzen: Leases, Gerätepolicy, Konfliktaudit, Key-Rotation | M/L | hoch |
| 13 | Backup-Monitoring, Cronstatus, ClamAV-Status, Retention-Job-Status | M | mittel |
| 14 | SECURITY.md, README, Image-Namespace, Doku-Drift korrigieren | S | mittel |
| 15 | AGPL-Source-Link + Versionshinweis im UI | S | mittel |

### — 1.0-Reife

| Prio | Maßnahme | Aufwand | Impact |
|---:|---|---:|---:|
| 16 | ADRs für RLS, Retention/History, Offline-Krypto, Statistik-Read-Model | M | hoch |
| 17 | mypy/pyright für `core/services` schrittweise aktivieren | M | mittel |
| 18 | Security-Scanner ergänzen: Bandit/Semgrep/Ruff-S/Bugbear | M | mittel |
| 19 | Statistics als eigenes Read-Model klären | M/L | hoch |
| 20 | FieldTemplate-Versionierung oder historische Felddefinitionen | L | mittel |
| 21 | Multi-Facility-/Organization-Modell sauber entwerfen | L | hoch |
| 22 | SBOM, Cosign, Provenance, Dependency-License-Scan | M | mittel |
| 23 | Endnutzerhandbuch für Sozialarbeitende | M | mittel |
| 24 | Co-Maintainer-/Governance-Struktur | XL | hoch |

---

## 7. Finale Einschätzung

**Anlaufstelle sollte nicht verworfen werden — im Gegenteil.** Die Basis ist stark genug, dass sich Härtung lohnt. Das Projekt hat ein echtes Problemfeld, ein gutes Fachmodell und eine ungewöhnlich ernsthafte Sicherheitsarchitektur.

Aber der nächste Schritt darf nicht primär „mehr Features“ sein. Der nächste Schritt muss sein:

1. **Datenlebenszyklus wasserdicht machen.** 
2. **Produktivbetrieb realistisch machen.** 
3. **Governance und Wartbarkeit stabilisieren.**

Erst danach ist die Software überzeugend genug für den Satz: „Ja, hier können echte sensible Sozialdaten hinein.“
