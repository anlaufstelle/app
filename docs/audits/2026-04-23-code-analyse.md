# Code-Analyse — Rundumschlag mit Schwerpunkt Tests

**Datum:** 2026-04-23
**Stand:** main @
**Umfang:** Gesamtrepo ohne `node_modules/`, `.venv/`, `htmlcov/`
**Methodik-Spec:** [docs/superpowers/specs/2026-04-23-code-analyse-design.md](../../docs/superpowers/specs/2026-04-23-code-analyse-design.md) (lokal, nicht committet)

---

## 1. Executive Summary

Das Repository ist in **überdurchschnittlich gutem Zustand**. 92 % Coverage auf 5.751 Statements, 1.512 passing Unit-/Integrationstests, 44 E2E-Test-Dateien, saubere Architektur-Guards, keine kritischen Security-Findings, keine `networkidle`-Waits, kein Inline-`<script>`-Block. Die Mehrheit der Findings sind **Verbesserungen**, keine Fehler.

### Health-Ampel pro Kategorie

| Kategorie | Ampel | Kurzbegründung |
|-----------|:-----:|----------------|
| **A — Architektur** | 🟢 | Import-Richtung sauber, Mixins konsistent, 70 % View-Service-Delegation. 3 große Hot-Spots (seed.py, enforce_retention.py, events.py) |
| **B — Security & DSGVO** | 🟢 | RLS vollständig, kein Secret-Leak, Lockout + MFA + Backup-Codes, bandit 0 High. Zwei kleine Beobachtungen (CSP-Source-Drift, keine explizite Referrer-Policy in Django) |
| **C — Performance & DB** | 🟡 | Gute Grundpatterns (`select_related` überwiegend da, Pagination in Lists), aber mehrere Indexe fehlen (`Case.status`, `WorkItem.status`, `AuditLog.timestamp`), `\|length` auf Querysets triggert Count-Queries |
| **D — Tests & QA** (Schwerpunkt) | 🟢 | 91.8 % Coverage, `domcontentloaded` durchgängig, Architektur-Guards greifen. 34 von 87 URL-Namen per `goto`/`reverse`-Heuristik getroffen — Rest teilweise nur via Klicks getestet (systematische Meß-Lücke, siehe Caveat in Abschnitt 6.3) |
| **E — Technical Debt** | 🟢 | 0 `TODO`/`FIXME`, keine ungenutzten URLs, keine orphan Views, keine toten Dependencies, 1 deprecatedes Setting (`USE_L10N`) |
| **F — Infra & Frontend** | 🟡 | Multi-Stage Docker, Non-Root, Caddy+HSTS, CI parallel — aber: kein `HEALTHCHECK` im Dockerfile, kein Playwright-Cache in CI, kein Multi-Arch-Build |

### Top-5 übergreifende Findings

1. **[]** Fehlende Indexe auf häufig gefilterten/sortierten Feldern (`Case.status`, `WorkItem.status`, `AuditLog.timestamp`/`action`/`user`, `Event.is_deleted`) — merkt man erst bei Last.
2. **[]** E2E-Mapping zeigt: 53 von 87 URL-Namen haben 0 Treffer in der `page.goto`/`reverse`-Heuristik — viele Update-/Bulk-/Export-Routen laufen nur über Klicks ins Ziel. Messung ist per Definition unvollständig, Lücken sind aber klar.
3. **[]** Drei Dateien sind außerhalb des Normalbereichs gewachsen: `seed.py` (1950 LOC), `enforce_retention.py` (639), `views/events.py` (615). `events.py` enthält 4 Methoden > 70 LOC.
4. **[]** CSP-Header wird in **Prod via Django-Middleware** gesetzt, in **Staging zusätzlich via Caddyfile** — beide Quellen können divergieren und niemand bemerkt es bis zum nächsten CSP-Refactor.
5. **[]** `\|length` auf Querysets in `deletion_requests/list.html` und `workitems/inbox_content.html` triggert zusätzliche COUNT-Queries bei jedem Page-Load.

### Quick Wins (XS / S-Aufwand, sofort umsetzbar)

- [] `USE_L10N = True` aus `base.py` entfernen (deprecated seit Django 4.0) — XS
- [] `HEALTHCHECK` in Dockerfile einfügen — XS
- [] `SECURE_REFERRER_POLICY` explizit in `prod.py` setzen — XS
- [] Doppelten CSP-Header in `Caddyfile.staging` entfernen — XS
- [] Playwright-Cache in E2E-CI einrichten — XS
- [] `db_index=True` auf `Case.status`, `WorkItem.status`, `Event.is_deleted`, `AuditLog.timestamp` — S (Migration + Test)
- [] Inline-AuditLog-Erstellung in `views/clients.py` in Service-Helper extrahieren — S

### Kennzahlen

| Metrik | Wert |
|--------|-----:|
| Python-Statements (gemessen) | 5.751 |
| Covered Lines | 5.281 (91.8 %) |
| Missing Lines | 470 |
| Unit-/Integrationstests (passed) | 1.512 |
| E2E-Test-Dateien | 44 |
| URL-Routen (named) | 87 |
| URL-Namen mit mindestens 1 E2E-`page.goto`/`reverse`-Treffer (Heuristik) | 34 (39 %) |
| Distinkte URL-Strings per `page.goto` getroffen | 55 |
| CBVs in `src/core/views/` | ~88 |
| Models | 40+ |
| Migrationen | 69 |
| Templates (`*.html`) | 84 |
| Inline-`<script>`-Blöcke | 0 |
| bandit High | 0 |
| bandit Medium (alle False Positives: `B608` auf Konstanten-SQL) | 10 |
| `networkidle`-Waits | 0 |

---

## 2. Methodik & Scope

**Durchgeführt:**

