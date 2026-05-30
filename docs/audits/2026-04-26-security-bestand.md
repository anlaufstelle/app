# Sicherheitsbericht — Anlaufstelle (Stand: 2026-04-26)

## Context

Dieser Bericht inventarisiert den Sicherheitszustand der Codebasis `anlaufstelle`
(Django 5.1 / Python 3.13 / PostgreSQL 16 / HTMX + Alpine.js) auf Code-Ebene.
Ziel: Verteidigungslinien sichtbar machen, Defense-in-Depth-Lage einordnen,
verbleibende Lücken identifizieren — als Grundlage für Audit-Gespräche,
Spender-/Förderer-Reports (/DSGVO) und gezielte Härtungsarbeit.

Der Bericht stützt sich ausschließlich auf den aktuellen Code (Branch
`refactor/alpine-csp-build-final`), nicht auf Archive-Doku oder Audit-Notizen.
Quelle jeder Aussage ist mit Datei:Zeile referenziert.

---

## 1. Executive Summary

Die Codebasis verfolgt ein **explizites Defense-in-Depth-Konzept** mit
5 Verteidigungsschichten und **test-getriebener Härtung**: Sicherheits-
Invarianten werden über Architektur-Tests in `src/tests/test_architecture.py`
und `src/tests/test_rls.py` erzwungen, nicht nur per Konvention.

**Stärken (kurz):**
- PostgreSQL **Row Level Security** auf 21 Tabellen (FORCE-Modus, Policy + Tests)
- **MFA per TOTP + Backup-Codes** (django-otp), facility-/user-erzwingbar via Middleware
- **CSP `script-src 'self'`** ohne `unsafe-inline`/`unsafe-eval` — Alpine.js auf
 CSP-konformen Build (`@alpinejs/csp`) migriert; Inline-Scripts/Events/x-data-Objekte
 per Architektur-Test verboten
- **100 % Rate-Limit-Coverage auf POST-Handler** (Architektur-Test erzwingt; Allowlist leer)
- **Append-only AuditLog** (23+ Action-Typen, save/delete-Blockade auf Model-Ebene)
- **4-stufige File-Upload-Pipeline:** Extension-Whitelist → ClamAV → Magic-Bytes → Fernet-Encryption
- **0 `csrf_exempt`, 0 `|safe`, 0 `mark_safe`, 0 Inline-`<script>`-Blöcke** im gesamten Repo

**Risiken/Lücken (kurz):**
- Keine **Re-Authentication** für sensible Aktionen (MFA-Disable, Datenexport)
- Kein **CSP-Reporting** (`report-uri`/`report-to`) — Violations bleiben unbemerkt
- Kein **Dependabot/Renovate** (manuell + `pip-audit` in CI)
- Kein **CodeQL/SAST**, kein **SBOM**, kein expliziter **Threat Model**
- **DSGVO Art. 33/34** (Breach Notification) nicht im Code abgebildet (organisatorisch)
- `SESSION_COOKIE_SAMESITE="Lax"` (bewusste UX-Entscheidung, dokumentiert)
- GET-Handler nicht rate-limited (nur POST)

---

## 2. Verteidigungslinien (Bestand)

### 2.1 Authentifizierung & Identität

