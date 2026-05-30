# Konsolidierter Gesamt-Audit: `anlaufstelle/app`

**Zusammenführung aus zwei konsolidierten Audit-Dokumenten** 
**Basisdokumente:** 
1. `konsolidierter-audit-anlaufstelle-chatgpt.md` 
2. `konsolidierter-audit-anlaufstelle-claude.md` 

**Geprüfter Stand laut beiden Dokumenten:** Commit `35e0f5b`, Tag/Release `v0.10.2`, 28.04.2026 
**Ziel dieser Fassung:** Die Management-Klarheit des ChatGPT-Dokuments mit der technischen Tiefe und Priorisierung des Claude-Dokuments verbinden.

---

## 0. Ergebnis der Zusammenführung

Ja, es macht Sinn, aus beiden Dokumenten **ein einziges Referenzdokument** zu bauen.

Die beiden Fassungen widersprechen sich im Kern nicht. Sie haben unterschiedliche Schwerpunkte:

| Dokument | Charakter | Stärke | Schwäche |
|---|---|---|---|
| `konsolidierter-audit-anlaufstelle-chatgpt.md` | kompakt, entscheidungsorientiert | klare Gesamtbewertung, gut lesbar, starke Management-Zusammenfassung | weniger technische Detailtiefe, weniger konkrete Einzelbefunde |
| `konsolidierter-audit-anlaufstelle-claude.md` | tief, technisch, auditnah | Methodik, Kontroversen, konkrete Belegstellen, Sprint-Roadmap, deduplizierte Maßnahmen | sehr lang, für Nicht-Techniker schwerer zu lesen |

**Empfohlene Verwendung:** 
Diese Gesamtfassung sollte als zentrales Audit-Dokument dienen. Das ChatGPT-Dokument eignet sich als Executive Summary, das Claude-Dokument als technischer Anhang. Für Projektplanung, Förderantrag, GitHub-Issues und v1.0-Roadmap ist eine zusammengeführte Fassung sinnvoller als zwei parallele Dokumente.

---

# 1. Executive Summary

`anlaufstelle/app` ist ein fortgeschrittenes Open-Source-Fachsystem für niedrigschwellige Sozialarbeit. Es ist kein CRUD-Prototyp, sondern ein fachlich ernst gemeinter, sicherheitsbewusster Django-Monolith mit Domänenmodell, PostgreSQL-RLS, Service-Layer, Retention, Audit, Feld-/Dateiverschlüsselung, MFA, Offline-Ansätzen, Tests und Dokumentation.

**Konsolidierte Gesamtbewertung: 7,0 / 10**

| Dimension | Bewertung |
|---|---:|
| Fachliches Konzept | **8/10** |
| Architektur | **7/10** |
| Codequalität | **7–8/10** |
| Security-Design | **7/10** |
| tatsächliche Security-/DSGVO-Durchsetzung | **5–6/10** |
| Betrieb / Self-Hosting | **5/10** |
| Open-Source-/Governance-Reife | **5–6/10** |
| Pilotbetrieb mit Aufsicht | **ja** |
| Regelbetrieb mit sensiblen Echtdaten | **noch nein** |

**Kernaussage:** 
Anlaufstelle ist **förderwürdig, pilotfähig und technisch substanziell**, aber noch nicht bereit für unbegleiteten Produktivbetrieb mit echten sensiblen Sozialdaten. Die nächsten Schritte müssen Härtung, Betrieb und Governance priorisieren — nicht Feature-Ausbau.

---

# 2. Differenz der beiden Ursprungsdokumente

## 2.1 Inhaltliche Differenz

Das ChatGPT-Dokument liefert eine gute, kompakte Konsolidierung mit klaren Blöcken:

- Gesamtbewertung
- Was gut ist
- kritische Blocker
- Befunde nach Bereichen
- Widersprüche zwischen Audits
- priorisierte Maßnahmenliste
- finales Urteil

