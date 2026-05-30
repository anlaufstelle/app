# 🔍 System-Audit: Anlaufstelle (v0.10.2 / 2026-04-28)

**Repository:** github.com/anlaufstelle/app · **Tag:** v0.10.2 · **120 Commits · 74 Migrationen · ~25k LOC Python · 84 Templates · 26 JS-Files**

> Vorbemerkung: Das Projekt ist explizit als Pre-Release deklariert ("noch nicht für den Produktiveinsatz freigegeben", README:7). Die Bewertung berücksichtigt das, ist aber an dem Anspruch gemessen, den das Projekt selbst formuliert: ein Fachsystem für Art. 9 DSGVO-Daten, das auf Augenhöhe zu kommerzieller Sozialwirtschafts-Software stehen soll.

---

## 1. 🏗️ Systemarchitektur

**Stil:** Modularer Monolith. Single Django-App `core` mit klar getrennten Sub-Paketen: `models/`, `views/`, `services/`, `forms/`, `middleware/`, `signals/`, `templatetags/`, `management/commands/`. Kein Microservice-Overengineering, kein DRF-Layer (HTMX-first). Postgres 16, Tailwind, Alpine.js (CSP-Build seit 0.10.2), ServiceWorker, IndexedDB.

**Service-Layer:** Existiert real und ist nicht nur Cargo-Cult — `core/services/` hat 30 Module (clients, cases, events, retention, encryption, file_vault, mfa, sensitivity, locking, …). Views ziehen sich keine Geschäftslogik aus den Models, sondern rufen Service-Funktionen. `services/audit.py:log_audit_event` ist ein zentraler Helper, der View-Boilerplate wegabstrahiert. Sensitivity-Gates leben in genau einem Modul (`services/sensitivity.py`) und werden überall zentral importiert.

**Mittelware-Pipeline** (`settings/base.py:43–65`): Sauber geordnet — Security → CSP (mit Admin-Relax-Outbound-Patch davor, korrekt erklärt mit Reihenfolge-Kommentar) → Whitenoise → Session → Locale → CSRF → Auth → OTP → HtmxSession → **FacilityScope** → UserLanguage → ForcePasswordChange → MFAEnforcement → Messages → Frame → HTMX. Die Reihenfolge ist begründet (siehe Inline-Kommentare); MFA-Gate kommt nach Auth, FacilityScope steht direkt nach OTP, was richtig ist.

**Stärken**
- Klare Schichtung. Models tragen Daten + minimale Invariants, Services tragen Business-Logik, Views tragen HTTP. Kein "fat model"-Antipattern, kein Service-Soup.
- `FacilityScopedManager` + `EventManager.visible_to(user)` als zentrale Querysets — Multi-Tenancy und Sensitivity sind nicht Boilerplate in jeder View, sondern QuerySet-Methoden.
- Defense-in-Depth-Schichten sind real und greifen ineinander: ORM-Filter (`for_facility`) + Postgres RLS (Migration 0047) + Audit-Log mit DB-Trigger gegen Mutation.

**Schwächen**
- Drei View-Stile koexistieren: hand-gerollte `View.dispatch`, `TemplateView` mit eigenem HTMX-Switch, und `HTMXPartialMixin` für Neue. Bewusste Entscheidung (`mixins.py:38–43`-Kommentar), aber für Newcomer Reibung.
- Service-Layer ist nicht stilistisch homogen: `cases.py`/`clients.py` sind sauber paramterisiert mit Audit + Activity + Optimistic-Lock; `event.py` rollt eigenen Optimistic-Check (`event.py:563`), obwohl `services/locking.check_version_conflict` existiert; `client_export.py` ist quasi prozedurale Daten-Sammlung ohne `for_facility`-Konvention.
- Innerhalb von Services werden Models in Funktionsbody-`from`-Importen geladen (`retention.py:446, 491, 561, 611`), um Zyklen zu umgehen — Anzeichen für leichte Verfilzung im Domänen-Layout.
- 660+-LOC-Datei `services/event.py` mischt CRUD, File-Marker-Plumbing, Sensitivity-Filter und Datenvalidierung. Reif für einen Split.

**Risiko-Level: niedrig.** Das ist nicht "zufällig gewachsen". Die Schichten tragen.

---

## 2. 📦 Domänenmodell & Konzept

Das Modell ist ungewöhnlich klar gedacht — **fachlich, nicht zufällig**. Es wirkt nicht nach „vier Tutorials und Stack Overflow", sondern nach jemandem, der das Feld kennt. Begründet durch: explizite Diplomarbeit als fachliche Grundlage (README:148–150 + `docs/fachkonzept-anlaufstelle.md` Abschnitt 2.4), 113 KB Fachkonzept mit Versionshistorie (1.0 → 1.4) und nachvollziehbar geschlossenen offenen Entscheidungen.

**Kernentitäten und ihre Sauberkeit:**

