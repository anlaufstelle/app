# Tiefenanalyse „Anlaufstelle" (github.com/anlaufstelle/app)

Stand: 2026-04-29 · Commit `35e0f5b` (Release v0.10.2, 2026-04-28) · Auditiert
gegen den lokalen Klon unter `/work/anlaufstelle`. Belege folgen dem Schema
`pfad/datei.py:zeilen`. Wo der Beleg aus einer Stichprobe stammt, ist das
markiert.

---

## A. Executive Summary

- **Reifegrad**: Spät-Beta / Release-Candidate. Architektonisch deutlich über
 Prototyp-Niveau — Service-Layer, Multitenancy via PostgreSQL-RLS
 (`src/core/migrations/0047_postgres_rls_setup.py`), Fernet-Encryption mit
 Key-Rotation (`src/core/services/encryption.py:26–62`), Audit-Log mit
 immutable `save/delete`-Override (`src/core/models/audit.py:104–112`),
 Retention-Workflow mit Vier-Augen-Prinzip
 (`src/core/models/retention.py:13–141`).
- **Single-Maintainer-Projekt** (`git log --format='%an' | sort -u | wc -l` → 1).
 Bus-Factor = 1, kein zweiter Reviewer, kein Co-Maintainer.
- **AGPL-3.0**-lizenziert, aber **Netzwerkklausel (§13 AGPL) im UI nicht
 umgesetzt** — kein Source-Link/„Powered by"-Footer in
 `src/templates/base.html` gefunden. Compliance-Risiko bei Drittbetrieb.
- **PostgreSQL-RLS ist gesetzt**, aber `core_user` und `core_activity` sind
 nicht in der RLS-Policy enthalten (`security-notes.md:7`,
 `src/core/models/activity.py:29`). Schutz hängt damit weiterhin am
 ORM-Manager + Middleware.
- **Encryption ist opt-in pro `FieldTemplate.is_encrypted`**
 (`src/core/models/document_type.py:79–82`, `src/core/models/event.py:90–112`).
 Art. 9 DSGVO-Daten (Sucht, Gesundheit) sind technisch verschlüsselbar, aber
 nicht erzwungen. `Client.pseudonym` selbst ist **plain in der DB**
 (`src/core/models/client.py:35–39`).
- **Anonymisierungs-Cascade unvollständig**: `Client.anonymize`
 (`src/core/models/client.py:120, 136`) berührt `EventHistory` und
 `DeletionRequest` nicht; `enforce_retention` löscht keine
 `EventAttachment`/`EventHistory` zum soft-deleteten Event
 (`src/core/services/retention.py:569–579`).