Das Claude-Dokument geht deutlich tiefer:

- Methodik über acht Audits
- Einordnung der Modellfamilien und Bewertungsstreuung
- Konsens-Stärken und Konsens-Schwächen
- Kontroversen zwischen Audits
- viele Einzelbefunde mit Schwere/Konsens
- sehr konkrete technische Maßnahmen
- 4–6-Wochen-Härtungssprint
- Quick Wins
- Anhang: welche Ausgangsaudits wofür nützlich sind

## 2.2 Bewertungsdifferenz

Beide Dokumente kommen praktisch zum selben Reifegrad:

- ChatGPT: technisch stark, aber produktiv noch nicht freigabefähig.
- Claude: konsolidierte Bewertung 7,0 / 10; Pilot ja, Regelbetrieb nein.

Die Unterschiede liegen eher im Risikoappetit und in der Granularität:

- ChatGPT formuliert stärker als Management-Entscheidung.
- Claude formuliert stärker als technischer Umsetzungsplan.

## 2.3 Konkrete Zusatzpunkte des Claude-Dokuments

Das Claude-Dokument ergänzt gegenüber der ChatGPT-Fassung insbesondere:

1. **MEDIA_ROOT-Volume fehlt in `docker-compose.prod.yml`** 
 Potenzieller stiller Datenverlust bei verschlüsselten Anhängen.

2. **Image-Namespace-Drift** 
 Uneinheitliche GHCR-Images/Namensräume zwischen Prod, Staging und Repository.

3. **AGPL §13 nicht im UI umgesetzt** 
 Kein Source-Link/„Powered by“-Hinweis für Netzwerknutzung.

4. **Bus-Faktor 1** 
 Governance- und Nachhaltigkeitsrisiko.

5. **Service-Layer-Konsistenz-Lücken** 
 Einzelne Pfade umgehen oder duplizieren Policy-Logik.

6. **Rate-Limit-/Autocomplete-Probleme** 
 Sensitive GET-Endpunkte sind nicht in allen Architekturtests abgedeckt.

7. **Performance-/N+1-Befunde** 
 Zeitstrom, Attachments, Pagination, JSONB-Indizes.

8. **Barrierefreiheit/I18n** 
 `html lang`, Fokusmanagement nach HTMX-Swaps, `aria-describedby`.

9. **Detaillierte Sprint-Roadmap** 
 Besonders nützlich für GitHub-Issues und v1.0-Planung.

## 2.4 Konkrete Stärke des ChatGPT-Dokuments

Das ChatGPT-Dokument ist deutlich besser als Einstieg:

- verständlicher für Nicht-Techniker,
- klarere Gesamtbewertung,
- weniger überfrachtet,
- gut geeignet für Förderantrag, Projektübersicht oder Entscheidungsvorlage.

---

# 3. Gemeinsames Kurzurteil

Anlaufstelle ist ein sehr gutes, fachlich fundiertes Pre-Production-System. Die Software zeigt echte Domänenkenntnis und einen ungewöhnlich hohen Sicherheitsanspruch für ein NGO-/Open-Source-Projekt.

Die größten Risiken liegen nicht im Grundstack und nicht in der Produktidee, sondern in vier Bereichen:

1. **Datenschutz-Lifecycle** 
 Retention, EventHistory, Anonymisierung, Freitextfelder und Exporte sind noch nicht durchgehend hart.

2. **Security-Durchsetzung** 
 RLS, Service-Layer, Encryption und Facility-Scoping existieren, sind aber nicht überall als harte, getestete Invarianten abgesichert.

3. **Betrieb** 
 Self-Hosting ist vorbereitet, aber für kleine Einrichtungen ohne IT nicht verantwortungsvoll genug. Medien, Backups, Restore, Monitoring und Image-Tags brauchen Härtung.