| Entität | Bewertung | Anmerkung |
|---|---|---|
| `Client` (`models/client.py`) | **Elegant** | Kein Namensfeld. Pseudonym + ContactStage (Identified/Qualified) + AgeCluster. K-Anonymisierung als First-Class-Funktion (`anonymize` + `k_anonymize`). Trigram-Index auf `pseudonym`. |
| `Event` (`models/event.py`) | **Elegant** | Generischer JSONB-`data_json` mit `DocumentType`-getriebenem Schema. `is_anonymous`, `is_deleted` als Soft-Flags. Composite-Index für Timeline. Verschlüsselungs-Hook im `save` (Refs Field-Templates). |
| `DocumentType` + `FieldTemplate` (`models/document_type.py`) | **Senior** | Pro-Einrichtung konfigurierbar. Sensitivity NORMAL/ELEVATED/HIGH. Slug ist nach Erstellung **immutable** (geschützt im `save`). FieldTemplate.sensitivity überschreibt Doc-Type-Sensitivity (Maximum gewinnt — `services/sensitivity.effective_sensitivity`). FILE-Felder sind erzwungen `is_encrypted=True` (`document_type.py:271`). |
| `Case` + `Episode` + `OutcomeGoal` + `Milestone` | **Solide** | Cases als optionaler Container; `client=SET_NULL` lässt Case auch nach Anonymisierung statistisch verwertbar. |
| `Organization` + `Facility` (`models/organization.py`) | **Bewusst minimal** | Schmale Datenstruktur, aber FK ist überall vorhanden (Prinzip 2 "Einrichtung als Ausgangspunkt", Fachkonzept §4). Vorbereitet für Multi-Tenancy ohne aktuell konsumiert zu werden. |
| `AuditLog` (`models/audit.py`) | **Elegant** | Append-only auf zwei Ebenen: Python-`save` lehnt Updates ab, Postgres-Trigger (`Migration 0024`) lehnt UPDATE/DELETE auf DB-Ebene. Das ist Forensik-tauglich. |
| `RetentionProposal` + `LegalHold` | **Senior** | DSGVO-Löschvorschläge mit 4-Augen-Approval und Defer-Folgeverhalten (`Settings.retention_auto_approve_after_defer`, `retention_max_defer_count`). |
| `WorkItem` (Hinweis/Aufgabe) | **Solide** | Operative Info-Kategorie neben Dokumentation, eigener Lifecycle (Prinzip 4 Fachkonzept). |

**Wo es zuerst bricht, wenn es bricht:**
1. **JSONB-Wildwuchs.** `Event.data_json` ist explizit JSON-Schema-frei. Bei einer Einrichtung mit 30 DocumentTypes × 15 FieldTemplates und 5 Jahren Historie wird das Schema-Drift (umbenannte Slugs, gelöschte Field-Templates) zur Reporting-Hürde. Das Fachkonzept ist sich dessen bewusst (§18 "JSONB-Performance-Monitoring") — aber das Risiko bleibt latent.
2. **`Client.cases` und `Client.events` haben `on_delete=SET_NULL`.** Korrekt für DSGVO (Anonymisierung darf Statistik nicht töten), aber Reporting-Queries müssen jetzt `client__isnull` korrekt behandeln. Mehrere Service-Funktionen tun das nicht explizit.
3. **`DocumentTypeField.sort_order` und `FieldTemplate.options_json` haben keine eigene Versionierung.** Wird ein Field umkonfiguriert, ändert sich rückwirkend die Anzeige historischer Events. Für Audit-Robustheit fragwürdig.

**Antwort auf die Leitfrage: Elegant, mit kleinem Beigeschmack.** Konzepte tragen, aber JSONB-Schema-Drift wird zwischen v1.0 und v3.0 zum Schmerzpunkt.

---

## 3. 🔐 Sicherheit & DSGVO

**Das ist der Bereich, in dem das Projekt am stärksten überrascht.** Die Maintainer haben sich ihre Threat-Models bewusst gemacht und mehrere Verteidigungslinien gestapelt. Beispiele aus dem geprüften Code:

| Mechanismus | Status | Belegstelle |
|---|---|---|
| **Pseudonymisierung** | Strukturell, kein Namensfeld im Schema | `models/client.py` — keine `first_name`/`last_name` in der Tabelle |
| **Field-Encryption** | Fernet/AES-128 mit MultiFernet-Rotation, `lru_cache` für Performance, `setting_changed`-Cache-Invalidation für Tests | `services/encryption.py:43–71` |
| **File-Encryption** | Chunked Fernet mit 64KB-Blocks, Versions-Header, separater `decrypt_file_stream`-Generator (kein OOM) | `services/encryption.py:139–207` |
| **Postgres RLS** | Aktiviert auf 15 facility-scoped Tabellen + 3 Join-Tables. `FORCE ROW LEVEL SECURITY` (auch Owner-bypass eingeschränkt) | Migration `0047_postgres_rls_setup.py` |
| **MFA** | TOTP via django-otp + StaticTokens (Backup-Codes, einmalig per `verify_token`-Konsumierung), middleware-erzwungen, MFA-Verify pro Session | `middleware/mfa.py`, `services/mfa.py` |
| **Audit-Log** | Append-only auf Python- UND Postgres-Trigger-Ebene, IP-Adresse mit `TRUSTED_PROXY_HOPS`-respektierender Extraction | `models/audit.py:104–113`, Migration `0024`, `signals/audit.py:15–48` |
| **Account-Lockout** | 10 Fehlversuche / 15 min, Admin-Unlock via separate AuditLog-Action, kein Side-Channel | `services/login_lockout.py` |
| **Rate-Limits** | Doppelt: IP (5/m) + Username (10/h) auf Login, 19 weitere POST-Handler in 0.10.1 ergänzt, Architektur-Test verbietet ungeschützte Mutationen | `views/auth.py:42–43` + CHANGELOG v0.10.1 |
| **CSP** | `script-src 'self'` ohne `unsafe-eval` seit 0.10.2 (vollständige `@alpinejs/csp`-Migration), `frame-ancestors 'none'`, Architektur-Tests verbieten Inline-Handler | `settings/base.py:240–267`, CHANGELOG v0.10.2 |
| **CSRF** | HttpOnly-Cookie + SameSite=Strict (Token aus gerendertem HTML, nicht aus Cookie) | `settings/prod.py:62–68` |
| **File-Upload-Härtung** | Extension-Whitelist + ClamAV (fail-closed) + libmagic-Magic-Byte-Verifikation mit OOXML/JPEG-Equivalence-Map, `SECURITY_VIOLATION` AuditLog | `services/file_vault.py:103–256` |
| **Anti-Enumeration** | Password-Reset gibt identische Antwort egal ob E-Mail existiert; Audit-Log nur bei eindeutigem Match | `views/auth.py:111–139` |
| **Sensitivity-Gates** | Zentrale Logic in `services/sensitivity.py`, `EventQuerySet.visible_to(user)`, 404 statt 403 bei fehlender Sichtbarkeit (kein Existenz-Leak) | `services/sensitivity.py:72–92` |
| **Offline-Kryptographie** | PBKDF2/600k Iterationen, AES-GCM-256, Key `extractable: false`, Salt-Rotation bei Passwort-Wechsel | `views/auth.py:142–179` + CHANGELOG v0.10.0 |
| **Prod-Settings fail-closed** | `ImproperlyConfigured` wenn `DJANGO_SECRET_KEY`, `ALLOWED_HOSTS`, `ENCRYPTION_KEY(S)` fehlen | `settings/prod.py:38–101` |

