# Konsolidiertes Audit: anlaufstelle/app v0.10.2 — Master

**Geprüfter Stand:** Commit `35e0f5b`, Tag `v0.10.2` (2026-04-28)
**Datum der Synthese:** 2026-04-29
**Quellenbasis:** acht unabhängige Quell-Audits, zusammengeführt über zwei
Vor-Konsolidierungen (eine kompakte und eine ausführliche). Dieses Dokument
vereint beide Konsolidierungen ohne Substanzverlust.

---

## Über dieses Dokument

Das Dokument hat drei Teile mit unterschiedlichen Lesepfaden:

- **Teil A — Executive Summary** (~5 Seiten). Für Förderer, Reviewer,
 Stage-2-Gespräche, Aufsichtsfunktionen. Bewertung, sieben Blocker,
 Phasenplan in groben Strichen.
- **Teil B — Detail-Audit mit Belegstellen** (~10 Seiten). Für die tatsächliche
 Umsetzung. K/S-Markierung pro Befund (K = Konsens mehrerer Audits,
 S = Single-Source), Datei:Zeilen-Referenzen, sechs-phasiger Maßnahmenplan
 mit Wochen-Taktung.
- **Teil C — Meta & Strategie** (~3 Seiten). Kontroversen zwischen den
 Quell-Audits, Punkte, die in keinem der Audits sauber adressiert wurden,
 Empfehlung der ersten drei Maßnahmen, Reading Guide für die acht
 Original-Audits.

---
---

# TEIL A — EXECUTIVE SUMMARY

## A.1 Kurzurteil

Anlaufstelle ist kein CRUD-Prototyp, sondern ein fortgeschrittenes
Pre-Release-System mit echtem Domänenverständnis, ernsthafter
Sicherheitsarchitektur und ungewöhnlich starker Test-Disziplin für ein
NGO-Open-Source-Projekt. Pilotbetrieb mit IT-Begleitung ist plausibel.

Für **produktiven Regelbetrieb mit sensiblen Sozialdaten** ist das System in
diesem Zustand jedoch nicht freigegeben. Sieben konkrete Blocker stehen einer
unkritischen Produktivfreigabe im Weg — am schwersten wiegen
Lifecycle-Themen (EventHistory bewahrt Klartext nach Löschung, unvollständige
Anonymisierung), Klartext-Freitexte außerhalb des geschützten Feldmodells und
RLS-Tests, die mit Superuser-Rechten laufen und damit die Mandantentrennung
nicht funktional belegen.

Das Projekt sollte nicht verworfen werden — die Substanz ist stark genug,
dass sich Härtung lohnt. Der nächste Schritt darf aber nicht primär „mehr
Features" sein, sondern: Datenlebenszyklus wasserdicht machen,
Produktivbetrieb realistisch machen, Governance stabilisieren.

## A.2 Konsolidierte Bewertung

| Dimension | Bewertung |
|---|---:|
| Fachliches Konzept | **8/10** |
| Architektur | **7/10** |
| Codequalität | **7–8/10** |
| Sicherheits-Design | **7/10** |
| Datenschutz-Härte (Durchsetzung) | **5–6/10** |
| Betrieb / Self-Hosting | **5/10** |
| Open-Source-Governance | **5–6/10** |
| **Gesamt** | **~7/10** |
| Pilotreife (mit Aufsicht) | **ja** |
| Produktreife für sensible Echtdaten | **noch nein** |

## A.3 Spreizung der Quell-Audits

Die acht Quell-Audits bewerten zwischen 6,5/10 und 9/10. Die Spreizung erklärt
sich durch Risikoappetit, nicht durch Faktendisput:

| Auditor | Prompt-Stil | Bewertung | Tendenz |
|---|---|---:|---|
| ChatGPT-1 | schonungslos | 6,5/10 | Operations-Fokus, kritisch |
| Codex (kritisch) | schonungslos | 6,5/10 | Policy-Layer-Fokus, kritisch |
| Codex (strukturiert) | umfassend | ~7/10 | präzise mit Belegstellen |
| Claude (kritisch) | schonungslos | 7,5/10 | balanciert |
| Claude (strukturiert) | umfassend | ~7/10 | sehr präzise mit Belegstellen |
| Claude (ChatGPT-Prompt) | schonungslos | 7,5/10 | balanciert, ops-aufmerksam |
| Gemini (ChatGPT-Prompt) | schonungslos | 9/10 | enthusiastisch (Ausreißer) |
| Gemini (Claude-Prompt) | umfassend | ~7/10 | strukturiert |

**Lesart:** Gemini-ChatGPT (9/10) liest die *Existenz* der Sicherheits-
mechanismen als ihre *Funktionstüchtigkeit* und übersieht systematisch
Durchsetzungslücken. Die 6,5-Bewertungen gewichten dieselbe Substanz, geben
aber Lifecycle-/Betriebslücken größeres Gewicht. Die 7,5-Bewertungen sind die
mittlere Position. Konsens-Realwert liegt zwischen 6,5 und 7,5.

## A.4 Was das System richtig gut macht

**Domänenpassung ist außergewöhnlich.** Pseudonym als Architektur-Constraint
ohne Klarnamenfeld, drei Kontaktstufen, Zeitstrom statt Akte, WorkItems neben
Events, anonyme Events, Übergabe-Flow, K-Anonymisierung statt Hard-Delete,
Retention/LegalHold als Domänenobjekte. Sieben von acht Auditoren benennen
das explizit. Das ist sichtbar von jemandem mit echter Domänenkenntnis gebaut,
nicht aus einem CRUD-Generator.

**Defense-in-Depth ist real, nicht dekorativ.** PostgreSQL Row Level Security
(`0047_postgres_rls_setup.py`) plus `FacilityScopedManager` plus
`FacilityScopeMiddleware` plus Field-Encryption mit MultiFernet-Rotation plus
AuditLog mit DB-Trigger plus ClamAV fail-closed plus MFA plus Login-Lockout
plus produktions-fail-closed-Settings. Sechs Audits werten das als
überdurchschnittlich.

**Service-Layer existiert wirklich.** 30+ Module in `core/services/`, Views
sind tendenziell dünn, Audit/Encryption/Sensitivity sind zentral implementiert.
Vier Audits werten das als Senior-Level.