4. **Governance** 
 Bus-Faktor, AGPL-Hinweis, Security Policy, Release-Supply-Chain und Co-Maintainer-Struktur sind noch nicht v1.0-reif.

---

# 4. Konsens-Stärken

## 4.1 Außergewöhnliche Domänenpassung

Die Software modelliert niedrigschwellige Sozialarbeit deutlich besser als ein generisches CRM:

- pseudonymisierte Klienten statt Klarnamen-Zwang,
- anonyme Kontakte,
- Zeitstrom statt klassischer Akte,
- Events als zentrale Dokumentationseinheit,
- WorkItems/Hinweise/Aufgaben neben Dokumentation,
- Fälle/Episoden/Ziele,
- Jugendamts-/Statistiklogik,
- Retention und Legal Holds,
- Offline-/Streetwork-Anforderungen.

Das ist die größte Stärke des Projekts.

## 4.2 Passender Architekturansatz

Django + PostgreSQL + HTMX + serverseitiges Rendering ist für kleine Einrichtungen sinnvoller als Microservices oder eine schwere SPA/API-Plattform.

Der Monolith ist nicht das Problem. Das Risiko entsteht erst dadurch, dass fast alles in einer großen `core`-App liegt und Security-Policy über viele Stellen verteilt ist.

## 4.3 Echtes Security-Design

Vorhanden sind unter anderem:

- PostgreSQL Row Level Security,
- `FacilityScopeMiddleware`,
- facility-scoped Manager,
- Rollen-/Sensitivitätslogik,
- Feldverschlüsselung mit Fernet/MultiFernet,
- verschlüsselte Dateiablage,
- MFA,
- Rate-Limits und Account-Lockout,
- AuditLog,
- EventHistory,
- ClamAV,
- produktive Fail-Closed-Settings,
- CI mit Tests, Ruff, pip-audit und E2E-Ansatz.

Das ist für ein Pre-Release in dieser Zielgruppe stark.

## 4.4 Service-Layer existiert wirklich

Die Geschäftslogik ist nicht vollständig in Views oder Models verteilt. Es gibt Services für Events, Retention, Export, File Vault, Offline, Statistik, Sensitivität, Lockout, MFA und Audit.

Aber: Der Service-Layer ist noch nicht überall eine harte Policy-Grenze.

## 4.5 Test- und Dokumentationsbasis ist substanziell

Positiv:

- viele Tests,
- Playwright/E2E,
- Architekturtests,
- CI,
- Fachkonzept,
- Admin-/Ops-Dokumentation,
- DSGVO-Templates,
- CONTRIBUTING.

Das ist deutlich mehr als bei vielen frühen Open-Source-Fachverfahren.

---

# 5. Kritische Blocker vor Echtdaten-Regelbetrieb

## Blocker 1: Retention löscht nicht wirklich, weil `EventHistory` Daten behalten kann

Automatische Retention löscht Event-Nutzdaten aus `Event.data_json`, kann dieselben Werte aber vorher in `EventHistory.data_before` schreiben. Da EventHistory append-only ist, bleibt der sensible Inhalt erhalten.

Zusätzlich unterscheiden sich manuelles Soft-Delete und Retention in ihrer Historien-Semantik: manuelles Löschen redaktiert stärker, Retention kann vollständige Daten historisieren.

**Schwere:** kritisch 
**Warum kritisch:** Die fachliche Aussage „gelöscht“ ist materiell nicht erfüllt. 
**Maßnahmen:**

- zentrale Funktion `record_delete_history(event, redacted=True)`,
- alle Löschpfade darüber führen,
- bestehende nicht-redaktierte DELETE-History migrieren/anonymisieren,
- Regressionstests gegen Klartext in `EventHistory.data_before`.

---

## Blocker 2: Anonymisierung ist nicht aggregatweit vollständig

`Client.anonymize` räumt Client-nahe Daten auf, berührt aber nicht zwingend alle Restspuren:

- EventHistory,
- EventAttachments,
- DeletionRequests,
- StatisticsSnapshots,
- Audit-/Activity-Spuren,
- Search-Indizes,
- Exporte.

**Schwere:** kritisch bis hoch 
**Maßnahmen:**

- Anonymisierung als Aggregat-Operation definieren,
- Datenklassen-Matrix erstellen,
- Tests: „Restdaten nach Anonymisierung == 0“,
- fachlich entscheiden, welche Audit-Spuren bleiben dürfen und wie sie minimiert werden.

---

## Blocker 3: Klartext-Freitexte liegen außerhalb des geschützten Field-Modells

Das dynamische Event-Feldsystem kann Sensitivität und Verschlüsselung abbilden. Daneben gibt es aber klassische Freitextfelder:

- `Client.notes`,
- `Case.description`,
- `Episode.description`,
- `WorkItem.description`,
- `AuditLog.detail`.

Gerade dort werden Nutzer sehr wahrscheinlich sensible Informationen eintragen.

**Schwere:** hoch 
**Maßnahmen:**

- alle Freitextfelder inventarisieren,
- je Feld klassifizieren: erlaubt, sensibel, verschlüsselt, verboten, retention-pflichtig,
- mindestens `Client.notes`, `Case.description` und `WorkItem.description` in ein Sensitivity-/Encryption-Modell bringen,
- UI-Hinweise reichen nicht als alleinige Schutzmaßnahme.

---

## Blocker 4: RLS ist vorhanden, aber nicht ausreichend realitätsgetestet

RLS ist ein starker Schutzmechanismus. Aber wenn Tests mit Superuser-/Owner-Rechten laufen, beweisen sie nicht, dass RLS im echten Betrieb greift.

**Schwere:** kritisch bis hoch 
**Maßnahmen:**

- dedizierte Postgres-Rolle ohne Superuser-Rechte in CI,
- Cross-Tenant-SELECT-Tests,
- Tests für Raw SQL, Search, Export, Materialized Views und Management Commands,
- `app.current_facility_id` für anonyme Requests explizit leeren.

---

## Blocker 5: `MEDIA_ROOT` / Medienbetrieb ist nicht ausreichend abgesichert

Wenn verschlüsselte Anhänge nicht persistent gemountet und gesichert werden, entsteht stiller Datenverlust.

**Schwere:** kritisch 
**Maßnahmen:**

- persistentes Volume für `MEDIA_ROOT`,
- Backup/Restore für DB + Medien,
- Restore-Drill,
- Test: Anhang bleibt nach Container-Recreate verfügbar.

---

## Blocker 6: CSV-Export braucht Formula-Injection-Schutz

CSV-Werte aus Pseudonymen und dynamischen Feldern dürfen nicht ungefiltert in Excel/LibreOffice landen.

**Schwere:** hoch 
**Maßnahmen:**

- zentraler CSV-Sanitizer,
- gefährliche Präfixe escapen: `=`, `+`, `-`, `@`, Tab, CR/LF,
- Regressionstests für alle Exportpfade.

---

## Blocker 7: `Sensitivity=HIGH` muss Verschlüsselung erzwingen

Wenn hochsensible Felder zwar als HIGH markiert sind, Verschlüsselung aber optional bleibt, ist das Schutzmodell inkonsistent.

**Schwere:** hoch 
**Maßnahmen:**

- Validator: `Sensitivity=HIGH => is_encrypted=True`,
- Migration/Management-Command zur Prüfung bestehender FieldTemplates,
- Test gegen falsch konfigurierte Felder.

---

# 6. Weitere wichtige Befunde

## 6.1 Architektur

**Stärken**

- Modularer Monolith ist passend.
- Kein Microservice-Overengineering.
- Service-Layer vorhanden.
- RLS + Middleware + Manager als Defense-in-Depth.
- SSR/HTMX passt zur Zielgruppe.

**Schwächen**

