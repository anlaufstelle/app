# Tiefenanalyse v0.10.0 — Sicherheit, Datenschutz, Refactoring, Tests & Performance

- **Datum:** 2026-04-21
- **Release-Stand:** v0.10.0 (Branch `main`, letzter geprüfter Commit)
- **Trigger:** Vorbereitung auf produktive Nutzung in sensiblen sozialen Einrichtungen (Obdachlosenhilfe, Suchtberatung) — Bestandsaufnahme vor weiteren Features.
- **Ursprungs-Tickets:** Erstanalyse #592, Umsetzungs-Parent #598.
- **Scope-Sperre:**–M6 (Admin-UI, Conditional Fields, `data_json`, WCAG, CSV-Import, Plugin-Architektur, Demo-Setup) bleiben tabu — siehe [`CLAUDE.md §-Sperre`](https://github.com/anlaufstelle/app/blob/main/CLAUDE.md).

---

## Methodik

- **Drei parallele Explore-Agents** haben je eine Domäne (Security/DSGVO, Refactoring, Tests/Performance) zeilengenau am Code geprüft.
- **Zusätzlich acht eigene Spot-Checks** in den kritischsten Dateien: [`migrations/0047_postgres_rls_setup.py`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0047_postgres_rls_setup.py), [`migrations/0050_quick_template.py`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0050_quick_template.py), [`models/quick_template.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/quick_template.py), [`settings/prod.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/prod.py), [`models/audit.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/audit.py), [`signals/audit.py`](https://github.com/anlaufstelle/app/blob/main/src/core/signals/audit.py), [`services/snapshot.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/snapshot.py), [`services/client_export.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/client_export.py), [`views/events.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/events.py), [`settings/base.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/base.py) (Logging).
- **Statusschema:** `BESTÄTIGT` (am Code belegt), `GEFIXT` (inzwischen behoben), `TEILWEISE` (teilbehoben oder ergänzungsbedürftig), `DESIGN` (bewusste Architekturentscheidung, kein Bug).

---

## Teil A — Findings

### A.1 Sicherheit & Auth

| ID | Status | Kurzbefund | Prio |
|----|--------|------------|------|
| **** | BESTÄTIGT | `QuickTemplate` ist seit Migration [`0050_quick_template.py`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0050_quick_template.py) live; Migration [`0047_postgres_rls_setup.py:22-38`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0047_postgres_rls_setup.py#L22-L38) kennt `core_quicktemplate` nicht; keine spätere RLS-Migration ergänzt das. Defense-in-Depth-Lücke. | **P1** |
| **** | DESIGN | `User.facility` (FK, `SET_NULL`, [`user.py:24-31`](https://github.com/anlaufstelle/app/blob/main/src/core/models/user.py#L24-L31)) ist beabsichtigt RLS-frei (Cross-Facility-Admin + Login-vor-Facility-Context). Nur dokumentieren. | P3 |
| **** | TEILWEISE | [`views/auth.py:27`](https://github.com/anlaufstelle/app/blob/main/src/core/views/auth.py#L27)/[:62](https://github.com/anlaufstelle/app/blob/main/src/core/views/auth.py#L62) — Login + Password-Reset nutzen nur `key="ip"` (5/m). [`OfflineKeySalt:92`](https://github.com/anlaufstelle/app/blob/main/src/core/views/auth.py#L92) nutzt schon `key="user"`. Kein Account-Lockout nach N Fehlversuchen; [`services/bans.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/bans.py) existiert als Util, aber ohne Lockout-Logik. | **P1** |
| **** | BESTÄTIGT | [`prod.py:40-48`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/prod.py#L40-L48) setzt weder `CSRF_COOKIE_HTTPONLY=True` noch `base.py`. Token ist für clientseitigen Skript-Zugriff erreichbar. | P2 |
| **** | BESTÄTIGT | Kein `SESSION_COOKIE_SAMESITE` / `CSRF_COOKIE_SAMESITE` in `prod.py` oder `base.py`. Django-Default `"Lax"`; für dieses Fachsystem wäre `"Strict"` sauberer. | P2 |
| **** | BESTÄTIGT (begründet) | [`base.py:239`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/base.py#L239) erlaubt `'unsafe-eval'`. Aktueller Alpine-Build in [`static/js/alpine.min.js`](https://github.com/anlaufstelle/app/blob/main/src/static/js/alpine.min.js) benötigt zur Laufzeit dynamische Ausdrucks-Evaluation. Alpine stellt auch einen CSP-kompatiblen Build bereit — bindet dann aber `x-data`-Ausdrücke anders, breiter Regressions-Test nötig. | P2 |
| **** | BESTÄTIGT | [`services/file_vault.py:96`](https://github.com/anlaufstelle/app/blob/main/src/core/services/file_vault.py#L96) übernimmt `mime_type=uploaded_file.content_type` ohne Magic-Bytes-Validierung. `allowed_file_types` (Extension-Whitelist) in [`models/settings.py:55-61`](https://github.com/anlaufstelle/app/blob/main/src/core/models/settings.py#L55-L61) wird in [`forms/events.py`](https://github.com/anlaufstelle/app/blob/main/src/core/forms/events.py) nicht gegen den Upload geprüft. Einzige Content-Validation: ClamAV. Kein `python-magic`-Import im Repo. | P2 |
| **** | BESTÄTIGT | [`base.py:226-228`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/base.py#L226-L228) setzt `core`-Logger auf `"level": "DEBUG"` — gilt auch in `prod.py` (da nur ergänzt). [`core/logging.py`](https://github.com/anlaufstelle/app/blob/main/src/core/logging.py) `JsonFormatter` hat keinen PII-Scrub-Filter. | P2 |
| **** | BESTÄTIGT | [`models/audit.py:15-33`](https://github.com/anlaufstelle/app/blob/main/src/core/models/audit.py#L15-L33) Enum hat 18 Actions, aber **keine `*_CREATE`**. [`signals/audit.py`](https://github.com/anlaufstelle/app/blob/main/src/core/signals/audit.py) hat nur drei Django-Auth-Signale (`user_logged_in/out/failed`). Keine `post_save`-Receiver auf `Client/Case/Event/WorkItem`. Rollenänderungen, Password-Reset-Requests, Sensitivity-Feld-Zugriffe ebenfalls nicht geloggt. | **P1** |

#### Detail

- Model: [`quick_template.py:15-81`](https://github.com/anlaufstelle/app/blob/main/src/core/models/quick_template.py#L15-L81) (`facility`-FK `CASCADE`, `related_name="quick_templates"`).
- Migration [`0050_quick_template.py:21-88`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0050_quick_template.py#L21-L88) erzeugt `core_quicktemplate`.
- Migration [`0047_postgres_rls_setup.py:22-38`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0047_postgres_rls_setup.py#L22-L38) `DIRECT_TABLES` listet 15 Tabellen — `core_quicktemplate` fehlt.
- Nur [`0050_quick_template.py`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0050_quick_template.py) und [`0051_merge_20260417_2123.py`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0051_merge_20260417_2123.py) berühren QuickTemplate. Keine spätere RLS-Migration.
- **Fix:** Neue Migration `0057_rls_quicktemplate.py` mit `ENABLE + FORCE ROW LEVEL SECURITY + POLICY facility_isolation ON core_quicktemplate USING (facility_id::text = current_setting('app.current_facility_id', true))`. Test: ORM-Query ohne Session-Variable liefert 0 Zeilen. `EXPECTED_TABLES` in [`src/tests/test_rls.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/test_rls.py) mitziehen (vgl. [`CLAUDE.md § Facility-Scoping + RLS zusammen mitpflegen`](https://github.com/anlaufstelle/app/blob/main/CLAUDE.md)).

#### Detail

Aktuelle Actions: LOGIN, LOGOUT, LOGIN_FAILED, VIEW_QUALIFIED, EXPORT, DELETE, STAGE_CHANGE, SETTINGS_CHANGE, DOWNLOAD, LEGAL_HOLD, OFFLINE_KEY_FETCH, CLIENT_UPDATE, CASE_UPDATE, WORKITEM_UPDATE, SECURITY_VIOLATION, MFA_ENABLED, MFA_DISABLED, MFA_FAILED.

Fehlend: `CLIENT_CREATE`, `CASE_CREATE`, `EVENT_CREATE`, `WORKITEM_CREATE`, `USER_ROLE_CHANGED`, `USER_DEACTIVATED`, `PASSWORD_RESET_REQUESTED` (optional: `QUALIFIED_SENSITIVITY_DENIED`).

**Fix:** Enum erweitern; `post_save`-Signal-Handler (bei `created=True`) in [`signals/audit.py`](https://github.com/anlaufstelle/app/blob/main/src/core/signals/audit.py). Rollenwechsel vor dem Save mit altem Wert in `detail`-JSON loggen.

### A.2 Datenschutz / DSGVO

| ID | Status | Kurzbefund | Prio |
|----|--------|------------|------|
| **D-1** | **DESIGN (2026-04-21)** | [`views/dsgvo.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/dsgvo.py) hat nur Dokument-Downloads; Grep `rectif|berichtig|correction` in `src/` → 0 Treffer. **Entscheidung 2026-04-21:** Self-Service ist nicht gewollt — Berichtigungen laufen organisatorisch über Mitarbeiter oder Leitung. Art. 16 wird außerhalb der App erfüllt. Nur in DSGVO-TOMs und Fachkonzept § 17 dokumentieren. | P3 |
| **D-2** | GEFIXT (2026-04-21 verifiziert) | Audit-Agent hat die bestehende Implementierung übersehen: [`ClientDataExportJSONView`](https://github.com/anlaufstelle/app/blob/main/src/core/views/clients.py#L233-L257) existiert in `views/clients.py:233-257`, ist per URL-Name `core:client_data_export_json` erreichbar, rate-limited (10/h/user), Audit-geloggt. Art. 20 DSGVO (JSON-Export) ist damit abgedeckt. ||
| **D-3** | DESIGN | [`management/commands/enforce_retention.py`](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/enforce_retention.py) synchron + Cron-gesteuert. Keine Celery/Django-Q/Huey/RQ. Für aktuelle Scale ausreichend. | P3 |

### A.3 Dead Code & Refactoring

| ID | Status | Kurzbefund | Prio |
|----|--------|------------|------|
| **R-1** | TEILWEISE | [`views/events.py:279-325`](https://github.com/anlaufstelle/app/blob/main/src/core/views/events.py#L279-L325) — File-Upload ist jetzt IM `transaction.atomic` (Commit). Inline-Upload-Handler (~45 LoC) bleibt, ist aber auf Service-Aufrufe (`create_event`, `store_encrypted_file`) reduziert. Pure Extraktion optional. | P3 |
| **R-2** | BESTÄTIGT | 87 Vorkommen von `request.current_facility` in 14 View-Dateien. [`views/mixins.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/mixins.py) (35 LoC) kennt nur Rollen-Mixins. | P2 |
| **R-3** | BESTÄTIGT | 11 Vorkommen von `if request.headers.get("HX-Request")` in 9 View-Files. Kein `HTMXPartialMixin`. | P2 |
| **R-4** | DESIGN | [`retention.py:192-244`](https://github.com/anlaufstelle/app/blob/main/src/core/views/retention.py#L192-L244) und [`workitems.py:314-367`](https://github.com/anlaufstelle/app/blob/main/src/core/views/workitems.py#L314-L367) strukturell ähnlich, aber WorkItems hat pro-Item-Ownership-Check (Commit) — Retention nur Status. Gemeinsamer Mixin würde Ownership-Logik verwässern. ||
| **R-5** | BESTÄTIGT | 4× `Client.objects.get(pk=client_id, facility=facility)` in [`events.py:215`](https://github.com/anlaufstelle/app/blob/main/src/core/views/events.py#L215)/[:233](https://github.com/anlaufstelle/app/blob/main/src/core/views/events.py#L233), [`workitems.py:202`](https://github.com/anlaufstelle/app/blob/main/src/core/views/workitems.py#L202), [`cases.py:132`](https://github.com/anlaufstelle/app/blob/main/src/core/views/cases.py#L132). [`services/clients.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/clients.py) hat keinen `get_client_or_none`. | P3 |
| **R-6** | STOLPERFALLE | [`templates/components/_time_filter_dropdown.html:10`](https://github.com/anlaufstelle/app/blob/main/src/templates/components/_time_filter_dropdown.html#L10)/[:30](https://github.com/anlaufstelle/app/blob/main/src/templates/components/_time_filter_dropdown.html#L30) Fallback `{% url 'core:timeline_events_partial' %}` — URL-Name existiert nicht. Aktueller Partial-URL-Name ist `zeitstrom_feed_partial`. Kein aktiver Bug, aber Stolperfalle. | P3 |
| **R-7** | N/A | Keine `-> HttpResponse` / `request: HttpRequest`-Annotationen in Views. Django-Praxis, kein Refactor-Kandidat. ||

**Zusatzfund — God-Object:** [`views/events.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/events.py) ist 744 LoC bei 13 Klassen + 4 Funktionen. Split-Kandidaten: DeletionRequest-Workflow nach `views/event_deletion.py`, EventAttachment-Download nach `views/attachments.py`. Zielgröße: 500 LoC pro View-File.

**Zusatzfund — Architektur-Guards:** [`tests/test_architecture.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/test_architecture.py) enthält bereits `TestFacilityScopingGuard` und `TestEventAccessPolicyGuard`. Ergänzbar: Guard gegen `Client.objects.get` ohne Service (nach R-5-Fix); Guard gegen inlined HTMX-Weiche (nach R-3-Fix).

### A.4 Tests & Performance

| ID | Status | Kurzbefund | Prio |
|----|--------|------------|------|
| **** | BESTÄTIGT (verschärft) | **42** `page.wait_for_timeout(...)` in 12 E2E-Dateien (ursprünglich 31 gemeldet). `networkidle` bleibt 0 in Tests. Top-Violators: [`test_mobile.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/e2e/test_mobile.py) (8), [`test_retention_dashboard.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/e2e/test_retention_dashboard.py) (6), [`test_statistics_charts.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/e2e/test_statistics_charts.py) (5), [`test_quick_capture.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/e2e/test_quick_capture.py) (3), [`test_zeitstrom_events.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/e2e/test_zeitstrom_events.py) (2). | **P1** |
| **** | GEFIXT (partial) | [`client_export.py:25-29`](https://github.com/anlaufstelle/app/blob/main/src/core/services/client_export.py#L25-L29) nutzt bereits `select_related("document_type", "created_by")`. Auch Cases, EventHistory, DeletionRequests sind fixiert. Ausnahme: [`WorkItem.objects.filter(client=client)` in Zeile 98](https://github.com/anlaufstelle/app/blob/main/src/core/services/client_export.py#L98) ohne `select_related("assigned_to", "created_by")`. | P3 |
| **** | BESTÄTIGT | [`services/snapshot.py:46-57`](https://github.com/anlaufstelle/app/blob/main/src/core/services/snapshot.py#L46-L57) Loop `DocumentType.objects.get(...)` pro Eintrag — 10-20 zusätzliche Queries pro Snapshot-Erstellung. | **P1** |
| **** | TEILWEISE | Meta-Indexes vorhanden: [`event.py:74-76`](https://github.com/anlaufstelle/app/blob/main/src/core/models/event.py#L74-L76) (3×), [`activity.py:55-56`](https://github.com/anlaufstelle/app/blob/main/src/core/models/activity.py#L55-L56), [`recent_client_visit.py:35`](https://github.com/anlaufstelle/app/blob/main/src/core/models/recent_client_visit.py#L35), [`quick_template.py:80`](https://github.com/anlaufstelle/app/blob/main/src/core/models/quick_template.py#L80). Fehlend in [`client.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/client.py) und [`work_item.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/work_item.py) — WorkItem-Dashboard-Filter `(facility, status, due_date)` wahrscheinlich langsam. | P2 |
| **** | TEILWEISE | Abgedeckt: [`test_mfa_enforcement.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/test_mfa_enforcement.py), [`e2e/test_pwa_offline.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/e2e/test_pwa_offline.py), [`test_file_vault.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/test_file_vault.py), [`test_snapshot_command.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/test_snapshot_command.py), `test_reencrypt_command_*` in [`test_encryption.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/test_encryption.py). Fehlend: dedizierte Middleware-Tests für [`facility_scope.py`](https://github.com/anlaufstelle/app/blob/main/src/core/middleware/facility_scope.py), [`htmx_session.py`](https://github.com/anlaufstelle/app/blob/main/src/core/middleware/htmx_session.py), [`password_change.py`](https://github.com/anlaufstelle/app/blob/main/src/core/middleware/password_change.py). | P2 |
| **** | BESTÄTIGT | `@pytest.mark.smoke` in 11 Dateien mit 34 Markierungen gesamt — [`CLAUDE.md`](https://github.com/anlaufstelle/app/blob/main/CLAUDE.md) spricht von ~40. Lücke: 6. | P3 |
| **** | BESTÄTIGT | 0 Treffer für `cache_page/cache.get/cache.set` in `src/core/`. Kein `CACHES`-Setting. Keine Cache-Abhängigkeiten. `@cached_property` 0 Treffer. | P3 |
| **** | BESTÄTIGT | [`views/clients.py:260-284`](https://github.com/anlaufstelle/app/blob/main/src/core/views/clients.py#L260-L284) `ClientDataExportPDFView.get` ruft `export_client_data_pdf` synchron im Request-Kontext. 6 DB-Queries + N Decrypt-Loops + WeasyPrint → bei großen Clients Timeout-Risiko. Rate-Limit 10/h/user vorhanden. | P2 |

#### Detail

```python
# snapshot.py:46-57 (heute):
for entry in stats.get("by_document_type", []):
    try:
        dt = DocumentType.objects.get(
            facility=facility,
            name=entry["name"],
            category=entry["category"],
        )
        entry["system_type"] = dt.system_type or ""
        entry["document_type_id"] = str(dt.id)
    except DocumentType.DoesNotExist:
        ...
```

**Fix-Pattern:** Dict aus einer einzigen Query bauen, dann Lookup:

```python
entries = stats.get("by_document_type", [])
dt_lookup = {
    (dt.name, dt.category): dt
    for dt in DocumentType.objects.filter(
        facility=facility,
        name__in=[e["name"] for e in entries],
        category__in=[e["category"] for e in entries],
    )
}
for entry in entries:
    dt = dt_lookup.get((entry["name"], entry["category"]))
    entry["system_type"] = dt.system_type if dt else ""
    entry["document_type_id"] = str(dt.id) if dt else ""
```

Regression-Test: `assertNumQueries` auf `create_or_update_snapshot(facility, 2026, 3)` mit 10 DocTypes. Vorher N+2 → nachher 3.

---

## Teil B — Remediation-Roadmap

Umsetzung in sechs Phasen, jede Phase als eigenes Planning-Issue unter #598.

### — Akute P1-Findings (~0.5-1 Tag, sequentiell)

1. **:** Migration `0057_rls_quicktemplate.py`, RLS-Tabellenzahl in CLAUDE.md/docs/ops-runbook.md auf 16 Direct + 3 Join = 19 aktualisieren, Testguard in `EXPECTED_TABLES` von `test_rls.py`.
2. **:** N+1 in `snapshot.py` beheben, Regression-Test via `CaptureQueriesContext`.
3. **:** Audit-Enum um `*_CREATE`-Actions und Rollenwechsel erweitern, `post_save`-Signal-Handler in `signals/audit.py`. Unit-Tests pro neue Action.
4. **:** Login-Brute-Force härten — zusätzlicher User-basierter Ratelimit `@ratelimit(key=lambda req: req.POST.get("username"), rate="10/h", block=True)` auf `LoginView`; Auto-Lockout via `services/bans.py` erweitern. E2E-Test: 10× falsches Passwort → Lockout → Admin-Unlock.

**Verifikation:** `make ci` + `make test-e2e-smoke` grün; `pytest src/tests/test_snapshot_command.py -x -v --capture=no`.

### — P2-Security/Performance/Tests (~1-2 Tage, teils parallel)

1. **:** `CSRF_COOKIE_HTTPONLY = True` in `prod.py`, HTMX-Smoke-Suite grün.
2. **:** `SESSION_COOKIE_SAMESITE = "Strict"` + `CSRF_COOKIE_SAMESITE = "Strict"`. Password-Reset-E-Mail-Link prüfen (ggf. Lax-Fallback für PW-Reset).
3. **:** `python-magic` evaluieren, in `services/file_vault.py:store_encrypted_file` Magic-Bytes-Validierung ergänzen; gegen `allowed_file_types` gegenchecken.
4. **:** `core`-Logger in Prod auf INFO; Regex-basierten PII-Scrubber in `core/logging.py` einbauen.
5. ** (zweistufig, Entscheidung 2026-04-21):**
 - **Schritt A — Messung:** `django-debug-toolbar` und/oder `silk` in Dev-Dependencies, typische Flows durchklicken, EXPLAIN ANALYZE auffallender Queries protokollieren.
 - **Schritt B — Indexe:** Nur real gemessen-langsame Queries indexieren. Migration `0058_perf_indexes.py`.
6. **:** Dedizierte Middleware-Tests (`test_middleware_facility_scope.py`, `test_middleware_htmx.py`, `test_middleware_password_change.py`).
7. **:** DSGVO-Export UX-Feedback (HTMX-Spinner + Caddy-Request-Timeout prüfen). Background-Task nur wenn Scale-Problem messbar.
8. ~~**D-1:**~~ **Verworfen 2026-04-21** — Self-Service Art. 16 nicht gewollt. Stattdessen DSGVO-TOMs + Fachkonzept § 17 aktualisieren .
9. ~~**D-2:** Dünner JSON-Export-View-Wrapper~~ — **bereits implementiert** (verifiziert 2026-04-21 bei in #601). `ClientDataExportJSONView` existiert in [`views/clients.py:233-257`](https://github.com/anlaufstelle/app/blob/main/src/core/views/clients.py#L233-L257). Audit-Agent-Miss.

### — Refactor/Dead-Code (~2-3 Tage, parallelisierbar)

1. **R-2:** `FacilityScopedViewMixin` in `views/mixins.py`, Domäne-für-Domäne migrieren (cases → events → workitems → clients → rest). Architektur-Guard in `test_architecture.py` ergänzen.
2. **R-3:** `HTMXPartialMixin` in `views/mixins.py`, 9 Views migrieren (1-2 pro PR).
3. **R-5:** `services/clients.get_client_or_none(facility, pk)` einführen, 4 Call-Sites migrieren.
4. **R-6:** Fallback-URL in `_time_filter_dropdown.html` entfernen; `events_partial_url` als required dokumentieren.
5. **God-Object-Split:** [`views/events.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/events.py) (744 LoC) → Deletion-Workflow nach `views/event_deletion.py`, Attachment-Download nach `views/attachments.py`.

**Parallel-Strategie:** Agents auf unabhängige Domains, E2E-Tests erst nach Merge seriell ([CLAUDE.md § Parallele Agents & Tests](https://github.com/anlaufstelle/app/blob/main/CLAUDE.md)).

### — P1 Test-Flakiness (~1-2 Tage)

1. **:** `wait_for_timeout`-Eradikation, Datei-für-Datei:
 - [`test_mobile.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/e2e/test_mobile.py) (8) → `data-testid`-Hooks + `locator(...).wait_for(state="visible")`.
 - [`test_retention_dashboard.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/e2e/test_retention_dashboard.py) (6) → `wait_for_url` nach Bulk-Aktionen.
 - [`test_statistics_charts.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/e2e/test_statistics_charts.py) (5) → Chart-rendered-Marker.
 - [`test_quick_capture.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/e2e/test_quick_capture.py) (3) → Formular-Prefill-Wait via Locator.
 - Rest (~20) systematisch.
2. **:** `@pytest.mark.smoke` auf ~40 kritische Flows erweitern (nach CLAUDE.md-Liste).

### — P3 Nice-to-have

1. ** Rest:** `WorkItem`-Query in [`client_export.py:98`](https://github.com/anlaufstelle/app/blob/main/src/core/services/client_export.py#L98) mit `select_related("assigned_to", "created_by")`.
2. **:** Selektiver Cache (Sidebar-Counts, 60s LOCMEM) als Experiment, messen, dann erweitern. Nur wenn Dashboard-Latenz real ist.
3. **:** Alpine-CSP-Build evaluieren — nur falls CSP-Audit es fordert. Breit testen, Fallback-Plan.
4. **D-3:** Nichts tun, bis Retention-Commands >60s dauern.

### — Architektur-Dokumentation

1. **:** `core_user`-RLS-Abwesenheit in **beiden** Quellen dokumentieren: `docs/security-notes.md` (Langform, Begründung, Cross-Facility-Admin-Flows) **und** [`CLAUDE.md`](https://github.com/anlaufstelle/app/blob/main/CLAUDE.md) (Kurz-Hinweis, damit Agents die Entscheidung beim Entwickeln nicht versehentlich „reparieren").
2. **D-1:** In DSGVO-TOMs und Fachkonzept § 17 festschreiben: „Anträge auf Berichtigung (Art. 16) laufen organisatorisch über Mitarbeiter/Leitung, kein Self-Service in der App."
3. **Report-Archiv:** Dieses Dokument wurde als [`docs/audits/2026-04-21-tiefenanalyse-v0.10.md`](https://github.com/anlaufstelle/app/blob/main/docs/audits/2026-04-21-tiefenanalyse-v0.10.md) abgelegt und zusätzlich als Kommentar an Issue #598 gepostet.

---

## Kritische Dateien (Kurzübersicht)

**Settings:** [`prod.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/prod.py) (,,), [`base.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/base.py) ( CSP, Logging).

**Sicherheit:** [`views/auth.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/auth.py) , [`services/bans.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/bans.py) ( Extension), [`models/audit.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/audit.py), [`signals/audit.py`](https://github.com/anlaufstelle/app/blob/main/src/core/signals/audit.py) , [`services/file_vault.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/file_vault.py) .

**RLS:** [`migrations/0047_postgres_rls_setup.py`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0047_postgres_rls_setup.py) (Muster), [`models/quick_template.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/quick_template.py) .

**Refactoring:** [`views/events.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/events.py), [`views/mixins.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/mixins.py), [`services/clients.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/clients.py), [`templates/components/_time_filter_dropdown.html`](https://github.com/anlaufstelle/app/blob/main/src/templates/components/_time_filter_dropdown.html).

**Performance:** [`services/snapshot.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/snapshot.py) , [`services/client_export.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/client_export.py) ( Rest), [`models/client.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/client.py), [`models/work_item.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/work_item.py) .

**Tests:** [`src/tests/e2e/`](https://github.com/anlaufstelle/app/tree/main/src/tests/e2e) , [`src/tests/`](https://github.com/anlaufstelle/app/tree/main/src/tests) , [`test_architecture.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/test_architecture.py) (neue Guards).

---

## Verifikations-Snippets

```bash
# Fokus / Gruppe / Parallel
pytest src/tests/test_<file>.py -x
pytest src/tests/test_a.py src/tests/test_b.py -x
make test-parallel

# Smoke (~2-3 min)
make test-e2e-smoke

# Full (vor Push)
make test-e2e-parallel

# N+1-Regression
pytest src/tests/test_snapshot_command.py src/tests/test_client_export.py -x -v --capture=no

# RLS-Sanity (erwartet 19 Zeilen nach S-1-Fix: 16 direct + 3 join)
sudo docker compose exec -T db psql -U anlaufstelle -c \
  "SELECT tablename FROM pg_policies WHERE policyname='facility_isolation' ORDER BY tablename;"

# Manuelle Verifikation: E2E-Server Port 8844 (docs/e2e-runbook.md § 6)
```

---

## Entscheidungen vom 2026-04-21

1. **D-1 (Art. 16 Self-Service):** Nicht gewollt. Berichtigungen laufen organisatorisch über Mitarbeiter/Leitung. In DSGVO-TOMs und Fachkonzept § 17 festschreiben.
2. ** (User-RLS):** Bewusste Architekturentscheidung. In `docs/security-notes.md` (Langform) **und** [`CLAUDE.md`](https://github.com/anlaufstelle/app/blob/main/CLAUDE.md) (Kurzhinweis) dokumentieren.
3. ** (Indexe):** Keine Slow-Query-Logs vorhanden. Zuerst silk/debug-toolbar + Messung, dann nur real-langsame Queries indexieren.
4. **Report-Speicherort:** Dieses Dokument + Kommentar an Issue #598.