**Test-Disziplin ist substanziell.** ~1.500–1.845 Testfunktionen, RBAC-Matrix-
Tests, Architektur-Tests, Playwright-E2E, RLS-Tests (wenn auch mit Lücke,
siehe Blocker 5), CI mit `pip-audit`, Migration-Drift-Check, Lock-Drift-Check.

**Dokumentationstiefe.** Fachkonzept (114 KB), Admin-Guide, Ops-Runbook, FAQ,
DSGVO-Templates, CONTRIBUTING zweisprachig. Für ein Solo-Maintainer-Projekt
ungewöhnlich vollständig.

## A.5 Die sieben Produktivblocker

Vor produktivem Einsatz mit sensiblen Echtdaten müssen mindestens diese
sieben Themen geschlossen werden. Die Reihenfolge ist die empfohlene
Bearbeitungsreihenfolge.

### Blocker 1 — Retention löscht nicht wirklich (kritisch, K)

`services/retention.py:566–580` kopiert vollständige `data_json`-Werte in
`EventHistory.data_before`, bevor das Original geleert wird. EventHistory ist
per Trigger append-only (`0012_eventhistory_append_only_trigger.py`).
Manuelles Soft-Delete schreibt dagegen nur `{"_redacted": True,...}`
divergierende Semantik für „dieselbe" fachliche Operation. Für Art. 5 Abs. 1
lit. e und Art. 17 DSGVO sowie § 67 SGB X ein Blocker.
**Maßnahme:** gemeinsame `record_delete_history(event, redacted=True)`, Daten-
Migration für bestehende nicht-redaktierte Einträge, Regressionstests.

### Blocker 2 — Anonymisierung nicht aggregatweit (kritisch, K)

`Client.anonymize` (`models/client.py:120, 136`) berührt EventHistory,
EventAttachment und DeletionRequest nicht. `enforce_retention` löscht keine
EventAttachment/EventHistory zu soft-deleteten Events.
Re-Identifikation über Audit-/History-Spuren bleibt möglich.
**Maßnahme:** Anonymisierung als Aggregat-Operation, testbare „Restdaten nach
Anonymisierung == 0"-Matrix.

### Blocker 3 — Klartext-Freitexte außerhalb des geschützten Modells (hoch, K)