- `core` enthält praktisch alles.
- `services/event.py` und `services/retention.py` sind Hotspots.
- Policy-Logik ist über Views, Services, Models, Middleware verteilt.
- Reporting/Statistics braucht klareres Read-Model.
- Multi-Facility-/Trägerstruktur ist noch schwach modelliert.

**Empfehlung:** 
Kein sofortiger App-Split als Selbstzweck. Erst Import-Linter/Grimp, Service-Grenzen, ADRs und Policy-APIs. Danach kontrollierter Split in z. B. `clients`, `documentation`, `workitems`, `retention`, `reporting`, `audit`, `offline`.

---

## 6.2 Datenschutz / DSGVO

Weitere relevante Risiken:

- `Client.pseudonym` liegt wahrscheinlich im Klartext.
- `StatisticsSnapshot.data` kann sensible Aggregate enthalten.
- K-Anonymität greift nicht automatisch auf alle Statistik-/Exportpfade.
- Lösch-/Anonymisierungsworkflow für Clients, Cases, Users ist noch nicht vollständig.
- AuditLog/EventHistory-Retention ist fachlich offen.
- Offline-Modus bringt entschlüsselte Daten in den Browserkontext.

**Empfehlung:** 
Eine zentrale Datenschutz-Policy-Schicht definieren: Sensitivity, Encryption, Retention, Export, Search, Offline, History und Audit dürfen nicht je Feature neu interpretiert werden.

---

## 6.3 Sicherheit / Access Control

Weitere relevante Risiken:

- Encryption über `save` kann durch `bulk_create`, `update(data_json=...)` oder Raw SQL umgangen werden.
- WorkItem-Edit-Policy ist möglicherweise inkonsistent.
- Autocomplete/GET-Endpunkte sind nicht überall blockierend rate-limited.
- Service-Layer enthält einzelne Pfade ohne ausreichende User-/Sensitivity-Checks.
- Materialized Views können RLS umgehen, wenn sie direkt abgefragt werden.

**Empfehlung:** 
Architekturtests ergänzen, die gefährliche Patterns verbieten, statt nur einzelne Bugs zu fixen.

---

## 6.4 Betrieb / Deployment

Wichtige Risiken:

- `:latest`-Tags statt gepinnter Versionen.
- Image-Namespace-Drift.
- Off-Site-Backups fehlen oder sind nicht ausreichend beschrieben.
- Restore-Verifikation ist zu flach.
- ClamAV-Ausfall muss eindeutig fail-closed/503 sein.
- Key-Rotation-Runbook fehlt.
- SBOM, Signierung, Provenance fehlen.
- Self-Hosting ist für kleine NGOs ohne IT zu anspruchsvoll.

**Empfehlung:** 
Managed-Hosting- oder Betreiber-Modell prüfen. Für die Zielgruppe ist Betriebssicherheit fast wichtiger als neue Features.

---

## 6.5 Codequalität / Wartbarkeit

Wichtige Risiken:

- kein mypy/pyright in CI,
- Ruff-Regeln zu schmal,
- kein Bandit/Semgrep,
- N+1-Risiken im Zeitstrom/Attachments,
- Pagination ohne harte Caps,
- JSONB-Filter ohne passende Indizes,
- Offline-JS ist eine eigene Komplexitätsinsel,
- Doku-Drift zwischen Code und Dokumentation.

**Empfehlung:** 
Erst risiko-orientierte Tests und Lints einführen, dann Refactorings.

---

## 6.6 Governance / Open Source

Wichtige Risiken:

- Bus-Faktor 1,
- AGPL §13 Source-Link fehlt im UI,
- SECURITY.md/Namensräume teils stale,
- kein Code of Conduct,
- keine klare DCO/CLA-Entscheidung,
- keine Co-Maintainer-Struktur,
- keine Release-Signierung/SBOM.

