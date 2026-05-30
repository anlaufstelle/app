# Konsolidiertes Audit: anlaufstelle/app

**Synthese aus 8 unabhängigen Audits**
**Geprüfter Stand:** Commit `35e0f5b`, Tag `v0.10.2` (2026-04-28)
**Datum der Synthese:** 2026-04-29

---

## Methodik

Konsolidiert wurden acht Audits aus drei verschiedenen Modellfamilien, jeweils mit
zwei verschiedenen Prompt-Stilen (umfassend-strukturiert vs. schonungslos-offen):

| Auditor | Prompt | Bewertung | Tendenz |
|---|---|---:|---|
| ChatGPT (1) | schonungslos | 6,5/10 | kritisch, Operations-Fokus |
| Codex (kritisch) | schonungslos | 6,5/10 | kritisch, Policy-Layer-Fokus |
| Codex (strukturiert) | umfassend | implizit ~7/10 | präzise mit Belegstellen |
| Claude (kritisch) | schonungslos | 7,5/10 | balanciert |
| Claude (strukturiert) | umfassend | implizit ~7/10 | sehr präzise mit Belegstellen |
| Claude (ChatGPT-Prompt) | schonungslos | 7,5/10 | balanciert, ops-aufmerksam |
| Gemini (ChatGPT-Prompt) | schonungslos | 9/10 | enthusiastisch |
| Gemini (Claude-Prompt) | umfassend | implizit ~7/10 | strukturiert |

**Wie ich die Spreizung lese:** Gemini-ChatGPT ist der Ausreißer nach oben — es
liest die vorhandenen Sicherheitsmechanismen als bereits funktional und übersieht
Durchsetzungslücken. Die 6,5-Bewertungen lesen die gleiche Substanz, gewichten
aber Lifecycle-/Betriebslücken stärker. Die 7,5-Bewertungen sind die mittlere
Position. Konsens-Realwert liegt zwischen 6,5 und 7,5; die Differenz erklärt
sich durch Risikoappetit, nicht durch Faktendisput.

**Konsolidierte Gesamtbewertung: 7,0 / 10** — fortgeschrittener Pre-Release,
fachlich stark, sicherheitsbewusst, mit drei konkreten Blockern für echten
produktiven Einsatz mit sensiblen Sozialdaten.

---

## TL;DR — Was alle Auditoren teilen

**Konsens-Stärken** (in mindestens 5 Audits explizit gewürdigt)

1. **Domänenpassung ist außergewöhnlich.** Pseudonym als Architektur-Constraint,
 drei Kontaktstufen, Zeitstrom statt Akte, WorkItems neben Events, Übergabe,
 anonyme Events — das ist sichtbar von jemandem mit echter Domänenkenntnis
 gebaut. Sieben von acht Auditoren benennen das explizit.
2. **Defense-in-Depth ist real, nicht dekorativ.** Postgres RLS + ORM-Manager +
 Middleware + Field-Encryption + AuditLog mit DB-Trigger + ClamAV fail-closed
 + MFA — die Schichten greifen ineinander. Sechs Audits werten das als
 überdurchschnittlich.
3. **Service-Layer existiert wirklich.** 30+ Module in `core/services/`, Views
 sind tendenziell dünn, Audit/Encryption/Sensitivity sind zentral implementiert.
 Vier Audits werten das als Senior-Level.
4. **Test-Disziplin ist substanziell.** ~1.500–1.845 Testfunktionen, RBAC-Matrix,
 Architektur-Tests, Playwright-E2E, RLS-Tests, CI mit `pip-audit`.
5. **Dokumentationstiefe.** Fachkonzept, Admin-Guide, Ops-Runbook, FAQ,
 DSGVO-Templates, CONTRIBUTING zweisprachig.

**Konsens-Schwächen** (in mindestens 3 Audits, mit Belegstellen verifiziert)

1. **`MEDIA_ROOT`-Volume fehlt in `docker-compose.prod.yml`.** Verschlüsselte
 Anhänge gehen bei jedem `docker compose pull && up -d` verloren.
 **Stiller Datenverlust-Bug**, kein Ops-Hinweis. Bestätigt von ChatGPT-1,
 Codex-kritisch, Claude-ChatGPT-Prompt.