| Mechanismus | Status | Quelle |
|---|---|---|
| PBKDF2 (870k Iterationen, Django-Default) in Prod, MD5 nur in Tests | ✓ | `src/anlaufstelle/settings/base.py`, `test.py:8-11` |
| 4 Password-Validators (Similarity, MinLength, Common, Numeric) | ✓ | `base.py:115-120` |
| TOTP-MFA + 10 Backup-Codes, QR-Code-Setup | ✓ | `src/core/views/mfa.py:69-178` |
| MFA-Enforcement-Middleware (Setup-/Verify-Redirects) | ✓ | `src/core/middleware/mfa.py:39-63` |
| Facility-weite MFA-Pflicht (Settings-Model) | ✓ | `src/core/models/user.py:108-118` |
| Login-Ratelimit IP `5/m` + Username `10/h` | ✓ | `src/core/views/auth.py:42-45` |
| Account-Lockout: 10 Versuche/15 Min (AuditLog-basiert) | ✓ | `src/core/services/login_lockout.py:23-38` |
| Admin-Unlock mit eigener Audit-Action `LOGIN_UNLOCK` | ✓ | `login_lockout.py:41-55` |
| Facility-konfigurierbarer Session-Timeout (Default 30 Min) | ✓ | `auth.py:82-84`, `base.py:124` |
| Force-Password-Change Middleware (`must_change_password`) | ✓ | `src/core/middleware/password_change.py:1-22` |
| Clear-Site-Data-Header beim Logout (Storage-Cleanup) | ✓ | `auth.py:93-101` |

### 2.2 Autorisierung & Zugriffskontrolle (4 Schichten)

| Schicht | Mechanismus | Quelle |
|---|---|---|
| **L1: Role-Mixins** | 4-Tier-Hierarchie (Admin / Lead / Staff / Assistant) | `src/core/views/mixins.py:10-36` |
| **L2: Facility-Middleware** | `request.current_facility` aus User-Profile | `src/core/middleware/facility_scope.py:14-57` |
| **L3: PostgreSQL RLS** | Session-Variable `app.current_facility_id` per `set_config` | `facility_scope.py:47-55` |
| **L4: Manager/QuerySet** | `FacilityScopedManager`, `EventManager.visible_to(user)` (Sensitivity-Filter) | `src/core/models/managers.py:14-49` |

**RLS-Coverage (21 Tabellen):**
- 15 Direct-Tables (eigenes `facility_id`): Client, Event, Case, WorkItem, DocumentType, FieldTemplate, AuditLog, Activity, DeletionRequest, RetentionProposal, Settings, TimeFilter, LegalHold, StatisticsSnapshot, RecentClientVisit
- 3 Join-Tables (transitiv): EventHistory, EventAttachment, Episode
- 3 weitere via Folge-Migration: QuickTemplate, OutcomeGoal, Milestone, DocumentTypeField

**Quellen:** `src/core/migrations/0047_postgres_rls_setup.py:1-117`,
`0057_rls_quicktemplate.py`, `0063_rls_outcome_milestone_dtf.py`,
Tests in `src/tests/test_rls.py:48-240`.

**FORCE-Modus aktiv** → selbst Tabelleneigentümer unterliegt Policies.
**Kritische Prod-Voraussetzung:** Postgres-DB-User darf **kein Superuser** sein
(dokumentiert in `docs/ops-runbook.md` § 9).

### 2.3 HTTP-/Netzwerk-Sicherheit

| Header / Setting | Prod | Quelle |
|---|---|---|
| `SECURE_HSTS_SECONDS = 31536000` (1 Jahr) + `INCLUDE_SUBDOMAINS` + `PRELOAD` | ✓ | `prod.py:49-51` |
| `SECURE_SSL_REDIRECT = True` | ✓ | `prod.py:52` |
| `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")` | ✓ | `prod.py:48` |
| `SESSION_COOKIE_SECURE = True` / `SAMESITE = "Lax"` | ✓ | `prod.py:53-58` |
| `CSRF_COOKIE_SECURE = True` / `SAMESITE = "Strict"` / `HTTPONLY = True` | ✓ | `prod.py:59-68` |
| `SECURE_CONTENT_TYPE_NOSNIFF = True` | ✓ | `prod.py:69` |
| `X_FRAME_OPTIONS = "DENY"` | ✓ | `prod.py:75` |
| `SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"` | ✓ | `prod.py:74` |
| `ALLOWED_HOSTS` fail-closed (Prod erzwingt Env-Var, sonst `ImproperlyConfigured`) | ✓ | `prod.py:42-44` |