**Dokumentierte bewusste Entscheidungen:**
- `core_user` bleibt RLS-frei (Bootstrap + Cross-Facility-Admin) — `docs/security-notes.md`
- `SESSION_COOKIE_SAMESITE = "Lax"` (Password-Reset-Mail-Links müssen funktionieren)
- `AuditLog.facility_id` nullable (System-Events vor Facility-Bindung)

Das ist seltene Disziplin: jede Abweichung vom strikten Default hat ein dokumentiertes Warum.

### Top-5-Sicherheitsrisiken (verbleibend)

1. **🔴 RLS wird in CI nicht funktional getestet.** `tests/test_rls.py:8–9` gibt explizit zu, dass der Test-DB-User Superuser ist und RLS damit komplett umgangen wird. Es gibt **keine** Integrationstest-Stelle, die mit einem `NOSUPERUSER`-Rollen-User Cross-Tenant-Isolation funktional verifiziert. Wenn jemand in Produktion versehentlich (oder durch Coolify-Default) den Django-DB-User als Superuser anlegt, wird RLS still abgeschaltet — und nichts schlägt Alarm. Der `ops-runbook.md`-Hinweis (Zeile 94–100) ist gut, aber kein Code-Gate.

2. **🟠 Handover-View leakt Pseudonyme über Sensitivity-Grenzen hinweg.** `services/handover._collect_open_tasks(facility)` (Zeile 110) hat keinen `user`-Parameter und keinen `visible_to`-Filter. Work-Items, die einer Klientel mit eingeschränktem DocumentType zugeordnet sind, erscheinen mit Pseudonym in der Übergabe-Übersicht für Assistants. Verifiziert.

3. **🟠 `services/clients.update_client` akzeptiert beliebige `**fields` ohne Allowlist** (`clients.py:79`). Fields wie `facility_id`, `created_by_id`, `is_active` könnten via Form-Tampering überschrieben werden, sofern eine View die Kwargs aus Request-Daten füttert. Das tut aktuell keine, aber es ist eine offene Tür. `cases.update_case` macht es korrekt (Whitelist).

4. **🟠 `client_export.export_client_data` überspringt Sensitivity-Filter** (`client_export.py:44–110`). Wird die Funktion via Staff-Aufruf benutzt (`views/clients.py:245`), enthält der Art.-15-Export auch HIGH-sensitivity-Felder, die der Staff-User normalerweise nicht sehen darf. Für Datenauskunft an den Betroffenen selbst korrekt — für Staff-Export fragwürdig. Außerdem verwendet die Funktion `Event.objects.filter(client=client)` statt `for_facility` — funktional korrekt (client ist scoped), aber konventionsbruch.

5. **🟡 Optimistic-Locking-Bypass im Event-Pfad.** `services/event.py:563` rollt eigenen `str(updated_at)`-Vergleich gegen rohen POST-String, statt `services/locking.check_version_conflict` zu nutzen. Subtil: Time-Zone-Format-Drift zwischen Browsern und Python-Datetime kann Conflict-Detection unterlaufen.

### Würde ich diesem System echte sensible Sozialdaten anvertrauen?

**Bedingt ja.** Konkret:
- **Ja, sobald** (a) der RLS-Live-Test existiert, (b) die obigen 4 Findings adressiert sind, (c) der MEDIA_ROOT-Volume-Mount in der prod-Compose existiert (siehe §6, Datenverlust-Bug), und (d) ein Penetrationstest mit Schwerpunkt auf IDOR/Tenant-Bypass läuft.
- **Nicht** als generische Aussage über v0.10.2 ohne diese Schritte.

Der Sicherheitskontext ist überdurchschnittlich durchdacht. Das ist nicht das übliche Django-Tutorial-Niveau. Aber „überdurchschnittlich durchdacht" reicht für Art. 9 nicht — es braucht **negative Tests**, die Bypass-Pfade aktiv abschließen, nicht nur Happy-Path-Beweise dass die Defense-Schichten existieren.

---

## 4. 🧪 Codequalität

**Verdikt: Senior.** Konsistent, defensiv, mit dokumentierten Trade-offs. Kommentare erklären WARUM, nicht WAS.