2. **EventHistory bewahrt Daten nach Löschung.** Retention kopiert vollständige
 `data_json`-Werte in `EventHistory.data_before` und löscht dann nur das
 Original. EventHistory ist append-only. Damit ist „Löschung" materiell keine
 Löschung. Bestätigt von ChatGPT-1, Codex-Claude (als „kritisch" markiert),
 Claude-Claude.
3. **RLS wird in CI nicht funktional getestet.** Tests laufen als Superuser,
 RLS wird damit zur Laufzeit umgangen. Eine zentrale Sicherheitsannahme ist
 nicht abgesichert. Bestätigt von Codex-Claude, Claude-ChatGPT-Prompt,
 Claude-Claude.
4. **Klartext-Freitext außerhalb des Sensitivity-Modells.** `Client.notes`,
 `Case.description`, `Episode.description`, `WorkItem.description`,
 `AuditLog.detail` — genau die Felder, in die Nutzer „hat konsumiert",
 „psychische Krise", „Klarname intern" schreiben werden. Bestätigt von
 Codex-kritisch, Claude-Claude, Codex-Claude.
5. **Bus-Faktor 1.** Single-Maintainer-Projekt mit AI-gestütztem Tempo. Bestätigt
 von Claude-Claude, Claude-ChatGPT-Prompt, Codex-kritisch.
6. **Image-Namespace-Drift.** `docker-compose.prod.yml` referenziert
 `ghcr.io/anlaufstelle/app:latest`, Repository ist `anlaufstelle/app`,
 Staging-Compose nutzt `ghcr.io/anlaufstelle/app:latest`. Bestätigt von
 Codex-kritisch, Codex-Claude, Claude-ChatGPT-Prompt.
7. **AGPL §13 nicht in UI umgesetzt.** Kein Source-Link/„Powered by"-Footer.
 Compliance-Risiko bei Drittbetrieb. Bestätigt von Claude-Claude, Codex-Claude.

---

## Faktenblock (konsolidiert)

| Kategorie | Wert | Quellen |
|---|---|---|
| Letzter Commit | `35e0f5b`, v0.10.2, 2026-04-28 | alle |
| Contributors | 1 | Claude-Claude, Codex-Claude |
| Lizenz | AGPL-3.0-or-later | alle |
| Python / Django | 3.13 / 5.1.15 | alle |
| Apps / Models | 1 (`core`) / ~25–29 | unterschiedliche Zählweisen |
| Views (CBV) | ~82–85 | Codex-Claude, Claude-Claude |
| Services | 30–33 Module | alle |
| Migrationen | 73–74 | alle |
| Tests | 150–152 Dateien, ~1.500–1.845 Testfunktionen | alle |
| LOC Python | ~52.819 (gesamt), ~19.780–25.000 in `core/` | unterschiedlich |
| Templates | 84–86 HTML | alle |
| Kritische Deps | `cryptography 46.0.7`, `django-csp 4.0`, `django-otp 1.7.0`, `django-ratelimit 4.1.0`, `pyclamd 0.4.0` (EOL 2016), `weasyprint 68.1`, `psycopg 3.3.3`, `sentry-sdk 2.58.0` | Claude-Claude, Codex-Claude |

---

## Kontroversen zwischen den Audits

Diese Punkte verdienen explizit eine Entscheidung — die Audits widersprechen sich.

### 1. CSP `'unsafe-eval'`-Status

- **Claude-ChatGPT-Prompt:** „seit 0.10.2 vollständige `@alpinejs/csp`-Migration,
 ohne `unsafe-eval`" (CHANGELOG v0.10.2)
- **Claude-Claude:** „CSP enthält `'unsafe-eval'`" als kritischer Befund
- **Claude-kritisch:** „ abgeschlossen, offen"
- **Codex-Claude:** „Kommentar in `base.py` beschreibt akzeptiertes `unsafe-eval`;
 die tatsächliche CSP setzt `script-src` nur auf `self`. Kommentar ist stale."

**Auflösung:** Codex-Claude ist hier vermutlich am genauesten — der Code ist
gefixt, aber der Kommentar ist stale. **Action:** Kommentar in
`settings/base.py:243–260` aktualisieren, damit künftige Audits das nicht erneut
als offenen Befund flaggen.

### 2. „Würde ich diesem System sensible Sozialdaten anvertrauen?"

- **Gemini-ChatGPT:** „Ja. Das Security-Design ist paranoider und solider als
 bei 90 % der mir bekannten GovTech-Applikationen."
- **Claude-ChatGPT-Prompt:** „Bedingt ja, sobald (a) Live-RLS-Test, (b) Service-
 Layer-Konsistenz, (c) MEDIA_ROOT-Mount, (d) Pen-Test."
- **ChatGPT-1, Codex-kritisch:** „Heute: nein, nicht im produktiven Regelbetrieb."

**Auflösung:** Gemini-ChatGPT überschätzt die Durchsetzung. Die Existenz von
RLS, AuditLog-Triggern und Field-Encryption ist *Voraussetzung* für „ja",
nicht *Beweis*. Die anderen Auditoren weisen zu Recht darauf hin, dass alle
drei Schichten testbare Bypass-Pfade haben (RLS-Superuser, Klartext-Freitexte,
Anonymisierungs-Cascade unvollständig). **Realistische Antwort:** Pilotbetrieb
ja, Regelbetrieb nein, bevor die kritischen Findings unten geschlossen sind.

### 3. Architekturqualität des Service-Layers

- **Claude-ChatGPT-Prompt:** „Service-Layer existiert wirklich und ist nicht
 kosmetisch."
- **Codex-kritisch:** „Service Layer ist inkonsistent. Manche Pfade sind sauber
 serviceorientiert, andere lassen Sicherheits- und Facility-Checks in Views,
 Forms und Services verstreut."

**Auflösung:** Beide haben recht. Der Service-Layer existiert *strukturell*, ist
aber *nicht durchgängig konsistent in der Sicherheitsdurchsetzung*. Konkrete
Lücken: `WorkItemUpdateView` prüft `can_user_mutate_workitem` nicht;
`update_client(**fields)` ohne Allowlist; `client_export` ohne Sensitivity-Filter;
`handover._collect_open_tasks` ohne `visible_to`. **Diese Lücken sind alle
einzeln fixbar — aber als Muster zeigen sie, dass „Service-Layer = sicher"
keine harte Invariante ist.**

### 4. „App-Split nötig oder nicht"

- **Codex-Claude:** „Kein App-Split als Selbstzweck."
- **Claude-Claude:** „Mittelfristig Split in `accounts`, `clients`, `cases`,
 `audit_log`, `retention`."
- **ChatGPT-1, Claude-kritisch:** „Domänen-Trennung als High-Impact-Refactoring."
- **Codex-kritisch:** „Domänenmodule aus `core` schneiden — mindestens:
 `clients`, `documentation/events`, `workitems`, `retention`, `reporting`,
 `security/audit`, `offline`."

**Auflösung:** Bei aktuell ~20–25k LOC noch nicht akut, aber das Wachstumsmuster
spricht klar für einen Cut vor v1.0. Vorher unbedingt: **Import-Linter / Grimp
einführen**, um die Modulgrenzen explizit zu machen, *bevor* der App-Split
kommt. Sonst wird der Split nur Verzeichnisse umbenennen.

---

## Befunde nach Dimension (konsolidiert, mit Schwere und Konsens-Level)

> Schwere: kritisch / hoch / mittel / niedrig / info
> Konsens: K = mehrere Auditoren bestätigen, S = Single-Source
> Belegstellen: aus den Audits zusammengetragen, vor Umsetzung im Repo verifizieren

### A. Datenschutz / DSGVO-Lifecycle (höchste Priorität)

**[kritisch K] Retention-Delete schreibt Klartext in unveränderliche Historie**
- `src/core/services/retention.py:566–580` kopiert volle `data_json`-Werte in
 `EventHistory.data_before`; EventHistory ist per Trigger append-only
 (`0012_eventhistory_append_only_trigger.py`)
- Manuelles Soft-Delete (`services/event.py:582–596`) schreibt dagegen nur
 `{"_redacted": True, "fields": [...]}` → divergierende Semantik für „gleiche"
 fachliche Operation
- **Wirkung:** Materielle Speicherbegrenzung und Löschung im Sinne von Art. 5
 Abs. 1 lit. e und Art. 17 DSGVO sind nicht gegeben. Für Sozialgeheimnis und
 § 67 SGB X ein Blocker.
- **Fix:** Gemeinsame `record_delete_history(event, redacted=True)`-Funktion;
 Daten-Migration für bestehende nicht-redaktierte Einträge; Tests gegen
 `EventHistory.data_before["_redacted"]`.

**[hoch K] Anonymisierungs-Cascade unvollständig**
- `Client.anonymize` (`models/client.py:120, 136`) berührt `EventHistory`,
 `EventAttachment` und `DeletionRequest` nicht
- `enforce_retention` (`services/retention.py:569–579`) löscht keine
 `EventAttachment`/`EventHistory`-Datensätze zu soft-deleteten Events
- **Wirkung:** Re-Identifikation über Audit-/History-Spuren möglich
- **Fix:** Anonymisierung als Aggregat-Operation; Tests „Restdaten nach
 Anonymisierung == 0".

**[hoch K] Klartext-Freitext außerhalb des geschützten Feldmodells**
- `Client.notes` (`models/client.py:54–58`)
- `Case.description`, `Episode.description` (`models/case.py:36–37`)
- `WorkItem.description` (`models/workitem.py:89–90`) — zusätzlich keine
 Sensitivitätsstufe modelliert
- `AuditLog.detail` als Klartext-JSON (`models/audit.py:83`)
- **Wirkung:** Genau die Felder, in die Nutzer kritische Hinweise schreiben
 werden, folgen nicht dem Sensitivity-/Encryption-/Retention-Modell
- **Fix:** Inventarisierung aller Freitextfelder; Klassifikation
 erlaubt/sensitivitätsgeschützt/verschlüsselt/verboten; mindestens
 `Client.notes` und `Case.description` in das Verschlüsselungsmodell überführen
 oder UI-Policy einführen.

**[hoch S — Claude-Claude] `Client.pseudonym` unverschlüsselt + GIN-Index**
- `models/client.py:35–39, 91`
- **Wirkung:** Backup-Leak ⇒ direkte Wiedererkennung
- **Fix:** `EncryptedTextField` + getrennter HMAC-Lookup-Index oder
 Postgres-`pgcrypto`-Hash-Spalte (Trade-off: Trigram-Suche bricht)
- **Bewertung:** Single-Source, aber konzeptionell sauber. Wenn Pseudonym
 ohnehin schon Pseudonym ist, ist Backup-Verschlüsselung ggf. der
 pragmatischere Hebel.

**[hoch S — Claude-Claude] `StatisticsSnapshot.data` als Klartext-JSON**
- `models/statistics_snapshot.py:25–26`
- **Fix:** Aggregate ohne Identifier persistieren oder `EncryptedJSONField`;
 Re-Validation gegen K-Anonymität.

**[mittel K] K-Anonymität schützt nicht alle Statistik-/Exportpfade**
- `top_clients` zeigt Pseudonyme (`services/statistics.py:85–103`)
- Jugendamt-Statistik aggregiert kleine Kategorien ohne Suppression
 (`services/export.py:180–236`)
- **Fix:** K-Schwelle zentral auf alle externen Berichte; `top_clients` rein
 intern oder entfernen.

**[mittel S — Codex-Claude] Lösch- und Anonymisierungs-Workflow für Clients/
Cases/Users fehlt**
- FAQ `docs/faq.md:427–433`: kein manueller Löschmechanismus für Clients,
 Cases/Episodes, User-Accounts; AuditLog unveränderlich
- **Wirkung:** Betroffenenrechte nur teilweise erfüllbar
- **Fix:** Lösch-/Anonymisierungs-Matrix je Datenklasse; Approval-Workflow für
 `DeletionRequest` (Modell vorhanden, View fehlt — `models/workitem.py:142–190`).

### B. Sicherheit / Defense-in-Depth-Durchsetzung

**[kritisch K] RLS in CI nicht funktional getestet**
- `tests/test_rls.py:1–9` bestätigt selbst: Test-DB-User ist Superuser
- **Wirkung:** Wenn Coolify-Default oder Migrations-Setup den Django-DB-User
 als Superuser anlegt, wird RLS still abgeschaltet. Kein CI-Alarm.
- **Fix:** Dedizierte Postgres-Rolle ohne Superuser-Rechte in CI; Cross-Tenant-
 Queries als negative Tests.

**[hoch K — ChatGPT-1, Claude-kritisch] Stale `app.current_facility_id`-Risiko
über Connection-Pooling**
- `FacilityScopeMiddleware` setzt Variable nur für authentifizierte Requests;
 bei `CONN_MAX_AGE=60` und Connection-Reuse für anonyme Routes bleibt der
 alte Wert stehen
- **Wirkung:** Latent harmlos (anonyme Routes fassen keine RLS-Tabellen an),
 aber eine *Annahme*, kein Mechanismus
- **Fix:** Für anonyme/unauthenticated Requests `app.current_facility_id`
 explizit leeren statt überspringen.

**[hoch K] Encryption als `save`-Aspekt umgehbar**
- `Event.save` ruft `_encrypt_sensitive_fields`. `bulk_create`,
 `update(data_json=...)`, `update_or_create` ohne `save` oder Raw-SQL
 umgehen die Verschlüsselung vollständig.
- **Fix:** Architektur-Test, der `Event.objects.bulk_create` und
 `update(data_json=...)` außerhalb von `services/encryption.py` verbietet;
 pre-save-Signal als zweite Linie; mittelfristig Custom-`JSONField` mit
 transparenter Encryption.

**[hoch K] Encryption für Art.-9-Daten optional**
- `FieldTemplate.is_encrypted` ist konfigurierbar, auch für Sensitivity=HIGH
 (`models/document_type.py:27–30, 79–82`)
- **Fix:** Validator: `Sensitivity=HIGH ⇒ is_encrypted=True` erzwingen.

**[hoch S — Codex-Claude] CSV-Export anfällig für Formula Injection**
- `services/export.py:88–150` schreibt Pseudonyme und Feldwerte direkt per
 `csv.writer`, ohne Neutralisierung für Werte mit `=`, `+`, `-`, `@`, Tab, CR
- **Wirkung:** Formel-Ausführung beim Öffnen in Excel/LibreOffice
- **Fix:** Führendes Apostroph oder Tab-Escape für gefährliche Präfixe;
 Regressionstest.

**[hoch S — Claude-ChatGPT-Prompt] Mehrere Service-Layer-Konsistenz-Lücken**
1. `services/handover._collect_open_tasks(facility)` hat keinen `user`-
 Parameter, kein `visible_to(user)` → Pseudonym-Leak über Sensitivity-Grenzen
2. `services/clients.update_client(**fields)` ohne Allowlist (vgl.
 `cases.update_case` mit Whitelist)
3. `services/client_export.export_client_data` überspringt Sensitivity-Filter
 (für Auskunft an Betroffene korrekt; für Staff-Export fragwürdig)
4. `services/event.py:563` rollt eigenen `str(updated_at)`-Vergleich statt
 `services/locking.check_version_conflict`
- **Fix:** Vier Stellen in einem Sweep schließen; Architektur-Test ergänzen.

**[hoch S — Codex-kritisch] WorkItem-Edit-Policy inkonsistent**
- `WorkItemStatusUpdateView` prüft `can_user_mutate_workitem`, der volle Edit-
 Pfad (`workitem_actions.py:119–161`) prüft nur `StaffRequiredMixin`
- **Wirkung:** Jede Fachkraft kann Aufgaben anderer bearbeiten
- **Fix:** Einheitliche Policy.

**[hoch K] Login-/Autocomplete-Lockout race-anfällig**
- `services/login_lockout.py:31–38`: Schwellwert ohne `select_for_update`/Redis
 → 11–12 Versuche möglich
- `ClientAutocompleteView` (`views/clients.py:196–234`) nutzt
 `@ratelimit(... method="GET")` ohne `block=True` → Rate-Limit greift nicht
 blockierend (Pseudonym-Enumeration)
- **Fix:** Atomares Counting (Redis INCR); `block=True` ergänzen;
 Architektur-Test auf sensible GET-Endpunkte ausweiten.

**[mittel K] AuditLog nicht DB-immutable für UPDATE/DELETE**
- `models/audit.py:104–112`: `save`/`delete` werfen, Raw-SQL möglich
- Migration `0024_auditlog_immutable_trigger.py` adressiert das laut Codex-Claude
 und Claude-ChatGPT-Prompt — hier widersprechen sich die Audits.
 **Verifizieren:** Existiert der Trigger und greift er für `UPDATE` und
 `DELETE`?

**[mittel K] Statistik-Materialized-View bewusst ohne RLS**
- `0049_statistics_event_flat_mv.py`; Schutz nur durch Service-`WHERE`-Klausel
- **Wirkung:** Künftiger direkter Query auf MV ohne WHERE hätte
 Cross-Facility-Reichweite
- **Fix:** Architektur-Test verbietet direkten Zugriff auf MV außerhalb
 Statistik-Service.

**[mittel S — Claude-kritisch] Search durchsucht JSONB inklusive
verschlüsselter Tokens**
- `services/search.py:64–69`: `data_json__icontains` matcht in Postgres-JSONB-
 Repräsentation auch verschlüsselte Tokens (False-Positive-Treffer als
 Information-Leak im Konjunktiv)
- **Fix:** JSONB-Pfad-Suche pro nicht-verschlüsseltem Field-Slug.

**[mittel K] CSP-`unsafe-eval`-Kommentar stale**
- `settings/base.py:243–260` beschreibt akzeptiertes `unsafe-eval`; CHANGELOG
 v0.10.2 sagt: entfernt. Codex-Claude bestätigt: Code ist gefixt, Kommentar nicht.
- **Fix:** Kommentar aktualisieren; AdminCSPRelaxMiddleware-Ausnahme verlinken.

**[mittel S — Claude-Claude] CSRF-Token im `<meta>` statt Cookie**
- Bei XSS lesbar; HTTPOnly-Flag auf Cookie wird konterkariert
- **Fix:** HTMX kann Token via `HX-Headers` aus Cookie lesen.

**[niedrig S — Claude-Claude, ChatGPT-1] `safe_decrypt` fail-open auf
„[verschlüsselt]"**
- `services/encryption.py:106–114`: Bei Tampering vs. Key-Loss kein Indikator-
 Unterschied im UI
- **Fix:** `KeyMissing` vs. `InvalidToken` unterscheiden; UI als Fehler markieren.

**[niedrig S — Claude-Claude] Dev-DB-Default-Passwort**
- `settings/base.py:96–105`: `POSTGRES_PASSWORD` Default `"anlaufstelle"`
- **Fix:** Default leer, ENV erzwingen.

### C. Betrieb (Datenverlust + Supply Chain)

**[kritisch K] `MEDIA_ROOT`-Volume fehlt in `docker-compose.prod.yml`**
- `.env.example:53` setzt `MEDIA_ROOT=/data/media`, aber kein Service mountet
 `/data`. Verschlüsselte Anhänge sind beim nächsten `docker compose pull && up -d`
 weg. Backup-Skript sichert nur DB.
- **Fix:** Named volume `media:` deklarieren, am `web`-Service mounten;
 `backup.sh` und `restore.sh` um Medien erweitern; Restore-Test über
 Container-Recreate.

**[hoch K] Image-Namespace-/Tag-Drift**
- `docker-compose.prod.yml:17`: `ghcr.io/anlaufstelle/app:latest`
- `docker-compose.staging.yml:25`: `ghcr.io/anlaufstelle/app:latest`
- Repository-Namespace: `anlaufstelle/app`
- SECURITY.md verweist auf `anlaufstelle/app`
- **Fix:** Image-Namespace einheitlich; Pin auf konkrete Version (`:v0.10.2`)
 oder SHA, nicht `:latest`; Rollback-Anweisung mit konkretem Tag.

**[hoch S — Claude-ChatGPT-Prompt] Keine Off-Site-Backups**
- `scripts/backup.sh` schreibt nach `${PROJECT_DIR}/backups/` — gleiche Disk
 wie pgdata. Disk-Failure = Total-Verlust inkl. Backups.
- **Fix:** rclone/restic/S3-Hook nach erfolgreichem Local-Write.

**[mittel K] Backup-Verifikation flach**
- `scripts/backup.sh --verify` prüft nur `SELECT COUNT(*) FROM core_facility`
- `scripts/restore.sh:34–49` warnt nur, pipe't in bestehende DB
- **Fix:** Restore-Drill mit Tabellenanzahlen, Attachment-Dateien,
 Trigger/RLS und Health-Check nach Restore.

**[mittel K] Healthcheck bei degradiertem ClamAV gibt 200**
- ChatGPT-1: „bei aktivem, nicht verfügbarem ClamAV mit 503 statt nur
 ‚degraded'"
- **Fix:** Health-Endpoint differenziert; ClamAV-Ausfall fail-closed im Pfad.

**[mittel S — Codex-Claude] Release-Workflow ohne SBOM/Signierung/Provenance**
- `.github/workflows/release.yml:24–38` pusht Multi-Arch-Images ohne Cosign,
 SLSA, Attestation, SBOM
- **Fix:** SBOM (`syft`/BuildKit attestations), Cosign, Release-Checksums.

**[mittel S — Claude-ChatGPT-Prompt] Caddy-Edge ohne Rate-Limit**
- Brute-Force nur auf App-Layer
- **Fix:** Caddy-Rate-Limit-Plugin auf `/login/`, `/password-reset/`,
 `/auth/offline-key-salt/`.

**[niedrig K] `pyclamd 0.4.0` (Release 2016, EOL)**
- **Fix:** Update auf `clamd 1.0.6+` oder Migration zur `clamd`-Bibliothek.

**[niedrig S — Codex-Claude] `GUNICORN_TIMEOUT=30 s` zu kurz für lange
Migrationen**
- `docker-entrypoint.sh:22`
- **Fix:** Längeres Timeout oder Migrationen vor Container-Start (siehe
 „Zero-Downtime-Migrationsstrategie").

### D. Architektur & Code-Wartbarkeit

**[mittel K] Mono-App `core` ohne harte Boundaries**
- 25–29 Models, 25 View-Module, 30+ Services, alles in einer App
- Konsens-Empfehlung: vor App-Split zuerst Import-Linter / Grimp einführen
- Mittelfristiger Split: `accounts`, `clients`, `cases`, `documentation`,
 `audit_log`, `retention`, `reporting`, `offline`

**[mittel K] Type-Hints unter ~14 %, kein mypy/pyright in CI**
- `pyproject.toml` aktiviert nur Ruff mit `E/F/I/W`
- **Fix:** mypy schrittweise für `core/services` (`strict-optional` zuerst),
 CI-pflichtig mit Baseline.

**[mittel K] Ruff-Set zu schmal**
- Bug-Klassen (`B`), Sicherheits-Pattern (`S`), Komplexität (`C90`),
 Django-Idiome (`DJ`) nicht aktiv
- **Fix:** `select = ["E","F","I","W","B","S","UP","C90","DJ"]` + Baseline.

**[mittel S — Claude-Claude] RunPython ohne `reverse_code` in `0068`**
- `0068_attachment_versioning_stage_b.py` nicht reversibel; Downgrade-Pfad unklar
- **Fix:** Reverse-Migration ergänzen oder `noop` bewusst dokumentieren.

**[mittel S — Claude-ChatGPT-Prompt] `services/event.py` zu groß und mit
zwei Optimistic-Lock-Patterns**
- 660+ LOC, mischt CRUD, File-Marker, Sensitivity, Validation
- **Fix:** Split in `event_crud.py` + `event_data.py`; einheitliches
 `services/locking.check_version_conflict`.

**[mittel S — Codex-Claude] Zwei Löschpfade haben divergierende Historien-Semantik**
- (siehe Datenschutz oben) — fachlich gleiche Operation, unterschiedliche
 Datenschutzwirkung. Wartbarkeitsfehler mit Compliance-Folge.

**[niedrig K] Drei View-Stile koexistieren**
- `View`-Subclass mit hand-gerolltem HTMX, `TemplateView` + `if HX-Request`,
 neuer `HTMXPartialMixin`
- **Fix:** Optional, aber je länger ungemacht, desto teurer.

**[niedrig S — Claude-ChatGPT-Prompt] `INPUT_CSS` 5× dupliziert**
- In `forms/clients.py`, `cases.py`, `events.py`, `episodes.py`, `workitems.py`
- **Fix:** zentrale `forms/_widgets.py`.

**[niedrig S — Claude-ChatGPT-Prompt] AuditLog-Lücken bei State-Transitions**
- `assign_event_to_case`, `remove_event_from_case` loggen nicht
- **Fix:** AuditLog-Sweep über alle State-Changes.

### E. Performance & Skalierung

**[hoch S — Claude-Claude] N+1 im Zeitstrom-Feed**
- `services/feed.py:38–64`, `views/zeitstrom.py:56` mischt
 Events/Activities/Workitems ohne konsequentes Prefetch
- **Fix:** `select_related("created_by","document_type")` +
 `prefetch_related` für Assignees; Benchmark bei 200+ Items.

**[hoch K] Pagination ohne `max_page`**
- `views/{cases,clients,audit}.py`
- **Wirkung:** `?page=99999` triggert seq-scan
- **Fix:** Cap + 404 oberhalb.

**[mittel S — Codex-Claude] Event-Edit N+1 bei Datei-Feldern**
- `views/events.py:331–345`: `event.attachments.filter(pk=...).first` in Schleife
- **Fix:** `in_bulk` oder Prefetch-Map.

**[mittel S — Codex-Claude] Attachment-Liste hart auf 200 abgeschnitten**
- `views/attachments.py:87–114`: `attachments[:200]`, kein `has_more`/Pagination
- **Fix:** Server-side Pagination plus Filterstatus.

**[mittel K] JSONB-Filter ohne GIN-Index**
- `models/event.py:73–81`: `data_json` nicht GIN-indiziert
- **Fix:** `GinIndex(fields=["data_json"])` oder Denormalisierung
 häufig gefilterter Felder.

**[mittel S — Claude-Claude] `SESSION_SAVE_EVERY_REQUEST=True`**
- DB-Write-Amplifikation bei HTMX-Microrequests
- **Fix:** `False`; Sliding-Expiry über Custom-Middleware.

**[niedrig S — Codex-Claude] Composite-Indizes nicht zur Filterrealität passend**
- `models/case.py` Index `["facility","status","-created_at"]`, View filtert
 zusätzlich nach `lead_user`
- **Fix:** Index erweitern oder Filter-Reihenfolge anpassen.

### F. Tests & QS

**[hoch K] Live-RLS-Test fehlt** (siehe Sicherheit oben)

**[mittel S — Codex-Claude] Rate-Limit-Architekturtest deckt nur POST**
- `tests/test_architecture.py:295–359` prüft nur `post`-Handler
- **Fix:** GET-Endpunkte (Autocomplete, Suche) systematisch in Architektur-Test.

**[mittel S — Claude-Claude] Auth-E2E unvollständig (Password-Reset-Flow)**
- `tests/test_auth.py`
- **Fix:** Form-zu-Form-Test ergänzen.

**[mittel S — Claude-Claude] Kein Property-Based-Testing für Validatoren**
- Pseudonym, Encryption-Roundtrip, K-Anon-Buckets
- **Fix:** Hypothesis ergänzen.

**[mittel S — Codex-Claude] Kein Axe/Pa11y/Contrast/Fokus-Test in CI**
- WCAG 2.2 AA wird nicht automatisiert belegt
- **Fix:** Playwright + axe-core für Kernseiten und HTMX-Swap-Fokuspfade.

**[info] CI-Matrix nur Python 3.13**
- **Empfehlung:** Matrix mit Postgres-Major-Versionen.

### G. Dokumentation & Governance

**[mittel K] AGPL §13 Source-Link/„Powered by" fehlt in UI**
- `templates/base.html`, `templates/auth/login.html:25–31`
- **Fix:** Footer-Block mit AGPL-Hinweis und Quell-URL; per Env konfigurierbar.

**[mittel K] SECURITY.md stale (verweist auf `0.9.x` und alten Namespace)**
- Aktueller Stand: 0.10.2
- Advisory-Link: `anlaufstelle/app` statt `anlaufstelle/app`
- **Fix:** Vor Pilotbetrieb aktualisieren.

**[mittel K] Doku-Drift Encryption: AES-GCM vs. Fernet**
- `docs/admin-guide.md:545–554` sagt AES-GCM
- Code nutzt Fernet/MultiFernet
- **Fix:** Doku korrigieren oder bewusste Migration auf AES-GCM (wäre sauberer).

**[mittel K] README-Quickstart-Pfadfehler**
- `git clone https://github.com/anlaufstelle/app.git` → Verzeichnis `app`
- README: `cd anlaufstelle`
- **Fix:** `git clone... anlaufstelle` oder `cd app`.

**[mittel S — Codex-Claude] Code of Conduct, DCO/CLA fehlen**
- Bei externen Beiträgen unvollständig
- **Fix:** Code of Conduct, DCO oder klare Copyright-Regel.

**[mittel S — Codex-Claude] ADRs fehlen**
- Entscheidungen verstreut in Fachkonzept, Security Notes, Issues
- **Fix:** ADR-Serie für RLS, Retention/Historie, Statistik-MV, Offline-Krypto,
 AGPL-Source-Angebot.

**[niedrig S — Claude-Claude] Encryption-Key-Rollover-Runbook fehlt**
- `services/encryption.py:26–62` unterstützt MultiFernet-Rotation, aber kein
 prozedurales Runbook
- **Fix:** Runbook in `docs/`.

### H. Fachliche Eignung

**[mittel K] Mehrere Aliase pro Person nicht modelliert**
- Constraint `unique_facility_pseudonym` erzwingt 1:1
- In Streetwork und Drogenhilfe sind mehrere Namen pro Person üblich
- **Fix:** Optionales `ClientAlias`-Modell mit Suchindex und `primary/obsolete`-
 Markierung.

**[mittel S — Codex-Claude] Anonymität ist Event-Level, nicht als
fortführbarer anonymer Fall modelliert**
- Wiederkehrende anonyme Kontakte ohne Pseudonym bleiben einzelne Events
- **Fix:** Klären, ob „anonymous cohort/contact token" fachlich gewollt ist
 oder Doku schärfen.

**[mittel S — Claude-kritisch] Kontaktstufen-Doku nicht deckungsgleich mit
Datenmodell**
- README beschreibt drei Kontaktstufen (anonym, identifiziert, qualifiziert),
 `Client.ContactStage` kennt nur identified/qualified — anonym ist
 Event-Level
- **Fix:** Doku trennen: „anonymer Kontakt" = Event-Level, „Klientel" beginnt
 mit Pseudonym.

**[mittel S — Claude-kritisch] `contact_stage`-Hilfetext gefährlich**
- „qualifiziert = vollständige Identität bekannt" widerspricht
 „keine Klarnamen"
- **Fix:** Hilfetext schärfen, kann sonst Organisationen in falsche
 Erfassungspraktiken schieben.

**[niedrig K] User: Facility ist 1:1**
- `User.facility = ForeignKey`. Springer und Nachschicht-Teams brechen das
- **Fix:** mittelfristig Many-to-Many über `OrganizationMembership` o.ä.

**[niedrig S — Codex-Claude] Mobile-/Fahrzeug-Streetwork nur über Facility**
- Kein Standort-/Tour-/Fahrzeugmodell
- **Fix:** Optional `Location/Route` einführen, falls Pilot-Workflow das fordert.

### I. Barrierefreiheit & I18n

**[mittel S — Codex-Claude] `html lang` hart auf Deutsch**
- `templates/base.html:1–3`: trotz Sprachumschaltung
- **Fix:** `lang="{{ LANGUAGE_CODE }}"`.

**[mittel S — Claude-Claude, Codex-Claude] HTMX-Fokus-Management nach Swap**
- Kein Listener für `htmx:afterSwap`, der `[autofocus]` setzt
- **Fix:** Globaler Listener; aria-live-Region für Status.

**[mittel S — Claude-Claude] Formular-Errors ohne `aria-describedby`**
- `templates/components/form_input.html`
- **Fix:** Linkage ergänzen.

---

## Priorisierte Maßnahmenliste (konsolidiert, dedupliziert)

Ich habe alle Maßnahmen aus den acht Audits zusammengeführt, dedupliziert und
nach Aufwand × Impact × Konsens sortiert. Die Reihenfolge ist meine Empfehlung
für einen **„v1.0-Härtungssprint" von 4–6 Wochen**.

### — Sofort (Datenverlust + Datenschutz-Blocker), Woche 1

| # | Maßnahme | Aufwand | Impact | Belege |
|---:|---|:---:|:---:|---|
| 1 | **`MEDIA_ROOT`-Volume in `docker-compose.prod.yml` mounten** + Backup/Restore um Medien erweitern + Restore-Test | S | kritisch | 3 Audits |
| 2 | **Retention-Delete-Historie redaktieren** (gemeinsame `record_delete_history(redacted=True)` für beide Pfade) | M | kritisch | 3 Audits |
| 3 | **Daten-Migration für bestehende nicht-redaktierte `EventHistory`-DELETE-Einträge** | M | kritisch | Codex-Claude |
| 4 | **Image-Tag pinnen + Namespace-Drift fixen** (prod/staging einheitlich, konkrete Version statt `:latest`) | S | hoch | 3 Audits |
| 5 | **AGPL §13 Source-Link in Footer + Login** (per Env konfigurierbar) | S | hoch | 2 Audits |

### — Sicherheits-Durchsetzung, Woche 1–2

| # | Maßnahme | Aufwand | Impact | Belege |
|---:|---|:---:|:---:|---|
| 6 | **Live-RLS-Integrationstest mit Non-Superuser-DB-Rolle in CI** | M | kritisch | 3 Audits |
| 7 | **Service-Layer-Konsistenz-Sweep:** `handover._collect_open_tasks(user,...)` mit `visible_to`; `clients.update_client` auf Whitelist; `client_export` Sensitivity-Filter optional via Param; `event.update` auf `services/locking.check_version_conflict` | M | hoch | Claude-ChatGPT-Prompt |
| 8 | **WorkItem-Edit-Policy einheitlich** (`can_user_mutate_workitem` in vollem Edit-Pfad) | S | hoch | Codex-kritisch |
| 9 | **`FacilityScopeMiddleware`:** anonyme Requests `app.current_facility_id` explizit leeren | S | hoch | 2 Audits |
| 10 | **Validator `Sensitivity=HIGH ⇒ is_encrypted=True`** | S | hoch | Claude-Claude |
| 11 | **Architektur-Test:** `Event.objects.bulk_create`/`update(data_json=...)` außerhalb `services/encryption.py` verboten | S | hoch | Claude-kritisch |
| 12 | **CSV-Formula-Injection-Escaping** zentral in Export-Service | S | mittel | Codex-Claude |
| 13 | **Login-Lockout atomar** (Redis INCR) + **Autocomplete `block=True`** + Architektur-Test auf sensible GET | M | mittel | 2 Audits |
| 14 | **AuditLog-Trigger gegen UPDATE/DELETE** verifizieren (Migration `0024`) und ggf. ergänzen | S | mittel | Claude-Claude |

### — Datenschutz-Vollständigkeit, Woche 2–3

| # | Maßnahme | Aufwand | Impact | Belege |
|---:|---|:---:|:---:|---|
| 15 | **Anonymisierungs-Cascade vervollständigen** (EventHistory, EventAttachment, DeletionRequest) + Tests „Restdaten = 0" | M | kritisch | 3 Audits |
| 16 | **Klartext-Freitext inventarisieren und klassifizieren** (Client.notes, Case.description, Episode.description, WorkItem.description, AuditLog.detail) | S | hoch | 3 Audits |
| 17 | **`Client.notes`/`Case.description`** in Encryption-Modell überführen oder UI-Policy | M | hoch | 2 Audits |
| 18 | **K-Anonymität auf alle externen Berichte** (Jugendamt, CSV, PDF); `top_clients` rein intern oder entfernen | M | hoch | 2 Audits |
| 19 | **DeletionRequest-Approval-Workflow** umsetzen (Modell vorhanden, View fehlt) | M | hoch | Claude-Claude |
| 20 | **Lösch-/Anonymisierungs-Matrix** je Datenklasse + Workflows für Client, Case, Episode, WorkItem, User | L | hoch | Codex-Claude |
| 21 | **`StatisticsSnapshot.data` aggregations-only** oder `EncryptedJSONField` | M | mittel | Claude-Claude |

### — Operations & Beobachtbarkeit, Woche 3–4

| # | Maßnahme | Aufwand | Impact | Belege |
|---:|---|:---:|:---:|---|
| 22 | **Off-Site-Backup-Hook** (rclone/restic/S3) | S | hoch | Claude-ChatGPT-Prompt |
| 23 | **Backup-Restore-Drill** mit Tabellen, Attachments, Trigger/RLS, Healthcheck | M | hoch | 2 Audits |
| 24 | **Healthcheck differenziert** (ClamAV-Ausfall → 503 oder fail-closed) | S | mittel | ChatGPT-1 |
| 25 | **Caddy-Edge-Rate-Limit** auf `/login/`, `/password-reset/`, `/auth/offline-key-salt/` | S | mittel | Claude-ChatGPT-Prompt |
| 26 | **Encryption-Key-Rollover-Runbook** | S | mittel | Claude-Claude |
| 27 | **Release-Pipeline:** SBOM (`syft`), Cosign-Signatur, Provenance/SLSA | M | mittel | Codex-Claude |
| 28 | **Dependabot, Trivy, Bandit** als CI-Jobs | S | mittel | Claude-ChatGPT-Prompt |
| 29 | **`pyclamd 0.4.0` → `clamd 1.0.6+`** | S | mittel | Claude-Claude |
| 30 | **Doku-Konsistenz:** SECURITY.md auf 0.10.x; AES-GCM-vs-Fernet-Drift; README `cd app` | S | mittel | mehrere |

### — Performance & Skalierung, Woche 4

| # | Maßnahme | Aufwand | Impact | Belege |
|---:|---|:---:|:---:|---|
| 31 | **N+1 im Zeitstrom-Feed** beheben | M | hoch | Claude-Claude |
| 32 | **Pagination-Cap** in cases/clients/audit | S | hoch | Claude-Claude |
| 33 | **Event-Edit N+1 bei Datei-Feldern** + Attachment-List-Pagination | M | mittel | Codex-Claude |
| 34 | **JSONB GinIndex** auf häufig gefilterten Pfaden | S | mittel | Claude-Claude |
| 35 | **`SESSION_SAVE_EVERY_REQUEST=False`** | S | mittel | Claude-Claude |
| 36 | **Search:** `data_json__icontains` durch JSONB-Pfad-Suche pro nicht-verschlüsseltem Field-Slug ersetzen | M | mittel | Claude-kritisch |

### — Strukturelle Hygiene, Woche 5–6

| # | Maßnahme | Aufwand | Impact | Belege |
|---:|---|:---:|:---:|---|
| 37 | **mypy in CI** (inkrementell, `core/services` zuerst) | M | hoch | Claude-Claude |
| 38 | **Ruff erweitern** (`B`,`S`,`UP`,`C90`,`DJ`) + Baseline | S | mittel | Claude-Claude |
| 39 | **Import-Linter / Grimp** für Modulgrenzen in `core` | S | mittel | Claude-Claude |
| 40 | **`services/event.py` splitten** (`event_crud.py` + `event_data.py`) | M | mittel | Claude-ChatGPT-Prompt |
| 41 | **`forms/`-`INPUT_CSS`** zentralisieren | S | niedrig | Claude-ChatGPT-Prompt |
| 42 | **AuditLog-Sweep** über alle State-Transitions | M | mittel | Claude-ChatGPT-Prompt |
| 43 | **HTMX-Fokus-Management + aria-live + `aria-describedby` + `html lang` dynamisch** | M | mittel | 2 Audits |
| 44 | **ADRs** für RLS, Retention/Historie, Statistik-MV, Offline-Krypto, AGPL-Source | M | mittel | Codex-Claude |
| 45 | **Code of Conduct, DCO/CLA-Entscheidung** | S | mittel | Codex-Claude |
| 46 | **Axe/Pa11y E2E-Tests** für Kernflows | M | mittel | 2 Audits |

### Strukturell — nach v1.0

| # | Maßnahme | Aufwand | Impact |
|---:|---|:---:|:---:|
| 47 | **App-Split** (`clients`, `documentation`, `workitems`, `retention`, `reporting`, `audit`, `offline`) — erst nach Import-Linter und mit klarem ADR | XL | mittel |
| 48 | **Co-Maintainer-Akquise** (Bus-Faktor 1 → 2+) | XL | hoch |
| 49 | **`ClientAlias`-Modell** für Mehrfach-Aliase | M | mittel |
| 50 | **Reporting-Fact-Modell** (normalisiert, neben JSONB-Erfassung) | XL | hoch |
| 51 | **`Client.pseudonym`-Verschlüsselung** mit HMAC-Lookup-Index (Trade-off: Trigram-Suche bricht) | L | hoch |

---

## Was alle Auditoren übersehen haben oder schwach bewerten

1. **Maintainer-Strategie als Sicherheitsfrage.** AI-gestützte Solo-Entwicklung
 produziert hohe Velocity bei niedrigem Bus-Faktor und hoher Konsistenz im
 Stil. Das ist genau der Modus, in dem subtile Inkonsistenzen (z. B. die vier
 Service-Layer-Lücken oben) entstehen, ohne dass ein Reviewer sie früh fängt.
 **Empfehlung:** vor v1.0 mindestens einen externen Senior-Reviewer für die
 Sicherheits-Schichten (RLS, Encryption, Retention, Audit) — Pen-Test allein
 reicht nicht, weil Pen-Tests Bypass-Pfade nicht systematisch enumerieren.

2. **-Roadmap-Realität.** Die Härtungs-Pipeline oben (Phasen 1–6) ist
 ~4–6 Wochen Vollzeit. Wenn der-Antrag bewilligt wird, sind die
 Milestones M1–M3 in dieser Phase eine bessere Investition als neue Features.
 Der Antrag enthält Hardening, aber falls er Feature-Milestones zuerst legt,
 sollte das vor der Bewilligung neu sortiert werden.

3. **Pilot-Strategie ist die größte ungelöste Frage.** Sechs Audits weisen
 darauf hin, dass das System für die plakatierte Zielgruppe (NGO ohne IT)
 ohne Managed-Hosting nicht betreibbar ist. Das ist kein Code-Bug, sondern
 eine Produktstrategie-Lücke. Mögliche Pfade:
 - Trägerinitiative für gemeinsames Hosting (z. B. ein Verein, der für 10–20
 Einrichtungen das Coolify-Setup übernimmt — Mitgliedsbeitrag deckt Ops)
 - Anlaufstelle GmbH/UG als Managed-Hosting-Anbieter (kollidiert mit AGPL
 nicht, weil eigener Code; setzt aber Geschäftsmodell-Fokus voraus)
 - Kooperation mit bestehender Sozialwirtschafts-IT (z. B. Caritas/Diakonie-
 Rechenzentren)

4. **Die Frage „kollabiert es unter eigener Komplexität" wird konsistent
 verneint, aber ohne Stresstest.** Die Komplexitäts-Hotspots (`event.py`,
 `retention.py`, JSONB-Schema-Drift, Offline-Sync) sind alle in der
 Größenordnung, in der man mit 3–6 Monaten harter Arbeit *jetzt*
 refaktorisieren kann oder in 18–24 Monaten gezwungen wird. Der Hebel ist
 höher, je früher das passiert.

5. **JSONB-Schema-Governance.** Drei Audits flaggen das, niemand schlägt eine
 konkrete Lösung vor. Empfehlung von Claude-ChatGPT-Prompt aufgreifen:
 Management-Command `audit_data_json_drift`, der Events findet, deren
 `data_json`-Keys nicht mehr in aktiven `FieldTemplate`-Slugs vorkommen.
 Plus optionaler Cleanup-Walk mit Migrations-Tabelle.

---

## Erste 3 Maßnahmen — meine Empfehlung

Wenn man das System morgen produktiv übergeben müsste, die drei Stellen, an
denen alle Audits zusammenlaufen:

### 1. **Datenverlust-Bug fixen (, Tag 1)**

`MEDIA_ROOT`-Volume in `docker-compose.prod.yml`, plus Backup/Restore um
Medien erweitern, plus E2E-Test, der einen Anhang über Container-Recreate
hinweg überlebt. Das ist 30 min Compose-Edit + 2 h Backup-Skript-Erweiterung
+ 1 h Restore-Test. Ohne diesen Fix ist jede andere Härtung Symbolpolitik.

### 2. **Retention/EventHistory-Datenfluss fixen (, Tag 2–4)**

Gemeinsame `record_delete_history(event, redacted=True)` für beide Löschpfade.
Daten-Migration für bestehende nicht-redaktierte Einträge. Tests, die genau
prüfen, dass nach `enforce_retention` keine Klartext-Werte in
`EventHistory.data_before` mehr stehen. Ohne diesen Fix ist „Löschung" im
DSGVO-Sinne nicht erfüllt — und das ist der schwerste Vorwurf, den drei
unabhängige Auditoren bestätigen.

### 3. **Live-RLS-Test in CI (, Woche 1)**

Dedizierte Postgres-Rolle ohne Superuser; Test-Fixture, die auf dieser Rolle
fährt; Cross-Tenant-SELECT-Tests, die 0 Rows asserten. Ohne diesen Test ist
„RLS schützt" eine Hoffnung, kein Mechanismus. Mit diesem Test ist
„Defense-in-Depth" belegbar — und das ist genau die Aussage, die das-Funding und Pilot-Pitches tragen muss.

Alles weitere — App-Split, Reporting-Fact-Modell, Pen-Test, Off-Site-Backup,
JSONB-Governance — folgt aus diesen drei.

---

## Anhang: Wie die acht Audits zueinander stehen

**Wenn du nur eines lesen willst:** Codex-Claude (umfassend-strukturiert) — am
präzisesten mit Belegstellen, kalibriert in der Schwere-Bewertung, wenig
Bias.

**Wenn du nur die Kritikpunkte willst:** ChatGPT-1 — kompakt, harte Sprache,
Fokus auf Lifecycle und Operations.

**Wenn du Selbstvertrauen brauchst:** Gemini-ChatGPT — aber bewusst
einordnen, dass es Durchsetzungslücken übersieht.

**Wenn du Service-Layer-Konsistenz prüfen willst:** Claude-ChatGPT-Prompt
beste Belege für die vier konkreten Service-Lücken.

**Wenn du Datenschutz-Lifecycle prüfen willst:** Codex-Claude und Codex-kritisch
gemeinsam — beide haben den EventHistory-Befund, mit verschiedenen Belegen.

**Wenn du Architektur prüfen willst:** Claude-Claude und Claude-kritisch
beide am tiefsten in der Bewertung von App-Split, Service-Layer-Patterns und
Connection-Pool-Risiken.

— Ende der Konsolidierung.