**Empfehlung:** 
Vor v1.0 Governance-Minimum herstellen: Source-Link, Security Policy, Co-Maintainer, ADRs, Release-Prozess.

---

## 6.7 Fachliche Eignung

Fachlich ist die Software stark. Offene Punkte:

- anonyme Kontakte sind Event-Level, nicht Client-Stage,
- Kontaktstufen-Doku und Datenmodell müssen sauberer getrennt werden,
- `contact_stage`-Hilfetext darf nicht zu Klarnamen-Erfassung verleiten,
- mehrere Aliase pro Person sind nicht modelliert,
- User:Facility ist eher 1:1 und für Springer-/Trägerstrukturen zu eng.

---

## 6.8 Barrierefreiheit / I18n

Noch zu prüfen bzw. zu verbessern:

- `html lang` dynamisch setzen,
- Fokusmanagement nach HTMX-Swaps,
- `aria-describedby` bei Formularfehlern,
- axe/Pa11y-Tests,
- Übersetzungsaktualität als Release-Check.

---

# 7. Kontroversen und konsolidierte Auflösung

## 7.1 CSP / `unsafe-eval`

Einige Audits markieren `unsafe-eval` als offen, andere sehen v0.10.2 als gefixt.

**Konsolidierte Auflösung:** 
Wahrscheinlich ist der Code gefixt, aber Kommentar/Dokumentation ist stale. Nicht als Top-Blocker behandeln, aber in der Roadmap als Verifikationspunkt führen.

## 7.2 „Würde ich sensible Sozialdaten anvertrauen?“

Die Audits reichen von „ja“ bis „heute nein“. Die Differenz entsteht durch Risikoappetit.

**Konsolidierte Auflösung:**

- Pilotbetrieb: ja, mit Aufsicht und begrenztem Scope.
- Produktiver Regelbetrieb: nein, nicht vor Schließen der Blocker.

## 7.3 Service-Layer: stark oder inkonsistent?

Beides stimmt.

**Konsolidierte Auflösung:** 
Der Service-Layer existiert und ist überdurchschnittlich, aber noch keine harte Sicherheitsgrenze. Die Lücken sind fixbar, müssen aber systematisch geschlossen werden.

## 7.4 App-Split nötig?

Nicht sofort, aber absehbar.

**Konsolidierte Auflösung:** 
Vor v1.0 erst Modulgrenzen mit Import-Linter absichern. Danach kontrollierter Split, wenn neue Domänen wachsen.

---

# 8. Priorisierte Maßnahmenliste

## — Sofort: Datenverlust und Produktivblocker

| # | Maßnahme | Aufwand | Impact |
|---:|---|:---:|:---:|
| 1 | `MEDIA_ROOT`-Volume in Prod-Compose mounten | S | kritisch |
| 2 | Backup/Restore um Medien erweitern | S/M | kritisch |
| 3 | Restore-Test mit Attachment nach Container-Recreate | S | kritisch |
| 4 | Retention/EventHistory redaktieren | M | kritisch |
| 5 | Datenmigration für bestehende nicht-redaktierte Delete-History | M | kritisch |
| 6 | RLS-Test mit Non-Superuser-DB-Rolle in CI | M | kritisch |
| 7 | Image-Namespace vereinheitlichen und Tags pinnen | S | hoch |
| 8 | AGPL §13 Source-Link in UI ergänzen | S | hoch |

---

## — Security-Durchsetzung

| # | Maßnahme | Aufwand | Impact |
|---:|---|:---:|:---:|
| 9 | Service-Layer-Konsistenz-Sweep | M | hoch |
| 10 | WorkItem-Edit-Policy vereinheitlichen | S | hoch |
| 11 | `FacilityScopeMiddleware`: Facility-ID bei anonymen Requests leeren | S | hoch |
| 12 | `Sensitivity=HIGH => is_encrypted=True` erzwingen | S | hoch |
| 13 | Architekturtest gegen `bulk_create`/`update(data_json=...)` | S | hoch |
| 14 | CSV-Formula-Injection-Escaping | S | hoch |
| 15 | Login-/Autocomplete-Rate-Limits härten | M | mittel |
| 16 | AuditLog-DB-Trigger verifizieren | S | mittel |