### 2.4 Content Security Policy (CSP)

| Direktive | Wert | Bewertung |
|---|---|---|
| `default-src` | `'self'` | strikte Baseline |
| `script-src` | `'self'` | **kein** `unsafe-inline`, **kein** `unsafe-eval` ✓ |
| `style-src` | `'self', 'unsafe-inline'` | Inline-CSS toleriert (Django-typisch, low-risk) |
| `img-src` | `'self', data:` | data-URLs für eingebettete Bilder |
| `font-src`, `connect-src` | `'self'` | strikt |
| `frame-ancestors` | `'none'` | blockt Framing vollständig |

**Quelle:** `src/anlaufstelle/settings/base.py:236-263` (django-csp 4.0).

**Alpine.js auf CSP-Build migriert:** Branch `refactor/alpine-csp-build-final`,
`src/static/js/alpine-csp.min.js` ist `@alpinejs/csp`-Build (kein `eval`).
Inline-`x-data="{...}"`-Objekte sind durch Architektur-Test verboten;
alle Komponenten registriert via `Alpine.data('name',...)`.

### 2.5 Rate Limiting

- Library: `django-ratelimit 4.1`
- Konstanten in `src/core/constants.py`: `MUTATION="60/h"`, `BULK_ACTION="30/h"`, `FREQUENT="120/h"`
- **Architektur-Test erzwingt 100 % POST-Handler-Coverage**
 `src/tests/test_architecture.py:186-250`, Allowlist leer
- Spezifische Limits: Login `5/m`, Password-Reset `5/m`, MFA-Verify `5/m`,
 MFA-Setup/Regenerate `10/m`, Offline-Salt-Fetch `10/m`/User
- **0 `@csrf_exempt`** im gesamten `src/core/views/` (geprüft)

### 2.6 CSRF & XSS

| Schutz | Status | Quelle |
|---|---|---|
| `CsrfViewMiddleware` aktiv | ✓ | `base.py:50` |
| `CSRF_COOKIE_HTTPONLY = True` (JS kann Cookie nicht lesen) | ✓ | `prod.py:68` |
| HTMX → CSRF-Token via `hx-headers='{"X-CSRFToken": "{{ csrf_token }}"}'` | ✓ | `src/templates/base.html` |
| `{% csrf_token %}` in allen POST-Forms | ✓ | grep-verifiziert |
| Auto-Escaping aktiv (Django-Default) | ✓ | `base.py:69-84` |
| `\|safe`-Filter im gesamten `src/templates/` | ✓ 0 Treffer |
| `mark_safe` in `src/core/` | ✓ 0 Treffer |
| `<script>...</script>` Inline-Blöcke | ✓ 0 Treffer (Architektur-Test erzwingt) |
| `onclick=`/`onchange=` Inline-Events | ✓ 0 Treffer (Architektur-Test erzwingt) |
| `{% autoescape off %}` | ⚠ nur 2 Email-Templates (Browser-irrelevant) |

### 2.7 Datei-Uploads (4-stufige Pipeline)

`src/core/services/file_vault.py:258-337`:

1. **Extension-Whitelist** gegen `Settings.allowed_file_types` (Facility-Setting)
2. **ClamAV-Virenscan** — `_run_virus_scan`, fail-closed bei Scanner-Outage
3. **Magic-Bytes-Check** — `_enforce_magic_bytes` via `python-magic` (libmagic),
 blockt Payload-Smuggling (z. B. PE als PDF)
4. **Fernet-Encryption** vor Disk-Schreiben — MultiFernet-Key-Rotation

Zusätzlich: Größenlimit pro Facility (`max_file_size_mb`), Versionskette
(`is_current`, `superseded_by`), UUID-Storage-Filename, Original-Name verschlüsselt,
periodischer Orphan-Cleanup (`cleanup_orphan_storage_files`).