- **Coverage-Lauf:** `pytest --cov=core --cov-report=json -m "not e2e"` (6:52 min, 1512 passed)
- **Statische Analyse per Grep/Read:** LOC, Methodenlängen, Import-Muster, Mixin-Usage, Pagination, `select_related`, Indexe, URL-Inventur
- **Parallele Sammelagents** für Kategorien A, B, C, E, F (je ein Explore-Subagent)
- **bandit -r src/** temporär installiert, Medium+High-Reports
- **vulture --min-confidence 70** temporär installiert
- **E2E-Flow-Mapping** durch Parsing von `page.goto(...)` und `reverse(...)` in `src/tests/e2e/`
- **Stichprobenhafte Test-Qualitätsaudits**: `test_account.py`, `test_auth.py`, `test_events.py`, `test_pwa_offline.py`, `test_bugfixes_v1.py`, `test_architecture.py`, `conftest.py`, `e2e/conftest.py`

**Bewusst ausgeklammert:**

- **Kein Performance-Benchmark** (statische Analyse, keine Lastmessung)
- **Kein E2E-Lauf** (Tests werden gelesen, nicht ausgeführt)
- **Keine Migrations-Inhaltsprüfung** (nur Struktur/Größe)
- **Keine vollständige `.vulture`-Auswertung** (Confidence < 70 ignoriert)
- **E2E-Flow-Mapping misst nur `page.goto`/`reverse`** — Routen, die nur per `page.click` getroffen werden, erscheinen fälschlich als „unbedeckt". Das ist eine systematische Messlücke und bei den Findings entsprechend kommentiert.

**Finding-Schema** siehe Design-Spec (lokal). Severity: Critical/High/Medium/Low/Info. Aufwand: XS (<1h) / S (1–4h) / M (½–1 Tag) / L (1–3 Tage) / XL (>3 Tage).

---

## 3. Kategorie A — Architektur & Code-Gesundheit

### LOC-Verteilung

| Datei | LOC | Bemerkung |
|-------|----:|-----------|
| `src/core/management/commands/seed.py` | 1.950 | Management-Command mit Seed-Daten, großes Ausmaß erwartbar |
| `src/core/management/commands/enforce_retention.py` | 639 | DSGVO-Retention-Command, komplex |
| `src/core/views/events.py` | 615 | Größte reguläre View-Datei |
| `src/core/admin.py` | 467 | Django-Admin-Konfiguration |
| `src/core/services/snapshot.py` | 340 | Statistik-Snapshot-Service |
| `src/core/services/retention.py` | 333 | |
| `src/core/views/retention.py` | 321 | |
| `src/core/services/workitems.py` | 306 | |

### — `src/core/views/events.py` ist der größte Hot-Spot im regulären Code

- **Severity:** Medium
- **Kategorie:** A
- **Ort:** [src/core/views/events.py:141-586](https://github.com/anlaufstelle/app/blob/main/src/core/views/events.py#L141-L586)
- **Kurzfassung:** 615 LOC mit 4 Methoden > 70 LOC (`EventCreateView.get` 74, `.post` 103, `EventDetailView.get` 91, `EventUpdateView.post` 103). Inline-Logik zum Aufbau von `field_display`-Strukturen, `field_templates`-Lookups (5× wiederholt).
- **Evidenz:** wiederholtes Pattern `{dtf.field_template.slug: dtf.field_template for dtf in doc_type.fields.select_related("field_template")}` in Zeilen 75–78, 124–126, 293–296, 349–350, 545–547.
- **Warum es relevant ist:** Zentrale Event-Logik ist auf 4 Methoden verteilt, inline-gebaute Datenstrukturen sind schwer testbar und schwer wiederzuverwenden. Nicht akut kaputt, aber wächst weiter.
- **Fix-Skizze:**
 1. `field_template_lookup(doc_type)` in `core/services/event.py` oder `core/utils/events.py` extrahieren
 2. `_filtered_server_data_json` und `_remove_restricted_fields` in Service verschieben
 3. EventDetailView-Datenaufbau (`field_display`, `prior_versions`-Chain) in `core/services/event.py::build_event_detail_context` kapseln
- **Aufwand:** M
- **Quer-Referenzen:** [], []

### — `AuditLog.objects.create(...)` wird inline 3× in `views/clients.py` kopiert

- **Severity:** Medium
- **Kategorie:** A
- **Ort:** [src/core/views/clients.py:108-114, 243-251, 270-278](https://github.com/anlaufstelle/app/blob/main/src/core/views/clients.py#L108-L114)
- **Kurzfassung:** Drei fast identische Blöcke mit `facility`, `user`, `action`, `target_type`, `target_id`, `detail`, `ip_address`.
- **Evidenz:** alle drei Blöcke folgen dem Pattern `AuditLog.objects.create(facility=..., user=request.user, action=..., target_type=..., target_id=str(...), detail={...}, ip_address=get_client_ip(request))`.
- **Warum es relevant ist:** Eine Änderung am Audit-Schema (z.B. neue Pflichtspalte) muss an drei Stellen nachgezogen werden; das Muster wird beim nächsten View-Add weiter kopiert werden.
- **Fix-Skizze:**
 1. `core/services/audit.py::log_view(request, action, target_obj, **detail)` anlegen
 2. Die 3 Inline-Calls durch den Service-Call ersetzen
 3. Unit-Test für den neuen Service-Helper
- **Aufwand:** S
- **Quer-Referenzen:**

### — `EventDetailView.get` und `RetentionDashboardView.get` bauen Datenstrukturen inline

- **Severity:** Medium
- **Kategorie:** A
- **Ort:** [src/core/views/events.py:339-429](https://github.com/anlaufstelle/app/blob/main/src/core/views/events.py#L339-L429), [src/core/views/retention.py:33-124](https://github.com/anlaufstelle/app/blob/main/src/core/views/retention.py#L33-L124)
- **Kurzfassung:** View-Methoden erzeugen komplexe Context-Strukturen (`field_display`, `prior_versions`-Chain bzw. `proposals_by_category`, `holds_by_target`, `urgency`-Berechnung) direkt in der View. Das widerspricht der CLAUDE.md-Regel „Business-Logik gehört in Services, nicht in Views".
- **Evidenz:** siehe oben; beide Methoden sind 90+ LOC.
- **Warum es relevant ist:** Business-Logik-Tests müssen über die View-Layer (mit Auth, Middleware, Template-Render) gehen; reine Logik-Tests auf Service-Layer wären billiger und schärfer.
- **Fix-Skizze:** je Methode: Context-Builder als pure function in `services/` ziehen, View ruft nur noch `context["X"] = build_X(...)`.
- **Aufwand:** M (pro View, sequenziell)
- **Quer-Referenzen:** []

### — `seed.py` mit 1.950 LOC ist zu groß für einen einzigen Command

- **Severity:** Low
- **Kategorie:** A
- **Ort:** [src/core/management/commands/seed.py](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/seed.py)
- **Kurzfassung:** Ein Django-Command mit fast 2.000 Zeilen — vermutlich linear aufgebaut (Fixtures für Organisation, Facility, Users, Clients, Cases, Events, WorkItems, Templates, …).
- **Evidenz:** siehe LOC-Tabelle oben.
- **Warum es relevant ist:** Schwer zu reviewen, schwer zu erweitern. Neue Seed-Daten landen blind hinten dran; das File-Wachstum verstärkt sich selbst.
- **Fix-Skizze:**
 - `core/management/commands/seed.py` bleibt als Einstieg
 - Seed-Blöcke pro Domäne in `core/seed/` (z.B. `seed/users.py`, `seed/clients.py`, `seed/cases.py`) auslagern
 - Commander ruft die Sub-Module sequenziell auf
- **Aufwand:** L
- **Quer-Referenzen:**

### — `enforce_retention.py` (639 LOC) ist ein zweiter Command-Hot-Spot

- **Severity:** Low
- **Kategorie:** A
- **Ort:** [src/core/management/commands/enforce_retention.py](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/enforce_retention.py)
- **Kurzfassung:** Retention-Command mit 249 Statements und 40 Missing Lines (84 % Coverage, verglichen mit >92 % im Rest).
- **Evidenz:** Coverage: 84 %. Laut Agent-A: Klasse `Command` mit 613 LOC.
- **Warum es relevant ist:** Retention ist DSGVO-kritisch — die 40 Missing Lines sollten mindestens bewusst „opted out" sein, nicht zufällig nicht getestet.
- **Fix-Skizze:** Retention-Policy-Anwendung in pure Functions in `core/services/retention.py` ziehen (ist teilweise schon dort), Command ist nur noch CLI-Wrapper. Dann reichen kleine Command-Tests und die dicken Logik-Tests laufen über Services.
- **Aufwand:** M
- **Quer-Referenzen:** []

### — Großzügige Service-Methoden > 100 LOC

- **Severity:** Low
- **Kategorie:** A
- **Ort:** Mehrere Services:
 - [services/handover.py::build_handover_summary](https://github.com/anlaufstelle/app/blob/main/src/core/services/handover.py) 138 LOC
 - [services/client_export.py::export_client_data](https://github.com/anlaufstelle/app/blob/main/src/core/services/client_export.py) 116 LOC
 - [services/export.py::export_events_csv](https://github.com/anlaufstelle/app/blob/main/src/core/services/export.py) 109 LOC
 - [services/event.py::create_event](https://github.com/anlaufstelle/app/blob/main/src/core/services/event.py) 103 LOC
- **Kurzfassung:** Einzelne Service-Methoden bauen lineare Pipelines aus Vorbereitung + ORM-Zugriff + Transformation + Serialisierung. Nichts davon ist falsch, aber > 100 LOC in einer Methode ist ein Signal, sie aufzubrechen.
- **Fix-Skizze:** Schrittweise Extraktion (build-→transform-→serialize) in privaten `_`-Helfern innerhalb desselben Moduls.
- **Aufwand:** M (pro Methode)
- **Quer-Referenzen:**

### — Import-Zyklen: keine gefunden

- **Severity:** Info
- **Ort:** Gesamtes `src/core/`
- **Beobachtung:** Grep nach `from core.views import` in `models/` und `services/` liefert **keine Treffer**. Import-Richtung ist sauber (views → services → models).
- **Relevanz:** Positiver Fund — kein Handlungsbedarf, zur Kenntnis.

### — Mixin-Audit: 88 CBVs, 13 ohne direkten Rollen-Mixin

- **Severity:** Info
- **Ort:** `src/core/views/*.py`
- **Beobachtung:** Von 13 „mixin-freien" CBVs sind 7 absichtlich öffentlich (Login/Logout/Password-Reset/Health/SW/Manifest) und 6 erben Schutz via Parent-Mixin (`_BulkActionMixin`). **Keine echte Lücke.**
- **Relevanz:** Positiver Fund. Ein kleiner Hinweis: es wäre hilfreich, in `_BulkActionMixin` einen Kommentar zu setzen, dass das Parent-Mixin den Schutz setzt — spart beim Audit Zeit.

---

## 4. Kategorie B — Security & DSGVO

### — `SECURE_REFERRER_POLICY` nicht explizit gesetzt

- **Severity:** Low
- **Kategorie:** B
- **Ort:** [src/anlaufstelle/settings/prod.py](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/prod.py)
- **Kurzfassung:** Django-Default ist `"same-origin"` (seit Django 3.1), aber im Caddyfile ist `Referrer-Policy: strict-origin-when-cross-origin` gesetzt. Zwei Quellen mit unterschiedlichen Werten → Caddy gewinnt in Prod, aber das ist Zufall.
- **Evidenz:** `prod.py` enthält `SECURE_HSTS_*`, `SESSION_COOKIE_*`, `CSRF_COOKIE_*`, `SECURE_SSL_REDIRECT` — aber kein `SECURE_REFERRER_POLICY`.
- **Warum es relevant ist:** Defense-in-Depth — wenn das Caddy-Layer wegfällt (lokale Dev-Prod-Simulation, andere Deployment-Umgebung) ist das Header-Set unvollständig.
- **Fix-Skizze:** `SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"` in `prod.py` ergänzen.
- **Aufwand:** XS
- **Quer-Referenzen:** []

### — CSP-Header wird doppelt gesetzt (Django + Caddyfile.staging)

- **Severity:** Low
- **Kategorie:** B
- **Ort:** [src/anlaufstelle/settings/base.py:237-247](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/base.py#L237), [Caddyfile.staging](https://github.com/anlaufstelle/app/blob/main/Caddyfile.staging)
- **Kurzfassung:** In **Prod** setzt Django-CSP den Header (Caddy reicht ihn durch). In **Staging** setzt zusätzlich Caddy einen CSP-Header. Doppelte Header sind spezifikations-mäßig nicht verboten, aber eine Header-Divergenz fällt nicht auf.
- **Evidenz:** Agent-F-Report: „Prod vs. Staging: CSP-Header-Location unterschiedlich (Caddy vs. Django) — potenzielle Inkonsistenz".
- **Fix-Skizze:** Staging-CSP-Block aus `Caddyfile.staging` entfernen; Django-CSP-Middleware setzt den Header für beide Umgebungen.
- **Aufwand:** XS
- **Quer-Referenzen:** []

### — `SECRET_KEY` in `dev.py` ist hardcoded, aber markiert

- **Severity:** Info
- **Kategorie:** B
- **Ort:** [src/anlaufstelle/settings/dev.py:9](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/dev.py#L9)
- **Kurzfassung:** `SECRET_KEY = "django-insecure-dev-only-change-in-production" # noqa: S105`
- **Beobachtung:** Absichtlich und sauber dokumentiert. `prod.py` zieht den Wert aus `DJANGO_SECRET_KEY` (Env-Var). bandit gibt hier korrekt kein Warning aus (`# noqa: S105`).
- **Relevanz:** Positiver Fund (zur Kenntnis).

### — Logging von `username` und `email` in `services/invite.py`

- **Severity:** Low
- **Kategorie:** B
- **Ort:** `src/core/services/invite.py`
- **Kurzfassung:** `logger.info("Invite-Mail an %s (%s) versendet", user.username, user.email)` — personenbezogene Daten landen im Log.
- **Warum es relevant ist:** DSGVO Art. 5(1)(c) Datenminimierung. Prod-Log-Level ist `INFO`, das `JsonFormatter` hat laut `prod.py` einen PII-Scrubber — aber ob der `%s`-formatierte Text aus dem Message-Template erreicht wird, ist nicht trivial.
- **Fix-Skizze:**
 1. Prüfen, ob der PII-Scrubber auf `record.msg` / `record.args` greift oder nur auf `record.extra`
 2. Username/E-Mail in `extra={"user_pk": user.pk}` setzen, Message-String nur Pseudonym enthalten
- **Aufwand:** S
- **Quer-Referenzen:**

### — bandit-Lauf: 0 High, 10 Medium (alle B608 False-Positives)

- **Severity:** Info
- **Kategorie:** B
- **Ort:** bandit-Output
- **Beobachtung:** 10 Medium-Findings sind alle B608 (hardcoded SQL expressions) in:
 - `migrations/0049_statistics_event_flat_mv.py` (CREATE MATERIALIZED VIEW, Tabellenname konstant)
 - `services/statistics.py:138` (Tabellenname konstant, Werte parameterisiert)
 - `e2e/conftest.py:44` (Postgres-Connect-String in Subprocess)
 - 7 Treffer in `test_statistics_mv.py` (Test-SQL mit parameterisierter facility_id)
- **Relevanz:** Positiver Fund — keine echten SQL-Injection-Risiken. Zur Kenntnis, keine Aktion.

### — RLS-Audit: vollständig

- **Severity:** Info
- **Kategorie:** B
- **Ort:** [src/tests/test_rls.py](https://github.com/anlaufstelle/app/blob/main/src/tests/test_rls.py), [src/core/migrations/0047_postgres_rls_setup.py](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0047_postgres_rls_setup.py)
- **Beobachtung:** 23 Tabellen in `EXPECTED_TABLES`, alle decken die facility-scoped Models ab (direkt oder transitiv via Cascade-FK: EventHistory, EventAttachment, Episode, OutcomeGoal, Milestone, DocumentTypeField). Migrations 0057 (QuickTemplate) und 0063 (Outcome/Milestone/DocumentTypeField) ziehen Policies sauber nach.
- **Relevanz:** Positiver Fund. Kein Handlungsbedarf.

### — Account-Lockout & MFA-Backup-Codes frisch integriert und getestet

- **Severity:** Info
- **Kategorie:** B
- **Ort:** [src/core/services/login_lockout.py](https://github.com/anlaufstelle/app/blob/main/src/core/services/login_lockout.py), [src/core/services/mfa.py](https://github.com/anlaufstelle/app/blob/main/src/core/services/mfa.py)
- **Beobachtung:** Lockout (Commit): 10 Fehlversuche, 15-min-Fenster, Admin-Unlock, 7 Unit-Tests + 2 Integrations + 2 E2E. MFA-Backup-Codes (Commit): 10 One-Time-Codes, TOTP-gated Regenerate, 14 Unit + 3 E2E.
- **Relevanz:** Positiver Fund — frisch und hochwertig implementiert.

### — Facility-Scoping-Guard existiert als Architektur-Test

- **Severity:** Info
- **Kategorie:** B
- **Ort:** [src/tests/test_architecture.py:10-24](https://github.com/anlaufstelle/app/blob/main/src/tests/test_architecture.py#L10)
- **Beobachtung:** `TestFacilityScopingGuard.test_no_unfiltered_objects_all_in_views` verbietet `.objects.all` in Views. Ergänzt durch `TestEventAccessPolicyGuard` (verbietet direktes `get_object_or_404(Event, …)`).
- **Relevanz:** Positiver Fund, sehr starke Guards.

---

## 5. Kategorie C — Performance & DB

### — Fehlende DB-Indexe auf häufig gefilterten Feldern

- **Severity:** Medium
- **Kategorie:** C
- **Ort:** Mehrere Models:
 - `Case.status`, `Case.created_at` → `src/core/models/case.py`
 - `WorkItem.status`, `WorkItem.created_at` → `src/core/models/workitem.py`
 - `Event.is_deleted` → `src/core/models/event.py`
 - `AuditLog.timestamp`, `action`, `user` → `src/core/models/audit.py`
 - `Client.created_at`, (facility+is_active) → `src/core/models/client.py`
 - `QuickTemplate.is_active` → `src/core/models/quick_template.py`
- **Kurzfassung:** Diese Felder werden in `filter` und `order_by` verwendet, ohne Index. Solange die Tabellen klein sind, unsichtbar; bei wachsenden Datenmengen wird es spürbar (AuditLog wächst append-only).
- **Evidenz:** Agent-C-Report mit Feldauflistung; AuditLogListView (`audit.py:48`) filtert per `timestamp__date__gte`, CaseListView sortiert `-created_at`.
- **Warum es relevant ist:** AuditLog ist der Kandidat, der als erstes schmerzt — audit-log-Listen mit Date-Range-Filter führen Full-Table-Scans ohne Index.
- **Fix-Skizze:**
 1. Je Model: `db_index=True` oder `Meta.indexes` ergänzen
 2. Migration mit `CREATE INDEX CONCURRENTLY` (Postgres) — keine Tabellen-Lock
 3. Priorität: AuditLog.timestamp zuerst, dann WorkItem.status, dann Rest
- **Aufwand:** S (pro Model, + 1 Migration)
- **Quer-Referenzen:**

### — `WorkItemInboxView` ohne Pagination auf 3 parallelen Listen

- **Severity:** Medium
- **Kategorie:** C
- **Ort:** [src/core/views/workitems.py:87-126](https://github.com/anlaufstelle/app/blob/main/src/core/views/workitems.py#L87)
- **Kurzfassung:** `open_items`, `in_progress_items`, `done_items` werden gefiltert, aber nicht paginiert. In einer großen Facility mit hunderten offener Aufgaben wird die Inbox langsam.
- **Evidenz:** Keine `.paginate`/`Paginator`-Nutzung sichtbar. Template: 3 `{% for item in... %}` Loops.
- **Fix-Skizze:**
 1. Pro Liste auf z.B. 50 Items begrenzen (ähnlich `AuditLogListView`)
 2. „Weitere anzeigen"-Link per HTMX, wenn Begrenzung greift
 3. Oder Tabs pro Status mit eigener Pagination
- **Aufwand:** M
- **Quer-Referenzen:**

### — `|length` auf Querysets in Templates triggert COUNT-Queries

- **Severity:** Medium
- **Kategorie:** C
- **Ort:** [src/templates/core/deletion_requests/list.html](https://github.com/anlaufstelle/app/blob/main/src/templates/core/deletion_requests/list.html), [src/templates/core/workitems/partials/inbox_content.html](https://github.com/anlaufstelle/app/blob/main/src/templates/core/workitems/partials/inbox_content.html)
- **Kurzfassung:** `{{ pending_requests|length }}`, `{{ open_items|length }}` etc. auf ungeslicten Querysets. Django's `|length`-Filter auf einem Queryset ruft `len(qs)`, was je nach Evaluation-Stand entweder eine zusätzliche COUNT-Query auslöst oder das gesamte Queryset lädt.
- **Evidenz:** Agent-C-Report, Zeilen 11/37/60 (deletion_requests) und 5/19/33 (inbox_content).
- **Fix-Skizze:**
 1. Entweder `{{ pending_count }}` aus dem Context-Builder (View ruft `.count` einmal)
 2. Oder Queryset-Evaluation mit `list(qs)` in der View (cached)
- **Aufwand:** S
- **Quer-Referenzen:**

### — `ClientDetailView` lädt WorkItems ohne `.select_related("client")` für Template-Zugriff

- **Severity:** Low
- **Kategorie:** C
- **Ort:** [src/core/views/clients.py:78-141](https://github.com/anlaufstelle/app/blob/main/src/core/views/clients.py#L78)
- **Kurzfassung:** Im WorkItem-Block wird das Queryset ohne `.select_related` geladen, Template greift dann auf `workitem.client.pseudonym` zu — jeder Render-Iteration ist eine Query.
- **Fix-Skizze:** `.select_related("client", "assigned_to")` auf das WorkItem-Queryset im View-Aufbau.
- **Aufwand:** XS
- **Quer-Referenzen:**

### — `RecentClientVisit.objects.filter(user=user).order_by(...)` ohne Slice

- **Severity:** Low
- **Kategorie:** C
- **Ort:** [src/core/views/clients.py:150-153](https://github.com/anlaufstelle/app/blob/main/src/core/views/clients.py#L150)
- **Kurzfassung:** Queryset wird vor `.exclude` vollständig evaluiert, wenn die Visit-Liste groß wird.
- **Fix-Skizze:** Entweder `[:N]` nach `.order_by` einfügen oder auf dem Model ein `.recent_for(user, limit=N)` als Manager-Method.
- **Aufwand:** XS
- **Quer-Referenzen:**

### — Doppel-Rendering von Client-Liste (Desktop + Mobile)

- **Severity:** Low
- **Kategorie:** C
- **Ort:** [src/templates/core/clients/partials/table.html](https://github.com/anlaufstelle/app/blob/main/src/templates/core/clients/partials/table.html)
- **Kurzfassung:** Tabelle (Desktop) und Cards (Mobile) werden beide im selben Template ausgegeben, sichtbar via CSS-Hide/Show. Loop wird 2× durchlaufen.
- **Evidenz:** Agent-C-Report.
- **Warum es relevant ist:** Pro Seite mit 25 Clients werden 50 Render-Iterationen gemacht. Schmerzt nicht, ist aber unnötig.
- **Fix-Skizze:** Ein einzelner Loop mit responsive Inner-Markup (CSS `flex`/`grid`). Alternativ: separate Partials per `srcset`-Analogon (HTMX: server liefert variant basierend auf Viewport-Header — aufwendiger).
- **Aufwand:** M
- **Quer-Referenzen:**

### — AttachmentListView iteriert 200 Items mit Service-Calls pro Item

- **Severity:** Low
- **Kategorie:** C
- **Ort:** [src/core/views/attachments.py:57-99](https://github.com/anlaufstelle/app/blob/main/src/core/views/attachments.py#L57)
- **Kurzfassung:** Manueller Loop `for a in attachments[:200]` mit Service-Call `get_original_filename(a)` pro Item.
- **Fix-Skizze:** `get_original_filenames_bulk(attachments)` als Service, der das Dekryptieren und Formatieren batchweise macht.
- **Aufwand:** S
- **Quer-Referenzen:**

### — Positive: Feed-Building ist Service-abstrahiert

- **Severity:** Info
- **Ort:** [src/core/services/feed.py](https://github.com/anlaufstelle/app/blob/main/src/core/services/feed.py)
- **Beobachtung:** `build_feed_items` mit `.select_related` pro Typ, Subquery-Exclusion statt In-Python-Filter, Cache für `DocumentTypeField.sensitivity`. Sauber.
- **Relevanz:** Positiver Fund, Referenz-Pattern.

---

## 6. Kategorie D — Tests & QA (SCHWERPUNKT)

### 6.1 Coverage-Matrix (Modul × %)

Mess-Basis: `pytest --cov=core -m "not e2e"` auf main@. Gesamt: **91.8 % (5.281 / 5.751 Statements)**.

| Modul | Stmts | Miss | Files | Coverage | Ampel |
|-------|------:|-----:|------:|---------:|:-----:|
| `apps.py` | 8 | 0 | 1 | 100 % | 🟢 |
| `constants.py` | 2 | 0 | 1 | 100 % | 🟢 |
| `logging.py` | 21 | 0 | 1 | 100 % | 🟢 |
| `middleware/` | 73 | 0 | 6 | 100 % | 🟢 |
| `urls.py` | 23 | 0 | 1 | 100 % | 🟢 |
| `models/` | 840 | 25 | 21 | 97.0 % | 🟢 |
| `services/` | 1.746 | 87 | 32 | 95.0 % | 🟢 |
| `context_processors.py` | 17 | 1 | 1 | 94.1 % | 🟢 |
| `forms/` | 196 | 13 | 6 | 93.4 % | 🟢 |
| `views/` | 1.833 | 134 | 25 | 92.7 % | 🟢 |
| `signals/` | 98 | 9 | 3 | 90.8 % | 🟢 |
| `templatetags/` | 153 | 26 | 4 | 83.0 % | 🟡 |
| `utils/` | 112 | 21 | 4 | 81.2 % | 🟡 |
| `management/` | 412 | 78 | 8 | 81.1 % | 🟡 |
| `admin.py` | 217 | 76 | 1 | 65.0 % | 🟡 |

### 6.2 Top-Blindflecken

| Datei | Coverage | Stmts | Miss | Kommentar |
|-------|---------:|------:|-----:|-----------|
| `management/commands/generate_dsgvo_package.py` | **0.0 %** | 23 | 23 | Gar nicht getestet |
| `views/pwa.py` | 51.7 % | 29 | 14 | Service-Worker-/Manifest-Endpoints |
| `admin.py` | 65.0 % | 217 | 76 | Django-Admin-Aktionen (ca. 35 Zeilen ohne Test) |
| `utils/formatting.py` | 66.7 % | 15 | 5 | kleine Utils ohne direkte Tests |
| `management/commands/refresh_statistics_view.py` | 70.4 % | 27 | 8 | |
| `templatetags/core_tags.py` | 71.0 % | 62 | 18 | |
| `views/handover.py` | 78.9 % | 38 | 8 | |
| `utils/dates.py` | 81.2 % | 85 | 16 | |
| `services/export.py` | 82.5 % | 97 | 17 | CSV-Export-Pfade |
| `management/commands/enforce_retention.py` | 83.9 % | 249 | 40 | DSGVO-Retention-Command |
| `views/retention.py` | 84.8 % | 158 | 24 | |
| `services/virus_scan.py` | 87.3 % | 79 | 10 | ClamAV-Error-Pfade |

### 6.3 E2E-Flow-Mapping

E2E-Test-Dateien: **44** (in `src/tests/e2e/`). URL-Routen: **87** (aus `src/core/urls.py` + `src/anlaufstelle/urls.py`). Mess-Heuristik: Route gilt als „getroffen", wenn `page.goto(pfad_mit_base)` oder `reverse("url_name")` in einer E2E-Datei vorkommt.

| Coverage | Anzahl | Beispiele |
|----------|-------:|-----------|
| ≥ 2 Tests | 20 | `event_create`, `event_detail`, `client_list`, `client_detail`, `login`, `statistics`, `workitem_inbox`, `workitem_detail`, `case_list`, `case_detail`, `audit_log`, `audit_detail`, `search`, `workitem_create`, `case_create`, `client_create`, `deletion_request_list` |
| 1 Test | 14 | `account_profile`, `attachment_list`, `handover`, `dsgvo_package`, `mfa_settings`, `retention_dashboard`, `password_change`, `password_reset`, `service_worker` |
| 0 Tests (goto/reverse-Heuristik) | 53 | Update/Edit-Routen, Bulk-Routen, API-Endpoints, MFA-Setup/Verify, Offline-APIs, Statistik-Exports |

> ⚠ **Caveat:** Die 53 „unbedeckten" Routen sind teilweise falsch gemessen — Edit-Views wie `case_update`, `client_update`, `event_update`, `workitem_update` werden typischerweise per `page.click` aus Detail-Seiten erreicht, nicht per `page.goto`. Testdateien wie `test_client_edit.py`, `test_workitem_edit.py`, `test_contact_stage.py` existieren und testen genau diese Flows. Eine präzisere Messung müsste Playwright-Request-Log auswerten, nicht nur den Test-Sourcecode parsen.

### — Echte E2E-Lücken

- **Severity:** Medium
- **Kategorie:** D
- **Ort:** verschiedene E2E-Tests fehlen
- **Kurzfassung:** Auch nach Abzug des Mess-Caveats bleiben reale Lücken:
 - **MFA-Setup/Verify/Disable/Backup-Codes-Regenerate** haben nur E2E-Smoke für Settings-Seite, keine vollen Setup-Flows
 - **Retention-Actions** (`retention_approve`, `retention_bulk_approve/defer/reject`, `retention_hold`, `retention_dismiss_hold`) — Klicks wahrscheinlich aus Dashboard heraus getestet, aber keine Solo-Routen-Tests
 - **Statistik-Exports** (`statistics_csv_export`, `statistics_jugendamt_export`, `statistics_pdf_export`) — keine Treffer in goto-Tests
 - **Offline-APIs** (`offline_bundle`, `offline_conflict_review`) — keine Direkt-Tests
 - **Logout-Flow** (`logout`) — einziger Test mit 0 Treffer, während Session-Reset für Offline-Cleanup wichtig ist
- **Evidenz:** Mapping-Tabelle oben, Kategorie-6.3.
- **Fix-Skizze:**
 1. Ergänzungstests für die oben genannten Export- und Offline-Endpoints
 2. Solo-E2E für MFA-Setup-Flow (TOTP-QR-Code abrufen, Code bestätigen, Backup-Codes anzeigen, einen verbrauchen)
 3. E2E-Test für Logout-Cleanup mit Verifikation, dass IndexedDB nach Logout leer ist
- **Aufwand:** L (gesamt), einzeln je M
- **Quer-Referenzen:** []

### — `generate_dsgvo_package` ohne Test (Coverage 0 %)

- **Severity:** High
- **Kategorie:** D
- **Ort:** [src/core/management/commands/generate_dsgvo_package.py](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/generate_dsgvo_package.py)
- **Kurzfassung:** 23 Statements, 0 getestet. Command ist DSGVO-relevant (Art. 15 Auskunft, Art. 20 Portabilität).
- **Evidenz:** Coverage-Output: `0.0% 23 stmts 23 miss`.
- **Warum es relevant ist:** Der Command wird bei Auskunftsersuchen laufen — wenn er bricht, gibt es keinen Rollback. Ein Smoke-Test, der den Command mit einer Seed-Facility ausführt und eine ZIP-Datei mit erwarteten Member-Namen erzeugt, würde die gröbsten Regressionen fangen.
- **Fix-Skizze:**
 1. `tests/test_dsgvo_package_command.py` anlegen
 2. Ein Test: Command ausführen → ZIP existiert → enthält mindestens `clients.csv`, `events.csv`, `cases.csv`, `metadata.json`
 3. Ein Test: Fehlerpfad (ungültige Facility) → sauberer Fehler
- **Aufwand:** M
- **Quer-Referenzen:**

### — `views/pwa.py` 52 % — Manifest- und SW-Serving-Pfade

- **Severity:** Medium
- **Kategorie:** D
- **Ort:** [src/core/views/pwa.py:17-57](https://github.com/anlaufstelle/app/blob/main/src/core/views/pwa.py)
- **Kurzfassung:** 14 von 29 Statements ungetestet. Laut E2E hat `test_pwa_offline.py::test_sw_endpoint` den SW-Endpoint; aber die Error-Pfade (Cache-Miss, Manifest-Regen) sind nicht abgedeckt.
- **Fix-Skizze:** Direkte Unit-Tests auf `ServiceWorkerView.get_response_headers`, `ManifestView.get_manifest_dict`, und einem Error-Szenario.
- **Aufwand:** S
- **Quer-Referenzen:**

### — `admin.py` 65 % — Django-Admin-Actions ungetestet

- **Severity:** Low
- **Kategorie:** D
- **Ort:** [src/core/admin.py](https://github.com/anlaufstelle/app/blob/main/src/core/admin.py)
- **Kurzfassung:** 76 Missing Lines, laut Missing-Report: Admin-Actions, `ModelAdmin.save_model` Custom-Logik, Read-only-Handler.
- **Warum es relevant ist:** Admin ist ein sensibler Bereich (Daten-Mutation), aber Nutzung ist selten und meist per Notfall. Low-Risk.
- **Fix-Skizze:** 3–4 Admin-Action-Tests (z.B. Bulk-Anonymize, Manual-Unlock) — das holt einen großen Teil der 76 Missing Lines.
- **Aufwand:** S
- **Quer-Referenzen:**

### — `enforce_retention` 40 Missing Lines (84 %)

- **Severity:** Medium
- **Kategorie:** D
- **Ort:** [src/core/management/commands/enforce_retention.py:85-578](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/enforce_retention.py)
- **Kurzfassung:** Missing-Lines-Report zeigt Cluster 211–221, 229–247, 533–578 — Error-/Edge-Cases im Retention-Enforcement.
- **Warum es relevant ist:** DSGVO-kritisch, Löschungen sind irreversibel. Uncovered Branches = unbewusste Pfade.
- **Fix-Skizze:** Fehlerfall-Tests pro Kategorie (Hold aktiv aber Proposal abgelaufen, kein Owner-Approver etc.).
- **Aufwand:** M
- **Quer-Referenzen:** []

### — Test-Qualität-Audit: Befunde aus Stichprobe

Stichprobe: `test_account.py`, `test_auth.py`, `test_events.py`, `test_pwa_offline.py`, `test_bugfixes_v1.py`, `test_architecture.py`, `e2e/conftest.py`, `conftest.py`.

**Positive Befunde (zur Kenntnis):**

- `test_account.py` (12 Tests) — testet nicht nur `status_code`, sondern auch Content (`staff_user.username.encode in response.content`), Context-Keys (`"recent_events" in response.context`) und Sensitivity-Gating (`high_event not in response.context["recent_events"]`). Solide.
- `test_auth.py` — umfassend, inkl. 7 Lockout-Tests, 4 Rate-Limit-Tests, 6 Role-Mixin-Tests. Lockout-Tests mocken `timezone.now` korrekt für Fenster-Expiry (Zeile 423–440).
- `test_events.py` — `TestEventServiceAtomicity::test_approve_deletion_rolls_back_on_failure` testet transaktionales Verhalten (mit `patch.object(DeletionRequest, "save", side_effect=RuntimeError)`) — das ist top.
- `test_architecture.py` — 6 Architektur-Guards (NoInlineScript, NoMultilineDjangoComments, UserFacingEntryPoint, DocumentedRoutes, FacilityScoping, EventAccessPolicy). Diese Tests schützen die Architektur dauerhaft.
- `e2e/conftest.py` (297 Zeilen) — worker-aware (xdist), `FileLock` für Setup, eigene DB + eigener Port pro Worker, passwort-hash-Warnung gut dokumentiert.

**Auffälligkeiten:**

- `test_bugfixes_v1.py` ruft Django-Shell-Befehle per `subprocess.run` auf (Zeilen 18–39), um Default-DocumentType zu setzen. Das ist ein hacky Pattern — funktioniert, aber brüchig (Worker-Isolation läuft über ENV, Timing-Abhängigkeiten). Kein Fix-Findung (dokumentiert).
- `test_pwa_offline.py::test_offline_form_submit_shows_feedback` ist `@pytest.mark.xfail(reason="Service Worker faengt POST-Requests im Offline-Modus noch nicht ab")` — bekannter Bug, sauber markiert.

### — Fixture-/Helper-Audit: sauber

- **Severity:** Info
- **Kategorie:** D
- **Ort:** [src/tests/conftest.py](https://github.com/anlaufstelle/app/blob/main/src/tests/conftest.py), [src/tests/e2e/conftest.py](https://github.com/anlaufstelle/app/blob/main/src/tests/e2e/conftest.py)
- **Beobachtung:** 265 Zeilen in `conftest.py` mit logischer Gruppierung (Organization → Facility → Users → Clients → DocTypes → Events → WorkItems → Cases → Episodes → Settings → Goals/Milestones). Keine erkennbaren Redundanzen. Fixture-Abhängigkeiten sind linear (keine Zyklen).
- **Relevanz:** Positiver Fund.

### — `networkidle` nirgends verwendet

- **Severity:** Info
- **Kategorie:** D
- **Beobachtung:** Grep `networkidle` in `src/tests/e2e/` liefert 0 Treffer. Alle Wait-Strategien verwenden `domcontentloaded` oder `wait_for_url`. Entspricht CLAUDE.md-Regel.
- **Relevanz:** Positiver Fund.

### — 10 Dateien mit `xfail`/`skip`-Markierungen

- **Severity:** Low
- **Kategorie:** D
- **Ort:** u.a. `test_statistics_mv.py` (12 xfails), `test_architecture.py` (4 skipif), `e2e/test_zeitstrom_enrichment.py` (4), `e2e/test_pagination.py` (3)
- **Kurzfassung:** Die Mehrzahl sind dokumentierte Temporär-Skipifs (DB-Variante, Feature nicht aktiv). `test_statistics_mv.py` mit 12 `xfail`s in einer Datei ist aber ein Signal, dass die Materialized-View-Integration nicht vollständig ist.
- **Fix-Skizze:** Review der 12 `xfail`s in `test_statistics_mv.py` — sind die Bug-Referenzen noch aktuell? Gibt es ein Tracking-Issue?
- **Aufwand:** S (Review), nicht XS weil inhaltlich
- **Quer-Referenzen:**

### — Viele Tests mit `status_code == 200`-only-Assertions

- **Severity:** Low
- **Kategorie:** D
- **Ort:** `test_events.py` (24), `test_scope.py` (11), `test_file_vault.py` (11), `test_cases.py` (11), `test_workitems.py` (10), `test_workitem_filters.py` (10), `test_workitem_due_filter.py` (10), `test_permissions.py` (10), `test_goals.py` (8)
- **Kurzfassung:** Grep nach `assert.*status_code\s*==\s*200` zeigt viele Tests. Stichprobenhaft in `test_events.py` geprüft: die `status_code`-Checks sind meist **kombiniert** mit weiteren Assertions auf Content oder DB-State — nicht isoliert „green checkmark". Aber nicht in allen Dateien — `test_permissions.py` und `test_scope.py` sollten manuell geprüft werden.
- **Fix-Skizze:**
 1. Grep in den 5 Dateien mit den meisten status-only-Checks
 2. Für Tests, wo `status_code == 200` die einzige Assertion ist, ergänzen durch Content-Check oder Context-Check
- **Aufwand:** M
- **Quer-Referenzen:**

### — 1.512 passing in 6:52 min — langsame Unit-Suite

- **Severity:** Low
- **Kategorie:** D
- **Beobachtung:** 6:52 Minuten für 1.512 Tests sind ~0,27 s/Test. `--reuse-db` ist an; Postgres-RLS-Setup + django-otp-OTP-Registrierung sind plausible Gründe.
- **Warum es relevant ist:** Je langsamer die Suite, desto seltener läuft sie lokal — und desto mehr Fehler rutschen durch.
- **Fix-Skizze:**
 1. `pytest --durations=25` einmal laufen lassen — welche 25 Tests dominieren die Laufzeit?
 2. Ggf. slow Tests mit `@pytest.mark.slow` markieren und aus der „schnellen Suite" ausschließen (`make test-fast`)
 3. `pytest -n auto` (xdist) — ist parallel schon in CI, lokal selten genutzt
- **Aufwand:** S (Analyse), M (Refactoring langsamer Tests)
- **Quer-Referenzen:**

---

## 7. Kategorie E — Technical Debt

### — `USE_L10N = True` in `base.py`:154 deprecated

- **Severity:** Low
- **Kategorie:** E
- **Ort:** [src/anlaufstelle/settings/base.py:154](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/base.py#L154)
- **Kurzfassung:** Seit Django 4.0 deprecated, Django 5.0+ ignoriert den Wert.
- **Fix-Skizze:** Zeile entfernen.
- **Aufwand:** XS

### — 0 TODO/FIXME/XXX im gesamten Repo

- **Severity:** Info
- **Beobachtung:** Agent-E-Grep liefert keine Treffer.
- **Relevanz:** Positiver Fund — außergewöhnlich sauber für einen Projektstand v0.9+.

### — Keine ungenutzten URLs, keine orphan Views, keine tote Dependency

- **Severity:** Info
- **Beobachtung:** Alle 65 URL-Namen werden in Templates/Python referenziert. Alle 82 CBVs sind in urls.py registriert. Alle Pakete aus `requirements.in`/`requirements-dev.in` werden importiert oder sind Runtime-Config (gunicorn, whitenoise, django-csp, django-htmx).
- **Relevanz:** Positiver Fund.

### — `vulture` findet 8 „Funde", alle sind False Positives

- **Severity:** Info
- **Beobachtung:** Alle 8 Hits sind Django-Signal-Receiver-Parameter (`sender`), Admin-Action-Parameter (`modeladmin`) oder Ratelimit-Key-Function-Parameter (`group`). Signature-Zwang.
- **Relevanz:** Positiver Fund.

### — Migrations: 69 Stück, 5 RunPython, 4 Bulk-AlterField

- **Severity:** Info
- **Beobachtung:** Die 4 Bulk-AlterField-Migrationen sind semantisch gruppiert (help_text + choices), nicht atomar zerlegt. Das ist erwartbar und nicht problematisch.
- **Relevanz:** Positiver Fund.

### — `secrets` Scope in prod.py sauber via Env-Vars

- **Severity:** Info
- **Beobachtung:** `DJANGO_SECRET_KEY`, `DB_PASSWORD`, `ENCRYPTION_KEY(S)`, Sentry-DSN — alle via `os.environ` geladen, nicht hardcoded. `.env.example` dokumentiert die erwarteten Variablen.
- **Relevanz:** Positiver Fund.

---

## 8. Kategorie F — Infrastruktur & Frontend-Assets

### — Kein `HEALTHCHECK` im Dockerfile

- **Severity:** Low
- **Kategorie:** F
- **Ort:** [Dockerfile](https://github.com/anlaufstelle/app/blob/main/Dockerfile)
- **Kurzfassung:** Healthchecks sind nur in den `docker-compose*.yml` definiert. Wer das Image ohne Compose startet (k8s, Coolify, reine Docker-Run), bekommt keinen Liveness-Check.
- **Fix-Skizze:** `HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/')"` ins Dockerfile.
- **Aufwand:** XS

### — CSP-Header-Quelle divergiert zwischen Prod (Django) und Staging (Caddy + Django)

- **Severity:** Low
- **Kategorie:** F
- **Ort:** [Caddyfile.staging](https://github.com/anlaufstelle/app/blob/main/Caddyfile.staging), [src/anlaufstelle/settings/base.py:237](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/base.py#L237)
- **Siehe []** für den Security-Aspekt.

### — CI hat keinen Playwright-Cache

- **Severity:** Low
- **Kategorie:** F
- **Ort:** [.github/workflows/e2e.yml](https://github.com/anlaufstelle/app/blob/main/.github/workflows/e2e.yml)
- **Kurzfassung:** `playwright install --with-deps chromium` läuft bei jedem E2E-Workflow-Run, lädt ~100 MB Chromium nach.
- **Fix-Skizze:**
 ```yaml
  - uses: actions/cache@v4
    with:
      path: ~/.cache/ms-playwright
      key: playwright-${{ hashFiles('requirements-dev.txt') }}
  ```
- **Aufwand:** XS
- **Quer-Referenzen:**

### — CI hat kein npm-Cache für Tailwind-Build

- **Severity:** Low
- **Kategorie:** F
- **Ort:** [.github/workflows/e2e.yml](https://github.com/anlaufstelle/app/blob/main/.github/workflows/e2e.yml)
- **Kurzfassung:** `npm install` im E2E-Workflow ohne Cache.
- **Fix-Skizze:** `actions/setup-node@v4 with: cache: 'npm'` + `package-lock.json` committet halten.
- **Aufwand:** XS

### — Release-Workflow ohne Buildkit-Cache und ohne Multi-Arch

- **Severity:** Low
- **Kategorie:** F
- **Ort:** [.github/workflows/release.yml](https://github.com/anlaufstelle/app/blob/main/.github/workflows/release.yml)
- **Kurzfassung:** Jeder Release-Build baut von null; nur linux/amd64.
- **Warum es relevant ist:** ARM-Deployments (Raspberry, Apple Silicon in Test) sind aktuell blockiert; Release-Time wächst mit Projektgröße.
- **Fix-Skizze:**
 1. `docker/setup-buildx-action` + `cache-from: type=gha, cache-to: type=gha,mode=max`
 2. `platforms: linux/amd64,linux/arm64`
- **Aufwand:** S

### — `tailwind.config.js` ohne Theme-Erweiterung

- **Severity:** Info
- **Ort:** [tailwind.config.js](https://github.com/anlaufstelle/app/blob/main/tailwind.config.js)
- **Beobachtung:** `theme: { extend: {} }` ist leer. Keine Corporate-Farben, keine Custom-Fonts, keine Spacing-Skala.
- **Relevanz:** Beobachtung. Wenn das Design-System wächst, wird diese Datei automatisch gefüllt. Aktuell kein Handlungsbedarf.

### — `package.json` ohne `scripts` und ohne `engines`

- **Severity:** Low
- **Kategorie:** F
- **Ort:** [package.json](https://github.com/anlaufstelle/app/blob/main/package.json)
- **Kurzfassung:** Keine `npm run build` / `npm run watch`-Scripts, keine Node-Version-Pinning via `engines`. Build-Befehl steht nur im Dockerfile und docker-compose.yml.
- **Fix-Skizze:**
 ```json
  "scripts": {
    "build": "tailwindcss -i src/static/css/input.css -o src/static/css/styles.css --minify",
    "watch": "tailwindcss -i src/static/css/input.css -o src/static/css/styles.css --watch"
  },
  "engines": { "node": ">=20" }
  ```
- **Aufwand:** XS

### — Service-Worker: kein User-Update-Prompt bei neuem SW

- **Severity:** Low
- **Kategorie:** F
- **Ort:** [src/static/js/sw-register.js](https://github.com/anlaufstelle/app/blob/main/src/static/js/sw-register.js)
- **Kurzfassung:** `skipWaiting` + `clients.claim` im SW aktivieren einen neuen SW ohne Nutzer-Feedback. Der nächste Page-Load zeigt schon die neue Version; der Nutzer weiß nicht, dass er gerade ein Update bekam.
- **Warum es relevant ist:** Bei offline-fähigen Apps ist User-Feedback über Updates UX-relevant — sonst wirkt die App „kaputt" oder „anders".
- **Fix-Skizze:** Nach `statechange: installed` → Message an alle Clients → Toast „Neue Version verfügbar, bitte neu laden" mit Refresh-Button.
- **Aufwand:** S

### — Positive: Docker-Layer gut optimiert

- **Severity:** Info
- **Beobachtung:** Multi-Stage, Non-Root-User, pip-wheel-Cache, `rm -rf /var/lib/apt/lists/*`, Tailwind-Build als separater Stage. Sauber.
- **Relevanz:** Positiver Fund.

### — Positive: Caddy HSTS mit Preload + HTTPS-Redirect

- **Severity:** Info
- **Beobachtung:** `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`, automatisches Let's-Encrypt, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`.
- **Relevanz:** Positiver Fund.

---

## 9. Finding-Index (Querliste, sortierbar)

| ID | Kategorie | Severity | Aufwand | Titel | Ort |
|----|-----------|----------|---------|-------|-----|
| | A | Medium | M | `views/events.py` zu groß, 4 Methoden > 70 LOC | `src/core/views/events.py` |
| | A | Medium | S | AuditLog-Erstellung 3× inline in `views/clients.py` | `src/core/views/clients.py:108,243,270` |
| | A | Medium | M | `EventDetailView.get` + `RetentionDashboardView.get` mit Inline-Context-Building | `src/core/views/events.py:339, retention.py:33` |
| | A | Low | L | `seed.py` mit 1.950 LOC in ein Command | `src/core/management/commands/seed.py` |
| | A | Low | M | `enforce_retention.py` Command mit 639 LOC | `src/core/management/commands/enforce_retention.py` |
| | A | Low | M | Service-Methoden > 100 LOC (handover, client_export, event, export) | mehrere `services/` |
| | A | Info || Import-Zyklen: keine gefunden | Gesamtes `src/core/` |
| | A | Info || Mixin-Audit: 88 CBVs, 13 „ungeschützt" aber alle abgedeckt | `src/core/views/*.py` |
| | B | Low | XS | `SECURE_REFERRER_POLICY` nicht explizit in Django | `settings/prod.py` |
| | B | Low | XS | CSP-Header doppelt gesetzt in Staging (Django + Caddy) | `Caddyfile.staging`, `base.py:237` |
| | B | Info || Dev-SECRET_KEY hardcoded, aber sauber markiert | `settings/dev.py:9` |
| | B | Low | S | Logging von `username` + `email` in `invite.py` | `src/core/services/invite.py` |
| | B | Info || bandit 0 High, 10 Medium (alle B608 FP) | bandit-Output |
| | B | Info || RLS-Audit: vollständig, 23 Tables abgedeckt | `src/tests/test_rls.py` |
| | B | Info || Account-Lockout + MFA-Backup-Codes hochwertig integriert | `services/login_lockout.py`, `services/mfa.py` |
| | B | Info || FacilityScoping- und EventAccess-Guard existieren | `src/tests/test_architecture.py` |
| | C | Medium | S | Fehlende DB-Indexe (AuditLog, WorkItem, Case, Event, Client) | mehrere Models |
| | C | Medium | M | `WorkItemInboxView` ohne Pagination auf 3 Listen | `src/core/views/workitems.py:87` |
| | C | Medium | S | `|length` auf Querysets triggert COUNT-Queries | `templates/deletion_requests/list.html`, `workitems/inbox_content.html` |
| | C | Low | XS | `ClientDetailView` WorkItems ohne `select_related("client")` | `src/core/views/clients.py:78` |
| | C | Low | XS | `RecentClientVisit`-Queryset ohne Slice | `src/core/views/clients.py:150` |
| | C | Low | M | Doppel-Rendering Client-Liste (Desktop + Mobile) | `templates/clients/partials/table.html` |
| | C | Low | S | AttachmentListView Service-Calls pro Item in 200er-Loop | `src/core/views/attachments.py:57` |
| | C | Info || Feed-Building Service-abstrahiert | `src/core/services/feed.py` |
| | D | Medium | L | Echte E2E-Lücken (MFA-Flows, Exports, Offline-APIs, Logout) | mehrere `e2e/` |
| | D | High | M | `generate_dsgvo_package` ohne Test (0 %) | `management/commands/generate_dsgvo_package.py` |
| | D | Medium | S | `views/pwa.py` 52 % Coverage | `src/core/views/pwa.py` |
| | D | Low | S | `admin.py` 65 % Coverage | `src/core/admin.py` |
| | D | Medium | M | `enforce_retention` 40 Missing Lines | `management/commands/enforce_retention.py` |
| | D | Info || Test-Qualitäts-Audit: Stichprobe insgesamt sehr gut ||
| | D | Info || Fixture-Design sauber | `src/tests/conftest.py` |
| | D | Info || `networkidle` nirgends verwendet | `src/tests/e2e/` |
| | D | Low | S | 12 `xfail` in `test_statistics_mv.py` — Review nötig | `src/tests/test_statistics_mv.py` |
| | D | Low | M | Viele Tests mit `status_code == 200`-Checks | u.a. `test_events.py` |
| | D | Low | S–M | Unit-Suite 6:52 min — `--durations=25` prüfen | Test-Suite |
| | E | Low | XS | `USE_L10N = True` deprecated entfernen | `settings/base.py:154` |
| | E | Info || 0 TODO/FIXME im Repo ||
| | E | Info || Keine toten URLs/Views/Dependencies ||
| | E | Info || vulture: alle Hits Signal-FP ||
| | E | Info || 69 Migrationen strukturell sauber | `src/core/migrations/` |
| | E | Info || Secrets sauber via Env-Vars | `settings/prod.py` |
| | F | Low | XS | Kein `HEALTHCHECK` im Dockerfile | `Dockerfile` |
| | F | Low | XS | CSP-Header doppelt in Staging (siehe) ||
| | F | Low | XS | Kein Playwright-Cache in E2E-CI | `.github/workflows/e2e.yml` |
| | F | Low | XS | Kein npm-Cache in E2E-CI | `.github/workflows/e2e.yml` |
| | F | Low | S | Release-CI ohne Buildkit-Cache + Multi-Arch | `.github/workflows/release.yml` |
| | F | Info || `tailwind.config.js` ohne Theme-Erweiterung ||
| | F | Low | XS | `package.json` ohne `scripts` + `engines` | `package.json` |
| | F | Low | S | Service-Worker-Update ohne User-Prompt | `sw-register.js` |
| | F | Info || Docker-Layer gut optimiert | `Dockerfile` |
| | F | Info || Caddy HSTS + Preload + Auto-HTTPS | `Caddyfile` |

**Severity-Verteilung (51 Findings gesamt):**
- Critical: 0
- High: 1
- Medium: 9 (A001, A002, A003, C001, C002, C003, D001, D003, D005)
- Low: 22
- Info: 19

**Aufwand-Verteilung (ohne Info, 32 actionable):**
- XS: 10
- S: 11
- M: 9
- L: 2
- XL: 0

---

## 10. Offene Fragen & nicht-behandelte Punkte

### Bewusst ausgelassen

- **Benchmark-Messung:** Keine Load-/Performance-Messung durchgeführt. Die N+1- und Index-Findings in Kategorie C basieren auf statischer Analyse. Eine echte Messung (z.B. Django-Debug-Toolbar mit einem realistischen Seed) würde zeigen, welche der Findings tatsächlich spürbare Auswirkungen haben.
- **Volle E2E-Ausführung:** E2E-Tests wurden statisch gelesen, nicht ausgeführt. „Roter E2E-Test in main" wäre ein eigenes Finding, das diese Analyse nicht erfasst.
- **Migrationsinhalt:** Nur strukturell (Anzahl, LOC, RunPython-Zähler) — keine Durchsicht einzelner Datenmigrationen.
- **`docs/` Content-Drift:** Die Analyse hat `docs/` im Scope, aber inhaltliche Drift (veraltete Runbooks, gebrochene Intra-Doku-Verlinkungen) wurde nicht systematisch geprüft. `TestDocumentedRoutesGuard` greift nur für URL-Pfade in `docs/user-guide.md`.
- **Archive-Docs (`docs/archive/`):** Nicht bewertet — laut CLAUDE.md sind diese „Muster, nicht Fakten".

### Unsicherheiten im Report

- **E2E-Flow-Mapping** (Abschnitt 6.3, Tabelle): misst `page.goto` + `reverse`, nicht `page.click`. Das unterschätzt die echte E2E-Abdeckung systematisch. Eine präzisere Messung wäre ein Playwright-Trace-Auswerter, der alle tatsächlich aufgerufenen Routen protokolliert. Das wäre ein eigenes Tooling-Projekt.
- ** (status_code-only-Checks):** Grep-Zählung ist per Datei, nicht per Test. In den meisten Dateien sind die `status_code`-Assertions in Tests, die *zusätzlich* Content-Checks haben — eine manuelle Prüfung jedes einzelnen Tests habe ich nicht gemacht.
- ** (PII-Logging):** Ich habe den `JsonFormatter` mit PII-Scrubber in `core/logging.py` nicht vollständig gelesen. Die Einschätzung, dass der Scrubber nicht zwangsläufig auf `record.msg + args` greift, basiert auf Python-Logging-Mechanik, nicht auf Code-Inspektion.

### Interessante Folge-Analysen

- Je nach Output dieses Reports lohnt sich ggf. ein fokussierter Deep-Dive auf:
 - **Test-Laufzeit** (`pytest --durations=50`)
 - **Security-Penetration** (semgrep mit Django-Ruleset, OWASP ZAP gegen den E2E-Server)
 - **DB-Query-Profiling** mit echtem Datensatz-Volumen

---

*Ende des Reports.*