---

## — Datenschutz-Vollständigkeit

| # | Maßnahme | Aufwand | Impact |
|---:|---|:---:|:---:|
| 17 | Anonymisierungs-Cascade vervollständigen | M/L | kritisch |
| 18 | Tests „Restdaten nach Anonymisierung == 0“ | M | kritisch |
| 19 | Freitextfelder inventarisieren und klassifizieren | S | hoch |
| 20 | `Client.notes`, `Case.description`, `WorkItem.description` absichern | M | hoch |
| 21 | K-Anonymität auf externe Berichte anwenden | M | hoch |
| 22 | DeletionRequest-Approval-Workflow umsetzen | M | hoch |
| 23 | Lösch-/Anonymisierungsmatrix je Datenklasse | M | hoch |
| 24 | `StatisticsSnapshot.data` prüfen/verschlüsseln/minimieren | M | mittel |

---

## — Betrieb / Operations

| # | Maßnahme | Aufwand | Impact |
|---:|---|:---:|:---:|
| 25 | Off-Site-Backup mit restic/rclone/S3 | S/M | hoch |
| 26 | Restore-Drill dokumentieren und testen | M | hoch |
| 27 | Healthcheck für ClamAV/RLS/Retention/Backup schärfen | S/M | mittel |
| 28 | Caddy-Rate-Limit für sensible Endpunkte | S | mittel |
| 29 | Encryption-Key-Rotation-Runbook | S | mittel |
| 30 | SBOM, Cosign, Provenance | M | mittel |
| 31 | Dependabot/Trivy/Bandit/Semgrep ergänzen | S/M | mittel |
| 32 | `pyclamd` ersetzen/aktualisieren | S | mittel |

---

## — Performance / Wartbarkeit

| # | Maßnahme | Aufwand | Impact |
|---:|---|:---:|:---:|
| 33 | N+1 im Zeitstrom beheben | M | hoch |
| 34 | Pagination-Caps setzen | S | hoch |
| 35 | Attachment-Listen paginieren | M | mittel |
| 36 | JSONB-Indizes oder Reporting-Fact-Modell prüfen | M/L | mittel/hoch |
| 37 | `SESSION_SAVE_EVERY_REQUEST=False` prüfen | S | mittel |
| 38 | Search auf nicht-verschlüsselte Field-Slugs beschränken | M | mittel |
| 39 | `services/event.py` splitten | M | mittel |
| 40 | `services/retention.py` fachlich zerlegen | L | hoch |

---

## — Governance / v1.0-Reife

| # | Maßnahme | Aufwand | Impact |
|---:|---|:---:|:---:|
| 41 | SECURITY.md aktualisieren | S | mittel |
| 42 | Doku-Drift AES-GCM/Fernet korrigieren | S | mittel |
| 43 | README-Quickstart korrigieren | S | niedrig |
| 44 | Code of Conduct ergänzen | S | mittel |
| 45 | DCO/CLA-Entscheidung treffen | S | mittel |
| 46 | ADRs für RLS, Retention, Offline, Statistik, AGPL | M | mittel |
| 47 | Co-Maintainer suchen | XL | hoch |
| 48 | Import-Linter/Grimp für Modulgrenzen | S/M | mittel |
| 49 | mypy/pyright schrittweise aktivieren | M | mittel |
| 50 | Ruff-Regeln erweitern | S | mittel |
| 51 | axe/Pa11y-Tests ergänzen | M | mittel |

---

# 9. Erste drei Maßnahmen

Wenn nur drei Dinge sofort umgesetzt werden können, dann diese:

## 1. Medien-Datenverlust schließen

- `MEDIA_ROOT` persistieren,
- Backups um Medien erweitern,
- Restore-Test durchführen.

Ohne diesen Fix können verschlüsselte Anhänge verloren gehen.

## 2. Retention/EventHistory reparieren

- Lösch-Historie redaktieren,
- bestehende Klartext-History migrieren,
- Regressionstest ergänzen.

Ohne diesen Fix ist „Löschung“ fachlich und rechtlich nicht belastbar.

## 3. Live-RLS-Test in CI

- echte Non-Superuser-DB-Rolle,
- Cross-Tenant-SELECT-Tests,
- negative Tests für Raw SQL/Search/Export.

Ohne diesen Test ist RLS ein Designversprechen, kein geprüfter Schutz.

---

# 10. Empfohlene Struktur für GitHub-Issues

Diese Gesamtfassung lässt sich gut in Epics aufteilen:

1. **Epic: Datenschutz-Lifecycle**
 - Retention/EventHistory
 - Anonymisierung
 - Freitexte
 - DeletionRequest
 - K-Anonymität

2. **Epic: Security-Invariants**
 - RLS-Test
 - Service-Layer-Policy
 - Encryption-Erzwingung
 - CSV-Injection
 - Rate-Limits

3. **Epic: Betrieb**
 - MEDIA_ROOT
 - Backup/Restore
 - Image-Tags
 - Healthchecks
 - Key-Rotation

4. **Epic: Governance**
 - AGPL-Footer
 - SECURITY.md
 - ADRs
 - Co-Maintainer
 - Release-Supply-Chain

5. **Epic: Wartbarkeit**
 - Import-Linter
 - mypy/pyright
 - Ruff-Erweiterung
 - Service-Splits
 - Performance

---

# 11. Finale Einschätzung

Anlaufstelle sollte nicht verworfen werden. Im Gegenteil: Die Basis ist stark genug, dass sich Härtung lohnt.

Das Projekt hat:

- ein echtes Problemfeld,
- eine plausible Zielgruppe,
- sichtbare Domänenkenntnis,
- einen passenden Stack,
- ungewöhnlich viele Security-Bausteine,
- gute Voraussetzungen für Förderung und Pilotierung.

Aber der nächste Schritt darf nicht primär Feature-Ausbau sein.

**Empfohlene strategische Reihenfolge:**

1. **Datenlebenszyklus wasserdicht machen.**
2. **Betrieb/Backups/Restore realistisch machen.**
3. **Security-Invarianten testbar machen.**
4. **Governance und Co-Maintenance aufbauen.**
5. **Danach erst neue Fachfeatures ausbauen.**

Erst danach ist die Software überzeugend genug für den Satz:

> Ja, hier können echte sensible Sozialdaten verantwortungsvoll hinein.

---

# 12. Kurzfassung für externe Kommunikation

`anlaufstelle/app` ist ein fachlich sehr starkes Open-Source-System für niedrigschwellige Sozialarbeit. Es modelliert anonyme Kontakte, pseudonymisierte Klienten, ereigniszentrierte Dokumentation, WorkItems, Retention und Reporting deutlich besser als generische CRM- oder Excel-Lösungen.

Technisch ist das Projekt für ein Pre-Release ungewöhnlich reif: Django/PostgreSQL, HTMX, Service-Layer, RLS, Audit, MFA, Verschlüsselung, CI und Tests sind vorhanden.

Für produktiven Regelbetrieb mit sensiblen Sozialdaten fehlen jedoch noch einige Härtungen: insbesondere Retention/EventHistory, vollständige Anonymisierung, RLS-Tests mit echter DB-Rolle, persistente Medien-Backups, Exporthärtung, Governance und Betriebsdokumentation.

**Einschätzung:** 
Pilotfähig mit enger technischer Begleitung. Noch nicht bereit für unbegleiteten Produktivbetrieb.