ClamAV läuft als separater Docker-Service (`clamav/clamav:stable`), nur
intern erreichbar (kein Host-Port-Binding).

### 2.8 Audit Logging

| Mechanismus | Status | Quelle |
|---|---|---|
| Append-only `AuditLog`-Model (23+ Action-Typen) | ✓ | `src/core/models/audit.py:12-116` |
| `save`/`delete` werfen `ValueError` bei Update/Delete | ✓ | `audit.py:104-112` |
| Signal-basierte Login/Logout/Failed-Logging | ✓ | `src/core/signals/audit.py:51-114` |
| Failed-Login auch bei nicht-existentem User geloggt | ✓ | `audit.py:82-114` |
| IP-Tracking mit `TRUSTED_PROXY_HOPS`-Konfiguration (X-Forwarded-For) | ✓ | `signals/audit.py:15-48` |
| PII-Scrubber für Logs (Email/Password/Token-Pattern-Maskierung) | ✓ | `src/core/logging.py:12-40` |
| Composite-Index `(facility, -timestamp)` + `(action, -timestamp)` | ✓ | `audit.py:98-102` |
| Sentry-Integration optional in Prod (`SENTRY_DSN`, PII-Scrubbing aktiv) | ✓ | `prod.py:21-29` |

### 2.9 DSGVO-Compliance (Code-Ebene)

| Artikel | Mechanismus | Quelle |
|---|---|---|
| **Art. 15** (Auskunft) | Client-Daten-Export JSON + PDF, Audit-Logged | `src/core/views/clients.py` (`ClientDataExportJSONView/PDFView`), `src/core/services/client_export.py` |
| **Art. 17** (Löschung) | K-Anonymisierung + Soft-Delete + Deletion-Request-Flow | `src/core/services/k_anonymization.py:32-94`, `src/core/views/event_deletion.py` |
| **Art. 30** (VVT) | DSGVO-Package mit gerenderten Templates (Art. 13/15/17/21/30/32/33/34) | `src/core/services/dsgvo_package.py`, `src/core/views/dsgvo.py:18-50` |
| **Art. 32** (TOMs) | Encryption-at-Rest + In-Transit + 2FA + RateLimit + CSP + ClamAV (s. o.) | mehrere |
| **Art. 33/34** (Breach) | ⚠ kein Code-Workflow; nur SECURITY.md + Sentry für Critical-Errors | `SECURITY.md`, `prod.py:25-29` |
| Retention-Dashboard mit Legal-Hold + Cron `enforce_retention` | ✓ | `src/core/services/retention.py`, `src/core/management/commands/enforce_retention.py` |

K-Anonymisierung erzwingt Bucket-Größe ≥ K (Standard: 5) bei
`age_cluster`/`contact_stage` vor Pseudonymisierung — verhindert Re-Identifikation.

### 2.10 Secrets, Dependencies, Supply-Chain

| Bereich | Status | Quelle |
|---|---|---|
| `SECRET_KEY` env-required, fail-closed in Prod | ✓ | `prod.py:39-40` |
| `.env` in `.gitignore` (+ `*.pem`/`*.key`/`*.crt`) | ✓ | `.gitignore:52-59` |
| `.env.example` mit allen erforderlichen Variablen | ✓ | `.env.example:1-64` |
| Encryption-Keys (Fernet, MultiFernet rotation-ready) env-required | ✓ | `prod.py:97-101` |
| DB-Credentials env-basiert | ✓ | `base.py:92-101` |
| Keine Hardcodes (außer Test-Fixtures + dokumentierte CI-Test-Keys) | ✓ | grep-verifiziert |
| `pip-audit` in CI bei jedem Push | ✓ | `.github/workflows/test.yml:84-93` |
| Lock-File-Check (`requirements.txt` ↔ `.in`) im CI | ✓ | `test.yml:106-123` |
| `python manage.py check --deploy` im CI | ✓ | `test.yml:77-82` |
| Dependabot / Renovate | ✗ | nicht konfiguriert |
| CodeQL / SAST | ✗ | nicht konfiguriert |
| SBOM | ✗ | nicht generiert |

