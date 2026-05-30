# Tiefenanalyse Anlaufstelle — 2026-04-30

**Auditgegenstand:** `/work/anlaufstelle` — Django 5.1 / Python 3.13 / PostgreSQL 16 / HTMX + Alpine + Tailwind, AGPL-3.0.
**Code-Stand:** `main` @ [`a224331`](https://github.com/anlaufstelle/app/commit/a224331) (2026-04-30, 689 Commits Tobias Nix + 9 Dependabot).
**Domäne:** Niedrigschwellige Soziale Arbeit — Kontaktläden, Notschlafstellen, Streetwork, Drogen-/Wohnungslosenhilfe (§ 67 ff. SGB X, § 203 StGB).
**Methode:** Code-First, parallele Multi-Agent-Analyse über 12 Dimensionen, Belege als `path:line` mit Kurzzitat. Bestehende Audits (`docs/audits/anlaufstelle-audit-master.md` u. a.) waren Basislinie, jeder Befund am aktuellen Code verifiziert.
**Auditor:** Claude (Opus 4.7).

---

## A. Executive Summary

1. **Reifegrad:** Solider Pre-1.0-Stand. Für 5–20 kleine Einrichtungen mit kompetentem Operator produktionsreif, sobald die in §D als „Kurzfrist" markierten Punkte (Open-Redirect, IP-Spoof in Maintenance, Pseudonym-Klartext-Defense, Init-Setup-Skript) gefixt sind. Horizontale Skalierung > 1 Worker erfordert Redis-`CACHES`.
2. **Defense-in-Depth ist konzeptionell sauber implementiert** — 4-Schicht-Autorisierung (Role-Mixins → `FacilityScopedManager` → `FacilityScopeMiddleware` → PostgreSQL-RLS) plus MultiFernet-Feldverschlüsselung, append-only-Trigger auf `AuditLog` und `EventHistory`, ClamAV + Magic-Bytes für Uploads, `RequireSudoModeMixin` auf sensitiven Aktionen.
3. **Tests sind das stärkste Asset** — 1.953 Funktionen über 162 Dateien, 4-Rollen-Matrix in `test_rbac_matrix.py`, **funktionaler Cross-Tenant-Test mit echter NOSUPERUSER-DB-Rolle** (`test_rls_functional.py`, Commit `4f4273a`) — überdurchschnittlich für Django-Projekte.
4. **Datenmodell trifft die niedrigschwellige Domäne präzise.** Pseudonym-First, datensparsame Pflichtfelder, anonyme Events, k-Anonymisierung, Schichtübergabe aus dem Modell heraus, abgestufter Offline-/PWA-Modus für Streetwork. Wenig „enterprise-Verkleben", viel passend.
5. **Kritische Sicherheits-Restlücken in den seit 27.04. neu eingeführten Layern:** Open-Redirect in `views/workitem_actions.py:61`, IP-Spoof in `middleware/maintenance.py:81`, SSRF-fähiger Webhook in `services/breach_detection.py:155`, Datei-Upload fail-open ohne Settings-Row in `services/file_vault.py:131`. Alles begrenzt im Blast-Radius, aber jedes ein vermeidbarer Fix in den neuen Bypass-Pfaden.
6. **Architektur-Schulden:** Single-App `core` mit 20 Models, 28 Views, 34 Services — fachliche Bounded Contexts existieren nur konventionell. Soft-Delete inkonsistent (nur `Event` hat `is_deleted`). `Client.anonymize` durchbricht Aggregate-Grenzen und nutzt `SET LOCAL session_replication_role = replica` direkt im Model. Das wird beim Plugin-Schnitt zur Bremse.
7. **Sprachleitlinie #604 nur halb durchgezogen.** UI/Templates und Views sind auf „Person" umgestellt; `Client.Meta.verbose_name`, `audit.py:CLIENT_*`-Action-Labels, `core_tags.py:157`, zwei Attachments-Templates und 22 deutsche `.po`-Einträge halten den alten Begriff. README selbst nennt 7× „Klientel" und die Screenshots heißen `klientenliste*.png` — größte sichtbare Code/Doku-Drift.
8. **k-Anonymisierung implementiert, aber nicht im Lebenszyklus angeschlossen.** Setting `retention_use_k_anonymization` (Migration 0049) existiert, `enforce_retention` ruft weiterhin `client.anonymize` statt `client.k_anonymize`. Heutiger Zustand suggeriert ein Feature, das real nicht greift.
9. **Performance auf DB-/ORM-Ebene gut, Caching-Schicht dünn.** `select_related`/`prefetch_related` an heißen Pfaden, GIN-pg_trgm-Index, Materialized View für Statistik mit `REFRESH … CONCURRENTLY`, Optimistic Locking in 4 Flows. **Aber:** Kein `CACHES`-Backend in Settings — Default ist LocMem pro Prozess. Bei `GUNICORN_WORKERS > 1` sind Maintenance-Cache und Ratelimit nicht synchron. Außerdem WorkItem-Inbox und Search ungedeckelt.
10. **Self-Hosting-Reibung für die Zielgruppe „Träger ohne IT-Abteilung":** Cron-Jobs (Backup, Retention, Breach-Detection) liegen außerhalb der Compose-Files — ein frisch deployter Stack läuft technisch, aber **ohne Backups und ohne Retention**, bis der Admin die Crontab manuell anlegt. Kein `scripts/initial-setup.sh` automatisiert den `setup_facility` + `ALTER ROLE … NOSUPERUSER` -Schritt; Vergessen deaktiviert RLS lautlos.

**Top-3-Stärken:**
1. Funktionaler RLS-NOSUPERUSER-Cross-Tenant-Test (`test_rls_functional.py`).
2. Datenmodell-Fit für niedrigschwellige Arbeit (Pseudonym-Pflicht-Minimum, Strichliste vs. Einzelfall, Schichtübergabe aus dem Modell, abgestuftes Offline).
3. Doku-Disziplin: 13 ADRs, FAQ-Sync-Footer mit Code-Verifikationsdatum, DE/EN-Mehrsprachigkeit, Threat-Model im Release-Sync, ausführbarer Restore-Drill.

**Top-3-Risiken:**
1. Vier Bypass-/Eingangslücken in den frisch eingeführten Sicherheitslayern (Open-Redirect, IP-Spoof, SSRF, Upload-Fail-Open) — jede einzeln klein, in Summe Defense-in-Depth-Erosion.
2. Self-Hosting-Postscript-Falle: NOSUPERUSER manuell, Cron-Jobs manuell, fail-open für Backup/Retention/Breach-Detection.
3. Bus-Factor 1, kein DCO/CLA, kein CoC, Tag-Signing inkonsistent — Governance-Skelett für ein AGPL-Repo unter-Förderkontext nicht ausreichend.

---

## B. Faktenblock

| Kategorie | Wert | Quelle |
|---|---|---|
| Python | 3.13 | `pyproject.toml:14` |
| Django | 5.1 (Migration auf 6.0.4 unter `[Unreleased]` im CHANGELOG) | `pyproject.toml`, `CHANGELOG.md:9` |
| Datenbank | PostgreSQL 16 mit Row-Level Security | `docker-compose.prod.yml`, `migrations/0047_postgres_rls_setup.py` |
| Lizenz | AGPL-3.0-or-later (Footer in `base.html:224–231`, LICENSE-Volltext) | `LICENSE`, `pyproject.toml:6` |
| Django-Apps | 1 (`core`) — Single-App-Architektur | `src/core/apps.py` |
| Models | 20 Dateien (`activity, attachment, audit, case, client, document_type, episode, event, event_history, managers, organization, outcome, quick_template, recent_client_visit, retention, settings, statistics_snapshot, time_filter, user, workitem`) | `src/core/models/` |
| Views | 28 Dateien, durchgängig CBV | `src/core/views/` |
| Services | 34 Dateien (Business-Logik-Layer) | `src/core/services/` |
| Forms | 6 Dateien | `src/core/forms/` |
| Middleware | 7 Dateien (`facility_scope, htmx_session, maintenance, mfa, password_change, user_language, admin_csp_relax`) | `src/core/middleware/` |
| Management Commands | 8 (Seed, Retention, Statistik-Snapshots, MV-Refresh, Breach-Detect, DSGVO-Paket, Re-Encrypt, Setup-Facility) | `src/core/management/commands/` |
| Templates | 88 HTML | `src/templates/` |
| Migrationen | 76 (numeriert bis `0076_auditlog_sudo_mode_action.py`) | `src/core/migrations/` |
| Tests | **162 Dateien, 1.953 `def test_*`-Funktionen** (Unit/Integration: 111/1.605, E2E Playwright: 51/348) | `src/tests/`, `src/tests/e2e/` |
| LOC (Code) | Python 45.340 · HTML 5.702 · JS 3.130 · Markdown 13.229 (Doku) | `tokei` |
| Letzter Commit | `a224331` (2026-04-30, „docs: Sprachleitlinie 'Klientel -> Person' adoptieren #604") | `git log -1` |
| Contributors | 2 (Tobias Nix 689, dependabot[bot] 9) | `git shortlog -sn` |
| ADRs | 13 (alle „Accepted") + 7 dokumentierte Backlog-Themen | `docs/adr/` |
| Releases | v0.10.2 (signiert), v0.10.1 + v0.10.0 (annotated, unsigniert), v0.9.1 + v0.9.0 (Lightweight-Tags) | `git tag --sort=-creatordate` |
| Workflows | `test.yml`, `e2e.yml`, `lint.yml`, `codeql.yml`, `release.yml` (CodeQL nur im public Mirror) | `.github/workflows/` |
| Kritische Dependencies | Django 5.1, psycopg, cryptography, django-csp, django-htmx, django-otp (TOTP), django-ratelimit, sentry-sdk, whitenoise, gunicorn, qrcode, python-magic, django-unfold | `requirements.txt` |

---

## C. Befunde nach Dimension

### Dimension 1: Architektur & Domain Design

Single-App-Architektur (`core`) mit ~20 Models, ~28 Views (alle CBV) und ~34 Service-Modulen. Aggregate-Wurzel ist `Facility`; alle fachlichen Entities sind über `FacilityScopedManager` und PostgreSQL-RLS gescoped. Domänen-Sprache (Klientel, Fall, Episode, Zeitstrom, Übergabe) ist konsistent eingehalten.

```
[SCHWERE: hoch]
Single-App "core" verhindert fachliche Bounded Contexts
Fundstelle: src/core/models/ (20 Dateien), src/core/views/ (28), src/core/services/ (34)
Beobachtung: Sämtliche Subdomänen — Fall-Akte (Case/Episode/Goal), Dokumentation (Event/DocumentType/FieldTemplate), Aufgaben (WorkItem), Übergabe, Retention, DSGVO, Offline, Statistik, MFA, Audit — liegen in derselben Django-App. `apps.py` deklariert nur `core`. `src/core/urls.py:91 app_name = "core"` zementiert das. 36 Cross-Service-Imports über `grep`.
Auswirkung: Kohäsion ist konventionell. Module dürfen frei untereinander importieren; Rename/Move einer Subdomäne erzwingt globale Migrationen; AppConfig-spezifische Konfigurationen (Signals pro Bounded Context, eigene Apps für Plug-In-Einrichtungstypen) sind nicht möglich. Skaliert schlecht, sobald NLnet-M5 (Plugin-Architektur) startet.
Empfehlung: Vor Plugin-Arbeit Schnitt nach DDD-Kontexten (z.B. `documentation`, `casework`, `dsgvo`, `streetwork_offline`, `audit`). Mindestens als Vorstufe: Service-Imports zwischen Subdomänen explizit deklarieren (`import-linter`).
```

```
[SCHWERE: hoch]
Soft-Delete-Strategie ist inkonsistent — nur Event hat `is_deleted`
Fundstelle: src/core/models/event.py:65 `is_deleted = models.BooleanField(default=False)`; src/core/models/attachment.py:34 `deleted_at`; übrige Models keine Marker
Beobachtung: `Event` markiert `is_deleted` und filtert in fast allen Queries. `Client.anonymize()` (client.py:105–203) macht Hard-Update. `Case`, `Episode`, `WorkItem` haben weder Soft-Delete noch eigene Anonymisierung.
Auswirkung: Gemischte Semantik bei Löschung erschwert Retention-/Anonymisierungslogik (974 LOC `services/retention.py`) und macht Recht-auf-Vergessen-Garantien schwer überprüfbar.
Empfehlung: Einheitliches Manager-Pattern (`SoftDeletableModel`-Mixin) oder explizite ADR, warum nur Event soft-deletet. Anonymisierung der Aggregate sauber an die jeweiligen Aggregate-Wurzeln binden.
```

```
[SCHWERE: hoch]
Domain-Logik in `Client.anonymize` durchbricht Aggregate-Grenzen
Fundstelle: src/core/models/client.py:105-203
Beobachtung: Methode auf Model-Ebene öffnet Transaktion, fasst sieben Fremd-Aggregate an (Case/Episode/WorkItem/Event/EventHistory/EventAttachment/DeletionRequest), inkl. Roh-SQL `SET LOCAL session_replication_role = replica` (Z.184) zur Trigger-Umgehung, und importiert aus `core.services.file_vault` (Z.168).
Auswirkung: Modell-Ebene kennt Trigger-Topologie der DB. Bricht Service-Layer-Prinzip aus CLAUDE.md („Business-Logik in services/, nicht in Views/Models"). Multi-Site-Edit für jede Trigger-Änderung.
Empfehlung: Logik nach `services/clients.py:anonymize_client(client, user)` ziehen, `Client.anonymize()` als dünner Aufruf erhalten oder entfernen. Trigger-Bypass in `services/_db_admin.py`-Helper kapseln.
```

```
[SCHWERE: mittel]
Alle Views CBV, aber als „Funktion in Klasse" geschrieben
Fundstelle: src/core/views/clients.py:32-75; events.py:93; cases.py:68
Beobachtung: 39 von 40 Views erben direkt von `django.views.View` mit `def get`/`def post`. Generische CBVs (`ListView`, `DetailView`) ungenutzt; vorhandene Mixins `FacilityScopedViewMixin`, `HTMXPartialMixin` (mixins.py:45-83) sind laut grep nirgends im Einsatz.
Auswirkung: Code-Duplikation: 16× per `request.headers.get("HX-Request")`-Branch. Pagination/Filter-Parsing in jeder ListView neu.
Empfehlung: Konsequent `ListView`/`UpdateView` + `get_queryset` oder die Mixins (`HTMXPartialMixin`) tatsächlich anwenden.
```

```
[SCHWERE: mittel]
Fall-Versionierung fehlt — nur Event hat EventHistory
Fundstelle: src/core/models/event_history.py:1-70 (append-only Trigger 0012); case.py/episode.py/workitem.py — keine analoge History
Beobachtung: `Case.title/description`, `Episode`, `OutcomeGoal`, `WorkItem` haben nur `updated_at`. Kein revisionsfester Diff für Fallakte-Updates.
Auswirkung: § 67 SGB X / DSGVO Art. 5 Abs. 1 lit. e schreiben Nachvollziehbarkeit von Veränderungen vor; Lücke ist insbesondere für Fall-Beschreibungen relevant.
Empfehlung: Generisches `RecordHistory`-Modell (analog EventHistory), Append-only-Trigger wiederverwenden.
```

```
[SCHWERE: mittel]
URL-Design vermischt REST und Verb-Endpunkte
Fundstelle: src/core/urls.py:108-208
Beobachtung: REST-Ressourcen koexistieren mit Verb-Pfaden (`cases/<pk>/close/`, `goals/<pk>/toggle/`). `api/`-Präfix für HTMX-/JSON-Endpunkte teilweise — andere Endpunkte ohne Präfix.
Empfehlung: Konvention dokumentieren (HTMX-Partials unter `partials/`, JSON unter `api/v1/`, Mutationen via REST + HTTP-Verb), mindestens ADR.
```

```
[SCHWERE: mittel]
`services/event.py` (683 LOC) und `services/retention.py` (974 LOC) sind God-Module
Fundstelle: src/core/services/event.py:1-683; retention.py
Beobachtung: `event.py` mischt Form-Lookup, Sensitivity-Filter, File-Marker-Parsing, CRUD und 4-Augen-Workflow. `retention.py` ist mit 974 LOC der größte Service.
Empfehlung: Aufteilen nach Subdomäne (`services/events/{crud,files,sensitivity_filter,deletion_workflow}.py`).
```

```
[SCHWERE: mittel]
HTMX: keine OOB-Swaps oder HX-Trigger-Header genutzt
Fundstelle: `grep "hx-swap-oob|HX-Trigger"` → 0 Treffer; `HX-Redirect` an 2 Stellen
Beobachtung: HTMX reduziert auf „Partial vs Full Page". Sidebar-Counter/Inbox-Badges werden serverseitig nicht nachgeführt.
Empfehlung: Pattern festlegen (OOB-Swap pro mutierender Aktion + zentrale `<aside hx-swap-oob>`-Region).
```

```
[SCHWERE: niedrig]
`Event.case`-Konsistenz nicht durchgängig validiert
Fundstelle: src/core/services/event.py:479-485 (create) vs. 575-601 (update)
Beobachtung: `create_event` validiert `case.client_id == client.pk`. `update_event` macht das nicht erneut. Nach `Client.anonymize()` bleiben Events am Fall, mit `SET_NULL`-Kaskaden.
Empfehlung: Konsistenz-Check periodisch in `services/retention.py` oder DB-Constraint.
```

```
[SCHWERE: niedrig]
Domänen-Begriffe für Einrichtungstyp hart codiert
Fundstelle: src/core/models/document_type.py:32-41 `class SystemType` (BAN, CRISIS, NEEDLE_EXCHANGE, …)
Beobachtung: `DocumentType` + `FieldTemplate` sind konfigurierbar, aber die `SystemType`-Choices steuern „Bann-Logik, Krisen-Highlight, Jugendamt-Export" und sind fest auf Streetwork zugeschnitten.
Empfehlung: Vor M5 ADR formulieren, welche Slots fest bleiben. Aktuell durch NLnet-Embargo geblockt.
```

```
[SCHWERE: info]
Defense-in-Depth-Schicht für Facility-Scoping vorbildlich
Fundstelle: src/core/middleware/facility_scope.py:31-55, migrations/0047_postgres_rls_setup.py
Beobachtung: Middleware setzt `app.current_facility_id` per `set_config` — auch für Anonymous explizit auf `''` (Refs #733). `FacilityScopedManager` plus RLS-Backstop. CLAUDE.md erzwingt Synchronisation.
```

**Was nicht prüfbar:** Vollständigkeit der RLS-Policies (Migration 0047 nur referenziert), Konsistenz `EventQuerySet.visible_to(user)` ↔ Sensitivity-Service im Detail.

---

### Dimension 2: Codequalität & Wartbarkeit

Konsistent strukturiert (CBVs, Service-Layer, ein Model pro Datei). Ruff-Lint und Format-Check in CI; mypy inkrementell auf `core/services`. Hauptlücken: enge ruff-Regeln, fehlendes `pre-commit`, lange verzweigte HTTP-Methoden, DRY-Verstoß bei den vier Retention-Strategien (per Kommentar zementiert), Inline-Imports in `retention.py`.

```
[SCHWERE: mittel]
Ruff-Regelumfang sehr eng — viele Bug-Klassen unentdeckt
Fundstelle: pyproject.toml:14-15 `select = ["E", "F", "I", "W"]`
Beobachtung: Aktiviert sind nur pycodestyle/pyflakes/isort. Es fehlen `B` (bugbear), `UP` (pyupgrade), `SIM`, `RUF`, `N`, `S`. Bestehende `# noqa: S310`-Marker (z.B. `services/breach_detection.py:167`) sind totes Inventar, weil S nicht aktiv ist.
Empfehlung: `select` schrittweise um `B`, `UP`, `SIM`, `N` erweitern (zunächst per-file-ignores).
```

```
[SCHWERE: mittel]
Keine `pre-commit`-Konfiguration trotz CONTRIBUTING-Erwähnung
Fundstelle: kein `.pre-commit-config.yaml`; `.git/hooks/` nur `*.sample`
Beobachtung: CLAUDE.md schreibt eine „Pre-Commit-Checkliste" vor, realisiert ist sie nicht.
Empfehlung: `.pre-commit-config.yaml` mit ruff (check + format), `mypy core/services`, `manage.py makemigrations --check --dry-run` einrichten.
```

```
[SCHWERE: mittel]
Lange, hochverzweigte HTTP-Methoden in views/events.py
Fundstelle: src/core/views/events.py:96-167 (EventCreateView.get, 71 LOC); :360-434 (EventUpdateView.post, 74 LOC); :305-358 (.get, 53 LOC)
Beobachtung: Initial-Map zweimal gebaut (events.py:310-314 ≈ :365-369). Logik gehört in `services/event.py`, das Service-Funktionen wie `build_field_template_lookup`, `filtered_server_data_json` schon exportiert.
Empfehlung: data_json → initial_data und Existing-Attachments-Aufstellung in Service ziehen.
```

```
[SCHWERE: mittel]
DRY-Verstoß bei Retention-Strategien — bewusst per Kommentar zementiert
Fundstelle: services/retention.py:485-551, :612-740, :861-973
Beobachtung: Drei Stellen drücken die vier Aufbewahrungsstrategien fast identisch aus. Kommentar bei :488 verlangt Synchronisation explizit.
Auswirkung: Drift-Risiko: `collect_doomed_events` (Vorhersage) und `enforce_*` (echte Löschung) können auseinanderlaufen, ohne dass Tests es zwingend abfangen.
Empfehlung: Zentrale `_strategy_querysets(facility, settings_obj, now)`-Funktion. Drei Konsumenten teilen einen Bauplan.
```

```
[SCHWERE: niedrig]
Inline-Imports in services/retention.py
Fundstelle: retention.py:446, :491, :561-563, :617, :643, :673, :711, :747, :866
Beobachtung: 9× `from core.models import …` innerhalb von Funktionsrümpfen, obwohl die Datei oben bereits importiert (Z.12). Klassisch Workaround gegen Zirkular-Imports — hier nicht offensichtlich nötig.
Empfehlung: Auf Modulkopf heben. Wenn Zirkel auftritt: dokumentieren und Strukturproblem als Folge-Issue.
```

```
[SCHWERE: niedrig]
Migrations: drei parallele 0049_*, zwei 0025_* (durch Merges aufgelöst)
Fundstelle: 0025_activity.py, 0025_episode_…, 0027_merge_…, drei 0049_*, 0051_merge_…
Beobachtung: Konkurrierende Branches haben Nummern doppelt vergeben; Merge-Migrationen fangen es ein. `0049_statistics_event_flat_mv` hängt parallel an 0048 ohne Merge bis 0050.
Empfehlung: Konvention bestätigen (umnummerieren statt Merge-Migration) oder Merge-Approach explizit dokumentieren.
```

```
[SCHWERE: niedrig]
RunPython ohne sinnvolle reverse_code
Fundstelle: 0035_fix_anonymous_events.py:17, 0049_statistics_event_flat_mv.py:71, 0068_attachment_versioning_stage_b.py:83, 0071_fieldtemplate_high_sensitivity_heal.py:30, 0074_redact_legacy_eventhistory_delete.py:69, 0018_populate_system_type.py:30
Beobachtung: `migrations.RunPython.noop` als Reverse. `0023_audit_detail_convert_text_to_json.py:38` zeigt, dass ein echter Inverse möglich wäre.
Empfehlung: In jedem `noop`-Reverse einen Docstring „warum nicht reversibel" hinterlassen.
```

```
[SCHWERE: niedrig]
print() in einer Migration
Fundstelle: 0037_migrate_encrypted_to_sensitivity.py:10
Empfehlung: Bei künftigen Daten-Migrationen `migration_logger = logging.getLogger("django.db.migrations")` verwenden.
```

```
[SCHWERE: niedrig]
Type-Hint-Abdeckung in views/ schwach
Fundstelle: 4 von 29 Views haben mindestens eine Annotation; 12 von 35 Services
Beobachtung: mypy-Strict-Zone deckt `core.services.*` ab, dort fehlen aber großflächig Annotationen. `disallow_untyped_defs` bewusst aus (Kommentar pyproject.toml:71-72).
Empfehlung: Inkrementell pro Service-Modul `disallow_untyped_defs = true` setzen, beginnend bei kleinen Modulen.
```

**Was nicht prüfbar:** echte zyklomatische Komplexität (kein `radon`/`xenon` lokal), reale Lint-/mypy-Lauf-Ergebnisse, `migrate --plan` für strikte Linearität.

---

### Dimension 3: Sicherheit (OWASP-orientiert)

Defense-in-Depth ist konzeptionell sauber: 4-Schicht-AuthZ (Role-Mixins → Facility-Middleware → RLS → Manager), CSP ohne `unsafe-inline`/`unsafe-eval` (außer für `style-src`), HSTS/HTTPS-Only, MultiFernet, ClamAV+Magic-Bytes, AuditLog-getriebenes Lockout. Die in der Baseline (security-bestand) notierten Lücken (Re-Auth, CSP-Reporting, Breach-Detection, CSRF-Failure-Page, Maintenance-Mode) sind seit 27.04. geschlossen. Die folgenden Befunde betreffen Restlücken in Hardening und Edge-Cases der **neu** eingeführten Layer.

```
[SCHWERE: hoch]
Open-Redirect in WorkItem-Status-View
Fundstelle: src/core/views/workitem_actions.py:61-64
Beobachtung: `next_url = request.POST.get("next"); if next_url and next_url.startswith("/"): return redirect(next_url)` — `startswith("/")` matcht auch `//evil.com`, das Browser als protokoll-relative URL interpretieren. SudoModeView._safe_next (sudo_mode.py:30) implementiert die richtige Prüfung (`not raw.startswith("//")`) — Pattern ist bekannt, hier vergessen.
Auswirkung: Phishing-Vektor: `?next=//phish.example/login` führt nach Klick zur Fake-Login-Seite.
Empfehlung: Helper `safe_redirect_path()` aus sudo_mode in `views/utils.py` heben und an allen Call-Sites anwenden. Architektur-Test, der `redirect(<unvalidiertes next>)` verbietet.
```

```
[SCHWERE: hoch]
Spoofbare Client-IP in Maintenance-Mode-IP-Allowlist
Fundstelle: src/core/middleware/maintenance.py:81-86
Beobachtung: `_client_ip` nimmt `forwarded.split(",")[0].strip()` — den ersten, vom Client setzbaren Eintrag. `core/signals/audit.py:15-48` macht es korrekt mit `TRUSTED_PROXY_HOPS` (split-from-rechts), Maintenance ignoriert die Variable.
Auswirkung: Während Wartung kann jeder den Header `X-Forwarded-For: <ops-ip>, ...` setzen und die Wartungsmauer umgehen — gefährdet den Defense-Layer, der bewusst pre-Auth läuft (Refs #700).
Empfehlung: `_client_ip` durch `core.signals.audit.get_client_ip` ersetzen. Test mit gespooftem XFF gegen Allowlist.
```

```
[SCHWERE: hoch]
Webhook-URL ungeprüft, SSRF-fähig
Fundstelle: src/core/services/breach_detection.py:155-171
Beobachtung: `urllib.request.urlopen(req, timeout=5)` ruft `BREACH_NOTIFICATION_WEBHOOK_URL` ohne Schema-/Host-Whitelist. `# noqa: S310 — vom Operator konfigurierte URL` deaktiviert Bandit explizit.
Auswirkung: Webhook auf `http://169.254.169.254/...` (Cloud-Metadata), `file://`, internen IP-Range leakt Breach-Payload (Facility-Name, User-IDs, Audit-IDs). Tenant-extern, aber nicht null.
Empfehlung: `urlparse(url)`, Schema gegen `{"https"}`, IP gegen `ipaddress.is_private/is_loopback`. Optional: DNS-Resolve-Check beim Setzen.
```

```
[SCHWERE: hoch]
Datei-Upload fail-open ohne Settings-Zeile
Fundstelle: src/core/services/file_vault.py:131-134, src/core/forms/events.py:198-200
Beobachtung: Beide Layer beenden Validierung bei `Settings.DoesNotExist` (`return cleaned` / `return  # No settings yet → no whitelist`).
Auswirkung: Eine Facility ohne Settings-Row akzeptiert jeden Dateityp, jede Größe, ohne Whitelist-Block. Race zwischen Facility-Anlage und Settings-Erzeugung.
Empfehlung: Hardcoded Default-Whitelist als Fallback. Architektur-Test: jede Facility hat genau eine Settings-Zeile (post-migrate-signal).
```

```
[SCHWERE: mittel]
Default-Passwort-Mindestlänge 8 Zeichen
Fundstelle: src/anlaufstelle/settings/base.py:127-132
Beobachtung: `MinimumLengthValidator` ohne `OPTIONS={"min_length": …}` — Django-Default 8.
Auswirkung: Für ein System mit § 203-/Art.-9-Daten unter aktuellem BSI-/NIST-Stand. Initial-Passwort-Generator (services/password.py:10-13) nutzt 12 Zeichen alphanumerisch (~71 Bit) — User-Passwörter werden so nicht erzwungen.
Empfehlung: `MinimumLengthValidator` auf 12 anheben; Initial-Passwort um Sonderzeichen ergänzen.
```

```
[SCHWERE: mittel]
SudoMode-Bypass via Setting
Fundstelle: src/core/services/sudo_mode.py:67-69
Beobachtung: `if not getattr(settings, "SUDO_MODE_ENABLED", True): return super().dispatch(...)` — Mixin lässt sich global deaktivieren. settings/test.py:9 setzt False.
Auswirkung: Versehentlicher `.env`-Eintrag `SUDO_MODE_ENABLED=false` kippt MFA-Disable, DSGVO-Export, Pseudonym-Daten-Download in einem Schritt.
Empfehlung: In prod.py analog SECRET_KEY: `if not SUDO_MODE_ENABLED: raise ImproperlyConfigured(...)`. Architektur-Test über Anzahl `RequireSudoModeMixin`-Verwendungen.
```

```
[SCHWERE: mittel]
Pseudonym im Klartext in DB, Index, Filename, Logs
Fundstelle: src/core/models/client.py:35-39, views/clients.py:264, services/breach_detection.py
Beobachtung: `pseudonym = CharField(max_length=100, db_index=True)` ohne `encrypt_field`. Trigram-GIN im Klartext (client.py:94-99). Export-Filename `f"datenauskunft_{client.pseudonym}.json"` (clients.py:264). AuditLog.detail enthält `{"pseudonym": client.pseudonym}` (clients.py:260, 288). Commit 9415e8d adressiert Freitexte, nicht das Pseudonym.
Auswirkung: Pseudonym ist „Anker-ohne-Klarname"-konzipiert; Fachkräfte tippen aber Initialen/Spitznamen, die de-anonymisieren. DB-Leck plus Disk-Image legt den Anker plus alle Events offen. Filename wandert in Browser-Historie.
Empfehlung: Trade-off bewusst entscheiden. Mindestens AuditLog.detail-Schreiber von `pseudonym` auf `client_id` umstellen. Filename: zufälliger Token, Pseudonym nur im PDF-Header.
```

```
[SCHWERE: mittel]
MFA nicht standardmäßig pflichtig
Fundstelle: src/core/models/user.py:108-118
Beobachtung: `is_mfa_enforced` returnt nur True, wenn `user.mfa_required` oder `facility.settings.mfa_enforced_facility_wide`. Default beides False.
Auswirkung: Frische Facility nimmt produktive Daten an, ohne dass irgendein Account MFA hat. TOM-Anforderung nur per organisatorischer Ansage erfüllt.
Empfehlung: Admin-Rolle hardcoded auf MFA-Pflicht, unabhängig von Settings. Optional Lead/Staff per Default True.
```

```
[SCHWERE: mittel]
Login-Lockout-Race bei verteiltem Brute-Force
Fundstelle: src/core/services/login_lockout.py:35-63
Beobachtung: Code dokumentiert „Threshold + 1 Versuch in seltenen Fenstern". `select_for_update` serialisiert nur `is_locked`, nicht den AuditLog-Write.
Empfehlung: Bei Pilot mit vielen Usern: dediziertes `LockoutState`-Model mit `select_for_update` + atomarem Increment, oder Redis-INCR-mit-TTL.
```

```
[SCHWERE: niedrig]
CSP erlaubt style-src 'unsafe-inline'
Fundstelle: src/anlaufstelle/settings/base.py:303 `"style-src": ["'self'", "'unsafe-inline'"]`
Empfehlung: Inline-Styles auflisten (Tailwind-utility-only sollte reichen), `style-src 'self'` engziehen.
```

```
[SCHWERE: niedrig]
Breach-Detection-Webhook: 5s Timeout, kein Retry, kein Auth
Fundstelle: src/core/services/breach_detection.py:160-171
Empfehlung: Retry mit Backoff (max 3) hinter Try. HMAC-Signature-Header (`X-Anlaufstelle-Signature`) mit Shared-Secret.
```

```
[SCHWERE: niedrig]
Maintenance-Mode-Cache pro Worker
Fundstelle: src/core/middleware/maintenance.py:39, 73-79
Beobachtung: `_cache: tuple[float, bool] | None = None` als Klassen-Attribut — pro Python-Prozess. Bei Gunicorn mit N Workers Lag von bis zu TTL Sekunden je Worker.
Empfehlung: TTL in Doku als „bis zu N Sekunden Lag pro Worker" expliziter machen. Optional SIGUSR1 als Cache-Invalidate-Signal.
```

```
[SCHWERE: niedrig]
SECURE_PROXY_SSL_HEADER ohne Trust-Boundary-Doku
Fundstelle: src/anlaufstelle/settings/prod.py:48
Empfehlung: Inline-Kommentar „Voraussetzung: Caddy strippt eingehendes XFP und setzt es selbst", ops-runbook verlinken.
```

```
[SCHWERE: info]
SAST-Stack vollständig, aber privates Repo skippt CodeQL
Fundstelle: .github/workflows/codeql.yml:19 (`if: github.event.repository.private == false`)
Beobachtung: CodeQL läuft nur im public app-Repo. pip-audit + dependabot sauber. Bandit nicht aktiv (siehe Dim 2 — Ruff `S` aus).
Empfehlung: Akzeptiert. Optional Bandit lokal in `make lint`.
```

**Was nicht prüfbar:** Echtes RLS-Verhalten unter Non-Superuser-DB-Rolle (geht nur live), View-für-View-Inventur aller HTMX-Auth-Pfade systematisch, Sentry-PII-Scrubber gegen reale Stack-Traces, Frontend-Service-Worker-/IndexedDB-Crypto-Pfade.

---

### Dimension 4: Datenschutz & Sozialdatenschutz

Hauptbefunde am Code: Niedrigschwelligkeit modellseitig erfüllt; Betroffenenrechte technisch unterstützt; Retention mehrschichtig, lösch- und redaktionssicher; Audit-Log gerichtsfest mit App- und DB-Triggern; Backup mit ausführbarem Restore-Drill und off-site-Hook; AVV/DSFA/Verzeichnis als Templates generierbar. Strukturlücken: K-Anonymisierung implementiert, aber **nicht im Lebenszyklus angeschlossen**; `reencrypt_fields` deckt nur `Event`, nicht `EventHistory`/`EventAttachment`; AuditLog-Pruning umgeht Trigger via `DISABLE TRIGGER` mit `try/finally` — bei SIGKILL bleibt Trigger disabled.

```
[SCHWERE: hoch]
K-Anonymisierung nicht im Lebenszyklus angeschlossen
Fundstelle: services/k_anonymization.py:1-94, migration 0049_k_anonymization.py, services/retention.py:771-806
Beobachtung: `Client.k_anonymized`, `Settings.k_anonymity_threshold` und `Settings.retention_use_k_anonymization` existieren (Migration 0049). `enforce_retention` und `RetentionProposal` rufen weiterhin `client.anonymize()`, nicht `client.k_anonymize()`. Suche nach `retention_use_k_anonymization` außerhalb der Migration: keine Treffer.
Auswirkung: Setting suggeriert betriebsbereite k-Anon, real greift sie nicht. Für DSGVO-Argumentation gegenüber Aufsicht potenziell irreführend.
Empfehlung: Verbindung schließen oder Setting aus Schema entfernen. Wenn implementiert: ADR ergänzen.
```

```
[SCHWERE: mittel]
reencrypt_fields-Command deckt nur Event, nicht EventHistory/EventAttachment
Fundstelle: src/core/management/commands/reencrypt_fields.py
Beobachtung: Iteriert nur `Event`. Bei Key-Wechsel müssen alle alten Keys in `ENCRYPTION_KEYS` verbleiben, um historische und Attachment-Daten weiter lesbar zu halten.
Empfehlung: Command um `EventHistory` und `EventAttachment` erweitern oder explizit dokumentieren, dass diese außer Scope sind und nur über die Multi-Key-Liste erreichbar bleiben.
```

```
[SCHWERE: mittel]
AuditLog-Pruning hängt am try/finally des Triggers
Fundstelle: services/retention.py:822-858
Beobachtung: `ALTER TABLE … DISABLE TRIGGER` in `transaction.atomic()` mit `try/finally`-`ENABLE`. Bei SIGKILL läuft `finally` nicht.
Auswirkung: Trigger bleibt disabled — AuditLog ist temporär nicht mehr append-only-geschützt.
Empfehlung: Pruning per separater Postgres-Funktion mit `SECURITY DEFINER`, die den Trigger lokal als `session_replication_role = replica` umgeht statt zu DISABLE-en. Schließt das SIGKILL-Fenster.
```

```
[SCHWERE: mittel]
Rechtsgrundlagen nicht im Code referenzierbar
Fundstelle: docs/dsgvo-templates/verarbeitungsverzeichnis.md (statisch); src/core/services/event.py:256 (Spurenverweis); 0074_redact_legacy_eventhistory_delete.py:8
Beobachtung: Vorlagen ausgewiesen, aber keine zentrale Stelle (Settings-Model, `LegalBasis`-Mapping pro Datenkategorie), die jeder Verarbeitung eine prüfbare Rechtsgrundlage zuordnet. § 67a SGB X (Erhebung mit Wissen der Betroffenen) nirgends explizit.
Empfehlung: Pro `DocumentType` ein optionales `legal_basis`-Feld (FK auf Enum/Modell).
```

```
[SCHWERE: niedrig]
DSGVO-Paket ohne Versions-/Settings-Stempel
Fundstelle: src/core/management/commands/generate_dsgvo_package.py
Beobachtung: Templates werden gerendert, aber das Ergebnis trägt keinen Versionsstempel des Codes oder Settings-Hash. Eine geänderte `retention_anonymous_days` schlägt nicht in die DSFA durch, wenn das Paket nicht neu generiert wird.
Empfehlung: Versions-Stempel (Datum + Settings-Hash + Commit-SHA) ins gerenderte Paket. Macht bei Aufsichts-Prüfungen erkennbar, ob DSFA aktuell ist.
```

```
[SCHWERE: info]
Niedrigschwelligkeit modellseitig erfüllt
Fundstelle: src/core/models/client.py:35-63, ClientForm (forms/clients.py:14-22)
Beobachtung: Pseudonym + age_cluster (Bucket) + contact_stage (IDENTIFIED/QUALIFIED). `notes` Help-Text warnt explizit vor Klarnamen/Art-9-Daten. ClientForm hält 4 Felder, davon 1 Pflicht. `Event.client = SET_NULL`, `Event.is_anonymous = BooleanField` — anonyme Strichlisten möglich.
```

```
[SCHWERE: info]
Audit-Log gerichtsfest, doppelt geschützt
Fundstelle: models/audit.py:101-110 + migration 0024_auditlog_immutable_trigger.py
Beobachtung: Python-Save blockt Update; PostgreSQL-Trigger raised auf BEFORE UPDATE OR DELETE. 38 Action-Choices, IP-Address gespeichert, drei Composite-Indizes.
Lücke: Kein Hash-Chaining (DB-Superuser kann Trigger droppen). Threat-Model dokumentiert das offen. Für Zielgruppe vertretbar.
```

```
[SCHWERE: info]
Retention/Löschung wirklich löschend
Fundstelle: services/retention.py:554-609 _soft_delete_events
Beobachtung: setzt is_deleted=True, leert event.data_json={}, schreibt redaktierten EventHistory-Eintrag (build_redacted_delete_history), löscht physisch alle EventAttachment-Files. Refs #714 hat die Lücke geschlossen, dass EventHistory früher Klartext konservierte.
```

**Was nicht prüfbar:** PIA/DSFA-Vollständigkeit gegen reale Aufsichtsbehörde-Maßstäbe (BayLDA-Praxis); transitive Verschlüsselungsketten in IndexedDB-/Service-Worker-Pfaden.

---

### Dimension 5: Tests & Qualitätssicherung

**1.953 Funktionen über 162 Dateien**, Pyramide klassisch (Unit/Integration:E2E ≈ 4,6:1 in Funktionen). Autorisierungs-Matrix vorbildlich (`test_rbac_matrix.py`, 33 Tests parametrisiert über 4 Rollen). Wait-Strategie diszipliniert (0× `networkidle`, 447× `wait_for_url`/`domcontentloaded`). 42 Smoke-Tests, `--reuse-db`, Postgres-CI-Service, SBOM, Lock-Drift-Check.

```
[SCHWERE: hoch]
Funktionaler RLS-Cross-Tenant-Test mit echter NOSUPERUSER-Rolle
Fundstelle: src/tests/test_rls_functional.py (Commit 4f4273a)
Beobachtung: Erstellt `CREATE ROLE rls_test_role NOSUPERUSER NOREPLICATION INHERIT NOLOGIN`, `SET ROLE`, `set_config('app.current_facility_id', ...)`, prüft 0-Rows über alle `core_*`-Tabellen. Adressiert exakt das Risiko, dass der Default-Postgres-Superuser RLS bypasst und Test-Lücken erzeugt.
Bewertung: Vorbildliches Muster, das viele Django-Projekte fehlt.
```

```
[SCHWERE: mittel]
Kein factory_boy
Fundstelle: grep -rE "factory_boy|DjangoModelFactory" src/tests/ → 0
Beobachtung: 1.953 Tests nutzen direkte `Model.objects.create(...)`-Aufrufe in jeder Funktion. Boilerplate, aber lesbar.
Empfehlung: Schrittweise Einführung für `Event`, `Client`, `WorkItem`, `Case`.
```

```
[SCHWERE: mittel]
Kein Hypothesis (Property-based Testing)
Fundstelle: grep -irlE "hypothesis|@given" → 0
Beobachtung: Validatoren werden nur exemplarisch getestet (Field-Sensitivity, JSON-Schema in test_field_template_validator.py, Datums-/Retention-Berechnung).
Empfehlung: `hypothesis` für `services/date_utils`, `services/k_anonymization`, Field-Template-Validator.
```

```
[SCHWERE: mittel]
Query-Count-Tests nur an drei Hot-Spots
Fundstelle: test_zeitstrom_perf.py, test_snapshot_command.py, test_attachment_versioning_stage_b.py
Beobachtung: `CaptureQueriesContext` (statt `assertNumQueries` — robuster). Listen-Views `ClientListView`, `EventListView`, `WorkItemListView` und Statistik-Aggregate haben keinen N+1-Test.
Empfehlung: Pattern aus test_zeitstrom_perf.py auf Detail/List-Views der Kerndomäne ausweiten.
```

```
[SCHWERE: niedrig]
Keine Coverage-Schwelle (`--cov-fail-under`)
Fundstelle: .github/workflows/test.yml
Beobachtung: Coverage wird reportet, aber nicht erzwungen. Coverage darf still sinken.
Empfehlung: `--cov-fail-under=85` (oder dem aktuellen Stand entsprechend) setzen.
```

```
[SCHWERE: niedrig]
Keine Last-Test-Schicht
Fundstelle: find -name "locustfile*" → leer; pytest-benchmark/locust nicht in requirements
Empfehlung: Locust-Skript + Nightly-Job mit Performance-Budgets (z.B. „Listenansicht < 500 ms bei 10k Events").
```

```
[SCHWERE: niedrig]
Keine Python-Versions-Matrix in CI
Fundstelle: .github/workflows/test.yml (nur 3.13)
Empfehlung: Vermerken in CONTRIBUTING.md („nur 3.13 supported") oder gegen 3.14-Future-Compat ergänzen.
```

```
[SCHWERE: info]
Wait-Strategie diszipliniert
Fundstelle: src/tests/e2e/, grep "networkidle" → 0; "wait_for_url|domcontentloaded" → 447 in 51 Dateien
Beobachtung: CLAUDE.md verbietet `networkidle` — Regel wird eingehalten.
```

**Was nicht prüfbar:** Tatsächliche CI-Lauf-Dauer (kein Timing-Logging); Coverage-Prozent (Artefakt im Workflow generiert, lokal nicht gelesen); Mutation-Test-Härte (`mutmut`/`cosmic-ray` nicht eingesetzt).

---

### Dimension 6: Performance & Skalierbarkeit

DB-/ORM-Disziplin gut: 25× `select_related`/`prefetch_related` in Views, 30+ in Services, GIN-pg_trgm-Index, Materialized View für Statistik mit `REFRESH … CONCURRENTLY`, gezielte Composite-Indizes. Optimistic Locking in 4 Flows (Case/Client/Settings/Event/WorkItem) mit dediziertem 409-Conflict-Pfad. **Caching-Schicht die Lücke:** kein `CACHES`-Backend in Settings, kein `cache_page`/Template-Fragment, keine Debug-Toolbar/Silk.

```
[SCHWERE: hoch]
Kein CACHES-Backend in Settings — LocMem-Default pro Worker
Fundstelle: settings/base.py, prod.py (kein CACHES = {…} gesetzt)
Beobachtung: Maintenance-Cache (middleware/maintenance.py:14, 45) und Ratelimit (django-ratelimit) nutzen Django-Default-Cache = LocMem, pro Prozess. Bei `GUNICORN_WORKERS > 1` sind Maintenance-Lockout und Ratelimit-State **nicht worker-übergreifend konsistent**.
Auswirkung: Horizontale Skalierung blockiert; Ratelimit umgehbar durch Worker-Wechsel; Maintenance-Cache-Lag pro Worker.
Empfehlung: `CACHES` mit Redis-Backend in prod.py. Ratelimit + Maintenance + Statistik-Dashboard explizit aliasieren.
```

```
[SCHWERE: mittel]
Pagination uneinheitlich
Fundstelle: views/clients.py:54-58 (25), cases.py:85, audit.py:49 (50). WorkItem-Inbox, Search, Zeitstrom: ungedeckelt oder hart [:200] in feed.py
Beobachtung: WorkItem-Inbox-View (workitems.py:88) hat keinen Limit-Cap. Zeitstrom-Feed deckelt 5× bei 200 (services/feed.py:64,88,100,116,124) — schneidet Tag bei sehr aktiven Einrichtungen ab.
Empfehlung: WorkItem-Inbox + Search auf `DEFAULT_PAGE_SIZE` ziehen oder explizites Slice-Limit + „Mehr"-HTMX-Loadmore.
```

```
[SCHWERE: niedrig]
Doku-Inkonsistenz MV-Refresh: Migration „täglich", Cron „stündlich"
Fundstelle: migrations/0049_statistics_event_flat_mv.py:6 (Docstring) vs. docs/ops-runbook.md:166,186 (`15 * * * *`)
Empfehlung: Migration-Docstring an Cron-Realität anpassen.
```

```
[SCHWERE: niedrig]
Query-Count-Coverage zu schmal (siehe auch Dim 5)
Empfehlung: ClientDetailView, EventDetailView, CaseDetailView, Handover als nächste Kandidaten.
```

```
[SCHWERE: info]
N+1-Prävention durchgängig, mit assertNumQueries-Backstop an drei Hot-Spots
Fundstelle: views/clients.py:88-122, views/zeitstrom.py:86-94, services/feed.py, services/handover.py:69, services/offline.py, services/client_export.py:53-100
Beobachtung: Commits f002e3e, afd504b, 2b35040 belegen aktive Pflege.
```

```
[SCHWERE: info]
pg_trgm-Suche mit GIN-Index, validierter Threshold
Fundstelle: migrations 0055/0056, services/search.py:120-127
Beobachtung: GIN(gin_trgm_ops) auf client.pseudonym, Threshold pro Facility (Default 0.3) mit CheckConstraint(gte=0, lte=1) + Validators.
```

```
[SCHWERE: info]
Materialized View statistics_event_flat
Fundstelle: migration 0049_statistics_event_flat_mv.py, refresh_statistics_view.py, services/statistics.py:14, 127
Beobachtung: drei Indizes (UNIQUE für CONCURRENTLY, Facility/Month, Facility/DocumentType/Month). Cron stündlich :15. SQLite-Fallback no-op.
```

```
[SCHWERE: info]
Optimistic Locking in 4 Flows aktiv
Fundstelle: services/locking.py:27, services/cases.py:55, services/workitems.py:94, services/event.py:586, services/clients.py:73, services/settings.py:45 — Views reichen `expected_updated_at` durch
Beobachtung: Real verdrahtet, EventUpdateView hat dediziertes _conflict_response (409).
```

**Was nicht prüfbar:** Durchsatz unter Last (kein Tooling); reale Refresh-Dauer der MV; tatsächliche Cache-Hit-Raten.

---

### Dimension 7: Barrierefreiheit & UX beachtet — keine WCAG-2.1-AA-Vollverdikte. Bestand: solides Fundament (Skip-Link, Landmarks, `<html lang>`, Pinch-Zoom erlaubt, `:focus-visible`-Styles), aber etliche Lücken jenseits von M3 nachschärfbar.

```
[SCHWERE: hoch]
Schrift 10–11 px im mobilen Outdoor-Einsatz (Streetwork-Kontext)
Fundstelle: base.html: 19× text-[10px]/[11px]/[9px]; Sidebar `text-[12px]`/`text-[11px]`
Auswirkung: DM-Sans bei 10–11 px auf Outdoor-Tablet bei Sonnenlicht grenzwertig. Domänen-relevant.
Empfehlung: Mindestgröße 12 px für sekundären Text — Tailwind-Tokens anpassen.
```

```
[SCHWERE: hoch]
non_field_errors fehlen in 4 zentralen Forms
Fundstelle: clients/form.html, cases/form.html, workitems/form.html, events/edit.html — kein {% for error in form.non_field_errors %}-Block
Auswirkung: Service-Layer-ValidationErrors verschwinden stumm.
Empfehlung: 4-Zeilen-Patch je Template.
```

```
[SCHWERE: mittel]
Stumme HTMX-Updates — Bulk-Aktionen, Goals, Retention bestätigen sich SR nicht
Fundstelle: base.html:217 `<div id="flash-messages">` keine dauerhafte Live-Region; goals_section.html etc. swappen via outerHTML
Empfehlung: Stabiler `<div role="status" aria-live="polite">` als Swap-Container, kein outerHTML des Containers selbst.
```

```
[SCHWERE: mittel]
ARIA-arme Form-Fehler: kein aria-invalid, kein aria-describedby
Fundstelle: clients/form.html:23-29, cases/form.html, workitems/form.html, events/create.html:48-54, events/edit.html:82
Beobachtung: Fehlertext daneben gerendert, aber nicht assoziiert. Pflichtfelder nur visuell mit `*` (rote Farbe als alleiniger Indikator).
Empfehlung: Zentralen Form-Renderer-Partial für Fehler+Hilfe konsistent.
```

```
[SCHWERE: mittel]
client-list Pseudo-Tabelle ohne <th>
Fundstelle: src/templates/core/clients/partials/table.html:14-20
Beobachtung: CSS-Grid statt echter Tabelle, sortierbare Daten ohne Spalten-Header für Screen-Reader.
Empfehlung: `role="table"`/`role="row"`/`role="columnheader"` ergänzen oder echte `<table>`.
```

```
[SCHWERE: niedrig-mittel]
Tabindex-Anti-Pattern in events/create.html
Fundstelle: src/templates/core/events/create.html:64, 101, 175, 179 (`tabindex="1"`, `"2"`, `"100"`, `"101"`)
Beobachtung: Positive `tabindex`-Werte > 0 brechen DOM-Reihenfolge — WCAG/ARIA-widrig.
Empfehlung: Entfernen, DOM-Reihenfolge aufräumen.
```

```
[SCHWERE: niedrig]
Form-Label _("Klientel") widerspricht Sprachleitlinie
Fundstelle: src/core/forms/events.py:64
Empfehlung: An Templates angleichen (siehe Dim 8 für die Vollumstellung).
```

```
[SCHWERE: niedrig]
Touch-Targets in Sidebar/Tabellen unter 44 px
Fundstelle: nur 12 Treffer für `min-h-[44]/[48]` repoweit
Empfehlung: Sidebar-Links und Tabellenzeilen-Aktionen anheben.
```

```
[SCHWERE: niedrig]
DE/EN-Toggle ohne aria-pressed
Fundstelle: base.html:199-204
Empfehlung: `aria-pressed="true"` auf den aktiven Knopf, damit SR-Nutzende den Status hören.
```

```
[SCHWERE: info]
Solides Fundament: Skip-Link, Landmarks, Lang-Attribut, Pinch-Zoom
Fundstelle: base.html:3, :27, :66, :215, :225, :235; static/css/input.css:104-107 (`:focus-visible { ring-2 ring-accent ring-offset-2 }`)
```

**Was nicht prüfbar:** Echtes Screen-Reader-Verhalten (NVDA/VoiceOver/TalkBack); Kontrastwerte mit Tooling (axe-core); Real-User-Fokus-Drift nach HTMX-Outer-Swaps.

---

### Dimension 8: Internationalisierung & Lokalisierung

Settings + Middleware sauber. gettext-Abdeckung in Templates breit (821 `{% trans %}`-Tags, keine Roh-Buttons in Stichprobe). Hauptbefund: **Sprachleitlinie #604 nicht durchgezogen**.

```
[SCHWERE: hoch]
de.po / Code-Seite: 22 msgid mit "Klient…" trotz adoptierter Sprachleitlinie
Fundstelle: locale/de/LC_MESSAGES/django.po (22× "Klient"); 37× `_("Klient…")` außerhalb Migrationen in src/core/{services,forms,models,templatetags}/
Beispiele: client.py:85 verbose_name=_("Klientel"); audit.py:28-29 _("Klientel angelegt"); services/cases.py:142; forms/cases.py:17,51,53; forms/events.py:64; services/event.py:483,511,523; templatetags/core_tags.py:157; templates: attachments/partials/attachment_table.html:13, attachments/list.html:28
Beobachtung: Templates und Views top-level umgestellt; Models, Services, Forms, Audit-Choices, Attachments-UI und 22 .po-Einträge nicht. Validierungsfehler, Audit-Log-Einträge, `verbose_name` im Admin geben weiter „Klientel" aus.
Empfehlung: Vollumstellung ziehen + `makemessages -a` + `compilemessages`. Audit-Choices-Labels brauchen Migration mit Choice-Update (0059 enthält noch „Klientel angelegt").
```

```
[SCHWERE: hoch]
en.po: 30 fuzzy + 65 leere msgstrs nach #706-Re-Build
Fundstelle: src/locale/en/LC_MESSAGES/django.po
Beobachtung: POT-Header heute, ergo Re-Build nach #706 ohne Nach-Übersetzen. EN-UI zeigt für 65 Strings den deutschen msgid (z.B. „Soft-deleted am" en.po:268, „Sicherheitsverletzung" :551, „Account-Sperre aufgehoben"). de.po: 124 fuzzy, 252 leere msgstrs.
Empfehlung: Nach-Übersetzen + CI-Check `msgattrib --untranslated --no-fuzzy` zählt 0.
```

```
[SCHWERE: mittel]
Datums-/Zahlenformate: L10N nicht genutzt
Fundstelle: 13 Templates mit hartcodiertem `|date:"d.m.Y"`/`"d.m.Y H:i"` (statistics, clients/detail+table, cases/event_list, retention/proposal_card)
Beobachtung: Bei `LANGUAGE_CODE=en` werden Daten weiter im DE-Format ausgegeben. Auch Python: forms/workitems.py:105,133, services/dsgvo_package.py:60, services/export.py:125 mit `strftime("%d.%m.%Y")`. Für Export-PDFs (deutsche Behörde) vertretbar, für UI-Validierungsmeldungen im EN-Modus nicht.
Empfehlung: 13 Templates auf `|date:"DATE_FORMAT"` umziehen.
```

```
[SCHWERE: info]
Settings, Middleware, Sprachumschaltung sauber
Fundstelle: settings/base.py:191-201; middleware-Reihenfolge (LocaleMiddleware → UserLanguageMiddleware); user.py:59 preferred_language; views/auth.py:189 persist
Beobachtung: UserLanguageMiddleware ignoriert bewusst Accept-Language, nimmt User-Feld. Konsistent mit FND-13.
```

**Was nicht prüfbar:** Tatsächlicher Übersetzungs-Stand pro msgstr (qualitativ); Plural-Forms-Korrektheit in EN.

---

### Dimension 9: Deploy & Betrieb

Multi-Stage-Dockerfile, drei Compose-Files, Caddy-Auto-TLS, verschlüsseltes Backup mit Rotation, Off-Site-Hook, ausführbarer Restore-Drill, JSON-Logging, Health-Endpoint, Maintenance-Mode, PWA-Offline-Fallback, Ops-Runbook + Coolify-Leitfaden. Schwächen: 12-Factor-Reinheit (Migrations im Web-Container), Operator-Reibung (Cron + `setup_facility` + `ALTER ROLE NOSUPERUSER` manuell), kein One-Command-Setup.

```
[SCHWERE: hoch]
Migrations laufen im Web-Container — kein echtes Zero-Downtime
Fundstelle: docker-entrypoint.sh:4-22
Beobachtung: `pg_advisory_lock(1)` → `migrate --noinput` → `collectstatic` → `gunicorn`. Erwartete Downtime laut ops-runbook.md:61 „~10–30 Sekunden". Kein separater Migration-Job.
Auswirkung: Lange RunPython-Migrationen blockieren alle Worker. >1 Replica wartet beim Start. Maintenance-Mode ist Workaround, nicht Lösung.
Empfehlung: Migration als One-Shot-Job (`docker compose run --rm web python manage.py migrate`) im Release-Runbook etablieren; Entrypoint nur `collectstatic` + `gunicorn`.
```

```
[SCHWERE: hoch]
Kein Init-Setup-Skript — NOSUPERUSER-Schritt wird vergessen
Fundstelle: docs/coolify-deployment.md:84-97
Beobachtung: Nach `docker compose up` muss Admin manuell `python manage.py setup_facility` ausführen, dann **als Postgres-Admin per psql** `ALTER ROLE anlaufstelle_user NOSUPERUSER;` setzen — sonst RLS bypasst.
Auswirkung: Träger ohne IT-Abteilung wird den NOSUPERUSER-Schritt vergessen — und damit das Facility-Isolations-Safety-Net deaktivieren, ohne dass Healthcheck oder UI das meldet.
Empfehlung: `scripts/initial-setup.sh` schreiben, das (a) `setup_facility` ausführt, (b) `rolsuper` prüft und `ALTER ROLE` ausführt, (c) Health-Check auf `degraded`. Im Health-Endpoint zusätzlich `db_user_is_superuser` ausweisen.
```

```
[SCHWERE: mittel]
docker-compose.prod.yml pinnt Image-Tag hart
Fundstelle: docker-compose.prod.yml:17 `image: ghcr.io/anlaufstelle/app:v0.10.2`
Beobachtung: Tag hardcoded statt `${APP_VERSION:-latest}`. Update/Rollback erfordert File-Edit; Coolify zieht `:latest` (Doku-Drift).
Empfehlung: `${APP_VERSION:-v0.10.2}` mit Default in `.env.example`.
```

```
[SCHWERE: mittel]
Caddyfile unterspezifiziert: keine www-Redirect, kein log, keine Rate-Limits
Fundstelle: Caddyfile:1-11, Caddyfile.staging:1-12
Beobachtung: kein `www.{$DOMAIN}` mit `redir`, kein `log { output file }`, kein `rate_limit`. Staging auf Port 8443 ohne LE-Hinweis.
Empfehlung: www-Redirect + Access-Log mit Volume-Mount; Staging `tls internal` oder Reverse-Proxy-Hinweis.
```

```
[SCHWERE: mittel]
Cron-Jobs liegen außerhalb von Compose
Fundstelle: docs/ops-runbook.md:172-193 (Backup, Retention, Snapshots, MV-Refresh, Breach-Detection als Host-Crontab)
Beobachtung: Kein cron-Service in docker-compose.prod.yml, keine systemd-Unit. Coolify-Doku erwähnt das Thema gar nicht. Frischer Stack läuft technisch, aber **ohne Backups**, **ohne Retention** (Art. 5 Abs. 1 lit. e) und **ohne Breach-Detection** (Art. 33), bis Admin Crontab anlegt.
Empfehlung: cron-Service in docker-compose.prod.yml (z.B. supercronic-Sidecar). Mindestens Backup + Retention + Breach-Detection als Default-on. Health-Endpoint mit `last_backup_age`.
```

```
[SCHWERE: mittel]
Off-Site-Backup-Hook ist Best-Effort und schlägt still (Exit 0)
Fundstelle: scripts/backup.sh:181-219
Beobachtung: Bei rclone/S3/SCP-Fehler wird ERROR geloggt, OFFSITE_OK=false gesetzt, Skript exit 0. Operator merkt nur über Log-Lesen.
Empfehlung: Bei zwei aufeinanderfolgenden Fehlern Exit != 0; alternativ Sentry-Capture wenn `SENTRY_DSN`. Restore-Drill könnte zusätzlich `last-modified` des Off-Site-Files prüfen.
```

```
[SCHWERE: mittel]
Health-Endpoint deckt nur DB + ClamAV
Fundstelle: src/core/views/health.py:25-56
Beobachtung: SELECT 1 + clamav_ping. Keine Prüfung von SMTP, ENCRYPTION_KEY, Disk-Füllstand, Backup-Alter. „degraded" nur für ClamAV.
Auswirkung: Lautloser SMTP-Ausfall blockt Token-Invites; Disk-voll wird erst beim Backup-Fehler entdeckt.
Empfehlung: Health-Endpoint erweitern um `smtp` (CONNECT-Test 2s), `encryption_key_valid` (Fernet-Decrypt), `last_backup_age_hours`, `disk_free_pct`.
```

```
[SCHWERE: niedrig]
docker-compose.yml (dev) bindet Postgres auf 5432:5432 mit trivialen Credentials
Fundstelle: docker-compose.yml:8-9
Beobachtung: `anlaufstelle/anlaufstelle/anlaufstelle`. Wenn Operator versehentlich dev-File auf Server startet — Postgres öffnet auf Host-IP.
Empfehlung: `127.0.0.1:5432:5432` (loopback only); Header-Kommentar „Niemals auf Public-Server".
```

```
[SCHWERE: niedrig]
3-Repo-Pipeline ohne Sync-Skript im Repo
Fundstelle: docs/adr/011-three-repo-release-pipeline.md:26-31
Beobachtung: Manueller Workflow, kein `scripts/release-to-stage.sh`. Release-Runbook liegt in „dev-Repo Issue #502". Bus-Faktor-relevant.
Empfehlung: Wrapper-Skripte als Rezept ins Repo; Issue #502 als `docs/release-runbook.md` überführen.
```

```
[SCHWERE: info]
PWA-Offline-Fallback und Service Worker sauber
Fundstelle: src/static/js/sw.js:13-44, src/templates/offline.html:1-30
Beobachtung: CACHE_NAME `anlaufstelle-v8`, App-Shell + offline.html precached, Multipart bewusst nicht offline-gequeuet (503 statt stiller Drop), POST-Queueing mit MessageChannel + ACK-Timeout.
```

```
[SCHWERE: info]
Restore-Drill ausführbar und prüft RLS + AuditLog-Trigger
Fundstelle: scripts/restore-drill.sh:1-159
Beobachtung: 7-Schritt-Drill verifiziert nicht nur DB-Restore, sondern dass RLS-Policies (>=18 Tabellen) und `auditlog_immutable`-Trigger im Image vorhanden sind. Cron-Vorschlag mit Mail bei FAIL.
```

**Was nicht prüfbar:** Reale Migrations-Dauer, ClamAV-Cold-Start, Caddy-Auto-TLS gegen LE-Quotas, ob Off-Site-Sync-Tools (rclone/aws/scp) im fertigen Image installiert sind (Dockerfile installiert sie nicht — Operator-Aufgabe).

---

### Dimension 10: Lizenz, Governance & Nachhaltigkeit

Lizenz technisch korrekt (AGPL-3.0-Volltext, Footer-Link, pyproject-Metadatum). SECURITY.md, 13 ADRs auf hohem Niveau. Strukturlücken: kein CoC, kein DCO/CLA, keine SPDX-Header, keine Roadmap, keine Lizenz-Inventur der Deps. Bus-Factor 1 wird in SECURITY.md offen benannt, aber nicht durch Nachfolge-Klausel adressiert.

```
[SCHWERE: hoch]
Tag-Signing inkonsistent
Fundstelle: git tag --sort=-creatordate
Beobachtung: v0.10.2 ED25519-signiert. v0.10.0/v0.10.1 annotated, unsigniert. v0.9.0/v0.9.1 Lightweight-Tags ohne Tag-Objekt — beliebig umschreibbar.
Auswirkung: Tag-Integrität als Lieferketten-Sicherheitsmerkmal nicht durchgängig gegeben. „Benigner Tag wird gegen bösartigen Tag getauscht" ohne Nachweis möglich.
Empfehlung: docs/release-checklist.md um `git tag -s` erzwingen. v0.10.0/0.10.1 nachträglich ersetzen oder durch RELEASES.md klarstellen. v0.9.x als „pre-signing baseline" markieren.
```

```
[SCHWERE: hoch]
Kein DCO/CLA, keine Lizenzklausel in CONTRIBUTING.md
Fundstelle: CONTRIBUTING.md:351-377 (PR-Prozess) — nur Branch/Tests/Squash-Merge
Auswirkung: AGPL Inbound = Outbound nicht automatisch. Bei NLnet-Förderung mit externen Mitwirkenden forensisch relevant.
Empfehlung: Abschnitt „Lizenz für Beiträge" + DCO-Sign-off im PR-Body. Optional REUSE-konformes SPDX-Setup.
```

```
[SCHWERE: mittel]
Kein Code of Conduct
Fundstelle: ls CODE_OF_CONDUCT.md → leer
Beobachtung: Sozialarbeits-Domäne (vulnerable Klientel) und öffentliches Repo. NLnet/Föderationen verlangen CoC explizit.
Empfehlung: Contributor Covenant 2.1 mit Eskalations-Adresse `kontakt@anlaufstelle.app`.
```

```
[SCHWERE: mittel]
Keine Lizenz-Inventur der Dependencies
Fundstelle: kein THIRDPARTY-LICENSES.md, kein pip-licenses-Workflow
Beobachtung: Stichprobe Top-Level zeigt nur AGPL-kompatible Lizenzen — aber keine institutionalisierte Prüfung gegen GPL-Inkompatibilitäten via transitive Dependencies.
Empfehlung: `pip-licenses --format=json` als CI-Step, Allowlist (BSD/MIT/Apache-2.0/LGPL/PSF) erzwingen, fail bei Drift.
```

```
[SCHWERE: mittel]
Bus-Factor = 1 nicht in CONTRIBUTING dokumentiert
Fundstelle: git shortlog -sn → Tobias Nix 689, Dependabot 9
Beobachtung: PR-Prozess verlangt „mindestens ein Approval", de facto Self-Approval (kein zweiter Maintainer). SECURITY.md:41 nennt das offen, CONTRIBUTING.md adressiert weder Self-Review-Checkliste noch Nachfolge.
Empfehlung: Abschnitt „Aktueller Maintainer-Status" mit Self-Review-Checkliste und Nachfolge-/Eskalationskontakt (NLnet-Liaison).
```

```
[SCHWERE: niedrig]
Roadmap nirgends explizit, NLnet-Sperre nur in CLAUDE.md/MEMORY.md
Empfehlung: docs/roadmap.md mit Meilenstein-Tabelle und „nicht annehmen"-Liste (NLnet-M0–M6), in CONTRIBUTING verlinken.
```

```
[SCHWERE: niedrig]
Keine SPDX-Header / Copyright-Notiz in Quelldateien
Fundstelle: Stichproben starten ohne `# SPDX-License-Identifier:`
Empfehlung: REUSE-Tooling (`reuse annotate`) mindestens für neue Dateien.
```

```
[SCHWERE: niedrig]
AGPL-§13-Footer ohne Versions-/Commit-Anker
Fundstelle: src/templates/base.html:224-231 (Default zeigt auf upstream `main`)
Beobachtung: Forks/Self-Hoster vergessen leicht, den URL anzupassen — liefern technisch falsche §13-Erklärung.
Empfehlung: Footer-URL aus Settings-Variable (`SOURCE_CODE_URL`) rendern; Versions-/Commit-SHA daneben anzeigen.
```

**Was nicht prüfbar:** GitHub-API-gestützte Sichtbarkeit der drei Repos, Issue-Triage-Tempo/Stale-Quote, PGP-Key-Veröffentlichung, Workflow-Konfigurationsqualität, transitive Lizenztiefe.

---

### Dimension 11: Fachliche Eignung (Domain-Fit)

Datenmodell trifft den niedrigschwelligen Use-Case sehr gut. Pseudonym-First, Datensparsamkeit, Statistik-ohne-Re-ID, Strichliste/Einzelfall-Dualität, Schichtübergabe sauber im Code verankert. Schwächen kosmetisch (Sprachleitlinie nicht im Modell-Layer) oder bewusst-deferred (kein Alias-Merge).

```
[SCHWERE: mittel]
Sprachleitlinie nicht im Modell-Layer durchgezogen
Fundstelle: client.py:85 verbose_name=_("Klientel"); event.py:30; case.py:34; workitem.py:52; recent_client_visit.py:37-38; document_type.py:77; audit.py:28-29 (CLIENT_CREATE Label "Klientel angelegt"); client.py:52 Help-Text
Beobachtung: UI/Templates und Views umgestellt; verbose_name + Audit-Action-Labels nicht. verbose_name ist nicht rein intern: er taucht in Admin, generischen Fehlermeldungen und Audit-Logs auf.
Empfehlung: verbose_name + verbose_name_plural auf Person/Personen, Audit-Action-Labels umstellen (Migration nötig für Choice-Update). Siehe auch Dim 8.
```

```
[SCHWERE: mittel]
Kein Alias-Modell für mehrere Spitznamen pro Person
Fundstelle: client.py:88-93 (UniqueConstraint nur (facility, pseudonym))
Beobachtung: Wer mehrfach unter unterschiedlichem Pseudonym auftaucht, erzeugt mehrere Client-Datensätze. Mergen nicht im Modell; nur Trigram-Suche federt ab.
Auswirkung: Streetwork-Realität („Maus" / „Maus aus dem Park" / „Saskia") wird nicht als eine Person erkannt.
Empfehlung: Alias-Tabelle `ClientAlias(client_fk, alias, source)` — auch wenn Fachkonzept es als YAGNI markiert, Trigram ist Workaround, nicht Lösung.
```

```
[SCHWERE: niedrig]
Goal nicht direkt mit Event verknüpft
Fundstelle: outcome.py:10-33 (OutcomeGoal hängt am Case)
Beobachtung: Wirkungsmessung „welche Kontakte trugen zu diesem Ziel bei?" muss heuristisch über Case erfolgen.
Empfehlung: Optionale `goal`-FK in Event oder Verknüpfungstabelle `EventGoal(event_fk, goal_fk)`. Reporting-Optimierung.
```

```
[SCHWERE: niedrig]
Client.notes-Sensitivitätsklassifizierung nur per Help-Text
Fundstelle: client.py:54-63
Beobachtung: Help-Text warnt vor Klarnamen/Art-9-Daten, ist aber nicht erzwungen.
Empfehlung: Optionales `notes_is_sensitive`-Flag konsistent mit der Trennlinie, die das Modell sonst zieht.
```

```
[SCHWERE: info]
Pseudonym-Pflicht-Minimum, datensparsam, anonyme Events
Fundstelle: client.py:35-39, ClientForm Meta.fields=4 (1 Pflicht); event.py:24-31 client null-able + is_anonymous-Flag; event.py:32-37 document_type Pflicht (PROTECT)
```

```
[SCHWERE: info]
k-Anonymisierung implementiert (Lebenszyklus-Anschluss siehe Dim 4)
Fundstelle: services/k_anonymization.py:1-94, client.py:65-72, 205-214
Beobachtung: Hash-Pseudonym, age_cluster + contact_stage (low-cardinality) bleiben, k_anonymized=True, is_active=False. Nicht-destruktiv für Fall-Daten — fachlich wichtig für Wirkungsbericht trotz Recht-auf-Vergessen.
```

```
[SCHWERE: info]
Schichtübergabe aus dem Modell heraus generiert
Fundstelle: views/handover.py:13-43 + services/handover.py + templates/core/handover/. Zeitstrom (views/zeitstrom.py:107-128) bindet die selbe build_handover_summary ein.
Beobachtung: Keine separate Tabelle, kein Doppelpflege-Aufwand. Genau der A4-Kladde-Use-Case aus dem Fachkonzept.
```

```
[SCHWERE: info]
Notfall/Vorfall/Schwarzes Brett: WorkItem.HINT + DocumentType.BAN
Fundstelle: workitem.py:15-53; document_type.py:33 (BAN = "ban", _("Hausverbot"))
Beobachtung: Hausverbot nicht hartcodiert, sondern Teil des Domänen-Bibliothek-Konzepts — flexibel auch für Frauenhaus/Drogenhilfe.
```

```
[SCHWERE: info]
QuickTemplates: Sensitivitäts-Filter im Service-Layer
Fundstelle: quick_template.py:15-84 (prefilled_data nur NORMAL-Felder, Z.20-26 dokumentiert)
Beobachtung: Sicherheits-by-Design gegen „Template enthält versehentlich Klarnamen". Kein Pseudonym/Identifier präfüllbar.
```

```
[SCHWERE: info]
Offline/PWA abgestuft: Read-Cache, audit-pflichtig
Fundstelle: 0041_offline_key_salt.py (User-Salt + offline_key_fetch-Action), views/offline.py, views/pwa.py, sw.js, offline.html
Beobachtung: PBKDF2-Ableitung client-seitig, audit-pflichtige Schlüsselabholung, IndexedDB-Read-Cache, kein bidirektionaler Sync. Bewusste Begrenzung passend zur Streetwork-Realität.
```

**Was nicht prüfbar:** Tatsächliche User-Akzeptanz (echte Pilot-Daten); ob die `Settings`-Felder für Aufbewahrungsfristen den jeweiligen Trägerkonventionen genügen.

---

### Dimension 12: Dokumentation

Doku überdurchschnittlich vollständig: 13 ADRs, FAQ-Sync-Footer mit Code-Verifikationsdatum, DE/EN-Mehrsprachigkeit, Threat-Model im Release-Sync, ausführbarer Restore-Drill, 5 DSGVO-Vorlagen. Drifts liegen an den Rändern: Django-Versions-Sprung 5.1 → 6.0.4 noch nicht in 4 Doku-Stellen reflektiert, README mit „Klientel"-Resten gegen die eigene Sprachleitlinie, EN-Doku teils 2 Minor-Reihen hinter, drei Kern-Themen im ADR-Backlog trotz stabiler Implementierung.

```
[SCHWERE: mittel]
README + Setup-Anleitung mit "Klientel"-Resten — widerspricht eigener Sprachleitlinie
Fundstelle: README.md:13, 17, 48, 58, 91, 114; docs/screenshots/klienten*.png
Beobachtung: Sprachleitlinie (a224331) verlangt Person/Personen. README selbst — Front-Page des Repos — nutzt 7× „Klientel" plus Screenshot-Dateinamen.
Empfehlung: README + Bildunterschriften umstellen, Screenshots umbenennen oder Aliase setzen.
```

```
[SCHWERE: mittel]
Django-Versions-Drift: 5.1 in 4 Stellen vs. 6.0.4 im CHANGELOG [Unreleased]
Fundstelle: README.md:190, CONTRIBUTING.md:11 (Badge), :226, CLAUDE.md:9, docs/ops-runbook.md:5 vs. CHANGELOG.md:9
Beobachtung: Solange [Unreleased] noch nicht getagt, Doku formal korrekt. Spätestens beim nächsten Release-Tag müssen 4 Stellen + Badge synchron mitziehen.
Empfehlung: Release-Checkliste (`docs/release-checklist.md:42-43`) prüfen, dass Doc-Sync-Block diese Stellen alle enthält.
```

```
[SCHWERE: mittel]
Setup-Anleitung bricht: cd anlaufstelle nach git clone .../app.git
Fundstelle: docs/admin-guide.md:45, CONTRIBUTING.md:51
Beobachtung: Klon erzeugt Verzeichnis `app/`, nicht `anlaufstelle/`. Reproduktionsfehler.
Empfehlung: `cd app` oder `git clone … anlaufstelle`-Variante.
```

```
[SCHWERE: niedrig]
EN-Doku teilweise zwei Minor-Reihen hinter
Fundstelle: CONTRIBUTING.en.md:383-385 (translation-version v0.9.0), docs/en/README.md:49-51 (v0.10.0). Die übrigen docs/en/ sind v0.10.2.
Empfehlung: Beide nachziehen; CI-Check auf translation-version-Header.
```

```
[SCHWERE: niedrig]
DSGVO-Vorlagen außer DSFA ohne Versionsstempel
Fundstelle: docs/dsgvo-templates/{av-vertrag,toms,informationspflichten,verarbeitungsverzeichnis}.md
Empfehlung: Header-Zeile analog zur DSFA (Version + Softwarestand + Datum).
```

```
[SCHWERE: niedrig]
Drei Kern-Themen im ADR-Backlog trotz stabiler Implementierung
Fundstelle: docs/adr/README.md:55-57 (Encrypted File Vault, MFA-Verfahren, Volltextsuche-Backend)
Empfehlung: Drei ADRs nachziehen — Architekturentscheidungen sind real getroffen und im Code stabil, fehlen nur als Dokument.
```

```
[SCHWERE: info]
Doku-Disziplin auf hohem Niveau
Fundstelle: docs/faq.md:519 (Sync-Footer mit Code-Verifikationsdatum), docs/release-checklist.md (Doc-Sync-Block), docs/adr/README.md (akzeptierte ADRs + Backlog), docs/threat-model.md:3 (Header mit Version + Revision)
```

**Was nicht prüfbar:** Aktualitätsbeurteilung der DSGVO-Vorlagen gegen reale Aufsichtspraxis; vollständige Konsistenz aller 51 Markdown-Dateien gegen den Code (Stichproben).

---

## D. Priorisierte Maßnahmenliste

Quick Wins zuerst (S = ≤ 1 PT, M = 1–3 PT, L = 1 Sprint, XL = mehrere Sprints).

### Kurzfrist (Top-10, ≤ 1 Sprint)

| # | Befund | Dim | Aufwand | Impact |
|---|---|---|---|---|
| 1 | Open-Redirect in `views/workitem_actions.py:61` fixen (Helper aus sudo_mode wiederverwenden) | 3 | S | hoch |
| 2 | IP-Spoof in `middleware/maintenance.py:81` fixen (`get_client_ip` aus signals/audit) | 3 | S | hoch |
| 3 | Webhook-URL-Validierung gegen SSRF in `services/breach_detection.py:155` | 3 | S | hoch |
| 4 | Datei-Upload-Default-Whitelist als Fallback bei `Settings.DoesNotExist` | 3 | S | hoch |
| 5 | k-Anonymisierungs-Setting an `enforce_retention` anschließen — oder Setting aus Schema entfernen | 4 | M | hoch |
| 6 | `non_field_errors`-Block in 4 Form-Templates ergänzen | 7 | S | mittel |
| 7 | `tabindex="1/2/100/101"` in `events/create.html` entfernen | 7 | S | mittel |
| 8 | README + Screenshot-Dateinamen auf „Person" umstellen (Sprachleitlinie #604 sichtbar abschließen) | 8/12 | M | mittel |
| 9 | `CACHES`-Backend (Redis) in `prod.py` konfigurieren — Voraussetzung für Multi-Worker | 6 | M | hoch |
| 10 | `scripts/initial-setup.sh` für `setup_facility` + `ALTER ROLE NOSUPERUSER` | 9 | M | hoch |

### Mittelfrist (1–2 Sprints)

| # | Befund | Dim | Aufwand | Impact |
|---|---|---|---|---|
| 11 | Sprachleitlinie #604 vollständig: `verbose_name`, Audit-Action-Labels, 22.po-Einträge, 37 `_("Klient…")`-Strings, 2 Attachments-Templates | 8/11 | M | mittel |
| 12 | en.po fertig übersetzen (30 fuzzy + 65 leere msgstrs) + CI-Check `msgattrib --untranslated --no-fuzzy = 0` | 8 | M | mittel |
| 13 | MFA für Admin-Rolle hardcoded auf Pflicht (`is_mfa_enforced` in `user.py:108-118`) | 3 | S | hoch |
| 14 | SudoMode-Bypass-Setting in `prod.py` mit `ImproperlyConfigured` schützen | 3 | S | mittel |
| 15 | Passwort-Mindestlänge auf 12 in `settings/base.py:127-132` | 3 | S | mittel |
| 16 | `style-src 'self'` enge ziehen (Inline-Styles auflisten und ersetzen) | 3 | M | niedrig |
| 17 | Cron-Service in `docker-compose.prod.yml` (supercronic-Sidecar) für Backup/Retention/Breach-Detection | 9 | M | hoch |
| 18 | Off-Site-Backup-Hook bei wiederholtem Fehler Exit ≠ 0 + Sentry-Capture | 9 | S | mittel |
| 19 | Health-Endpoint um SMTP, Encryption-Key, Backup-Alter, Disk-Frei erweitern | 9 | M | mittel |
| 20 | docker-compose.yml (dev) Postgres an `127.0.0.1` binden + Header-Warnung | 9 | S | niedrig |
| 21 | `docker-compose.prod.yml` Image-Tag parametrisieren (`${APP_VERSION:-…}`) | 9 | S | mittel |
| 22 | Caddyfile: www-Redirect + Access-Log + Rate-Limit-Hinweis | 9 | M | mittel |
| 23 | `.pre-commit-config.yaml` mit ruff/mypy/makemigrations | 2 | S | mittel |
| 24 | Ruff-Regeln um `B`, `UP`, `SIM`, `N`, `S` erweitern (per-file zunächst) | 2 | M | mittel |
| 25 | `--cov-fail-under=N` in `test.yml` setzen | 5 | S | niedrig |
| 26 | Query-Count-Tests auf ClientDetail/EventDetail/CaseDetail/Handover ausweiten | 5/6 | M | mittel |
| 27 | WorkItem-Inbox + Search paginieren | 6 | M | mittel |
| 28 | DRY-Refactor der vier Retention-Strategien (`_strategy_querysets`) | 2 | M | mittel |
| 29 | `Client.anonymize` in Service-Layer ziehen | 1 | M | mittel |
| 30 | `reencrypt_fields` um EventHistory + EventAttachment erweitern | 4 | M | mittel |
| 31 | Tag-Signing in Release-Checkliste verbindlich; `git tag -s` | 10 | S | mittel |
| 32 | DCO-Sign-off in CONTRIBUTING + PR-Body-Pflicht | 10 | S | hoch |
| 33 | Code of Conduct (Contributor Covenant 2.1) | 10 | S | mittel |

### Langfrist (≥ 3 Sprints, strukturell)

| # | Befund | Dim | Aufwand | Impact |
|---|---|---|---|---|
| 34 | DDD-Bounded-Context-Schnitt vor Plugin-Architektur — App-Aufteilung | 1 | XL | hoch |
| 35 | Generisches `RecordHistory`-Modell für Case/Episode/Goal/WorkItem (Append-only) | 1/4 | L | mittel |
| 36 | Soft-Delete-Mixin oder ADR „warum nur Event soft-deletet" | 1 | M | mittel |
| 37 | Alias-Modell für mehrere Spitznamen pro Person | 11 | L | mittel |
| 38 | Pseudonym-Hashing-Strategie (HMAC-SHA256) prüfen — Trade-off gegen Trigram-Suche | 3/4 | L | hoch |
| 39 | factory_boy für Top-Cluster (Event/Client/WorkItem/Case) | 5 | L | niedrig |
| 40 | Hypothesis für Datums-/Field-Template-/k-Anon-Validatoren | 5 | L | niedrig |
| 41 | Lizenz-Inventur via `pip-licenses --format=json` als CI-Step + Allowlist | 10 | M | mittel |
| 42 | Migrations als separater One-Shot-Job (Zero-Downtime) | 9 | L | mittel |
| 43 | AuditLog-Pruning per `SECURITY DEFINER`-Funktion statt DISABLE TRIGGER (SIGKILL-Schutz) | 4 | M | mittel |
| 44 | DSGVO-Paket mit Versions-/Settings-Hash-Stempel | 4 | S | mittel |
| 45 | Drei ADRs nachziehen (File Vault, MFA, Search) | 12 | M | mittel |
| 46 | Last-Test-Schicht (Locust-Skript, Nightly mit Performance-Budgets) | 5/6 | M | niedrig |

---

## E. Offene Fragen

Diese Punkte konnte ich aus dem Repo allein nicht beantworten — sie brauchen Maintainer-Input:

1. **RLS-Verhalten unter Last:** Ist die NOSUPERUSER-Rolle in echten Coolify-Deployments aktiv, oder hat sich der Schritt schon einmal als vergessen erwiesen?
2. **K-Anonymisierung — Strategie-Entscheidung:** Soll `retention_use_k_anonymization` ans `enforce_retention` angeschlossen werden (Breaking Change im DSGVO-Workflow), oder ist k-Anon bewusst manuell, und das Setting wandert weg?
3. **Pseudonym-Klartext:** Trade-off bewusst akzeptiert (Trigram-Suche pro Facility ⇒ niedriges Real-Risiko), oder wird ein Hash-/Encrypted-Pseudonym mit reduzierter Suchfunktion erwogen?
4. **Cron im Compose:** Ist der Verzicht eine bewusste Entscheidung (Operator-Transparenz), oder fehlt nur das Sidecar?
5. **Bus-Factor:** Ist eine Co-Maintainership oder Eskalations-Klausel mit/Träger geplant? `kontakt@anlaufstelle.app` als Eskalation reicht aus?
6. **Django 6.0-Migration:** Ist das im aktuellen Sprint, oder dauert `[Unreleased]` noch — mit Konsequenz für die vier Doku-Stellen?
7. **-Embargo M3 (WCAG):** Welche A11y-Punkte aus Dim 7 dürfen vor M3 gefixt werden (Bug-Fixes wie Tabindex), welche sind „M3 only" (systematisches Audit)?
8. **HTMX OOB-Patterns:** Gibt es einen bewussten Verzicht oder ist das im Backlog?

---

## F. Was bewusst NICHT bewertet wurde

- **Frontend-Performance live:** Ohne reales Deployment keine Latenz-/Time-to-Interactive-Aussage. Statische Code-Analyse hat Grenzen.
- **Suchqualität bei realen Daten:** Trigram-Threshold 0.3 ist plausibel, aber ohne realen Datensatz (Pseudonyme, Tippfehler-Realität) keine harte Aussage.
- **Real-User-A11y mit Screen-Readern:** NVDA/VoiceOver/TalkBack nicht ausgeführt. Kontrastwerte mit axe-core nicht gemessen. M3-Sperre beachtet.
- **Sentry-PII-Scrubber gegen reale Stack-Traces:** Konfiguration vorhanden, Ergebnis nicht verifiziert.
- **Lasttest-Profile:** Kein Tooling im Repo, keine Aussage zu Antwortzeiten unter Last möglich.
- **CI-Lauf-Dauer und -Stabilität:** GitHub-API nicht konsultiert.
- **3-Repo-Pipeline (dev/stage/app):** Nur das public app-Mirror lokal verfügbar; Sync-Skripte und Workflow-Drift nicht beurteilbar.
- **Tag-Signaturen jenseits v0.10.2:** Verifikation der Signatur selbst (gegen welche Identität?) nur lokal über `git tag -v` möglich; Web-of-Trust-Verifikation nicht durchgeführt.
- **Dependency-CVE-Status:** pip-audit läuft in CI, lokal nicht ausgeführt.
- **-Embargo-Wirksamkeit:** Nur indirekt aus CLAUDE.md/MEMORY.md beobachtbar; keine Aussage zu real abgewiesenen PRs.
- **Volle Migration-Plan-Linearität:** `migrate --plan` nicht ausgeführt; statisch über Datei-Header argumentiert.
- **Inhalt der ~30 Migrationen seit 0050:** Nur Dateinamen gesichtet, nicht Zeile-für-Zeile.

---

*Audit-Ende. Erzeugt am 2026-04-30 durch Claude (Opus 4.7) im parallelen Multi-Agent-Modus über 12 Dimensionen. Belege im Code verifiziert, nicht aus Vor-Audits paraphrasiert.*