**Indikatoren:**
- Lange Doc-Strings auf jedem nicht-trivialen Service (z. B. `middleware/facility_scope.py:1–29` erklärt SET LOCAL → SET-Session-Migration mit Issue-Referenz #586).
- Kommentare verweisen auf konkrete Issues (Refs #542, #586, #598, #635, …) — nachvollziehbare Architektur-Entscheidungs-History.
- `lru_cache` mit Cache-Invalidation via `setting_changed`-Signal (`encryption.py:43–71`) — kein Footgun.
- `pyproject.toml`: `ruff` als Linter, mit Tests-Pin auf 0.15.11 in CI.
- Type Hints sind nicht durchgängig, aber vorhanden wo es zählt.
- Naming ist klar: `is_assistant_or_above`, `effective_sensitivity`, `safe_decrypt`, `_log_security_violation`.

**Wo der Code am schwächsten ist:**

| Bereich | Problem | Belegstelle |
|---|---|---|
| `services/event.py` | 660+ LOC, mischt 4 Verantwortlichkeiten (CRUD, File-Marker, Sensitivity, Validation). Zwei verschiedene Optimistic-Lock-Patterns nebeneinander. | `services/event.py:286, 456, 563` |
| `services/client_export.py` | Konventionsbruch zur sonstigen Codebase: kein `for_facility`, kein Sensitivity-Filter. Lesefluss ist okay, aber inkonsistent. | `client_export.py:44–110` |
| Forms-Layer | `INPUT_CSS = "w-full bg-canvas …"` 5× verbatim dupliziert in `forms/clients.py:10`, `cases.py:10`, `events.py:11`, `episodes.py:9`, `workitems.py:10`. Tailwind-Theme-Tweak = 5 Edits. | s. o. |
| Audit-Konsistenz | `cases.assign_event_to_case`/`remove_event_from_case` akzeptieren `user` aber loggen nicht. `cases.update_case`/`close_case` loggen. | `cases.py:124, 149` |
| Business-Rule-Duplikate | Anonym/Case-Klient-Invariant in `cases.assign_event_to_case` und `event.create_event`; FieldTemplate-Lookup-Loop in `offline.py:71`, `search.py:51`, `event.py:36` mit drei Signaturen. | s. Service-Audit |

**Test-Qualität:** Senior. Real-Postgres in CI (kein Mocking der DB). Parametrisierte Rollen-Matrix-Tests (`test_rbac_matrix.py`: 4 Rollen × ~25 Views). E2E mit Playwright + xdist-parallelisiertem Worker-DB-Setup. Negative Tests existieren (z. B. `test_encryption.py:50–63` testet, dass nach Key-Drop alte Daten als `[verschlüsselt]` zurückkommen — destruktiver Pfad). `xfail` wird bewusst eingesetzt für bekannte Bugs (`test_retention_bulk_defer.py:174`).

Die Schwächen liegen nicht in der Test-Qualität, sondern in der **Test-Coverage-Strategie**: Live-RLS, IDOR-Fuzzing und CSRF-Bypass-Tests fehlen.

---

## 5. 🧩 Komplexität & technische Schulden

### Top-5-Tech-Debt-Hotspots

1. **`services/event.py` (660 LOC)** — Splittet sich nicht von selbst. Brauchbar in zwei: `event_crud.py` (create/update/delete + locking) und `event_data.py` (filter_data_json, build_field_template_lookup, encryption-hooks). 1–2 Tage Aufwand.

2. **JSONB-Schema-Governance** — Es gibt keinen Linter, keinen Migration-Hook, kein Reporting für „in welchen Events liegen Werte zu Field-Slugs, die nicht mehr aktiv sind?". Bei drei Iterationen am Field-Set einer Einrichtung wird `data_json` zum Müll-Container. `FieldTemplate.is_active` (Migration 0049) ist ein erster Schritt, aber kein Cleanup-Pfad.

3. **Drei View-Stile koexistieren** — `View`-Subclass mit hand-gerolltem HTMX, `TemplateView` + `if request.headers.get("HX-Request")`, neuer `HTMXPartialMixin`. `mixins.py:38–43` rechtfertigt es bewusst, aber ein neuer Contributor muss drei Patterns lernen statt einem. Migration ist optional, aber je länger sie unterbleibt, desto teurer.

4. **`forms/`-Duplikat-CSS und Form-Helpers** — `INPUT_CSS` 5× kopiert. Trivial zu beheben, aber Symbol für „nichts hat es bisher zentralisiert", was bei Theme-Wechseln (z. B. v0.10.1 "Visual Refresh Grün") unnötig laut wird.

5. **Audit-Log-Lücken** — Nicht alle State-Transitions loggen. `assign_event_to_case` (cases.py:125) loggt nicht, `remove_event_from_case` (cases.py:149) nicht. CHANGELOG v0.10.1 erwähnt explizit, dass `close_case`/`reopen_case`/`delete_milestone` "vorher silent" waren — d.h. das Pattern wird kontinuierlich nachgezogen, aber ist offensichtlich noch nicht erschöpft.

**Was wird zuerst unwartbar?** Wahrscheinlich die `services/event.py` + die JSONB-Daten. Beide skalieren als Code/Daten-Fragestellungen unabhängig voneinander schlecht. Wenn eine Einrichtung 18 Monate produktiv ist und ihren Field-Satz dreimal überarbeitet hat, ist das ein Reporting-Albtraum.

---

## 6. ⚙️ Entwicklererfahrung & Betrieb

### Deploy-Stack: solide

- **Multi-Stage-Dockerfile** (Builder, Tailwind-Builder, Slim-Runtime), Non-Root `appuser` UID 1000, `python:3.13-slim` als Base, Image-`HEALTHCHECK`. (`Dockerfile`)
- **Migration-Race korrekt gelöst** via `pg_advisory_lock(1)` im Entrypoint (`docker-entrypoint.sh:5–15`) — multi-replica-safe.
- **Backup-Skript** mit `openssl aes-256-cbc -pbkdf2`, 7/28/90-Day-Rotation, `--verify`-Flag (Restore in Temp-DB + Row-Count). (`scripts/backup.sh`)
- **Network-Isolation** in `docker-compose.prod.yml:75–77`: `internal: true` auf db/clamav-Netz, frontend nur via Caddy.
- **MultiFernet-Key-Rotation** dokumentiert in `.env.example:17–21`.
- **Multi-Arch-Release** (amd64+arm64) mit GHA-Cache, Versions-Build-Arg.

### Aber: drei Gotchas, die einer Förderentscheidung im Weg stehen

**🔴 (1) `MEDIA_ROOT`-Volume fehlt in `docker-compose.prod.yml`.** `.env.example:53` setzt `MEDIA_ROOT=/data/media`, aber **kein** Service mountet `/data` irgendwo. Verschlüsselte Datei-Anhänge schreibt der File Vault in den Container — und sind beim nächsten `docker compose pull && up -d` (release-checklist.md:55–57) **weg**. Das ist ein **stiller Datenverlust-Bug**, kein bloßer Ops-Hinweis. Siehe `docker-compose.prod.yml:16–36` (volumes-Block fehlt am `web`-Service komplett).

**🔴 (2) Image-Tag-Drift zwischen Compose-Files.** `docker-compose.prod.yml:17` referenziert `ghcr.io/anlaufstelle/app:latest`, `docker-compose.staging.yml:25` referenziert `ghcr.io/anlaufstelle/app:latest`. Unterschiedliche Orgs. Staging wird das falsche Image ziehen — oder schlimmer: gar nichts ziehen, falls die zweite Org keine Bilder hat.

**🟠 (3) `:latest` Pinning + keine Off-Site-Backups.** `docker-compose.prod.yml:17` pinnt `:latest`. Rollback-Anweisung im `ops-runbook.md` setzt manuelles Edit voraus. Backup-Skript schreibt nach `${PROJECT_DIR}/backups/` — **gleiche Disk wie pgdata**. Disk-Failure = Total-Datenverlust inkl. Backups. Kein rsync/restic/S3-Mirror.

### Weitere Ops-Schwächen

- **Keine Caddy-Edge-Rate-Limits** auf `/login/`, `/password-reset/`, `/auth/offline-key-salt/` — Brute-Force nur auf App-Layer. (`Caddyfile:1–11`)
- **Dev-Compose exponiert Postgres auf allen Interfaces** (`docker-compose.yml:7–9`). Bind sollte `127.0.0.1:5432` sein.
- **`gunicorn` ohne Log-Flags** (`docker-entrypoint.sh:19–22`) — Request-Logs gehen nirgendwohin.
- **Beobachtbarkeit minimal**: optionales Sentry, sonst nichts. Kein `/metrics`, keine Log-Shipping-Doku, keine Alerting-Wiring.
- **Kein Schlüssel-Rotations-Runbook** für `DJANGO_SECRET_KEY` (Sessions invalidieren), `BACKUP_ENCRYPTION_KEY` (Re-Encryption-Walk). `ENCRYPTION_KEYS`-Rotation ist unterstützt, aber nicht prozedural dokumentiert.
- **Staging hat keinen ClamAV-Service** (`docker-compose.staging.yml`), aber `prod.py` defaultet `CLAMAV_ENABLED=true` — Staging-File-Uploads schlagen fail-closed fehl, sofern man nicht `CLAMAV_ENABLED=false` in `.env.staging` setzt. Nicht dokumentiert.

### Ist man in 1–2 Tagen produktiv?

**Lokal: ja** (`docker compose up` + `seed`-Command). Dokumentation ist erstaunlich gut — `admin-guide.md` (58 KB), `ops-runbook.md` (28 KB), FAQ (30 KB), Fachkonzept (114 KB). README ist klar.

**Produktiv für eine NGO: nein, nicht ohne IT-Begleitung.** Die `coolify-deployment.md` macht es zugänglich, aber NOSUPERUSER-Rollen-Setup, Backup-Verifikation, MV-Refresh-Cron und Schlüssel-Rotation brauchen jemanden mit Linux/SQL-Komfortzone. Realistisch: **4 Std/Monat externe IT-Stütze oder Managed-Hosting**. Das Projekt selbst formuliert das in §3 README über kommerzielles Hosting-Angebot ehrlich.

---

## 7. 📊 Datenmodell & Speicher

**Postgres 16 mit gezielten Erweiterungen:**
- `pg_trgm` für Pseudonym-Suche (Migration 0055)
- `gin_trgm_ops` GIN-Index auf `Client.pseudonym` (`models/client.py:90–94`)
- Materialisierte View `statistics_event_flat_mv` (Migration 0049) — feature-flagged
- Composite-Indexe gezielt für Listen-Queries (`models/event.py:73–80`, `models/audit.py:98–102`, `models/case.py:76–78`)
- Postgres RLS (Migration 0047) als Defense-in-Depth

**Stärken:**
- 74 Migrationen ohne Schema-Implosion. `0027_merge_*` und `0051_merge_20260417_2123.py` zeigen ordentliches Branch-Merge-Handling.
- DB-Trigger für AuditLog-Immutability (`Migration 0024`) und `EventHistory`-Append-Only (`Migration 0012`) — Daten-Integrität ist nicht nur App-Code-Sache.
- `validate_trigram_threshold` (Migration 0056) zeigt, dass Performance bewusst getuned wird, nicht nur „Index machen und beten".

**Schwächen:**
- **JSONB ohne Schema-Linting.** `Event.data_json` schluckt alles. Es gibt keinen `CHECK`-Constraint auf bekannte Slugs, keinen Cleanup-Job für Orphan-Keys nach FieldTemplate-Soft-Delete. Das Fachkonzept (§18 "JSONB-Performance-Monitoring") nennt es als bewusste, aber das verschiebt das Problem.
- **`AuditLog.detail` ist freies JSON.** Wenn der Aggregations-/Reporting-Druck steigt, fehlt Struktur.
- **Reporting-Skalierbarkeit ist noch ungetestet.** Statistics-Service nutzt MV oder Live-Aggregation. MV-Refresh-Strategie ist im `ops-runbook.md` benannt, aber die Performance-Charakteristik bei 5 Jahre × 50 Events/Tag × 30 DocumentTypes pro Einrichtung ist nicht dokumentiert (oder gemessen, soweit erkennbar).
- **Keine Read-Replica-Strategie.** Bei größeren Auswertungen können Statistik-Queries die Schreib-DB belasten. Für die Zielgruppe (5–20 User pro Einrichtung) ist das wahrscheinlich kein Problem; bei Träger-Multi-Standort-Setup ändert sich das.

**Inkonsistenz-Risiken:**
- `Event.client = SET_NULL` + `Event.case = SET_NULL` + `Case.client = SET_NULL`: wenn Client gelöscht/anonymisiert wird, bleibt `Event` ohne Client und ohne Case. Reporting-Queries müssen das überall behandeln. `services/client_export.py` tut es nicht.
- `is_deleted=False`-Flag auf Events: Soft-Delete und Hard-Delete koexistieren (Retention-Job hard-deleted, User-Aktionen soft-deleten). Vier Indizes haben den Flag (`event_facility_del_occ_idx`), aber nicht alle Service-Funktionen filtern konsistent.

**Skalierbarkeit:** Für die anvisierte Zielgruppe (5–20 User, 1 Einrichtung) **mehr als ausreichend**. Für einen großen Träger mit 10+ Standorten wäre das Schema tragfähig, aber Statistics-Performance müsste vorher belastet werden.

---

## 8. 🧠 Produkt- & UX-Denken

**Ja, das wurde von jemandem mit echter Domänenkenntnis gebaut.** Die Belege sind nicht atmosphärisch, sondern strukturell:

1. **Pseudonymisierung als Architektur-Constraint, nicht Feature-Flag.** Das Schema hat kein Namensfeld. Ein Entwickler ohne Domänenkenntnis hätte "Klarname optional verschlüsselt" gebaut.
2. **Drei Kontaktstufen im Schema** (Anonym/Identified/Qualified) und die Sichtbarkeit-Folgen-Hierarchie zwischen Roles und Sensitivity sind ineinander gesteckt — das modelliert das reale Asymmetrie-Verhältnis zwischen Assistenz, Fachkraft und Leitung.
3. **`TimeFilter` statt fixer Schichten** (Fachkonzept §1.1, Prinzip 1) — wer Nachtcafés und Streetwork in derselben Software machen will, weiß, dass „Schicht 21:30–09:00" und „Vormittag" denselben Mechanismus brauchen.
4. **Hinweis vs. Aufgabe vs. Dokumentation** als drei separate Konzepte mit eigenen Lifecycles (Prinzip 4) — das ist die genaue Beobachtung, die in Excel-Lösungen fehlt: dass ein Dienstbuch-Eintrag „Bitte bei M. nachfragen" weder Dokumentation noch Statistik ist.
5. **System-DocumentType "Hausverbot"** als immutable system_type (`document_type.py:99–107`). Niemand stellt Hausverbote sich nicht-konfigurierbar vor, der nicht weiß, was passiert, wenn jemand das Konfig-Feld wegnimmt.
6. **"30-Sekunden-Ziel"** ist im Fachkonzept §24 als nicht-funktionale Anforderung mit Validierung benannt — und im Visual-Refresh v0.10.1 finden sich Mobile-Bottom-Nav, Single-Loop-Klientelliste und KPI-Cards. Das ist auf real-world Umsetzungsdruck gemünzt.
7. **Übergabe-Seite, Zeitstrom als Startseite** — keine Marketing-Begriffe, sondern direkte Übersetzung aus dem Sprach-Repertoire der Domäne.

**Wo arbeitet die Software gegen den Nutzer?**

- **JSONB-`data_json`-Konfigurations-UX.** Field-Templates, Document-Types, Sort-Order, Sensitivity, Statistik-Kategorie — die Konfiguration ist mächtig, aber Admin-UX ist Django-Unfold-Default. Für den Diplomarbeits-Befund "Sozialarbeiter sollen Sozialarbeit machen" ist das Konfigurations-UI noch nicht weit genug.
- **K-Anonymisierung als Funktion** (`k_anonymize_client`) wird aufgerufen, aber `k` wird nicht erzwungen (`services/k_anonymization.py:33`). Ein Lead könnte glauben, einen Datensatz „k=5"-anonymisiert zu haben, der real nicht k-anonym ist. Das ist eine UX-trifft-Korrektheit-Lücke.
- **Offline-Modus ist eindrucksvoll**, aber Konflikt-Resolution-UI für Streetwork-Teams unter Stress (Side-by-Side-Diff, `core/templates/core/events/conflict_review.html`) braucht Schulung. Das geht für die Zielgruppe nicht ohne.

**Wo besonders gut gedacht?**
- AuditLog auf zwei Ebenen (App + DB-Trigger) — schützt nicht nur gegen Bugs, sondern gegen Insider-Mutation.
- Sensitivity-404-statt-403 — zeigt, dass jemand verstanden hat, dass auch Existenz-Information ein Leak ist.
- File-Vault mit Magic-Bytes + ClamAV + Whitelist + AuditLog auf jeden Verstoß — Defense-in-Depth wo nichts überflüssig ist.

---

## 9. 🚀 Langfristige Tragfähigkeit

**3–5 Jahre Wartbarkeit:** Wahrscheinlich tragfähig, **wenn** die Maintainer-Bandbreite hält. Das Risiko ist nicht die Code-Qualität, sondern die **Personenabhängigkeit**.

**Indikatoren für Tragfähigkeit:**
- 120 Commits mit klaren Conventional-Commit-Messages (`fix:`, `feat:`, `chore:`, `refactor:`, `security:`, `perf:`).
- CHANGELOG ist gepflegt mit `Added/Changed/Fixed/Security/Performance/Accessibility`-Sektionen — kein „chore: misc"-Sumpf.
- Architektur-Tests (laut CHANGELOG v0.10.0/0.10.1) verbieten Regressionen: keine neuen Inline-Handler, keine i18n-f-Strings, keine ungeschützten Mutationen, keine fehlenden ARIA-Labels auf SVGs. Das ist Senior-Disziplin.
- AGPL v3 ist die richtige Lizenz für Sozialarbeitskontext (verhindert Privatisierung als SaaS-Service).
- `CONTRIBUTING.md` (18 KB) und englische Variante zeigen Bereitschaft für externe Beiträge.
- `SECURITY.md` mit SLA und Coordinated Disclosure.

**Indikatoren für Stagnations-Risiko:**
- **GitHub-Issues-Aktivität nicht direkt einsehbar im Clone.** Die Code-Referenzen verweisen auf Issues #500–#680, was hohe Issue-Nummern sind. Aber: Entwicklung wirkt aus 1–2 Personen-Quelle, nicht aus einer Community.
- **Generative-AI-getrieben** (README:148): "AI als integralen Bestandteil des Entwicklungsprozesses". Erklärt die schiere Velocity (120 Commits in ~5 Monaten ab Initial-Commit), aber wirft Bus-Faktor-Frage auf: was passiert, wenn der menschliche Maintainer pausiert? Andere Contributors müssen das AI-gestützte Tempo mit weniger Tooling halten.
- **Niedrige Anzahl Tests pro Service ist gut, aber Test-Tiefe in einigen Bereichen (RLS-live, IDOR) ist schwach** — das sind die Stellen, an denen ein neuer Contributor unbeabsichtigt Sicherheitsregresse einführen kann ohne dass CI Alarm schlägt.
- **Keine Dependabot-Konfiguration**, kein CodeQL, kein Trivy. Bei einer 3–5-Jahres-Vorhaben mit Art.-9-Daten ist Supply-Chain-Drift ein realer Faktor.

**Hat das Potenzial für ein ernsthaftes Open-Source-Projekt?**

**Ja**, aber das hängt an drei Faktoren:
1. **Pilotinstallationen finden** (READMEs Aufruf an Piloten ist real). Software ohne erste echte Nutzer wächst nur im Kopf.
2. **Bus-Faktor erhöhen** — wer ist der zweite Mensch, der den Stack tief versteht? Das ist nicht aus dem Code allein lesbar, aber für jede Förderzusage wesentlich.
3. **Externe Audits aufschalten.** Eine Penetrations-Studie + DSGVO-Audit von einer dritten Stelle — das ist die Stufe, auf die das Projekt zusteuert (siehe selbst-referenzierte `docs/audits/2026-04-{21,23,25,26}-*.md` im CHANGELOG, leider nicht im Public-Repo eingecheckt). Selbstaudits sind gut, externe sind besser.

Kollabiert es unter eigener Komplexität? **Nein, aktuell nicht.** Die Komplexität ist eingehegt. Die Gefahr liegt nicht in Code-Komplexität, sondern in **Domänen-Komplexität** (DSGVO ändert sich, neue Träger-Anforderungen, Reporting-Druck).

---

## 10. 💣 Schonungslose Gesamtbewertung

**Gesamtbewertung: 7,5 / 10** — überdurchschnittlich für ein Pre-Release-Projekt dieser Größe und Domäne.

**Was es ist:**
- Ein architektonisch ungewöhnlich klares, fachlich substanziell durchdachtes, sicherheitsbewusst umgesetztes Sozialwirtschafts-Open-Source-Projekt.
- Senior-Level-Engineering: dokumentierte Trade-offs, Defense-in-Depth, append-only-Audit auf DB-Ebene, RLS, MFA, File-Vault mit ClamAV, Field-Encryption mit Key-Rotation.
- **Eine seltene Kombination** in diesem Sektor: Technik UND Domänenkenntnis. Das meiste Open-Source-Sozialwirtschaft-Tooling hat das eine oder das andere — selten beides.

**Was es nicht ist:**
- Produktionsreif. Die `MEDIA_ROOT`-Volume-Lücke ist ein **Datenverlust-Bug**, nicht eine Stilfrage. Dazu: kein Live-RLS-Test, kein Off-Site-Backup, kein SAST/CodeQL/Dependabot, ein halbes Dutzend Service-Layer-Konsistenz-Lücken.
- Selbst-betreibbar von einer NGO ohne IT-Begleitung — **trotz** überdurchschnittlicher Doku.

### Würde ich es einsetzen?

**Bedingt ja**, in dieser Reihenfolge:
1. Bei einer Pilot-Einrichtung mit ergänzender IT-Begleitung (Coolify + Off-Site-Backup-Mirror selbst gestrickt + Pen-Test).
2. **Nicht** als „install und vergiss" für eine Einrichtung ohne Tech-Affinen im Team — der ops-runbook ist gut, aber Schlüssel-Rotation, Disaster-Recovery und MV-Refresh müssen jemand betreuen.

### Würde ich darin investieren?

**Ja**, gezielt:
- Förder-Investment in (a) Pen-Test, (b) Live-RLS-Test, (c) SAST/CodeQL-Pipeline, (d) ein zweites menschliches Maintainer-Brain — das ist der Hebel für Bus-Faktor-Reduktion. **NICHT** Förderung für „mehr Features", sondern für **Härtung und Communitybildung**.

### Würde ich darauf aufbauen?

**Ja, eingeschränkt.** Es ist eine bessere Grundlage als die meisten „Selbstgebauten Access-DBs" (so beschreibt es das Fachkonzept §2 selbst), aber es ist v0.10.2. Eine Forking-Initiative würde ich nicht empfehlen — entweder Beitragen zum Projekt oder Verzicht. AGPL setzt das auch durch.

---

## 🧪 Quick Wins (1–2 Tage)

| # | Maßnahme | Datei/Stelle | Aufwand |
|---|---|---|---|
| 1 | **`MEDIA_ROOT` als Volume in `docker-compose.prod.yml` mounten** (z. B. `media:/data/media`) plus named volume `media:` deklarieren — verhindert stillen Datenverlust verschlüsselter Anhänge | `docker-compose.prod.yml:16–36, 69–73` | 30 min |
| 2 | **Image-Tag im prod-Compose pinnen** auf eine konkrete Version (`:v0.10.2`) oder SHA, und Org-Drift zwischen prod/staging reparieren | `docker-compose.prod.yml:17`, `docker-compose.staging.yml:25` | 30 min |
| 3 | **Off-Site-Backup-Pfad in `scripts/backup.sh`** ergänzen (rclone/restic-Hook nach erfolgreichem Local-Write) | `scripts/backup.sh:91` | 2 h |
| 4 | **Caddy-Edge-Rate-Limit** auf `/login/`, `/password-reset/` (rate_limit-Plugin) | `Caddyfile` | 1 h |
| 5 | **Coverage-Floor** in CI (`--cov-fail-under=85`) zur Verhinderung stiller Test-Regression | `.github/workflows/test.yml`, `pyproject.toml` | 30 min |
| 6 | **Dependabot + Trivy + Bandit** als CI-Jobs ergänzen | `.github/dependabot.yml`, `release.yml` | 4 h |
| 7 | **`services/handover._collect_open_tasks(facility, user)`** auf Sensitivity filtern — Pseudonym-Leak fixen | `services/handover.py:110–127` | 1 h + Test |
| 8 | **`services/clients.update_client`** auf Whitelist statt `**fields` umstellen, Pattern von `cases.update_case` übernehmen | `services/clients.py:79` | 1 h + Test |
| 9 | **`assign_event_to_case`/`remove_event_from_case`** AuditLog ergänzen (User-Param wird bereits durchgereicht) | `services/cases.py:124, 149` | 30 min |
| 10 | **`forms/`-`INPUT_CSS`** in zentralen `forms/_widgets.py` extrahieren | 5 Form-Files | 30 min |

## 🔧 High-Impact-Refactorings

1. **`services/event.py`** in `event_crud.py` + `event_data.py` splitten, einheitlich `services/locking.check_version_conflict` verwenden statt eigenem `str(updated_at)`-Vergleich. (1–2 Tage)
2. **Live-RLS-Integrationstest** schreiben: dedizierter `pytest.fixture`, der mit `NOSUPERUSER`-Connection arbeitet und cross-tenant-SELECT auf Null assertet — schließt das einzige Audit-Loch in der Sicherheits-Test-Strategie. (3 Tage, inklusive CI-Wiring)
3. **JSONB-Schema-Governance** — Management-Command `audit_data_json_drift`, der Events findet, deren `data_json`-Keys nicht mehr in aktiven `FieldTemplate`-Slugs vorkommen. Plus optionaler Cleanup-Walk mit Migration-Tabelle. (1 Woche)

## 🪜 Nächster Architekturschritt

**„v1.0-Härtungs-Sprint"** — ein 4-Wochen-Block mit drei Vektoren:
- **Sicherheit**: externer Pen-Test + Live-RLS-Test + SAST/Dependabot in CI.
- **Betrieb**: Off-Site-Backup, Schlüssel-Rotations-Runbook, Caddy-Edge-RateLimit, Observability-Minimalkit (`/metrics`, JSON-Logs in stdout, optionaler Loki-Hook).
- **Konsistenz**: Service-Layer-Audit-Log-Lücken schließen, `forms/`-DRY, drei View-Stile auf einen reduzieren.

Danach v1.0-Release mit ehrlicher Pilotbasis.

---

# 🔚 Wenn ich dieses Projekt morgen übernehmen müsste — die ersten 3 Maßnahmen

1. **`MEDIA_ROOT`-Volume in `docker-compose.prod.yml` reparieren — heute, vor allem anderen.** Das ist ein latenter Datenverlust-Bug für verschlüsselte Anhänge, der bei jedem Image-Update zuschlägt. Volume mounten, Restore-Pfad dokumentieren, e2e-Test ergänzen, der einen Anhang über Container-Recreate hinweg überlebt.

2. **Live-RLS-Integrationstest aufsetzen.** Eine `pytest`-Fixture mit dediziertem `NOSUPERUSER`-DB-User, ein Test pro RLS-geschützter Tabelle, der einen Cross-Tenant-SELECT macht und 0 Rows assertet. Das schließt die größte Lücke in der Test-Strategie und macht jede Regression sofort sichtbar. CI-Wiring inklusive.

3. **Service-Layer-Audit-Konsistenz-Sweep.** Eine PR, die (a) `handover._collect_open_tasks` auf `visible_to(user)` umstellt, (b) `clients.update_client` auf Whitelist, (c) `cases.assign_event_to_case`/`remove_event_from_case` auf AuditLog, (d) `event.update_event` auf `services/locking.check_version_conflict`. Vier Lücken, ein Sweep, getestet — danach ist das Sicherheits-Versprechen belastbarer.

Alles weitere — JSONB-Governance, Beobachtbarkeit, Pen-Test, Off-Site-Backup — folgt aus diesen drei.