- **CSP enthält `'unsafe-eval'`** (`src/anlaufstelle/settings/base.py:243–259`)
 als Architektur-Schuld für Alpine.js (Issue #672). Defense-in-Depth durch
 RLS/Audit, aber CSP-Wirkung deutlich reduziert.
- **Type-Safety schwach**: ~14 % der Codebase mit Return-Type-Hints, ruff nur
 `E/F/I/W` (`pyproject.toml`), kein mypy/pyright in CI.
- **N+1 auf der Hochfrequenzseite Zeitstrom**
 `src/core/services/feed.py:38–64` mischt Events/Activities/Workitems ohne
 konsequentes `select_related`/`prefetch_related`.
- **Test-Suite solide** (152 Test-Dateien, 455 `pytest.mark`, 53 Playwright-E2E,
 `--reuse-db`), aber **kein mypy, kein bandit, ruff ohne `S`/`B`**.

**Top-3 Stärken (belegt)**

1. Service-Layer sauber von Views getrennt, Transaktions- und Audit-Disziplin
 konsistent (`src/core/services/cases.py`, `src/core/services/episodes.py`).
2. Multitenancy in Tiefe verteidigt: Manager-Default
 (`src/core/models/managers.py`), Middleware
 (`src/core/middleware/facility_scope.py`), PostgreSQL-RLS-Migration
 (`0047_postgres_rls_setup.py`) — drei unabhängige Schichten.
3. Audit + Retention sind echte Domänenobjekte, nicht Bolt-On:
 `src/core/models/audit.py`, `src/core/models/retention.py`,
 `src/core/services/retention.py` (~930 Zeilen).

**Top-3 Risiken (belegt)**

1. **DSGVO-Lücken trotz Architektur**: Pseudonym im Klartext, opt-in-Encryption
 für Art. 9-Daten, unvollständige Anonymisierungs-Cascade,
 `StatisticsSnapshot.data` als unverschlüsseltes Roh-JSON
 (`src/core/models/statistics_snapshot.py:25–26`).
2. **CSP-`'unsafe-eval'`** plus 14 OOB-Swap-Stellen ohne Dokumentation
 (`src/templates/core/retention/partials/`) — die wichtigste Browser-seitige
 Mitigation gegen DOM-XSS ist abgeschwächt.
3. **Single-Maintainer** + AGPL-Netzwerkklausel nicht im UI: Operative und
 lizenzrechtliche Nachhaltigkeitsrisiken für Trägerorganisationen, die das
 System produktiv betreiben.

---

## B. Faktenblock

| Kennzahl | Wert | Beleg |
|---|---|---|
| Letzter Commit | `35e0f5b` 2026-04-28 „chore: Release v0.10.2" | `git log -1` |
| Contributors | 1 | `git log --format='%an' \| sort -u` |
| Lizenz | AGPL-3.0-or-later | `pyproject.toml`, `LICENSE` |
| Python | `>=3.13` | `pyproject.toml` |
| Django | 5.1.15 | `requirements.txt:23` |
| Apps | 1 (`core`) | `src/core/` |
| Models | ~29 (21 Modul-Dateien) | `src/core/models/` |
| Views | 25 Module / ~85 CBV | `src/core/views/` |
| Services | 33 Module | `src/core/services/` |
| Templates | 84 `.html` | `src/templates/` |
| Migrations | 73–74 (ohne `__init__.py`) | `src/core/migrations/` |
| Tests | 152 Dateien, 455 `pytest.mark`, 53 E2E | `src/tests/` |
| LOC Python | ~52.819 (gesamt), ~19.780 in `core/` | `wc -l` |
| Locale | `de`, `en` (~518 Strings) | `src/locale/` |
| CI | 4 Workflows (test, lint, release, e2e) | `.github/workflows/` |
| Kritische Deps | `cryptography 46.0.7`, `django-otp 1.7.0`, `django-csp 4.0`, `django-ratelimit 4.1.0`, `pyclamd 0.4.0` (EOL 2016), `weasyprint 68.1`, `psycopg[binary] 3.3.3`, `sentry-sdk[django] 2.58.0`, `Pillow 12.2.0` | `requirements.txt` |
| Deployment | Multi-Stage `Dockerfile`, `docker-compose.{prod,staging,dev}.yml`, `Caddyfile{,.staging}`, `docker-entrypoint.sh` mit `pg_advisory_lock` | Repo-Root |
| Doku | `README.md/.en.md`, `CONTRIBUTING.md/.en.md`, `SECURITY.md`, `CHANGELOG.md`, `docs/` (8 Guides + 5 Runbooks) | Repo-Root, `docs/` |

---

## C. Befunde nach Dimension

> Format pro Befund: `[SCHWERE] Titel` · Fundstelle · Beobachtung · Auswirkung
> · Empfehlung. Schwere-Skala: kritisch / hoch / mittel / niedrig / info.

### 1. Architektur & Domain Design

**[mittel] Mono-App `core`**
- Fundstelle: `src/core/` (29 Models, 25 View-Module)
- Beobachtung: Sämtliche Domäne in einer Django-App.
- Auswirkung: Aggregat-Grenzen (Client, Case/Episode, Audit, Retention,
 Organization) sind nur semantisch, nicht durch App-Schnitt erzwungen;
 zirkuläre Imports müssen über Konvention vermieden werden.
- Empfehlung: Mittelfristig Split in `accounts`, `clients`, `cases`,
 `audit_log`, `retention`. Vorher Modulgrenzen explizit machen
 (Import-Linter / Grimp).

**[info] Service-Layer konsequent**
- Fundstelle: `src/core/services/cases.py`, `…/episodes.py`,
 `…/event.py`, `…/retention.py`
- Beobachtung: Services nutzen `@transaction.atomic`, schreiben AuditLog +
 Activity, Views rufen Services statt `.save`.
- Empfehlung: Beibehalten; Service-Boundary explizit dokumentieren.

**[mittel] HTMX-Integration ohne Slot-/Component-Pattern**
- Fundstelle: `src/templates/components/_event_card.html`,
 `…/_activity_card.html`
- Beobachtung: 11 Partials, fest an Kontextvariablen gekoppelt, 14
 OOB-Swap-Stellen ohne Kommentare
 (`src/templates/core/retention/partials/`).
- Auswirkung: Mehrfach genutzte Cards (Client-/Case-Timeline) duplizieren
 Markup; Wartbarkeit der OOB-Logik leidet.
- Empfehlung: `{% include … with hide_client=True %}`-Slot-Konvention,
 Kommentar-Header pro OOB-Partial.

### 2. Codequalität & Wartbarkeit

**[hoch] Type-Hints unter ~14 %**
- Fundstelle: stichprobenartig `src/core/services/`, `src/core/views/`
 (ca. 51/371 Dateien mit Return-Type-Annotationen)
- Beobachtung: `pyproject.toml` aktiviert nur `ruff` mit `E/F/I/W`. Kein mypy,
 pyright oder Pyre.
- Auswirkung: Refactoring-Risiken; `None | dict`-Verwechslungen erst zur
 Laufzeit.
- Empfehlung: mypy (`--strict-optional` zuerst, dann `--disallow-untyped-defs`
 inkrementell), CI-Job mit Baseline.

**[mittel] Ruff-Set zu schmal**
- Fundstelle: `pyproject.toml` `[tool.ruff.lint] select = ["E","F","I","W"]`
- Auswirkung: Bug-Klassen (`B`), Sicherheits-Pattern (`S`/Bandit), Komplexität
 (`C90`) bleiben unentdeckt.
- Empfehlung: `select = ["E","F","I","W","B","S","UP","C90","DJ"]` und
 Baseline.

**[info] Migrations-Hygiene**
- Fundstelle: `src/core/migrations/`, exemplarisch `0037_*` (`backward`),
 `0049_statistics_event_flat_mv.py` (DB-Vendor-Guard).
- Beobachtung: Lineare Historie, Materialized Views nur unter Postgres,
 `advisory-lock` in `docker-entrypoint.sh`.

**[hoch] RunPython ohne `reverse_code`**
- Fundstelle: `src/core/migrations/0068_attachment_versioning_stage_b.py`
- Auswirkung: Nicht reversibel; Downgrade-Pfad unklar.
- Empfehlung: Reverse-Migration ergänzen oder `migrations.RunPython.noop`
 bewusst dokumentieren.

### 3. Sicherheit (OWASP)

**[kritisch] CSP `'unsafe-eval'` global**
- Fundstelle: `src/anlaufstelle/settings/base.py:243–259`
- Beobachtung: Für Alpine.js gesetzt, Issue #672 als Folgearbeit markiert.
- Auswirkung: Bei jedem DOM-Sink → potenzielles `eval`. CSP als
 XSS-Mitigation deutlich entwertet.
- Empfehlung: Alpine ohne `unsafe-eval` (CSP-Build oder Alpine-CSP-Plugin),
 Nonce-Strategie + `strict-dynamic`.

**[hoch] `pyclamd 0.4.0` (Release 2016)**
- Fundstelle: `requirements.txt:55`, Nutzung
 `src/core/services/virus_scan.py:52`
- Auswirkung: Unmaintained; bei Protokoll-/TLS-Updates der ClamAV-Seite
 unklar.
- Empfehlung: Update auf `1.0.6+` oder Migration zu `clamd`-Bibliothek.

**[hoch] Login-Lockout race-anfällig**
- Fundstelle: `src/core/services/login_lockout.py:31–38`
- Beobachtung: Schwellwert 10 Versuche / 15 min, ohne `select_for_update` /
 Redis.
- Auswirkung: 11–12 Versuche möglich; in der Praxis klein, aber das Feature
 suggeriert Härte, die es nicht liefert.
- Empfehlung: Atomares Counting (`select_for_update` oder `INCR` in Redis).

**[mittel] AuditLog nicht DB-immutable**
- Fundstelle: `src/core/models/audit.py:104–112`
- Beobachtung: `save`/`delete` werfen, aber `UPDATE … WHERE id=…` per Raw-SQL
 möglich.
- Empfehlung: PostgreSQL-Trigger (`BEFORE UPDATE/DELETE → RAISE`) oder
 append-only Tabellenpartitionierung; ggf. `pgaudit` ergänzend.

**[mittel] IDOR-Schutz manuell**
- Fundstelle: z. B. `src/core/views/cases.py:156–160`
 (`get_object_or_404(Case.objects.select_related(...), pk=pk, facility=facility)`)
- Beobachtung: Tenant-Filter wird in jeder View per Hand gesetzt; Manager
 filtert per Default, aber Custom-`get`-Methode fehlt.
- Empfehlung: `FacilityScopedManager.get_for_facility(pk)` als kanonische API,
 Lint-Regel gegen `Model.objects.get(pk=…)` ohne Tenant-Filter.

**[mittel] HTTPOnly-CSRF, aber Token im `<meta>`**
- Fundstelle: `src/templates/base.html` (Stichprobe Z. ~25)
- Auswirkung: Bei XSS lesbar; HTTPOnly-Cookie-Flag wird damit teilweise
 konterkariert.
- Empfehlung: HTMX kann Token via Header (`HX-Headers`) aus Cookie lesen.

**[mittel] Offline-Key-Salt ohne Server-Pepper**
- Fundstelle: `src/core/middleware/mfa.py:34` exempt für
 `/auth/offline-key-salt/`
- Empfehlung: Server-Pepper (HMAC-Key in `SECRET_KEY`-Familie) in
 PBKDF2-Pfad einbeziehen.

**[mittel] CLAMAV-Timeout 30 s**
- Fundstelle: `src/anlaufstelle/settings/base.py:192`
- Auswirkung: Mehrere parallele Uploads halten Worker.
- Empfehlung: 5–10 s + Async-Scan via Queue.

**[mittel] PII-Scrubber-Regex schmal**
- Fundstelle: `src/core/logging.py:23`
- Empfehlung: Whitelist-Logging, nicht Blacklist; Stack-Trace-Bodies mit
 durchscrubben.

**[niedrig] Dev-DB-Default-Passwort**
- Fundstelle: `src/anlaufstelle/settings/base.py:96–105`
 (`POSTGRES_PASSWORD` Default `"anlaufstelle"`)
- Empfehlung: Default leer, ENV erzwingen.

**[niedrig] `style-src 'unsafe-inline'`**
- Fundstelle: `src/anlaufstelle/settings/base.py:261`
- Empfehlung: Nonce/Hash-Migration auf Roadmap.

**[info] HTMX-Endpoints werden nicht über `HX-Request` autorisiert**
- Stichprobe: `src/core/views/cases.py:93`, `…/clients.py:77`
 Auth-Decorators greifen unabhängig vom Header.
- Beobachtung: Korrekt umgesetzt (Header ist UX, nicht Sicherheit).

### 4. Datenschutz & Sozialdatenschutz

**[hoch] `Client.pseudonym` unverschlüsselt + GIN-Index**
- Fundstelle: `src/core/models/client.py:35–39, 91`
- Auswirkung: Backup-Leak ⇒ direkte Wiedererkennung in Kontaktläden.
- Empfehlung: `EncryptedTextField` + getrennter HMAC-Lookup-Index (oder
 Postgres-`pgcrypto`-Index auf Hash-Spalte).

**[hoch] Encryption für Art. 9-Daten optional**
- Fundstelle: `src/core/models/document_type.py:79–82` + `event.py:90–112`
- Beobachtung: `FieldTemplate.is_encrypted` ist konfigurierbar, auch für
 `Sensitivity=HIGH` (`document_type.py:27–30`).
- Empfehlung: Validator: `Sensitivity=HIGH` ⇒ `is_encrypted=True` erzwingen.

**[hoch] Anonymisierungs-Cascade unvollständig**
- Fundstellen: `src/core/models/client.py:120, 136`,
 `src/core/services/retention.py:569–579, 788–789`
- Beobachtung: `EventHistory`, `EventAttachment`, `DeletionRequest` werden
 nicht miterfasst.
- Auswirkung: Re-Identifikation über Audit-/History-Spuren.
- Empfehlung: Anonymisierung als Aggregat-Operation; Tests gegen
 „Restdaten nach Anonymisierung".

**[hoch] `StatisticsSnapshot.data` als Klartext-JSON**
- Fundstelle: `src/core/models/statistics_snapshot.py:25–26`
- Empfehlung: Aggregate ohne Identifier persistieren oder
 `EncryptedJSONField`; Re-Validation gegen K-Anonymität.

**[hoch] `Case.title/description` unverschlüsselt**
- Fundstelle: `src/core/models/case.py:36–37`
- Auswirkung: Freitext kann Echtnamen enthalten.
- Empfehlung: Wie `Event.data_json` behandeln, oder hartes Merkblatt im UI.

**[mittel] K-Anonymität wird nicht re-validiert**
- Fundstelle: `src/core/services/k_anonymization.py:81–93`,
 `src/core/services/retention.py:765`
- Empfehlung: `post_delete`-Signal, das `k_anonymized` zurücksetzt, wenn
 Bucket unter k fällt; periodischer Recheck-Job.

**[mittel] AuditLog speichert IP ohne explizite Rechtsgrundlage**
- Fundstelle: `src/core/models/audit.py:84–88`,
 `src/core/signals/audit.py:62`
- Empfehlung: Konfigurierbar (`AUDIT_LOG_IP_ADDRESS`), DSFA-Vorlage
 ergänzen.

**[mittel] `core_user`/`core_activity` nicht in RLS-Policy**
- Fundstellen: `docs/security-notes.md:7`,
 `src/core/migrations/0047_postgres_rls_setup.py:22`,
 `src/core/models/activity.py:29`
- Empfehlung: `core_activity` in `DIRECT_TABLES` ergänzen; Login-Pfad
 dokumentieren + Test (`test_rls.py` mit zwei Facilities).

**[mittel] AuditLog `facility_id` nullable, RLS filtert nicht auf NULL**
- Fundstelle: `src/core/models/audit.py:52–59`,
 `src/core/migrations/0047_postgres_rls_setup.py:29`
- Empfehlung: System-Default-Facility oder Spezial-Policy für `IS NULL`.

**[mittel] DeletionRequest-Workflow unvollständig**
- Fundstelle: `src/core/models/workitem.py:142–190`
- Beobachtung: Modell + Status, aber keine Approval-View.
- Empfehlung: Approval-View + Audit + UI; sonst Feature im Footprint
 irreführend.

**[mittel] Export ohne Rate-Limit**
- Fundstellen: `src/core/services/export.py:131`,
 `src/core/services/client_export.py:133`
- Empfehlung: Per-User/Facility-Limit + Audit-Eintrag mit Hash der
 Exportausgabe.

**[niedrig] `safe_decrypt`-Fallback `"[verschlüsselt]"`**
- Fundstelle: `src/core/services/encryption.py:106–114`
- Empfehlung: Im UI als Fehler markieren, nicht als Wert.

### 5. Tests & QS

- **Pyramide**: 152 Test-Dateien, 53 davon E2E (Playwright unter
 `src/tests/e2e/`). Verhältnis vernünftig.
- **`--reuse-db`** in `pyproject.toml` — schnelle Iteration; aber Risiko
 inkonsistenter Schemas, wenn Migrationen nachgezogen werden.
- **[mittel] Kein Property-Based-Testing** für Validatoren (Pseudonym,
 Encryption-Roundtrip, K-Anon-Buckets) — Empfehlung: Hypothesis.
- **[mittel] Kein Test, der die RLS-Policy gegen ORM-Bypass prüft** (z. B.
 rohe Queries unter zwei Facility-Sessions). Empfehlung: dedizierter
 `test_rls.py`-Suite-Block.
- **[mittel] Auth-E2E unvollständig**: Password-Reset-Flow nicht
 Form-zu-Form getestet (`src/tests/test_auth.py`).
- **[info] CI**: 4 Workflows (`test/lint/release/e2e`) — Matrix nicht
 geprüft (nur Python 3.13?), Empfehlung: Matrix mit Postgres-Major-Versionen.

### 6. Performance & Skalierbarkeit

**[hoch] N+1 im Zeitstrom**
- Fundstelle: `src/core/services/feed.py:38–64`,
 `src/core/views/zeitstrom.py:56`
- Empfehlung: `select_related("created_by","document_type")` +
 `prefetch_related` für Assignees; Benchmarks bei 200+ Items.

**[hoch] Pagination ohne `max_page`**
- Fundstellen: `src/core/views/{cases,clients,audit}.py`
- Auswirkung: `?page=99999` triggert seq-scan.
- Empfehlung: Cap + 404 oberhalb.

**[mittel] JSONB-Filter ohne GIN-Index**
- Fundstelle: `src/core/models/event.py:73–81`
- Empfehlung: `GinIndex(fields=["data_json"])` oder Denormalisierung
 häufig gefilterter Felder.

**[mittel] Composite-Indizes nicht zur Filterrealität passend**
- Fundstelle: z. B. `src/core/models/case.py` Index
 `["facility","status","-created_at"]`, View filtert zusätzlich nach
 `lead_user`.
- Empfehlung: Index erweitern oder Filter-Reihenfolge anpassen.

**[mittel] `SESSION_SAVE_EVERY_REQUEST=True`**
- Fundstelle: `src/anlaufstelle/settings/base.py:128–129`
- Auswirkung: Bei HTMX-Microrequests DB-Write-Amplifikation.
- Empfehlung: `False`; Sliding-Expiry über Custom-Middleware nur bei
 echten Logikschritten.

### 7. Barrierefreiheit & UX

**[mittel] Kein zentrales Fokus-Management nach HTMX-Swap**
- Fundstelle: `src/templates/base.html` (kein `htmx:afterSwap`-Handler
 gefunden)
- Empfehlung: Globaler Listener, der `[autofocus]` im neuen Fragment
 setzt; aria-live-Region für Status-Meldungen.

**[mittel] Formular-Errors ohne `aria-describedby`-Konnektor**
- Fundstelle: `src/templates/components/form_input.html` (Stichprobe)
- Empfehlung: `<div id="error-{{ field.id_for_label }}">` + Linkage.

**[info] Mobile/Touch-Tauglichkeit nicht ohne Live-Deployment beurteilbar**.

### 8. Internationalisierung

- `src/locale/de/`, `src/locale/en/` mit ~518 Strings.
- **[niedrig] Englischer Stand** vermutlich unvollständig — nicht
 feldweise verifiziert; Empfehlung: `manage.py compilemessages` in CI,
 Fuzzy-Anteil messen.

### 9. Deploy & Betrieb

- Multi-Stage `Dockerfile`, `docker-compose.{prod,staging,dev}.yml`,
 `Caddyfile{,.staging}`, `docker-entrypoint.sh` mit
 `pg_advisory_lock` für Migrate-Race.
- **[mittel] `GUNICORN_TIMEOUT=30 s`** (`docker-entrypoint.sh:22`):
 Long-Running-Migrations können scheitern.
- **[mittel] Coverage-HTML als 14 d-Artefakt** (`.github/workflows/test.yml`):
 Test-Daten könnten enthalten sein.
- **[niedrig] PII-Scrubber nur in `prod.py`**, nicht in `base.py`
 Dev-Logs leaken potenziell.
- **[info] Self-Hosting für kleine Träger**: README + `docs/` adressieren
 Admins; ein „Endnutzer-Handbuch" für Sozialarbeitende ist nicht
 identifizierbar.

### 10. Lizenz, Governance, Nachhaltigkeit

**[kritisch] AGPL-Netzwerkklausel im UI fehlt**
- Fundstelle: `src/templates/base.html` (kein Source-Link/„Powered by"
 gefunden), `LICENSE` vorhanden.
- Empfehlung: Footer-Block mit AGPL-Hinweis und Quell-URL; Doku in
 `CONTRIBUTING.md` für Forks.

**[hoch] Bus-Factor 1**
- Fundstelle: `git log --format='%an' | sort -u` → 1.
- Empfehlung: Co-Maintainer suchen, Roadmap publik, ADRs in `docs/`.

**[info] `SECURITY.md`** vorhanden mit Meldekanal.

### 11. Fachliche Eignung (Domain-Fit)

- **Pseudonym-Modell** ist erste Wahl: kein Klarname-Pflichtfeld
 (`src/core/models/client.py:13`), `ContactStage`
 ANONYMOUS/IDENTIFIED/QUALIFIED, `is_anonymous`-Flag auf `Event`
 (`src/core/models/event.py:12`).
- **Strichliste vs. Einzelfall**: `Event.client` nullable → niederschwellige
 Erfassung möglich (`event.py:12`); `RecentClientVisit`
 (`src/core/models/recent_client_visit.py`) unterstützt
 Übergabe-Workflow.
- **[mittel] Mehrere Aliase pro realer Person**: Constraint
 `unique_facility_pseudonym` (`client.py:84–87`) erzwingt 1:1; mehrere
 Pseudonyme erfordern getrennte `Client`-Datensätze ohne Verknüpfung.
- **Träger/Standorte/Fahrzeuge**: `Organization`/`Facility`
 (`src/core/models/organization.py:9, 25`) deckt Mehr-Standort-Träger.
 Für Fahrzeug-Streetwork keine spezifischen Flags gefunden — funktional
 über `Facility.system_type` denkbar, nicht verifiziert.
- **Statistik / Förderberichte**: `services/statistics.py` +
 `k_anonymization.py` + `statistics_snapshot.py` sind eigenständige
 Bausteine; KOMM-AT/COMBASS-Export-Formate nicht erkennbar.

### 12. Dokumentation

- README zweisprachig, `CONTRIBUTING` zweisprachig, `SECURITY.md`,
 `CHANGELOG.md`, `docs/` mit Setup, `security-notes.md`,
 `dsgvo-templates/dsfa.md`, Ops-Runbooks.
- **[mittel] Keine ADRs** identifiziert — Architekturentscheidungen
 (RLS, Encryption-Strategie, HTMX-Pattern) sind über Code/Issues
 verstreut.
- **[mittel] Kein Endnutzer-Handbuch** für Sozialarbeitende.
- **[niedrig] Encryption-Key-Rollover-Runbook** fehlt explizit
 (`src/core/services/encryption.py:26–62`).

---

## D. Priorisierte Maßnahmenliste

| # | Befund | Aufwand | Impact | Reihenfolge |
|---|---|---|---|---|
| 1 | AGPL-Footer mit Source-Link in `base.html` | S | hoch | Quick Win |
| 2 | `SESSION_SAVE_EVERY_REQUEST=False` | S | mittel | Quick Win |
| 3 | Dev-DB-Default-Passwort entfernen | S | mittel | Quick Win |
| 4 | Pagination-Cap (`max_page`) | S | hoch | Quick Win |
| 5 | Ruff `B/S/UP/C90/DJ` aktivieren + Baseline | S | mittel | Quick Win |
| 6 | CSRF-Token aus `<meta>` → Cookie/HX-Header | S | mittel | Quick Win |
| 7 | `pyclamd` updaten / ersetzen | S | hoch | Quick Win |
| 8 | N+1 im Zeitstrom-Feed beheben | M | hoch | |
| 9 | mypy in CI (inkrementell) | M | hoch | |
| 10 | RunPython `0068` reverse_code | S | mittel | |
| 11 | Anonymisierungs-Cascade vervollständigen + Tests | M | kritisch | |
| 12 | `Client.pseudonym` verschlüsseln (Hash-Index) | L | kritisch | |
| 13 | `Sensitivity=HIGH ⇒ is_encrypted=True` Validator | S | hoch | |
| 14 | `StatisticsSnapshot.data` Aggregations-Only | M | hoch | |
| 15 | RLS für `core_activity` + NULL-Policy für `audit_log` | M | hoch | |
| 16 | DeletionRequest-Approval-Workflow umsetzen | M | hoch | |
| 17 | Login-Lockout atomar (Postgres/Redis) | M | mittel | |
| 18 | AuditLog DB-Trigger gegen UPDATE/DELETE | M | mittel | |
| 19 | `Case.title/description` Encryption-Optionen | M | hoch | |
| 20 | CSP ohne `'unsafe-eval'` (Alpine-CSP-Build) | L | kritisch | |
| 21 | HTMX-Fokus-Management + aria-live | M | mittel | |
| 22 | `aria-describedby` durchgängig in Forms | M | mittel | |
| 23 | E2E Password-Reset, K-Anon Property-Tests | M | mittel | |
| 24 | Co-Maintainer / Governance / ADRs | XL | hoch | strukturell |
| 25 | App-Split (clients/cases/audit/retention) | XL | mittel | strukturell |

---

## E. Offene Fragen an die Maintainer

1. **Welche Felder enthalten heute realistisch Art. 9-Daten** (Sucht,
 Gesundheit, Sex, Religion)? Die Architektur erlaubt es überall, das
 tatsächliche Konfigurationsbild ist im Repo nicht ableitbar.
2. **Ist Issue #672 (CSP `'unsafe-eval'`) priorisiert?** Status, Zielversion?
3. **Für welche Förderträger-Statistik** (KOMM-AT, COMBASS, Hilfesystem
 NRW, …) ist Anlaufstelle gedacht? Aktuell keine Export-Profile gefunden.
4. **Fahrzeug-/Mobile-Streetwork**: gibt es geplante Felder auf `Facility`,
 oder soll das über `system_type` gelöst werden?
5. **Wie wird Encryption-Key-Rollover heute durchgeführt?** Runbook fehlt.
6. **DeletionRequest-Approval** — bewusst zurückgehalten oder unfertig?
7. **CI-Matrix**: läuft die Suite gegen mehrere Postgres-Versionen?
8. **Wer ist Co-Maintainer**, gibt es einen Aufsichts-/Beirat aus der
 Sozialarbeit?
9. **Backups & DR**: existiert ein dokumentierter, getesteter
 Wiederherstellungs-Drill?
10. **Endnutzer-Handbuch** für Sozialarbeitende — existiert separat
 (Wiki/PDF) oder geplant?

---

## F. Was bewusst NICHT bewertet wurde

- **Frontend-Performance & UX live** — ohne lokales Deployment nicht
 messbar (LCP, INP, Tastatur-Fluss, Screenreader-Realtest).
- **Suchqualität** auf realen Daten (Pseudonym-Recall, Tokenisierung,
 Umlaut-Verhalten).
- **Tatsächliche CVE-Lage** — keine `pip-audit`-Ausführung im Audit,
 Befunde basieren auf Versionsständen und Maintenance-Reputation.
- **Sentry-Integration in Betrieb** — DSN nicht im Repo,
 PII-Scrubbing nur statisch geprüft.
- **Visuelle Barrierefreiheit** (Kontrast/Zoom) — ohne gerendertes UI
 nicht messbar; nur Markup-Stichproben.
- **Übersetzungs-Vollständigkeit** und Fachterminologie der deutschen
 Sozialarbeit — `.po`-Dateien nicht inhaltlich gegengelesen.
- **Lasttest / Skalierungsverhalten** — keine Benchmarks gefahren; Aussagen
 zu N+1 sind statisch und müssen unter Last verifiziert werden.
- **Lizenzkompatibilität jeder transitiven Dependency** — nur direkte
 Dependencies stichprobenartig gesichtet.
- **Realer DSFA-Inhalt** (`docs/dsgvo-templates/dsfa.md`) — Existenz
 bestätigt, inhaltliche Vollständigkeit nicht juristisch bewertet.
- **Migrations-Replay auf Realdaten** — nur statisch geprüft.