`Client.notes`, `Case.description`, `Episode.description`, `WorkItem.description`,
`AuditLog.detail` sind Klartext und folgen nicht dem Sensitivity-/Encryption-/
Retention-Modell. Genau dort werden Sozialarbeitende sensible Hinweise
notieren („hat konsumiert", „psychische Krise", „Klarname intern").
**Maßnahme:** Inventarisierung; Überführung in das geschützte FieldTemplate-
Modell oder pro Modell Sensitivität/Verschlüsselung/Retention erzwingen.

### Blocker 4 — Verschlüsselung teilweise optional (hoch, K)

`FieldTemplate.is_encrypted` ist konfigurierbar, auch für Sensitivity=HIGH
(`models/document_type.py:27–30, 79–82`). Zusätzlich liegt
`Client.pseudonym` im Klartext mit Trigram-Index (`models/client.py:35–39, 91`).
Backup-Leak ⇒ direkte Wiedererkennung in Kontaktläden.
**Maßnahme:** Validator `Sensitivity=HIGH ⇒ is_encrypted=True`. Für Pseudonym
prüfen: `EncryptedTextField` plus separater HMAC-/Hash-Lookup-Index
(Trade-off: Trigram-Suche bricht).

### Blocker 5 — RLS nicht funktional getestet (kritisch, K)

`tests/test_rls.py:1–9` bestätigt selbst: Test-DB-User ist Superuser, RLS
wird damit zur Laufzeit umgangen. Wenn ein Coolify-Default oder ein Migrations-
Setup den Django-DB-User als Superuser anlegt, wird RLS still abgeschaltet
kein CI-Alarm. Statistik-Materialized-View ist zusätzlich bewusst ohne RLS
modelliert (`0049_statistics_event_flat_mv.py`).
**Maßnahme:** dedizierte Postgres-Rolle ohne Superuser in CI; Cross-Tenant-
SELECT-Tests mit 0-Rows-Assertion; Tests müssen Raw SQL, MVs, Search, Export
und Management-Commands abdecken.

### Blocker 6 — CSV-Formula-Injection (hoch, S → Codex-Claude)

`services/export.py:88–150` schreibt Pseudonyme und Feldwerte direkt per
`csv.writer`, ohne Neutralisierung für Werte mit `=`, `+`, `-`, `@`, Tab oder
CR/LF. Beim Öffnen in Excel/LibreOffice klassischer Injection-Pfad.
**Maßnahme:** zentraler CSV-Sanitizer; Regressionstest für alle dynamischen
Felder.

### Blocker 7 — Datenverlust durch fehlendes Medien-Volume (kritisch, K)

`docker-compose.prod.yml` mountet kein persistentes Volume für `MEDIA_ROOT`.
`.env.example:53` setzt `MEDIA_ROOT=/data/media`, aber kein Service mountet
`/data`. Verschlüsselte Anhänge sind beim nächsten `docker compose pull && up -d`
weg. Backup-Skript sichert nur DB. Stiller Datenverlust-Bug, kein Ops-Hinweis.
**Maßnahme:** named volume `media:` deklarieren und mounten; `backup.sh` und
`restore.sh` um Medien erweitern; Restore-Test über Container-Recreate.

## A.6 Phasen-Empfehlung in Kurzform

| Phase | Inhalt | Ergebnis |
|---|---|---|
| ** — Härtung vor Pilot** (~3 Wochen) | Blocker 1, 2, 5, 7 vollständig + Blocker 6, Service-Layer-Konsistenz | Pilot mit echten Daten unter Aufsicht möglich |
| ** — Pilotreife** (~4 Wochen) | Blocker 3, 4 + K-Anonymität-Reporting + WorkItem-Rechte + Doku-Konsistenz + AGPL-Footer | Pilotbetrieb formal absicherbar |
| ** — 1.0-Reife** (~6 Wochen) | ADRs, mypy, Statistics-Read-Model, FieldTemplate-Versionierung, SBOM/Cosign, Co-Maintainer | v1.0-Release |

Detaillierte Maßnahmenliste mit 50 Einzelpunkten in **Teil B.5**.

---
---

# TEIL B — DETAIL-AUDIT MIT BELEGSTELLEN

## B.1 Faktenblock

| Kategorie | Wert |
|---|---|
| Letzter Commit | `35e0f5b`, v0.10.2, 2026-04-28 |
| Contributors | 1 |
| Lizenz | AGPL-3.0-or-later |
| Python / Django | 3.13 / 5.1.15 |
| Apps / Models | 1 (`core`) / ~25–29 |
| Views (CBV) | ~82–85 |
| Services | 30–33 Module |
| Migrationen | 73–74 |
| Tests | 150–152 Dateien, ~1.500–1.845 Testfunktionen |
| LOC Python | ~52.819 (gesamt), ~19.780–25.000 in `core/` |
| Templates | 84–86 HTML |
| Kritische Deps | `cryptography 46.0.7`, `django-csp 4.0`, `django-otp 1.7.0`, `django-ratelimit 4.1.0`, `pyclamd 0.4.0` (EOL 2016), `weasyprint 68.1`, `psycopg 3.3.3`, `sentry-sdk 2.58.0` |

## B.2 Befunde nach Dimension

> Schwere: kritisch / hoch / mittel / niedrig / info
> Konsens: K = mehrere Auditoren bestätigen, S = Single-Source

### B.2.1 Datenschutz / DSGVO-Lifecycle (höchste Priorität)

**[kritisch K] Retention-Delete schreibt Klartext in unveränderliche Historie**
— `services/retention.py:566–580` kopiert volle `data_json` in
`EventHistory.data_before`; EventHistory append-only per
`0012_eventhistory_append_only_trigger.py`. Manuelles Soft-Delete
(`services/event.py:582–596`) schreibt dagegen nur Redaktions-Marker.

**[hoch K] Anonymisierungs-Cascade unvollständig** — `Client.anonymize`
(`models/client.py:120, 136`) berührt EventHistory, EventAttachment,
DeletionRequest nicht; `enforce_retention` (`services/retention.py:569–579`)
lässt EventAttachment/EventHistory zu soft-deleteten Events stehen.

**[hoch K] Klartext-Freitext außerhalb des geschützten Feldmodells**
`Client.notes` (`models/client.py:54–58`), `Case.description` /
`Episode.description` (`models/case.py:36–37`), `WorkItem.description`
(`models/workitem.py:89–90` — zusätzlich keine Sensitivity modelliert),
`AuditLog.detail` als Klartext-JSON (`models/audit.py:83`).

**[hoch S — Claude-Claude] `Client.pseudonym` unverschlüsselt + GIN-Index**
`models/client.py:35–39, 91`. Backup-Leak ⇒ direkte Wiedererkennung.

**[hoch S — Claude-Claude] `StatisticsSnapshot.data` als Klartext-JSON**
`models/statistics_snapshot.py:25–26`.

**[mittel K] K-Anonymität schützt nicht alle Statistik-/Exportpfade**
`top_clients` zeigt Pseudonyme (`services/statistics.py:85–103`); Jugendamt-
Statistik aggregiert kleine Kategorien ohne Suppression.

**[mittel S — Codex-Claude] Lösch- und Anonymisierungs-Workflow für Clients/
Cases/Users fehlt** — FAQ `docs/faq.md:427–433`: kein manueller
Löschmechanismus für Clients, Cases/Episodes, User-Accounts; AuditLog
unveränderlich. `DeletionRequest`-Modell vorhanden, View fehlt
(`models/workitem.py:142–190`).

### B.2.2 Sicherheit / Defense-in-Depth-Durchsetzung

**[kritisch K] RLS in CI nicht funktional getestet** — `tests/test_rls.py:1–9`
bestätigt selbst Superuser-DB-User. Statistik-MV bewusst ohne RLS
(`0049_statistics_event_flat_mv.py`).

**[hoch K — ChatGPT-1, Claude-kritisch] Stale `app.current_facility_id`-Risiko
über Connection-Pooling** — `FacilityScopeMiddleware` setzt Variable nur für
authentifizierte Requests; bei `CONN_MAX_AGE=60` und Connection-Reuse für
anonyme Routes bleibt der alte Wert stehen. Latent harmlos, aber Annahme
statt Mechanismus. **Fix:** Variable für anonyme Requests explizit leeren.

**[hoch K] Encryption als `save`-Aspekt umgehbar** — `Event.save` ruft
`_encrypt_sensitive_fields`. `bulk_create`, `update(data_json=...)`,
`update_or_create` ohne `save` oder Raw-SQL umgehen die Verschlüsselung.
**Fix:** Architektur-Test, der `Event.objects.bulk_create` und
`update(data_json=...)` außerhalb `services/encryption.py` verbietet;
mittelfristig Custom-`JSONField` mit transparenter Encryption.

**[hoch K] Encryption für Art.-9-Daten optional**
`FieldTemplate.is_encrypted` konfigurierbar, auch für Sensitivity=HIGH
(`models/document_type.py:27–30, 79–82`). **Fix:** Validator
`Sensitivity=HIGH ⇒ is_encrypted=True`.

**[hoch S — Codex-Claude] CSV-Export anfällig für Formula Injection**
`services/export.py:88–150` ohne Neutralisierung gefährlicher Präfixe.

**[hoch S — Claude-ChatGPT-Prompt] Service-Layer-Konsistenz-Lücken (vier
konkrete Stellen)**:
1. `services/handover._collect_open_tasks(facility)` ohne `user`-Parameter,
 ohne `visible_to(user)` → Pseudonym-Leak über Sensitivity-Grenzen
2. `services/clients.update_client(**fields)` ohne Allowlist (vgl.
 `cases.update_case` mit Whitelist)
3. `services/client_export.export_client_data` überspringt Sensitivity-Filter
4. `services/event.py:563` rollt eigenen `str(updated_at)`-Vergleich statt
 `services/locking.check_version_conflict`

**[hoch S — Codex-kritisch] WorkItem-Edit-Policy inkonsistent**
`WorkItemStatusUpdateView` prüft `can_user_mutate_workitem`, der volle Edit-
Pfad (`workitem_actions.py:119–161`) prüft nur `StaffRequiredMixin`.

**[hoch K] Login-/Autocomplete-Lockout race-anfällig**
`services/login_lockout.py:31–38` ohne `select_for_update`/Redis;
`ClientAutocompleteView` (`views/clients.py:196–234`) ohne `block=True`.
**Fix:** atomares Counting (Redis INCR); `block=True` ergänzen; Architektur-
Test auf sensible GET-Endpunkte.

**[mittel K] AuditLog DB-Immutability für UPDATE/DELETE — verifizieren**
`models/audit.py:104–112`: `save`/`delete` werfen, Raw-SQL möglich. Migration
`0024_auditlog_immutable_trigger.py` adressiert das laut Codex-Claude und
Claude-ChatGPT-Prompt — Audits widersprechen sich. **Action:** im aktuellen
Head verifizieren, ob Trigger für UPDATE und DELETE existieren und greifen.

**[mittel S — Claude-kritisch] Search durchsucht JSONB inklusive
verschlüsselter Tokens** — `services/search.py:64–69`: `data_json__icontains`
matcht in Postgres-JSONB-Repräsentation auch verschlüsselte Tokens.
False-Positive-Treffer als Information-Leak im Konjunktiv. **Fix:** JSONB-
Pfad-Suche pro nicht-verschlüsseltem Field-Slug.

**[mittel K] CSP-`unsafe-eval`-Status — verifizieren und Kommentar
aktualisieren** — `settings/base.py:243–260` beschreibt akzeptiertes
`unsafe-eval`; CHANGELOG v0.10.2 sagt: entfernt. Code ist nach Codex-Claude
gefixt, Kommentar nicht. **Fix:** Kommentar aktualisieren; AdminCSPRelax-
Middleware-Ausnahme verlinken.

**[mittel S — Claude-Claude] CSRF-Token im `<meta>` statt Cookie** — Bei XSS
lesbar; HTTPOnly-Flag auf Cookie konterkariert. **Fix:** HTMX kann Token via
`HX-Headers` aus Cookie lesen.

**[niedrig S — Claude-Claude, ChatGPT-1] `safe_decrypt` fail-open auf
„[verschlüsselt]"** — `services/encryption.py:106–114`: Bei Tampering vs.
Key-Loss kein Indikator-Unterschied. **Fix:** `KeyMissing` vs. `InvalidToken`
unterscheiden.

**[niedrig S — Claude-Claude] Dev-DB-Default-Passwort**
`settings/base.py:96–105`: `POSTGRES_PASSWORD` Default `"anlaufstelle"`.

### B.2.3 Betrieb (Datenverlust + Supply Chain)

**[kritisch K] `MEDIA_ROOT`-Volume fehlt in `docker-compose.prod.yml`**
siehe Blocker 7.

**[hoch K] Image-Namespace-/Tag-Drift** — `docker-compose.prod.yml:17`
verweist auf `ghcr.io/anlaufstelle/app:latest`,
`docker-compose.staging.yml:25` auf `ghcr.io/anlaufstelle/app:latest`,
Repository-Namespace ist `anlaufstelle/app`, SECURITY.md verweist auf
`anlaufstelle/app`. **Fix:** Namespace einheitlich; Pin auf konkrete
Version oder SHA, nicht `:latest`.

**[hoch S — Claude-ChatGPT-Prompt] Keine Off-Site-Backups**
`scripts/backup.sh` schreibt nach `${PROJECT_DIR}/backups/` (gleiche Disk wie
pgdata). Disk-Failure = Total-Verlust inkl. Backups. **Fix:** rclone/restic/
S3-Hook nach erfolgreichem Local-Write.

**[mittel K] Backup-Verifikation flach** — `scripts/backup.sh --verify` prüft
nur `SELECT COUNT(*) FROM core_facility`. **Fix:** Restore-Drill mit
Tabellenanzahlen, Attachment-Dateien, Trigger/RLS, Health-Check.

**[mittel K] Healthcheck bei degradiertem ClamAV gibt 200** — sollte 503
oder fail-closed im Pfad. ChatGPT-1.

**[mittel S — Codex-Claude] Release-Workflow ohne SBOM/Signierung/Provenance**
— `.github/workflows/release.yml:24–38`. **Fix:** SBOM (`syft`/BuildKit),
Cosign, Release-Checksums.

**[mittel S — Claude-ChatGPT-Prompt] Caddy-Edge ohne Rate-Limit**
Brute-Force nur auf App-Layer. **Fix:** Caddy-Rate-Limit-Plugin auf
`/login/`, `/password-reset/`, `/auth/offline-key-salt/`.

**[niedrig K] `pyclamd 0.4.0` (Release 2016, EOL)** — Update auf
`clamd 1.0.6+`.

**[niedrig S — Codex-Claude] `GUNICORN_TIMEOUT=30 s` zu kurz für lange
Migrationen** — `docker-entrypoint.sh:22`.

### B.2.4 Architektur & Code-Wartbarkeit

**[mittel K] Mono-App `core` ohne harte Boundaries** — alles in einer App.
**Konsens-Empfehlung:** vor App-Split zuerst Import-Linter / Grimp einführen.
Mittelfristiger Split-Vorschlag: `accounts`, `clients`, `cases`,
`documentation`, `audit_log`, `retention`, `reporting`, `offline`.

**[mittel K] Type-Hints unter ~14 %, kein mypy/pyright in CI**
`pyproject.toml` aktiviert nur Ruff `E/F/I/W`. **Fix:** mypy schrittweise
für `core/services`, CI-pflichtig mit Baseline.

**[mittel K] Ruff-Set zu schmal** — Bug-Klassen (`B`), Sicherheits-Pattern
(`S`), Komplexität (`C90`), Django (`DJ`) fehlen.

**[mittel S — Claude-ChatGPT-Prompt] `services/event.py` zu groß und mit
zwei Optimistic-Lock-Patterns** — 660+ LOC, mischt CRUD, File-Marker,
Sensitivity, Validation. **Fix:** Split in `event_crud.py` + `event_data.py`.

**[mittel S — Claude-Claude] RunPython ohne `reverse_code` in `0068`**
`0068_attachment_versioning_stage_b.py`.

**[niedrig K] Drei View-Stile koexistieren** — `View`-Subclass mit
hand-gerolltem HTMX, `TemplateView` + `if HX-Request`, neuer
`HTMXPartialMixin`.

**[niedrig S — Claude-ChatGPT-Prompt] `INPUT_CSS` 5× dupliziert** — in
`forms/{clients,cases,events,episodes,workitems}.py`.

**[niedrig S — Claude-ChatGPT-Prompt] AuditLog-Lücken bei State-Transitions**
— `assign_event_to_case`, `remove_event_from_case` loggen nicht.

### B.2.5 Domänenmodell

**Stärken:** `Event` als zentrale Dokumentationseinheit; `DocumentType` +
`FieldTemplate` als konfigurierbares Schema; Sensitivity zweiachsig;
Pseudonym-first; anonyme Events ohne Client; K-Anonymisierung als
First-Class.

**[mittel K] Konzeptionelle Überlappung** — `Case` vs. `Episode`,
`WorkItem` neben `Case`/`Episode`, vier Activity-Streams (`Activity`,
`AuditLog`, `EventHistory`, `RecentClientVisit`).

**[mittel K] Kontaktstufen-Doku nicht deckungsgleich mit Datenmodell**
README beschreibt drei Kontaktstufen, `Client.ContactStage` kennt nur
identified/qualified — anonym ist Event-Level.

**[mittel S — Claude-kritisch] `contact_stage`-Hilfetext gefährlich**
„qualifiziert = vollständige Identität bekannt" widerspricht
„keine Klarnamen".

**[niedrig K] User: Facility ist 1:1** — `User.facility = ForeignKey`.
Springer und Nachschicht-Teams brechen das. **Fix mittelfristig:** M2M über
`OrganizationMembership`.

**[mittel K] Mehrere Aliase pro Person nicht modelliert** — Constraint
`unique_facility_pseudonym` erzwingt 1:1; Streetwork und Drogenhilfe haben
oft mehrere Namen pro Person. **Fix:** optionales `ClientAlias`-Modell.

**[mittel S — Codex-Claude] Anonymität ist Event-Level, nicht als
fortführbarer anonymer Fall modelliert** — wiederkehrende anonyme Kontakte
bleiben einzelne Events.

**[niedrig S — Codex-Claude] Mobile-/Fahrzeug-Streetwork nur über Facility**
Kein Standort-/Tour-/Fahrzeugmodell.

### B.2.6 Performance & Skalierung

**[hoch S — Claude-Claude] N+1 im Zeitstrom-Feed** — `services/feed.py:38–64`,
`views/zeitstrom.py:56` ohne konsequentes Prefetch.

**[hoch K] Pagination ohne `max_page`** — `views/{cases,clients,audit}.py`.
`?page=99999` triggert seq-scan.

**[mittel S — Codex-Claude] Event-Edit N+1 bei Datei-Feldern**
`views/events.py:331–345`: `event.attachments.filter(pk=...).first` in
Schleife.

**[mittel S — Codex-Claude] Attachment-Liste hart auf 200 abgeschnitten**
`views/attachments.py:87–114`.

**[mittel K] JSONB-Filter ohne GIN-Index** — `models/event.py:73–81`.

**[mittel S — Claude-Claude] `SESSION_SAVE_EVERY_REQUEST=True`** — DB-Write-
Amplifikation bei HTMX-Microrequests.

### B.2.7 Tests & QS

**[hoch K] Live-RLS-Test fehlt** — siehe B.2.2.

**[mittel S — Codex-Claude] Rate-Limit-Architekturtest deckt nur POST**
`tests/test_architecture.py:295–359`.

**[mittel S — Claude-Claude] Auth-E2E unvollständig (Password-Reset-Flow)**.

**[mittel S — Claude-Claude] Kein Property-Based-Testing für Validatoren**
Pseudonym, Encryption-Roundtrip, K-Anon-Buckets. **Fix:** Hypothesis.

**[mittel S — Codex-Claude] Kein Axe/Pa11y/Contrast/Fokus-Test in CI**
WCAG 2.2 AA nicht automatisiert belegt.

### B.2.8 Offline / PWA

**Stärken:** Streetwork-Tauglichkeit ist fachlich wertvoll; Offline-Krypto
(PBKDF2/600k, AES-GCM-256, non-extractable Key, Salt-Rotation) und
Konfliktlogik zeigen ernsthafte Produktambition.

**[hoch K] Offline-Cache mit entschlüsselten Daten im Browser** — Threat
Model verschiebt sich auf Endgerät. Während aktiver Session entschlüsselbar.
XSS in Origin-Kontext kann auf entschlüsselte Bundles zugreifen.

**[hoch S — Codex-kritisch] Offline ist eigene Komplexitätsinsel**
Service Worker, IndexedDB, Sync-Queue, Konflikt-Resolution clientseitig
(`conflict-resolver.js`). „Last Write Wins" ohne CRDT.

**Konsolidierte Empfehlung:** Offline erst nach Security-Härtung als
eingeschränkten Pilotmodus freigeben. Klare Gerätepolicy, kurze Offline-
Leases, Remote-Wipe-Konzept, serverseitige Konfliktprüfung, Auditierung aller
Sync-Konflikte. Per Facility/Rolle/Sensitivity steuerbar; High-Sensitivity-
Default „nicht offline".

### B.2.9 Dokumentation & Governance

**[mittel K] AGPL §13 Source-Link/„Powered by" fehlt in UI** — `templates/
base.html`, `templates/auth/login.html:25–31`. **Fix:** Footer-Block mit
AGPL-Hinweis und Quell-URL.

**[mittel K] SECURITY.md stale** — verweist auf `0.9.x` und alten Namespace.

**[mittel K] Doku-Drift Encryption: AES-GCM vs. Fernet**
`docs/admin-guide.md:545–554` sagt AES-GCM, Code nutzt Fernet/MultiFernet.

**[mittel K] README-Quickstart-Pfadfehler** — `git clone.../app.git` →
Verzeichnis `app`; README sagt `cd anlaufstelle`.

**[mittel S — Codex-Claude] Code of Conduct, DCO/CLA fehlen**.

**[mittel S — Codex-Claude] ADRs fehlen** — Entscheidungen verstreut in
Fachkonzept, Security Notes, Issues. **Fix:** ADR-Serie für RLS,
Retention/Historie, Statistik-MV, Offline-Krypto, AGPL-Source.

**[niedrig S — Claude-Claude] Encryption-Key-Rollover-Runbook fehlt**.

### B.2.10 Barrierefreiheit & I18n

**[mittel S — Codex-Claude] `html lang` hart auf Deutsch** — trotz
Sprachumschaltung; `templates/base.html:1–3`.

**[mittel K] HTMX-Fokus-Management nach Swap** — kein Listener für
`htmx:afterSwap`.

**[mittel S — Claude-Claude] Formular-Errors ohne `aria-describedby`**
`templates/components/form_input.html`.

## B.3 Priorisierte Maßnahmenliste (50 Einzelmaßnahmen, 6 Phasen)

### — Sofort, Woche 1 (Datenverlust + Datenschutz-Blocker)

| # | Maßnahme | Aufwand | Impact |
|---:|---|:---:|:---:|
| 1 | `MEDIA_ROOT`-Volume in `docker-compose.prod.yml` + Backup/Restore um Medien + Restore-Test | S | kritisch |
| 2 | Retention-Delete-Historie redaktieren (gemeinsame `record_delete_history(redacted=True)`) | M | kritisch |
| 3 | Daten-Migration für bestehende nicht-redaktierte `EventHistory`-DELETE-Einträge | M | kritisch |
| 4 | Image-Tag pinnen + Namespace-Drift fixen (prod/staging einheitlich) | S | hoch |
| 5 | AGPL §13 Source-Link in Footer + Login | S | hoch |

### — Sicherheits-Durchsetzung, Woche 1–2

| # | Maßnahme | Aufwand | Impact |
|---:|---|:---:|:---:|
| 6 | Live-RLS-Integrationstest mit Non-Superuser-DB-Rolle in CI | M | kritisch |
| 7 | Service-Layer-Konsistenz-Sweep (vier konkrete Stellen aus B.2.2) | M | hoch |
| 8 | WorkItem-Edit-Policy einheitlich (`can_user_mutate_workitem`) | S | hoch |
| 9 | `FacilityScopeMiddleware`: anonyme Requests `app.current_facility_id` explizit leeren | S | hoch |
| 10 | Validator `Sensitivity=HIGH ⇒ is_encrypted=True` | S | hoch |
| 11 | Architektur-Test gegen `Event.objects.bulk_create`/`update(data_json=...)` | S | hoch |
| 12 | CSV-Formula-Injection-Escaping zentral in Export-Service | S | mittel |
| 13 | Login-Lockout atomar (Redis INCR) + Autocomplete `block=True` + Architektur-Test GET | M | mittel |
| 14 | AuditLog-Trigger gegen UPDATE/DELETE verifizieren (`0024`) und ggf. ergänzen | S | mittel |

### — Datenschutz-Vollständigkeit, Woche 2–3

| # | Maßnahme | Aufwand | Impact |
|---:|---|:---:|:---:|
| 15 | Anonymisierungs-Cascade vervollständigen (EventHistory, EventAttachment, DeletionRequest) + Tests | M | kritisch |
| 16 | Klartext-Freitext inventarisieren und klassifizieren | S | hoch |
| 17 | `Client.notes`/`Case.description` Encryption oder UI-Policy | M | hoch |
| 18 | K-Anonymität auf alle externen Berichte; `top_clients` rein intern | M | hoch |
| 19 | DeletionRequest-Approval-Workflow umsetzen | M | hoch |
| 20 | Lösch-/Anonymisierungs-Matrix je Datenklasse | L | hoch |
| 21 | `StatisticsSnapshot.data` aggregations-only oder `EncryptedJSONField` | M | mittel |

### — Operations & Beobachtbarkeit, Woche 3–4

| # | Maßnahme | Aufwand | Impact |
|---:|---|:---:|:---:|
| 22 | Off-Site-Backup-Hook (rclone/restic/S3) | S | hoch |
| 23 | Backup-Restore-Drill mit Tabellen, Attachments, Trigger/RLS, Healthcheck | M | hoch |
| 24 | Healthcheck differenziert (ClamAV-Ausfall → 503) | S | mittel |
| 25 | Caddy-Edge-Rate-Limit auf `/login/`, `/password-reset/`, `/auth/offline-key-salt/` | S | mittel |
| 26 | Encryption-Key-Rollover-Runbook | S | mittel |
| 27 | Release-Pipeline: SBOM (`syft`), Cosign-Signatur, Provenance/SLSA | M | mittel |
| 28 | Dependabot, Trivy, Bandit als CI-Jobs | S | mittel |
| 29 | `pyclamd 0.4.0` → `clamd 1.0.6+` | S | mittel |
| 30 | Doku-Konsistenz: SECURITY.md auf 0.10.x; AES-GCM-vs-Fernet-Drift; README `cd app` | S | mittel |

### — Performance & Skalierung, Woche 4

| # | Maßnahme | Aufwand | Impact |
|---:|---|:---:|:---:|
| 31 | N+1 im Zeitstrom-Feed beheben | M | hoch |
| 32 | Pagination-Cap in cases/clients/audit | S | hoch |
| 33 | Event-Edit N+1 bei Datei-Feldern + Attachment-List-Pagination | M | mittel |
| 34 | JSONB GinIndex auf häufig gefilterten Pfaden | S | mittel |
| 35 | `SESSION_SAVE_EVERY_REQUEST=False` | S | mittel |
| 36 | Search: `data_json__icontains` durch JSONB-Pfad-Suche ersetzen | M | mittel |

### — Strukturelle Hygiene, Woche 5–6

| # | Maßnahme | Aufwand | Impact |
|---:|---|:---:|:---:|
| 37 | mypy in CI (inkrementell, `core/services` zuerst) | M | hoch |
| 38 | Ruff erweitern (`B`,`S`,`UP`,`C90`,`DJ`) + Baseline | S | mittel |
| 39 | Import-Linter / Grimp für Modulgrenzen in `core` | S | mittel |
| 40 | `services/event.py` splitten (`event_crud.py` + `event_data.py`) | M | mittel |
| 41 | `forms/`-`INPUT_CSS` zentralisieren | S | niedrig |
| 42 | AuditLog-Sweep über alle State-Transitions | M | mittel |
| 43 | HTMX-Fokus-Management + aria-live + `aria-describedby` + `html lang` dynamisch | M | mittel |
| 44 | ADRs (RLS, Retention/Historie, Statistik-MV, Offline-Krypto, AGPL-Source) | M | mittel |
| 45 | Code of Conduct, DCO/CLA-Entscheidung | S | mittel |
| 46 | Axe/Pa11y E2E-Tests für Kernflows | M | mittel |

### Strukturell — nach v1.0

| # | Maßnahme | Aufwand | Impact |
|---:|---|:---:|:---:|
| 47 | App-Split (`clients`, `documentation`, `workitems`, `retention`, `reporting`, `audit`, `offline`) — erst nach Import-Linter und ADR | XL | mittel |
| 48 | Co-Maintainer-Akquise (Bus-Faktor 1 → 2+) | XL | hoch |
| 49 | `ClientAlias`-Modell für Mehrfach-Aliase | M | mittel |
| 50 | Reporting-Fact-Modell (normalisiert, neben JSONB-Erfassung) | XL | hoch |
| 51 | `Client.pseudonym`-Verschlüsselung mit HMAC-Lookup-Index | L | hoch |

---
---

# TEIL C — META & STRATEGIE

## C.1 Kontroversen zwischen den Quell-Audits

Diese Punkte verdienen eine Entscheidung — die Audits widersprechen sich.

### C.1.1 CSP `'unsafe-eval'`-Status

- Claude-ChatGPT-Prompt: „seit 0.10.2 vollständige `@alpinejs/csp`-Migration,
 ohne `unsafe-eval`" (CHANGELOG v0.10.2)
- Claude-Claude: „CSP enthält `'unsafe-eval'`" als kritischer Befund
- Codex-Claude: „Kommentar in `base.py` beschreibt akzeptiertes `unsafe-eval`;
 die tatsächliche CSP setzt `script-src` nur auf `self`. Kommentar ist stale."

**Auflösung:** Codex-Claude ist hier vermutlich am genauesten — Code gefixt,
Kommentar stale. **Action:** Kommentar in `settings/base.py:243–260`
aktualisieren, damit künftige Audits das nicht erneut als offenen Befund
flaggen.

### C.1.2 AuditLog-DB-Trigger-Status

Ein Audit kritisiert, AuditLog sei nur im Python-Modell immutable; andere
nennen Migration `0024_auditlog_immutable_trigger.py`. **Action:** im
aktuellen Head verifizieren, ob Trigger für UPDATE und DELETE existieren,
greifen und bei Restore erhalten bleiben.

### C.1.3 „Würde ich diesem System sensible Sozialdaten anvertrauen?"

- Gemini-ChatGPT: „Ja. Paranoider und solider als 90 % der mir bekannten
 GovTech-Applikationen."
- Claude-ChatGPT-Prompt: „Bedingt ja, sobald Live-RLS-Test, Service-Layer-
 Konsistenz, MEDIA_ROOT-Mount, Pen-Test."
- ChatGPT-1, Codex-kritisch: „Heute: nein, nicht im produktiven Regelbetrieb."

**Auflösung:** Gemini-ChatGPT überschätzt die Durchsetzung. Die Existenz von
RLS, AuditLog-Triggern und Field-Encryption ist Voraussetzung für „ja",
nicht Beweis. Realistische Antwort: Pilot ja, Regelbetrieb nein, bevor die
sieben Blocker geschlossen sind.

### C.1.4 Architekturqualität des Service-Layers

- Claude-ChatGPT-Prompt: „Service-Layer existiert wirklich und ist nicht
 kosmetisch."
- Codex-kritisch: „Service Layer ist inkonsistent. Manche Pfade sind sauber,
 andere lassen Sicherheits- und Facility-Checks in Views, Forms und Services
 verstreut."

**Auflösung:** Beide haben recht. Der Service-Layer existiert *strukturell*,
aber *nicht durchgängig konsistent in der Sicherheitsdurchsetzung*. Die vier
konkreten Lücken (B.2.2) sind alle einzeln fixbar — als Muster zeigen sie,
dass „Service-Layer = sicher" keine harte Invariante ist.

### C.1.5 „App-Split nötig oder nicht"

- Codex-Claude: „Kein App-Split als Selbstzweck."
- Mehrere andere: „Domänen-Trennung als High-Impact-Refactoring."

**Auflösung:** Bei aktuell ~20–25k LOC noch nicht akut, aber Wachstumsmuster
spricht klar für Cut vor v1.0. **Vorher unbedingt: Import-Linter / Grimp
einführen**, um Modulgrenzen explizit zu machen, bevor der Split kommt. Sonst
wird der Split nur Verzeichnisse umbenennen.

### C.1.6 Gesamtbewertung 6,5 vs. 7,5 vs. 9

Die Abweichung erklärt sich aus Perspektive: technisch/architektonisch ist
das Projekt eher 7–8/10; produktionsrechtlich/operativ für sensible
Sozialdaten eher 5–6/10. Konsolidiert: **starkes Pre-Release, aber kein
unbegleiteter Produktivbetrieb.**

## C.2 Was alle Audits übersehen oder schwach bewerten

### C.2.1 Maintainer-Strategie als Sicherheitsfrage

AI-gestützte Solo-Entwicklung produziert hohe Velocity bei niedrigem
Bus-Faktor und hoher Konsistenz im Stil. Das ist genau der Modus, in dem
subtile Inkonsistenzen entstehen, ohne dass ein Reviewer sie früh fängt — die
vier Service-Layer-Lücken sind ein typisches Symptom. Vor v1.0 ist mindestens
ein **externer Senior-Review der Sicherheits-Schichten** (RLS, Encryption,
Retention, Audit) die mit Abstand beste Investition. Pen-Test allein reicht
nicht, weil Pen-Tests Bypass-Pfade nicht systematisch enumerieren.

### C.2.2 Pilot-Strategie ist die größte ungelöste Frage

Sechs der acht Audits weisen darauf hin, dass das System für die plakatierte
Zielgruppe (NGO ohne IT-Person) ohne Managed-Hosting nicht betreibbar ist.
Das ist kein Code-Bug, sondern eine Produktstrategie-Lücke. Mögliche Pfade:

- **Trägerinitiative für gemeinsames Hosting** — ein Verein, der für 10–20
 Einrichtungen das Coolify-Setup übernimmt; Mitgliedsbeitrag deckt Ops.
- **Anlaufstelle GmbH/UG als Managed-Hosting-Anbieter** — kollidiert mit AGPL
 nicht (eigener Code), setzt aber Geschäftsmodell-Fokus voraus.
- **Kooperation mit Sozialwirtschafts-IT** — Caritas-/Diakonie-Rechenzentren.

### C.2.3 JSONB-Schema-Governance

Drei Audits flaggen das als Problem, niemand schlägt eine konkrete Lösung
vor. Empfehlung: Management-Command `audit_data_json_drift`, der Events
findet, deren `data_json`-Keys nicht mehr in aktiven `FieldTemplate`-Slugs
vorkommen. Plus optionaler Cleanup-Walk mit Migrations-Tabelle.

### C.2.4-Roadmap-Realität

Die Härtungs-Pipeline (Phasen 1–6 in B.3) ist ~4–6 Wochen Vollzeit. Wenn der-Antrag bewilligt wird, sind die ersten Milestones in dieser Phase eine
bessere Investition als neue Features. Falls der Antrag Feature-Milestones
zuerst legt, sollte das vor der Bewilligung neu sortiert werden — oder als
Nachschärfung im Stage-2-Gespräch eingebracht werden.

### C.2.5 „Kollabiert es unter eigener Komplexität" — ohne Stresstest

Die Frage wird konsistent verneint, aber ohne Stresstest. Die Komplexitäts-
Hotspots (`event.py` 660 LOC, `retention.py` 929 LOC, JSONB-Schema-Drift,
Offline-Sync) sind alle in der Größenordnung, in der man mit 3–6 Monaten
harter Arbeit *jetzt* refaktorisieren kann oder in 18–24 Monaten gezwungen
wird. Der Hebel ist höher, je früher das passiert.

## C.3 Erste 3 Maßnahmen — Empfehlung

Wenn man das System morgen produktiv übergeben müsste, die drei Stellen, an
denen alle Audits zusammenlaufen:

### C.3.1 Datenverlust-Bug fixen (Tag 1)

`MEDIA_ROOT`-Volume in `docker-compose.prod.yml`, plus Backup/Restore um
Medien erweitern, plus E2E-Test, der einen Anhang über Container-Recreate
hinweg überlebt. 30 min Compose-Edit + 2 h Backup-Skript + 1 h Test. Ohne
diesen Fix ist jede andere Härtung Symbolpolitik.

### C.3.2 Retention/EventHistory-Datenfluss fixen (Tag 2–4)

Gemeinsame `record_delete_history(event, redacted=True)` für beide
Löschpfade. Daten-Migration für bestehende nicht-redaktierte Einträge. Tests,
die nach `enforce_retention` keine Klartext-Werte in
`EventHistory.data_before` zulassen. Ohne diesen Fix ist „Löschung" im
DSGVO-Sinne nicht erfüllt — der schwerste Vorwurf, den drei unabhängige
Auditoren bestätigen.

### C.3.3 Live-RLS-Test in CI (Woche 1)

Dedizierte Postgres-Rolle ohne Superuser; Test-Fixture, die auf dieser Rolle
fährt; Cross-Tenant-SELECT-Tests, die 0 Rows asserten. Ohne diesen Test ist
„RLS schützt" eine Hoffnung, kein Mechanismus. Mit diesem Test ist
„Defense-in-Depth" belegbar — das ist genau die Aussage, die-Funding
und Pilot-Pitches tragen muss.

Alles weitere — App-Split, Reporting-Fact-Modell, Pen-Test, Off-Site-Backup,
JSONB-Governance — folgt aus diesen drei.

## C.4 Reading Guide für die acht Quell-Audits

Wenn Toni für eine bestimmte Frage in die Originale zurückgehen will:

- **Wenn nur eines lesen:** Codex-Claude (umfassend-strukturiert) — am
 präzisesten mit Belegstellen, kalibriert in der Schwere-Bewertung, wenig
 Bias.
- **Wenn nur die Kritikpunkte:** ChatGPT-1 — kompakt, harte Sprache, Fokus
 auf Lifecycle und Operations.
- **Wenn Selbstvertrauen nötig:** Gemini-ChatGPT — aber bewusst einordnen,
 dass es Durchsetzungslücken übersieht.
- **Wenn Service-Layer-Konsistenz prüfen:** Claude-ChatGPT-Prompt — beste
 Belege für die vier konkreten Service-Lücken.
- **Wenn Datenschutz-Lifecycle prüfen:** Codex-Claude und Codex-kritisch
 gemeinsam — beide haben den EventHistory-Befund mit verschiedenen Belegen.
- **Wenn Architektur prüfen:** Claude-Claude und Claude-kritisch — beide am
 tiefsten in App-Split, Service-Layer-Patterns und Connection-Pool-Risiken.

---

## Anhang: Differenz der zwei Vor-Konsolidierungen

Dieses Master-Dokument vereint zwei Vor-Konsolidierungen:

| Eigenschaft | ChatGPT-Konsolidierung | Claude-Konsolidierung |
|---|---|---|
| Länge | 390 Zeilen / 19 KB | 788 Zeilen / 41 KB |
| Stärke | Executive Summary, Bewertungstabelle | Working Document, Belegstellen |
| Methodik | knapp benannt | Auditoren-Tabelle mit Spreizung |
| Befunde | 7 Blocker durchnummeriert | K/S-Markierung pro Befund |
| Belegstellen | wenige | viele (Datei:Zeilen) |
| Phasen | 3 (/1/2) | 6 (Wochen-getaktet) |
| Meta-Sektion | Widersprüche zwischen Audits | + „Was alle übersehen haben" |
| Reading Guide | nicht enthalten | enthalten |
| Adressat | Förderer, Reviewer | Umsetzung, Refactoring |

Beide Vor-Konsolidierungen waren in sich konsistent und in den Schlüssel-
Befunden deckungsgleich. Das Master-Dokument übernimmt:

- **aus ChatGPT:** Bewertungstabelle (A.2), 7-Blocker-Struktur (A.5),
 prägnante Phasen-Übersicht (A.6), Auflösung der Widersprüche (C.1)
- **aus Claude:** Auditoren-Methodik (A.3), K/S-Markierung und Belegstellen
 (B.2), 6-phasige Maßnahmenliste mit 50 Einzelmaßnahmen (B.3), Meta-Sektion
 (C.2), Erste 3 Maßnahmen (C.3), Reading Guide (C.4)

Was nicht übernommen wurde: redundante Aufzählungen derselben Stärken (beide
listen `Event` als zentrale Dokumentationseinheit), wechselseitig schwächere
Formulierungen derselben Befunde, Phasen-Doppelungen.

— Ende.