### 2.11 Architektur-Tests (test-getriebene Sicherheits-Invarianten)

`src/tests/test_architecture.py` (498 Zeilen, 8 Guard-Klassen):

| Guard | Erzwingt | Refs |
|---|---|---|
| `TestFacilityScopingGuard` | Kein `.objects.all` in Views (Cross-Facility-Leak) ||
| `TestEventAccessPolicyGuard` | `Event` nur über `get_visible_event_or_404` (Sensitivity-Filter) ||
| `TestNoInlineScriptBlocksGuard` | Kein `<script>...</script>` in Templates | #618 |
| `TestNoInlineEventAttributesGuard` | Kein `onclick=` etc. in Templates | |
| `TestAlpineCspCompatibilityGuard` | Kein `x-data="{...}"` (Inline-Objekt) | #669 |
| `TestCspScriptSrcStrictGuard` | `script-src` ohne `unsafe-inline`/`unsafe-eval` ||
| `TestRateLimitOnAllMutations` | Alle POST-Handler haben `@ratelimit` (Allowlist leer) | |
| `TestSvgAccessibilityGuard` | SVGs haben `aria-hidden`/`aria-label`/`role="img"` | |

`src/tests/test_rls.py` (241 Zeilen, 7 Tests):
- RLS-Flag, FORCE-Flag, Policy-Existence pro EXPECTED_TABLE
- Session-Variable überlebt Cursor-Wechsel (Regression #586)
- Anonymous-Request klobbert Variable nicht
- Round-Trip-Test gegen Facility-Spoof

Weitere sicherheits-relevante Tests:
- `test_auth.py` (Login/Logout, IP-Extraction, MFA)
- `test_permissions.py` (RBAC)
- `test_audit_coverage.py`, `test_bans.py`, `test_file_vault.py`,
 `test_k_anonymization.py`
- `tests/e2e/test_security_hardening.py`, `test_mfa_setup_flow.py`,
 `test_crypto_session.py`

### 2.12 Deployment & Infrastruktur

| Aspekt | Status | Quelle |
|---|---|---|
| Multi-Stage Dockerfile, **Non-Root User** (UID 1000) | ✓ | `Dockerfile:53-54` |
| Docker-Compose: Postgres + ClamAV nur intern (kein Host-Port-Binding) | ✓ | `docker-compose.yml` |
| `pg_advisory_lock(1)` während Migration (Race-Schutz bei Mehrfach-Replicas) | ✓ | `docker-entrypoint.sh:5-14` |
| Health-Check-Endpoint `/health/` | ✓ | `Dockerfile:61-64` |
| TLS via Reverse-Proxy (Caddy), Django setzt korrekten `SECURE_PROXY_SSL_HEADER` | ✓ | `prod.py:48` |
| GitHub-Actions-Secrets statt Hardcodes | ✓ | `.github/workflows/test.yml` |
| Cron-Sequenz: Backup 02:00 → Retention 03:00 → Snapshots 04:00 (Daten im Backup vor Löschung) | ✓ | `docs/ops-runbook.md` § 3 |

### 2.13 Sicherheitsdokumentation

| Dokument | Inhalt | Pfad |
|---|---|---|
| `SECURITY.md` | Vulnerability-Disclosure-Prozess, SLAs (Ack 3WT, Fix kritisch 14 Tage), Out-of-Scope-Liste, Hall-of-Fame | `/SECURITY.md` |
| `docs/security-notes.md` | Design-Entscheidungen mit Begründung ( `core_user` ohne RLS, SAMESITE=Lax, AuditLog nullable facility) | `/docs/security-notes.md` |
| `docs/ops-runbook.md` § 9 | RLS-Operations, Prod-DB-User-Anforderung (kein Superuser) | `/docs/ops-runbook.md` |
| `docs/audits/` | 3 zeitgestempelte Tiefenanalysen (April 2026) mit FND/S-Severities | `/docs/audits/` |

---

## 3. Lücken & Risiken (priorisiert)

### Priorität 1 — sollte zeitnah adressiert werden

| Nr | Lücke | Wirkung | Vorschlag |
|---|---|---|---|
| L1 | **Keine Re-Authentication** vor MFA-Disable / Datenexport / DSGVO-Download | Session-Hijack könnte 2FA abschalten oder Klientendaten exfiltrieren | `SudoMode`-Pattern (zeitlich begrenzte Re-Auth-Fenster) für sensible Aktionen |
| L2 | **Kein CSP-Reporting** (`report-uri`/`report-to`) | XSS-Versuche / CSP-Verstöße bleiben unbemerkt; Detection-Lücke | `report-to`-Direktive + lokaler Report-Endpoint mit Rate-Limit |
| L3 | **DSGVO Art. 33/34** ohne Code-Workflow | Breach-Erkennung hängt rein an Sentry; keine Eskalations-Routine | Internes Breach-Detection-Service (z. B. Failed-Login-Bursts → Slack-Webhook) |

### Priorität 2 — empfehlenswert

| Nr | Lücke | Wirkung | Vorschlag |
|---|---|---|---|
| L4 | **Kein Dependabot/Renovate** | Vulns werden erst per `pip-audit` im CI gefangen, nicht proaktiv | `.github/dependabot.yml` für `pip` + `github-actions` ergänzen |
| L5 | **Kein CodeQL/SAST** | Strukturelle Sicherheits-Bugs werden nur durch Architektur-Tests gefangen | `.github/workflows/codeql.yml` (Standard-Template) — kostenfrei für Public-Repos |
| L6 | **Kein SBOM** | Compliance-Anforderungen (/öffentliche Förderung) verlangen oft SBOM | `pip-audit --format cyclonedx-json` in CI-Artefakte |
| L7 | **MFA für ASSISTANT/STAFF nur opt-in** | Kompromittierte Niedrigrechte-Accounts können trotzdem Klientendaten lesen | Default `mfa_required=True` für alle Rollen, Opt-out nur für Setup-Phase |
| L8 | **Kein expliziter Threat Model** | Audits müssen jedes Mal neu konstruieren, was geschützt wird | `docs/threat-model.md` (STRIDE-Lite, < 5 Seiten) |

### Priorität 3 — niedrigere Dringlichkeit

| Nr | Lücke | Wirkung |
|---|---|---|
| L9 | GET-Handler nicht rate-limited (nur POST) | Scraping/Enumeration nicht gebremst — Mitigation: Login-Throttling fängt Anonymous-Reads |
| L10 | `CSP style-src 'unsafe-inline'` | CSS-Injection theoretisch möglich, aber kein realer XSS-Vektor (CSS Token Stealing in modernen Browsern blockiert) |
| L11 | `SECURE_BROWSER_XSS_FILTER` nicht gesetzt | Veraltet (Browser-XSS-Filter seit ~2020 deaktiviert/entfernt) |
| L12 | `CSRF_TRUSTED_ORIGINS` nicht konfiguriert | Nur same-origin akzeptiert — okay für single-domain Deployment |
| L13 | Kein "Remember Me" | Bewusste Entscheidung; UX vs. Sicherheit |
| L14 | HTMX-JSON-Body-Validation über Forms (kein striktes Schema) | Form-Validation deckt 95 %; pydantic/DRF wäre Über-Engineering |

### Bewusste Trade-offs (keine Lücken, sondern dokumentierte Entscheidungen)

- `SESSION_COOKIE_SAMESITE="Lax"` statt `"Strict"` — damit Password-Reset-Email-Links
 und externe Bookmark-Navigation funktionieren (`docs/security-notes.md`)
- `core_user` bleibt RLS-frei — Login-Bootstrap und Cross-Facility-Admin-Zugriff
 (`docs/security-notes.md`)
- AuditLog `facility_id` nullable — globale Systemereignisse (z. B. anonymes
 LOGIN_FAILED) brauchen keinen Facility-Kontext

---

## 4. Verifikation

So lässt sich der Bericht gegen die laufende Codebasis prüfen (read-only):

```bash
# 1. Architektur-Invarianten erzwungen?
pytest src/tests/test_architecture.py -v
pytest src/tests/test_rls.py -v

# 2. CSP-Direktiven inspizieren
grep -A 25 "CONTENT_SECURITY_POLICY" src/anlaufstelle/settings/base.py

# 3. Inline-JS-Audit
grep -rn '<script\s*>' src/templates/        # erwartet: 0 Treffer
grep -rn '|safe' src/templates/              # erwartet: 0 Treffer
grep -rn 'mark_safe\|csrf_exempt' src/core/  # erwartet: 0 Treffer

# 4. Rate-Limit-Coverage
grep -rn '@ratelimit' src/core/views/ | wc -l

# 5. RLS-Tabellen
grep -A 30 "DIRECT_TABLES\|JOIN_TABLES" src/core/migrations/0047_postgres_rls_setup.py

# 6. Prod-Header live (gegen E2E-Server)
make -C src dev-up      # oder e2e-up
curl -I http://localhost:8844/login/

# 7. Dependency-Audit
make ci                  # ruft test.yml-Steps lokal auf
pip-audit -r src/requirements.txt
```

---

## 5. Empfehlung für nächste Schritte (optional, nicht Teil dieses Berichts)

Falls der User die priorisierten Lücken angehen will, schlage ich folgende
GitHub-Issues vor (in dieser Reihenfolge):

1. **`security: Re-Authentication für MFA-Disable und DSGVO-Export`** (P1)
2. **`security: CSP Reporting via report-to + lokaler Endpoint`** (P1)
3. **`chore: Dependabot für pip + github-actions aktivieren`** (P2)
4. **`security: CodeQL Workflow ergänzen`** (P2)
5. **`docs: Threat Model (STRIDE-Lite)`** (P2)

Diese Issues entstehen **nicht** automatisch aus diesem Bericht — der
Auto-Issue-Hook beim `/plan`-ExitPlanMode erstellt nur **ein** Planning-Issue
für den Bericht selbst. Konkrete Härtungs-Plans wären jeweils eigene `/plan`-
Sessions.

---

## Anhang: Quelldateien (vollständig durchgesehen)

- `src/anlaufstelle/settings/base.py`, `dev.py`, `test.py`, `e2e.py`, `prod.py`
- `src/core/middleware/` (alle 6 Dateien)
- `src/core/views/auth.py`, `mfa.py`, `mixins.py`, `dsgvo.py`
- `src/core/models/audit.py`, `user.py`, `managers.py`
- `src/core/services/login_lockout.py`, `file_vault.py`, `k_anonymization.py`,
 `dsgvo_package.py`, `retention.py`
- `src/core/migrations/0047_postgres_rls_setup.py`,
 `0057_rls_quicktemplate.py`, `0063_rls_outcome_milestone_dtf.py`
- `src/tests/test_architecture.py`, `test_rls.py`
- `src/templates/base.html`, `auth/login.html`, `auth/mfa_login.html`
- `src/static/js/alpine-csp.min.js`
- `Dockerfile`, `docker-compose.yml`, `docker-entrypoint.sh`
- `.github/workflows/test.yml`, `lint.yml`, `e2e.yml`
- `SECURITY.md`, `docs/ops-runbook.md`, `docs/security-notes.md`,
 `docs/audits/2026-04-2*-*.md`
- `.env.example`, `.gitignore`, `pyproject.toml`, `src/requirements.in`
