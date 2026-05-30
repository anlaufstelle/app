# Refactoring-Audit: Anlaufstelle

**Stand:** 2026-04-30
**Auditgegenstand:** `/work/anlaufstelle` — Django 6.0.4 / Python 3.13 / PostgreSQL 16 / HTMX + Alpine + Tailwind / AGPL-3.0
**Code-Stand:** `main` @ [`ec11530`](https://github.com/anlaufstelle/app/commit/ec11530) (Klientel→Person Sweep #604)
**Methode:** Code-First, parallele Multi-Agent-Analyse (5 Agents über Services / Views+Forms+Templates / Models+Migrationen / Tests / Performance+Ops+Doku), jede Aussage am aktuellen Code mit `path:line` belegt.
**Auditor:** Claude (Opus 4.7).
**Abgrenzung:** Aufbauend auf den Tiefenanalysen vom selben Tag ([2026-04-30-tiefenanalyse.md](2026-04-30-tiefenanalyse.md), [2026-04-30-tiefenanalyse-codex.md](2026-04-30-tiefenanalyse-codex.md)). Dieses Dokument ist **refactoring-fokussiert** — RF-nummerierte Kandidaten, Sicherungs-Reihenfolge, kleine PRs. Kein Code-Eingriff, nur Plan.

> **Hinweis Falsifikation:** Drei Vor-Audit-/Agent-Behauptungen konnten am Code nicht bestätigt werden und sind in §13 als „nicht aktionsreif" markiert.

---

## 1. Executive Summary

**Was solide ist:**

- Defense-in-Depth ist konzeptionell sauber: 4-Schicht-AuthZ (Role-Mixins → `FacilityScopedManager` → `FacilityScopeMiddleware` → PostgreSQL-RLS), append-only-Trigger auf `AuditLog`/`EventHistory`, MultiFernet-Feldverschlüsselung, ClamAV + Magic-Bytes, `RequireSudoModeMixin` auf sensitiven Aktionen.
- Test-Asset überdurchschnittlich: 1.953 Funktionen über 162 Dateien, **funktionaler Cross-Tenant-Test mit echter NOSUPERUSER-DB-Rolle** ([`test_rls_functional.py`](../../src/tests/test_rls_functional.py)), RBAC-Matrix über 4 Rollen, Architektur-Guards.
- Datenmodell trifft die niedrigschwellige Sozialarbeits-Domäne präzise (Pseudonym-First, datensparsame Pflichtfelder, anonyme Events, k-Anonymisierung, Schichtübergabe, abgestuftes Offline).
- Doku-Disziplin (13 ADRs, FAQ-Sync, DSGVO-Vorlagen, ausführbarer Restore-Drill).

**Was riskant ist (in Reihenfolge der Behebung):**

1. **Vier kleine Bypass-Lücken in den frisch eingeführten Sicherheitslayern** — Open-Redirect (`views/workitem_actions.py:62`), File-Upload fail-open (`services/file_vault.py:131-134` + `forms/events.py:198-200`), SSRF im Webhook (`services/breach_detection.py:155-171`), IP-Spoof in Maintenance (laut Vor-Audit `middleware/maintenance.py:81-86`). Alle einzeln klein, in Summe Defense-in-Depth-Erosion.
2. **K-Anonymisierungs-Setting ist Dead Code:** `Settings.retention_use_k_anonymization` (Migration 0049) wird in `services/retention.py` **nie** abgefragt (verifiziert: `grep` ergibt nur Doc + `exclude`-Filter). DSGVO-Argumentation potenziell irreführend.
3. **`Client.anonymize` (`models/client.py:105–203`) durchbricht Aggregate-Grenzen:** Raw-SQL `SET LOCAL session_replication_role = replica`, Service-Imports im Model-Body, fasst 7 Fremd-Aggregate an. Bremst jeden zukünftigen Plugin-Schnitt.
4. **`services/retention.py` (974 LOC) DRY-Verstoß** über vier Retention-Strategien an drei Stellen — per Kommentar zementiert (Z.488). Drift zwischen `collect_doomed_events` (Vorhersage) und `enforce_*` (echte Löschung) testtechnisch nicht abgedeckt.
5. **Self-Hosting-Postscript-Falle:** Cron-Jobs (Backup/Retention/Breach-Detection) liegen außerhalb von Compose; NOSUPERUSER manuell. Frischer Stack läuft technisch, aber **ohne Backups** und **ohne Retention**.

**Was zuerst verbessert werden sollte (Bottom-Line):**

| Sprint | Fokus | Begründung |
|---|---|---|
| 1 | **Charakterisierungstests + 4 Quick-Wins-Security** | Grundlage für jedes Refactoring. Open-Redirect-Helper, Default-Whitelist, SSRF-Schutz, k-Anon-Test sind alle ≤1 PT — größter Sicherheits-Impact pro Stunde. |
| 2 | **Service-Aufteilung Event/Retention** | Voraussetzung für Plugin-Architektur . Größte mittelfristige Wartbarkeits-Schuld. |
| 3 | **Strukturelle Verbesserungen** | k-Anonymisierung an Lebenszyklus, `Client.anonymize`-Service-Extraktion, Pagination-Mixin, HTMX-Mixin aktivieren. |
| 4 | **Doku/Ops-Härtung** | `scripts/initial-setup.sh`, Cron-Sidecar, Health-Endpoint-Erweiterung, ADRs. |

**Was bewusst NICHT angefasst werden sollte (siehe §13):** AuditLog-Triggerwerk (Migration 0024 / 0067 / 0074), RLS-Policies (Migration 0047), `test_rls_functional.py`, append-only-EventHistory-Mechanik. Erst **mehr** Tests dort, dann nichts.

---

## 2. Architekturüberblick

| Bereich | Zweck | Bewertung | Risiko | Refactoring-Bedarf |
|---|---|---|---|---|
| `src/core/models/` (22 Dateien) | Domänen-Aggregate (Client, Event, Case, Episode, WorkItem, AuditLog, EventHistory, …) | Gut strukturiert (1 Model/Datei). Sprache fast einheitlich. | Soft-Delete nur an Event/Attachment. `Client.anonymize` zu mächtig. | Mittel (RF-002, RF-006, RF-007) |
| `src/core/services/` (35 Module, 6.259 LOC) | Business-Logik-Layer (verbindlich, ADR-002) | Service-Layer-Pattern konsequent durchgezogen. | `event.py` 683 LOC, `retention.py` 974 LOC, `file_vault.py` 395 LOC. DRY-Verstoß in Retention. | **Hoch (RF-001, RF-003, RF-004)** |
| `src/core/views/` (28 Module, 4.112 LOC) | CBVs, dünne Schicht über Services | Konvention CBV durchgezogen. Mixins (`FacilityScopedViewMixin`, `HTMXPartialMixin`) ungenutzt. | 13× HTMX-Branching dezentral. Keine Generic-CBVs (`ListView`/`UpdateView`) genutzt. | Mittel (RF-008, RF-009, RF-013) |
| `src/core/forms/` (7 Module) | ModelForms mit Tailwind-Widget-Klassen | Konsistent. | `forms/events.py:198-200` fail-open bei `Settings.DoesNotExist`. | Niedrig (RF-005) |
| `src/templates/` (88 HTML) | DE/EN, HTMX-Partials in `partials/` | gettext-Abdeckung breit (821 `{% trans %}`). | `non_field_errors`-Block in 3 Form-Templates fehlt. `tabindex="1/2/100/101"` in `events/create.html`. | Niedrig (RF-014, RF-015) |
| `src/core/middleware/` (7 Dateien) | Facility-Scoping, HTMX-Session, Maintenance, MFA, Sprache | Reihenfolge bewusst. Defense-Layer korrekt vor Auth. | Maintenance-Cache pro Worker (LocMem), spoofbare IP in Allowlist. | Mittel (RF-019, RF-020) |
| `src/core/migrations/` (79 Migrationen) | Schema-Evolution, RLS, Trigger | Reife: 13 ADRs, Sentinel-Konstanten in `0047`, RLS + Trigger gerichtsfest. | 9× `RunPython.noop` ohne Reverse-Doku. Inline-Imports in `retention.py`. | Niedrig (kosmetisch, RF-024) |
| `src/anlaufstelle/settings/` | base/dev/test/e2e/prod | Saubere Vererbung, Sentry mit `send_default_pii=False`. | **Kein `CACHES`-Backend** → LocMem pro Worker. Sudo-Mode-Bypass per Setting. Passwort-Mindestlänge implizit 8. | **Hoch (RF-016, RF-017, RF-018)** |
| `docker-compose.{yml,prod.yml,e2e.yml}` + `Caddyfile` | Multi-Stage-Image, Caddy-Auto-TLS, ClamAV-Sidecar | Solide Grundlage; Restore-Drill ausführbar. | Migrations im Web-Container, Cron als Host-Crontab, dev-Postgres `0.0.0.0:5432`. | Mittel (RF-021, RF-022, RF-023) |
| `src/tests/` + `src/tests/e2e/` | 1.953 Tests / 348 E2E | Pyramide klassisch. RLS + RBAC-Matrix vorbildlich. | Query-Count nur an 3 Hot-Spots. k-Anon-Negativtest fehlt. Open-Redirect-Fuzz fehlt. | Mittel (–T08) |

**Architekturprinzipien (erkennbar, ADR-002):** Service-Layer als verbindliche Logikschicht; CBV-Konvention; Multi-Tenant via 4-Schicht-Defense; Pseudonym-First; Datensparsamkeit als Modellprinzip.

**Wo Verantwortlichkeiten vermischt sind:**

- `Client.anonymize` (Model) öffnet Transaktion und nutzt Roh-SQL → gehört in Service.
- `services/event.py` mischt Field-Template-Lookup, Sensitivity-Filter, File-Marker-Parsing, CRUD und 4-Augen-Workflow.
- `services/retention.py` enthält Strategy-Logik dreifach (Vorhersage, Ausführung, Settings-Mapping).
- `views/events.py` enthält Attachment-Marker-Normalisierung statt Service-Aufruf.

---

## 3. Wichtigste Refactoring-Kandidaten

> Vollständige Reihenfolge nach Priorität (P0 zuerst). Nur Top-Kandidaten ausführlich; gesammelte Liste am Ende von §3.

---

### RF-001: Open-Redirect-Helper zentralisieren

- **Priorität:** P0
- **Bereich:** Views/Sicherheit
- **Fundstellen:**
 - [`src/core/views/workitem_actions.py:61-63`](../../src/core/views/workitem_actions.py): `next_url = request.POST.get("next"); if next_url and next_url.startswith("/"): return redirect(next_url)` — **fehlt `not startswith("//")`**.
 - [`src/core/views/sudo_mode.py:25-32`](../../src/core/views/sudo_mode.py): `_safe_next` — Pattern korrekt mit `raw.startswith("/") and not raw.startswith("//")`.
- **Problem:** `startswith("/")` matcht auch `//evil.example/login`, das Browser als protokoll-relative URL interpretieren. Phishing-Vektor.
- **Risiko:** Authentifizierte Nutzer können zu Fake-Login-Seite umgeleitet werden — Glaubwürdigkeit der App leidet, Vertrauensbruch besonders bei Klientel mit erhöhter Schutzbedürftigkeit.
- **Empfehlung:** `_safe_next` in `src/core/views/utils.py` als `safe_redirect_path(raw: str | None) -> str` ausziehen, beide Call-Sites darauf umstellen, Architektur-Test ergänzen, der `redirect(<unvalidiertes next>)` per AST-Walk verbietet.
- **Geschätzter Aufwand:** S (≤1 PT)
- **Vorher notwendige Tests:** Parametrischer Fuzz (``): `["/", "/x", "//evil", "///evil", "javascript:alert(1)", "http://x", "/x/../../y"]` → nur die ersten beiden „safe".
- **Mögliche Nebenwirkungen:** Keine — engt nur die Pfad-Akzeptanz ein.
- **Sichere Umsetzungsschritte:**
 1. Test schreiben ( → 6 Cases).
 2. `safe_redirect_path` in `views/utils.py` (Copy aus `sudo_mode.py:25-32`).
 3. Beide Call-Sites umstellen.
 4. Architektur-Test `test_no_unchecked_next_redirect` in `test_architecture.py`.

---

### RF-002: `Client.anonymize` → `services/clients.anonymize_client`

- **Priorität:** P0 (Strukturschuld mit Compliance-Hebel)
- **Bereich:** Models / Services / Domain Design
- **Fundstellen:** [`src/core/models/client.py:105-203`](../../src/core/models/client.py).
- **Problem:** Methode auf Model-Ebene (1) öffnet Transaktion, (2) fasst 7 Fremd-Aggregate an (Case/Episode/WorkItem/Event/EventHistory/EventAttachment/DeletionRequest), (3) nutzt Roh-SQL `SET LOCAL session_replication_role = replica` (Z.184) zur Trigger-Umgehung, (4) importiert mitten im Body aus `core.services.file_vault` (Z.168). Bricht ADR-002 („Business-Logik in services/").
- **Risiko:** Modell-Layer kennt Trigger-Topologie der DB. Multi-Site-Edit für jede Trigger-Änderung. Bei (Plugin-Architektur) wird das zur Bremse, weil Anonymisierungs-Logik nicht ausgetauscht werden kann, ohne das Model zu ändern.
- **Empfehlung:**
 1. Logik 1:1 nach `services/clients.py:anonymize_client(client, *, user, request=None)`.
 2. Trigger-Bypass in `services/_db_admin.py` als `with_replica_role`-ContextManager kapseln.
 3. `Client.anonymize` als dünner Aufruf erhalten (rückwärtskompatibel) oder entfernen, wenn keine Aufrufer mehr.
- **Geschätzter Aufwand:** M (1–3 PT)
- **Vorher notwendige Tests:** Charakterisierungs-Tests gegen aktuellen Output : mind. 3 Cases (anonymizable, with-attachments, with-deletion-request) + Trigger-State-Verify.
- **Mögliche Nebenwirkungen:** Wenn Tests Klartext nach `client.anonymize` lesen statt nach `anonymize_client(...)`, müssen sie umgestellt werden.
- **Sichere Umsetzungsschritte:**
 1. Charakterisierungstests fixieren das aktuelle Verhalten.
 2. Service-Funktion neu erstellen, Body kopieren, alle Imports top-level.
 3. `Client.anonymize` ruft Service auf (1-Zeile-Delegation).
 4. Tests grün → Direkt-Aufrufe in Code-Base auf Service umstellen, `Client.anonymize` deprecaten.

---

### RF-003: `services/retention.py` Strategy-Duplikate entkoppeln

- **Priorität:** P1
- **Bereich:** Services / DRY
- **Fundstellen:** [`src/core/services/retention.py:485-551`](../../src/core/services/retention.py) (`collect_doomed_events`), Z.612-740 (vier `enforce_*`), Z.861-973 (`create_proposals_for_facility`). Inline-Imports an Z.446, 491, 561-563, 617, 643, 673, 711, 747, 866 (9 Stellen).
- **Problem:** Vier Aufbewahrungsstrategien (anonymous / closed-case / disengaged / document-type) werden an drei Stellen quasi-identisch ausgedrückt. Kommentar bei Z.488 verlangt Synchronisation explizit. Drift-Risiko: `collect_doomed_events` (Vorhersage für UI) und `enforce_*` (echte Löschung) können auseinanderlaufen, ohne dass Tests es zwingend abfangen.
- **Risiko:** Compliance-Fehler (DSGVO Art. 5 lit. e): Vorhersage zeigt 50 zu löschende Events, ausgeführt werden 47. Erste Stelle, wo Aufsicht auf Nachfrage Belege anfordert, wird unbequem.
- **Empfehlung:** Zentrale `_strategy_querysets(facility, settings_obj, now)` mit vier `RetentionStrategy`-Datacontainern (`name`, `filter_q`, `cutoff_attr`, `audit_label`). Drei Konsumenten teilen denselben Bauplan. Inline-Imports an Modulkopf heben.
- **Geschätzter Aufwand:** L (1 Sprint)
- **Vorher notwendige Tests:** Charakterisierung : jede Strategie einzeln + Cross-Strategy-Intersection (Event in mehreren Kategorien) + Boundary (`>cutoff` weg, `=cutoff` bleibt).
- **Mögliche Nebenwirkungen:** Soft-Delete-Counter in UI (`RetentionProposal`) muss exakt-identische Mengen produzieren wie `enforce_*` — Test muss das prüfen.
- **Sichere Umsetzungsschritte:**
 1. Inline-Imports an Top heben (kleiner separater PR).
 2. `RetentionStrategy`-Dataclass + `_strategy_querysets`-Generator (1. Konsument: `collect_doomed_events`).
 3. `enforce_*` als 2. Konsument umstellen.
 4. `create_proposals_for_facility` als 3. Konsument.

---

### RF-004: `services/event.py` (683 LOC) aufteilen

- **Priorität:** P1
- **Bereich:** Services / Bounded Contexts
- **Fundstellen:** [`src/core/services/event.py`](../../src/core/services/event.py) — 23 Funktionen, mischt:
 - Field-Template-Lookup (Z.24-39)
 - Sensitivity-Filter (Z.42-76)
 - File-Marker-Parsing (`normalize_file_marker`, Z.266-300)
 - CRUD (Z.473-689)
 - 4-Augen-Deletion-Workflow (Z.640-689)
- **Problem:** God-Modul. Jede Änderung in einem Bereich riskiert Regression in den anderen. Imports von `file_vault`, `sensitivity`, `encryption` in einem Modul.
- **Risiko:** Verlangsamt Plugin-Schnitt; Reviewer können den Blast-Radius einer Änderung nicht eingrenzen; Test-Suite läuft länger als nötig.
- **Empfehlung:** Aufteilen in `services/events/{crud,context,deletion,fields}.py`:
 - `crud.py`: `create_event`, `update_event`, `soft_delete_event`
 - `context.py`: `build_event_detail_context`, `filtered_server_data_json`
 - `deletion.py`: `request_deletion`, `approve_deletion`, `reject_deletion`
 - `fields.py`: `build_field_template_lookup`, `normalize_file_marker`, `remove_restricted_fields`
- **Geschätzter Aufwand:** M (1–3 PT)
- **Vorher notwendige Tests:** Service-Unit-Tests — die heutige Test-Abdeckung ist über View-Tests, nicht direkt am Service. Das macht ein Move-Refactoring riskant.
- **Mögliche Nebenwirkungen:** ~30 Imports im Code-Base umstellen — Routine.
- **Sichere Umsetzungsschritte:**
 1. Service-Unit-Tests gegen aktuelle Funktionsschnittstellen.
 2. `events/__init__.py` als Re-Export-Hub anlegen, alle Funktionen weiter über `from core.services.event import …` verfügbar.
 3. Funktionen modulweise umziehen, jeden Move einzeln committen.
 4. Re-Export-Hub am Ende ggf. entfernen.

---

### RF-005: File-Upload Default-Whitelist (fail-closed)

- **Priorität:** P1
- **Bereich:** Services / Forms / Sicherheit
- **Fundstellen:**
 - [`src/core/services/file_vault.py:131-134`](../../src/core/services/file_vault.py): `try: facility_settings = Settings.objects.get(facility=facility) except Settings.DoesNotExist: return # No settings yet → no whitelist to enforce.`
 - [`src/core/forms/events.py:198-200`](../../src/core/forms/events.py): `try: facility_settings = … except Settings.DoesNotExist: return cleaned`.
 - In beiden zusätzlich: `if not allowed: return` lässt leere Whitelist passieren.
- **Problem:** Eine Facility ohne Settings-Row akzeptiert jeden Dateityp, jede Größe, ohne Whitelist-Block. Race zwischen Facility-Anlage und Settings-Erzeugung.
- **Risiko:** Upload-Vektor offen; Defense-Layer kompromittiert. Auch wenn `setup_facility` Settings normalerweise mit anlegt — die Annahme darf nicht in Code zementiert sein.
- **Empfehlung:** Hardcoded Default-Whitelist (`{"pdf","jpg","jpeg","png","docx","odt"}`) und `max_file_size_mb=10` als Konstanten in `core.constants`. Bei `Settings.DoesNotExist` → Default. Architektur-Test `test_every_facility_has_settings` als post-migrate-Signal.
- **Geschätzter Aufwand:** S (≤1 PT)
- **Vorher notwendige Tests:**: Settings löschen → Upload muss fail-closed sein.
- **Mögliche Nebenwirkungen:** Keine, Default ist restriktiver als heute.
- **Sichere Umsetzungsschritte:**
 1.-Test schreiben (rot).
 2. Konstanten anlegen, beide Stellen umstellen.
 3. Test grün, post-migrate-Signal ergänzen.

---

### RF-006: K-Anonymisierung an `enforce_retention` anschließen oder Setting entfernen

- **Priorität:** P1 (Compliance-Glaubwürdigkeit)
- **Bereich:** Services / Domain Design
- **Fundstellen:** Verifiziert via `grep "retention_use_k_anonymization\|k_anonymize" src/core/services/retention.py` — nur 2 Treffer (Docstring Z.776, `exclude(Q(k_anonymized=True))` Z.784). Setting kommt aus Migration `0049_k_anonymization.py`. `Client.k_anonymize` existiert in `models/client.py:205-214`, wird aber nirgends aufgerufen.
- **Problem:** Das Schema hat ein Setting, das real nichts bewirkt. Für DSGVO-Argumentation gegenüber Aufsicht potenziell irreführend — „wir haben k-Anonymisierung aktiviert" stimmt nicht.
- **Risiko:** Wenn die Aufsicht das Setting prüft und nachweist, dass es nicht greift, beschädigt es die Glaubwürdigkeit weiterer DSGVO-Aussagen.
- **Empfehlung:** **Entscheidung verlangt** — zwei Pfade:
 - **A (Anschluss):** In `anonymize_clients` (Z.794ff.) Conditional einführen: `if settings_obj.retention_use_k_anonymization: k_anonymize_client(client, k=settings_obj.k_anonymity_threshold) else: client.anonymize`. Tests für beide Pfade.
 - **B (Entfernen):** Setting + `Client.k_anonymized` + Service in eigene Cleanup-Migration entfernen, ADR „warum wieder rausgeflogen" schreiben.
- **Geschätzter Aufwand:** S (Pfad A inkl. Tests) oder M (Pfad B inkl. Migration + ADR).
- **Vorher notwendige Tests:** (k-Anon-Negativtest, k-Anon-Positivtest mit `k=5`).
- **Mögliche Nebenwirkungen Pfad A:** Bestehende Retention-Läufe verändern Verhalten — User-OK einholen.
- **Sichere Umsetzungsschritte (Pfad A):**
 1.-Tests schreiben (zeigen heute, dass Setting ignoriert wird).
 2. Conditional in `anonymize_clients` ergänzen.
 3. Top-level-Import `from core.services.k_anonymization import k_anonymize_client`.
 4. Doku-Update in `docs/dsgvo-templates/verarbeitungsverzeichnis.md`.

---

### RF-007: Webhook-URL gegen SSRF härten

- **Priorität:** P1
- **Bereich:** Services / Sicherheit
- **Fundstellen:** [`src/core/services/breach_detection.py:155-171`](../../src/core/services/breach_detection.py) — `urllib.request.urlopen(req, timeout=5)` ohne Schema-/Host-Whitelist. `# noqa: S310 — vom Operator konfigurierte URL` deaktiviert Bandit.
- **Problem:** Webhook-URL kommt aus DB-gespeicherter `Settings` — Operator-konfiguriert, aber nicht statisch. URL `http://169.254.169.254/...` (Cloud-Metadata), `file://`, internes Netz leakt Breach-Payload (Facility-Name, User-IDs, Audit-IDs).
- **Risiko:** Begrenzt (Tenant-extern), aber Defense-in-Depth-Erosion in einem Layer, der bewusst pre-Auth läuft (#685, DSGVO Art. 33/34).
- **Empfehlung:** `_validate_webhook_url(url) -> None` mit (a) `urlparse` + Schema in `{"https"}`, (b) `socket.gethostbyname` + `ipaddress.ip_address(...).is_private/is_loopback/is_link_local` → Reject. Bei Setzen der URL in der UI denselben Check.
- **Geschätzter Aufwand:** S
- **Vorher notwendige Tests:**: parametrisch über `[file://, http://127.0.0.1, http://169.254.169.254, http://10.0.0.1, https://valid.example/hook]`.
- **Mögliche Nebenwirkungen:** DNS-Resolve macht Setting-Save langsamer (~50 ms). Cache nicht nötig (selten gespeichert).
- **Sichere Umsetzungsschritte:**
 1.-Test schreiben.
 2. Validator implementieren (in `services/breach_detection.py` oder neu `services/_url_safety.py`).
 3. In `_post_webhook` aufrufen, in Settings-Form auch.

---

### RF-008: HTMX-Partial-Mixin aktivieren

- **Priorität:** P2
- **Bereich:** Views / Konsistenz
- **Fundstellen:** [`src/core/views/mixins.py:61-83`](../../src/core/views/mixins.py) `HTMXPartialMixin` — laut Verifikation **0 Verwendungen**. Stattdessen 13× direktes `request.headers.get("HX-Request")` in `views/clients.py:73`, `cases.py:98`, `audit.py:70`, `statistics.py`, `retention.py` (×2), `workitem_bulk.py`, `events.py:63`, `search.py`, `workitems.py`, `attachments.py` u. a.
- **Problem:** Pattern ist 13× dezentral wiederholt. Jede Anpassung am HTMX-Header-Check (z.B. zusätzlicher `HX-Boosted`-Check für progressive enhancement) braucht 13 Edits.
- **Risiko:** Niedrig technisch, mittel wartungstechnisch. Code-Drift wahrscheinlich.
- **Empfehlung:** `HTMXPartialMixin` mit `template_name` + `partial_template_name` als Klassenattribute, `render_htmx_or_full(context)`-Methode. List-Views umstellen (start: `ClientListView`, `CaseListView`, `AuditLogView`, `SearchView`).
- **Geschätzter Aufwand:** M (13 Views × ~5 min, plus Tests pro View)
- **Vorher notwendige Tests:**: pro umgestellter View `HX-Request: true` → Partial; ohne Header → Full-Page.
- **Mögliche Nebenwirkungen:** Wenn ein View neben Partial/Full-Page noch andere Branches hat (z.B. JSON), Mixin nicht erzwingen.
- **Sichere Umsetzungsschritte:**
 1.-Tests schreiben (für Ziel-View 1 von 13).
 2. View 1 umstellen, Test grün.
 3. Iterativ je View: 1 Commit pro View.

---

### RF-009: Pagination zentralisieren + WorkItem-Inbox cappen

- **Priorität:** P2
- **Bereich:** Views / Performance
- **Fundstellen:**
 - [`src/core/views/clients.py:53-59`](../../src/core/views/clients.py): nutzt `DEFAULT_PAGE_SIZE` aus `core/constants.py`.
 - [`src/core/views/cases.py:85-86`](../../src/core/views/cases.py): identisches Muster.
 - [`src/core/views/audit.py:49-50`](../../src/core/views/audit.py): hartkodiert `Paginator(queryset, 50)`.
 - [`src/core/views/workitems.py`](../../src/core/views/workitems.py): **keine Pagination** für Inbox. Bei 500 Inbox-Items kein Cap.
 - [`src/core/services/feed.py:64,88,100,116,124`](../../src/core/services/feed.py): 5× hartes `[:200]`-Slice — schneidet busy days ab.
- **Problem:** Pagination ist ad hoc pro View. Audit hartkodiert `50` — Inkonsistenz. WorkItem-Inbox ohne Cap → Performance-Risiko bei aktiven Einrichtungen. Feed-Slice schneidet Tage stumm ab.
- **Risiko:** UI-Timeout bei großen Datensätzen, Kommunikation „warum sehe ich nicht alle Events von gestern?".
- **Empfehlung:**
 1. `PaginatedListMixin` in `views/mixins.py` mit `page_size` als Klassenattribut (Default `DEFAULT_PAGE_SIZE`).
 2. Audit auf `DEFAULT_PAGE_SIZE` umstellen oder explizit `page_size = 50` als Klassenattribut.
 3. WorkItem-Inbox Pagination einführen (Default 25).
 4. Feed-Service: `[:200]` in benannte Konstante `FEED_MAX_PER_TYPE = 100` und „Mehr"-HTMX-Loadmore.
- **Geschätzter Aufwand:** M
- **Vorher notwendige Tests:** Page-Boundary (page=0/page=999999), Filter+Pagination kombiniert.

---

### RF-010 … RF-027 (Kompakt-Liste)

| ID | Titel | Prio | Aufwand | Bereich |
|---|---|---|---|---|
| **RF-010** | AuditLog-Pruning ohne `DISABLE TRIGGER` (SECURITY-DEFINER-Funktion mit `session_replication_role = replica` lokal) | P1 | M | Services/DSGVO |
| **RF-011** | `reencrypt_fields` um `EventHistory` + `EventAttachment` erweitern | P2 | M | Services |
| **RF-012** | Soft-Delete-Mixin (oder ADR „warum nur Event soft-deletet") | P2 | M | Models |
| **RF-013** | `EventCreateView.get` (72 LOC) und `EventUpdateView.get` (54 LOC) auf Service-Aufrufe reduzieren — Attachments-Marker-Normalisierung in `services/event.fields:build_attachment_context` ziehen | P2 | S | Views/Services |
| **RF-014** | `non_field_errors`-Block in 3 Form-Templates ergänzen (`clients/form.html`, `cases/form.html`, `workitems/form.html`) — Vorbild `events/create.html:48-54` | P2 | S | Templates |
| **RF-015** | `tabindex="1/2/100/101"` in `templates/core/events/create.html:64,101,175,179` entfernen (DOM-Reihenfolge aufräumen) | P3 | S | Templates |
| **RF-016** | `CACHES`-Backend (Redis) in `prod.py` — Voraussetzung für Multi-Worker (Maintenance-Cache + Ratelimit konsistent) | P1 | M | Settings/Ops |
| **RF-017** | Sudo-Mode-Bypass-Setting in `prod.py` mit `ImproperlyConfigured` schützen (`services/sudo_mode.py:67-69`) | P1 | S | Settings |
| **RF-018** | Passwort-Mindestlänge auf 12 (`settings/base.py:127-132`, `MinimumLengthValidator OPTIONS`) | P2 | S | Settings |
| **RF-019** | IP-Spoof-Fix in `middleware/maintenance.py` — `_client_ip` durch `core.signals.audit.get_client_ip` ersetzen, `TRUSTED_PROXY_HOPS` respektieren | P1 | S | Middleware/Sicherheit |
| **RF-020** | `style-src 'unsafe-inline'` in `settings/base.py:303` enge ziehen | P3 | M | Settings/Sicherheit |
| **RF-021** | `scripts/initial-setup.sh` für `setup_facility` + `ALTER ROLE … NOSUPERUSER` + Health-Verifikation; Health-Endpoint um `db_user_is_superuser` erweitern | P1 | M | Ops |
| **RF-022** | Cron-Service in `docker-compose.prod.yml` (supercronic-Sidecar) für Backup/Retention/Breach-Detection — frischer Stack soll **nicht** ohne Backups starten | P1 | M | Ops |
| **RF-023** | `docker-compose.yml` (dev) Postgres an `127.0.0.1:5432:5432` binden + Header-Kommentar „Niemals auf Public-Server" | P2 | S | Ops/Sicherheit |
| **RF-024** | Inline-Imports in `services/retention.py` an Modulkopf heben (Z.446, 491, 561-563, 617, 643, 673, 711, 747, 866) — wenn Zirkel auftritt, dokumentieren | P3 | S | Services |
| **RF-025** | `.pre-commit-config.yaml` mit ruff (check + format), mypy `core/services`, `manage.py makemigrations --check --dry-run` | P2 | S | Tooling |
| **RF-026** | Ruff-Regelumfang erweitern (`B`, `UP`, `SIM`, `N`, `S`) — schrittweise per `[tool.ruff.lint.per-file-ignores]` | P2 | M | Tooling |
| **RF-027** | DCO-Sign-off in `CONTRIBUTING.md` + `Code of Conduct` (Contributor Covenant 2.1) | P2 | S | Governance |

---

## 4. Security- und Datenschutzbefunde

| ID | Prio | Fundstelle | Risiko | Empfehlung | Testbedarf |
|---|---|---|---|---|---|
| RF-001 | P0 | `views/workitem_actions.py:62` | Open-Redirect via `//evil` | `safe_redirect_path` zentral | (Fuzz) |
| RF-005 | P1 | `services/file_vault.py:131-134` + `forms/events.py:198-200` | Upload fail-open ohne Settings-Row | Hardcoded Default-Whitelist | |
| RF-007 | P1 | `services/breach_detection.py:155-171` | SSRF im Webhook | URL-Validator (Schema + IP-Klassen) | |
| RF-019 | P1 | `middleware/maintenance.py:81-86` (laut Vor-Audit) | IP-Spoof — Maintenance-Allowlist umgehbar | `get_client_ip` aus `signals/audit` | E2E mit gespooftem `X-Forwarded-For` |
| RF-017 | P1 | `services/sudo_mode.py:67-69` + `settings/prod.py` | Sudo-Mode global per `.env` deaktivierbar | `ImproperlyConfigured` in `prod.py` | Architektur-Test |
| RF-018 | P2 | `settings/base.py:127-132` | Passwort-Mindestlänge 8 (Django-Default) | `OPTIONS={"min_length": 12}` | Form-Validator-Test |
| RF-020 | P3 | `settings/base.py:303` | CSP `style-src 'unsafe-inline'` | Inline-Styles auflisten + entfernen | CSP-Reporting-Endpoint |
| RF-006 | P1 | `services/retention.py` (Setting nie abgefragt) | k-Anon-Setting ist Dead Code | Conditional in `anonymize_clients` | |
| RF-010 | P1 | `services/retention.py:822-858` | AuditLog-Trigger bei SIGKILL disabled | `SECURITY DEFINER`-Funktion | Chaos-Test mit SIGKILL |
| RF-011 | P2 | `commands/reencrypt_fields.py` | Deckt nur Event, nicht History/Attachment | Command erweitern | Re-Encrypt-Test über alle Tabellen |
|| P2 | `models/client.py:35-39` | Pseudonym im Klartext (DB-Index, Filename, AuditLog-Detail) | **Bewusste Defer-Entscheidung** ([Issue #717](https://github.com/anlaufstelle/app/issues/717)). Kein Action-Item. ||
|| P3 | `models/client.py:54-63` | `Client.notes` Help-Text warnt, ist nicht erzwungen | Optional `notes_is_sensitive`-Flag analog Event-Sensitivity | Form-Validation-Test |

> **Aufgenommen aus Vor-Audit, jeweils am Code verifiziert.** Pseudonym-Klartext und `Client.notes`-Sensitivität sind als bewusste Defer-Entscheidungen dokumentiert (s. README + Threat-Model).

---

## 5. Datenmodell und Domänenlogik

**Bestand:**

- 22 Models in einer App `core` — Aggregate-Wurzel `Facility`, alle fachlichen Entities über `FacilityScopedManager` und PostgreSQL-RLS gescoped.
- Domänen-Sprache (Person, Fall, Episode, Zeitstrom, Übergabe) konsistent.
- Pseudonym-First in `Client`-Form (4 Felder, 1 Pflicht).
- Sensitivity-3-Stufen (NORMAL/ELEVATED/HIGH) im Field-Template.
- Anonyme Events via `client=None` + `is_anonymous=True`.

**Hauptbefunde:**

| Befund | Status | Aktion |
|---|---|---|
| `Client.anonymize` durchbricht Aggregate-Grenzen | RF-002 | Service-Extraktion |
| Soft-Delete inkonsistent (nur `Event.is_deleted`, `EventAttachment.deleted_at`, sonst nichts) | RF-012 | Mixin oder ADR |
| Fall-Versionierung fehlt: `Case`, `Episode`, `OutcomeGoal`, `WorkItem` haben nur `updated_at`, kein History-Append | offen | Generisches `RecordHistory` (Append-only-Trigger) — **erst nach Plugin-Schnitt-Entscheidung**, nicht jetzt |
| Sprachleitlinie #604 nur halb durchgezogen: 22 `_("Klient…")`-Strings in Models/Services/Forms/Audit-Choices | offen | Bulk-Replace + `makemessages -a` (siehe Vor-Audit). Für diesen Refactoring-Plan **nicht prioritär**, kosmetisch. |
| Aliasing für Pseudonyme: `UniqueConstraint(facility, pseudonym)` zwingt 1:1, Realität: gleiche Person mit mehreren Spitznamen | offen | `ClientAlias`-Tabelle — nur wenn Pilot-Daten Bedarf zeigen, sonst YAGNI |
| `document_type.py` (399 LOC, größtes Model): `class SystemType` (BAN, CRISIS, NEEDLE_EXCHANGE) hartcodiert auf Streetwork | bewusst | Erst (Plugin-Architektur) — heute **nicht anfassen** |

**Migrations-Hygiene** (verifiziert): doppelte Nummern `0025_*` und `0049_*` existieren, sind aber durch `0027_merge_*` und `0051_merge_20260417_2123.py` korrekt aufgelöst. Django-Migrate funktioniert. **Kein P0-Befund** — entgegen einer Behauptung im Agent-Report.

---

## 6. Views, Forms, Templates und HTMX

**Bestand:**

- 28 View-Module, 4.112 LOC. Konvention CBV durchgezogen, aber als „Funktion in Klasse" (35 von 36 erben direkt von `django.views.View` mit `def get/post`, nur 4 nutzen `TemplateView`).
- `HTMXPartialMixin` und `FacilityScopedViewMixin` existieren in `views/mixins.py:45-83`, aber **0 Verwendungen** (verifiziert).
- 13× HTMX-Branching dezentral (`request.headers.get("HX-Request")`).
- 0× OOB-Swap, 0× HX-Trigger, 2× HX-Redirect.
- 5 Form-Module, alle ModelForm mit Tailwind-Klassen.
- 88 HTML-Templates, gettext-Abdeckung 821 `{% trans %}`-Tags.

**Hauptbefunde:**

| Befund | Status | Aktion |
|---|---|---|
| Open-Redirect in `workitem_actions.py:62` | RF-001 | P0 — sofort |
| HTMX-Partial-Mixin ungenutzt | RF-008 | P2 — pro List-View einzeln |
| Pagination ad hoc, WorkItem-Inbox ohne Cap, Feed `[:200]`-Slice | RF-009 | P2 |
| `EventCreateView.get` 72 LOC mischt Template-Auswahl + Default-Logik + Vorbelegung | RF-013 | P2 — Service-Extraktion |
| `EventUpdateView.get` Attachment-Marker-Normalisierung in View | RF-013 | P2 |
| `non_field_errors`-Block fehlt in 3 Form-Templates | RF-014 | P2 — 4-Zeilen-Patch |
| `tabindex="1/2/100/101"` in `events/create.html` | RF-015 | P3 |
| Generic-CBVs ungenutzt | offen | **Nicht jetzt umstellen** — Aufwand XL, Nutzen mittel. Empfehlung: für **neue** Views als Standard etablieren, bestehende lassen. |

**Zielstruktur — bewusst nicht „enterprise-DDD":**

```text
src/core/
├── models/        # 1 Model pro Datei (heutige Konvention behalten)
├── services/      # Bounded Contexts via Modul-Naming, NICHT App-Split
│   ├── events/    # crud.py, context.py, deletion.py, fields.py (RF-004)
│   ├── retention/ # strategies.py, audit_pruning.py (RF-003, RF-010)
│   └── …
├── views/         # CBVs, dünn (Service-Aufrufe)
├── forms/         # ModelForms
└── templates/core/<feature>/partials/  # HTMX-Partials
```

**Bewusst nicht eingeführt:** `selectors/`, `presenters/`, `policies/`-Schichten. Django-Konventionen (Manager + Service + CBV + Template) reichen für die Größe. Mehr Schichten erhöhen die kognitive Last ohne nachweisbaren Nutzen. ADR-002 ist verbindlich, neue Schichten nur per neuer ADR.

---

## 7. Tests und Testlücken

**Bestand:**

- 1.953 `def test_*`-Funktionen über 162 Dateien (Unit/Integration: 111/1.605, E2E Playwright: 51/348).
- RBAC-Matrix `test_rbac_matrix.py` (33 Tests parametrisiert über 4 Rollen).
- **Funktionaler Cross-Tenant-Test mit echter NOSUPERUSER-DB-Rolle:** `test_rls_functional.py` — vorbildlich.
- Architektur-Guards `test_architecture.py` (5 Klassen: Facility-Scoping, Event-Access-Policy, Encryption-Bypass, CSP/Alpine-CSP, HTMX-Handler).
- Wait-Strategie diszipliniert (0× `networkidle`, 447× `wait_for_url`/`domcontentloaded`).
- 42 Smoke-Tests, `--reuse-db`, Postgres-CI-Service.
- Kein `factory_boy`, kein `hypothesis`, keine `--cov-fail-under`-Schwelle.

### Test-Gap-Matrix

| Refactoring-Bereich | Risiko | Vorhandene Tests | Fehlende Tests | Empfohlene neue Tests | Priorität |
|---|---|---|---|---|---|
| Open-Redirect | P0 | `test_sudo_mode.test_open_redirect_protection` (1 Case) | Parametrisches Fuzz | (≥6 Cases) | P0 |
| `Client.anonymize`-Service-Move | P0 | View-getriebene Anonymisierungs-Tests | Service-Charakterisierung | (3 Cases + Trigger-State) | P0 |
| Retention-Strategien | P1 | `test_retention.py` (28 Tests, alle via `enforce_retention`-Command) | Direkte Unit-Tests, Cross-Strategy-Intersection, Boundary | (4 Strategien × pos/neg/boundary) | P1 |
| Event-Service-Aufteilung | P1 | View-getriebene Event-Tests (70) | Service-Unit-Tests | (`create_event`/`update_event`/`request_deletion`/`approve_deletion`) | P1 |
| File-Vault Default-Whitelist | P1 | `test_file_vault_magic_bytes.py` (6 Cases mit Whitelist) | Settings-Row-Deletion-Test | (Settings null/empty → Reject) | P1 |
| k-Anonymisierung | P1 | `test_k_anonymization.py` (10 Tests Equivalence/Determinismus) | Negativ-Case | (k=5, &lt;5 Clients → noop) | P1 |
| Webhook-SSRF | P1 || Validator-Tests | (parametrisch) | P1 |
| HTMX-Mixin | P2 || Per-View-Test pro umgestellter View | (HX-Request: true → Partial) | P2 |
| Query-Count Detail-Views | P2 | `test_zeitstrom_perf.py`, `test_snapshot_command.py` | `ClientDetail`/`EventDetail`/`CaseDetail`/`Handover` | 4 neue `assertNumQueries`-Tests | P2 |
| RBAC Action-Konsequenzen | P2 | 33 Status-Code-Tests | Action-Resultat (Delete-Folge, State-Transition) | Action-Result-Tests | P2 |
| Form `non_field_errors` Rendering | P2 || Template-Render-Test | Pro Form-Template | P2 |

### RF-T-Tasks (8 Pflichttasks vor den Refactorings)

| ID | Bereich | Aufwand | Voraussetzung für |
|---|---|---|---|
| | `safe_redirect_path` Fuzz (6+ Cases) | S | RF-001 |
| | `Client.anonymize` Charakterisierung | S | RF-002 |
| | Retention-Strategien Unit (Boundary, Cross-Strategy) | M | RF-003 |
| | `services/event.py` Unit-Tests (CRUD + Deletion) | M | RF-004 |
| | File-Vault fail-closed (Settings null/empty) | S | RF-005 |
| | k-Anon-Negativtest + Settings-Test | S | RF-006 |
| | SSRF-Validator parametrisch | S | RF-007 |
| | HTMX-Partial-Mixin Per-View | S/M | RF-008 |

**Test-Hygiene-Maßnahmen (mittelfristig):**

- `--cov-fail-under=N` in `.github/workflows/test.yml` (`N` zunächst dem aktuellen Stand entsprechend, danach Schrittweise +1 %).
- Query-Count-Pattern aus `test_zeitstrom_perf.py` auf 4 weitere Detail-Views (`Client`, `Event`, `Case`, `Handover`).
- `factory_boy` nur, wenn die Test-LOC-Wirtschaftlichkeit es rechtfertigt — heute **kein** dringender Bedarf.

---

## 8. Performance

**Bestand (gut gepflegt):**

- `select_related`/`prefetch_related` an heißen Pfaden (25× in Views, 30+ in Services, Commits `f002e3e`, `afd504b`, `2b35040` belegen aktive Pflege).
- GIN-pg_trgm-Index auf `Client.pseudonym` (Migration 0055/0056, validierter Threshold pro Facility).
- Materialized View `statistics_event_flat` mit `REFRESH … CONCURRENTLY` (Migration 0049).
- Optimistic Locking in 4 Flows (Case/Client/Settings/Event/WorkItem) mit dediziertem 409-Conflict-Pfad.

**Lücken:**

| Befund | Status | Aktion |
|---|---|---|
| **Kein `CACHES`-Backend** in Settings → LocMem-Default pro Worker. Maintenance-Cache (`middleware/maintenance.py:14, 45`) und django-ratelimit nicht worker-übergreifend konsistent. | RF-016 | P1 — Voraussetzung für Multi-Worker |
| Pagination-Lücken (RF-009) | RF-009 | P2 |
| Feed `[:200]`-Slice schneidet Tage stumm ab | RF-009 | P2 — Konstante + UI-Loadmore |
| Query-Count-Tests nur an 3 Hot-Spots | RF-T (Test-Gap) | P2 — 4 weitere Views |
| **Keine Last-Test-Schicht** (`locust`/`pytest-benchmark` nicht im Repo) | offen | P3 — nur wenn reale Pilot-Größenordnung Bedarf zeigt |

**Doku-Inkonsistenz** verifiziert: `migrations/0049_statistics_event_flat_mv.py:6` Docstring sagt „täglich", `docs/ops-runbook.md:166,186` cron `15 * * * *` (stündlich). Empfehlung: Migration-Docstring an Cron-Realität angleichen — S.

---

## 9. Deployment und Betrieb

**Bestand (überdurchschnittlich für Pre-1.0-Pre-Pilot):**

- Multi-Stage-Dockerfile, drei Compose-Files, Caddy-Auto-TLS, verschlüsseltes Backup mit Rotation, Off-Site-Hook, **ausführbarer Restore-Drill** (`scripts/restore-drill.sh:1-159` verifiziert 7-Schritt-Drill, prüft RLS + AuditLog-Trigger), JSON-Logging, Health-Endpoint, Maintenance-Mode, PWA-Offline-Fallback.

**Lücken:**

| Befund | Status | Aktion |
|---|---|---|
| Migrations laufen im Web-Container (`docker-entrypoint.sh:4-22`) — kein echtes Zero-Downtime, lange RunPython blockiert Worker | offen | Optional: separater One-Shot-Migrate-Job. Heute Maintenance-Mode als Workaround. — M |
| **NOSUPERUSER manuell** (`docs/coolify-deployment.md:84-100`) — RLS bypasst, wenn Operator den Schritt vergisst | RF-021 | P1 — `scripts/initial-setup.sh` |
| **Cron-Jobs außerhalb Compose** (`docs/ops-runbook.md:172-193`) — frischer Stack ohne Backups/Retention/Breach-Detection | RF-022 | P1 — supercronic-Sidecar |
| `docker-compose.yml` (dev) Postgres `5432:5432` (alle Interfaces) | RF-023 | P2 — `127.0.0.1:5432:5432` |
| `docker-compose.prod.yml:17` Image-Tag hartcodiert `:v0.10.2` | offen | S — `${APP_VERSION:-v0.10.2}` |
| Caddyfile unterspezifiziert (kein www-Redirect, kein `log`, keine Rate-Limits) | offen | M |
| Off-Site-Backup-Hook `scripts/backup.sh:181-219` — bei Fehler still mit Exit 0 | offen | S — bei wiederholtem Fehler Exit ≠ 0 + Sentry-Capture |
| Health-Endpoint deckt nur DB + ClamAV | offen | M — SMTP, Encryption-Key, Backup-Alter, Disk-Frei ergänzen |

**Lizenz/Governance** (verifiziert):

- Tag-Signing inkonsistent (`v0.10.2` ED25519-signiert, `v0.10.0/0.10.1` annotated unsigned, `v0.9.x` Lightweight) → docs/release-checklist um `git tag -s` erzwingen.
- `CODE_OF_CONDUCT.md` existiert nicht.
- DCO/CLA in `CONTRIBUTING.md:351-377` fehlt.
- AGPL-§13-Footer hardcoded auf upstream `main` (`templates/base.html:228`) — Forks/Self-Hoster bekommen technisch falsche §13-Erklärung. Empfehlung: `SOURCE_CODE_URL` aus Settings.

---

## 10. Dokumentation

**Bestand (überdurchschnittlich):**

- 13 ADRs (alle „Accepted") + 7 dokumentierte Backlog-Themen.
- FAQ-Sync-Footer mit Code-Verifikationsdatum (`docs/faq.md:519`).
- DE/EN-Mehrsprachigkeit für alle Kern-Dokumente.
- Threat-Model (`docs/threat-model.md`) mit Header (Version + Revision).
- DSGVO-Vorlagen (5 Dateien: AVV, TOMs, Informationspflichten, Verarbeitungsverzeichnis, DSFA).
- Restore-Drill ausführbar (`scripts/restore-drill.sh:1-159`).
- Coolify-Deployment-Anleitung (`docs/coolify-deployment.md`).

**Lücken (verifiziert):**

| Befund | Status | Aktion |
|---|---|---|
| Django-Versions-Drift: 4 Stellen `5.1` (README:190, CONTRIBUTING:11/226, CLAUDE.md:9, ops-runbook:5) vs. CHANGELOG `[Unreleased]` 6.0.4. `requirements.txt` zeigt aber bereits `django==6.0.4`. | offen | M — beim nächsten Tag in einem Schritt |
| Setup-Anleitung-Drift: `docs/admin-guide.md:45`, `CONTRIBUTING.md:51` — `cd anlaufstelle` nach `git clone … app.git` | offen | S |
| README + 7 Stellen + Screenshot-Dateinamen mit „Klientel"-Resten — Sprachleitlinie #604 sichtbar abschließen | offen | M |
| Drei ADRs im Backlog trotz stabiler Implementierung (File Vault, MFA, Volltextsuche-Backend) | offen | M — nachziehen |
| EN-Doku teils zwei Minor-Reihen hinter (`CONTRIBUTING.en.md:383-385` v0.9.0, `docs/en/README.md:49-51` v0.10.0) | offen | S |
| AGPL-§13-Footer hartcodiert (siehe §9) | offen | S |
| `CODE_OF_CONDUCT.md` fehlt | RF-027 | P2 — Contributor Covenant 2.1 |
| DCO-Sign-off in `CONTRIBUTING.md` fehlt | RF-027 | P2 |

---

## 11. Empfohlene Refactoring-Roadmap

> **Goldene Regel:** Erst absichern, dann refaktorieren. Jeder Sprint endet mit `make ci` + `make test-e2e-parallel` grün, sonst rollback.

### — Absichern vor Umbau (1 Sprint)

**Nur Tests, Charakterisierungstests, Security-Tests, kein großes Refactoring.**

- [ ] **** safe_redirect_path-Fuzz (6 Cases)
- [ ] **** `Client.anonymize` Charakterisierungs-Tests (3 Cases + Trigger-State)
- [ ] **** Retention-Strategien Unit-Tests (4 Strategien × pos/neg/boundary)
- [ ] **** `services/event.py` Service-Unit-Tests (CRUD + Deletion)
- [ ] **** File-Vault fail-closed (Settings null/empty)
- [ ] **** k-Anon-Negativtest
- [ ] **** SSRF-Validator parametrisch
- [ ] Query-Count-Tests für 4 Detail-Views (Client, Event, Case, Handover)
- [ ] `--cov-fail-under=N` in `test.yml` (N=heutiger Stand)
- [ ] **RF-001** Open-Redirect-Helper (Sprint-1 Side-PR — die Tests sind der Knackpunkt; Fix selbst ist 5 Zeilen)

**Sprint-1-Output:** Alle Refactorings ab sind durch Tests abgesichert.

### — Kritische Zentralisierung & Quick-Wins-Security (1 Sprint)

- [ ] **RF-005** File-Upload Default-Whitelist + post-migrate-Signal
- [ ] **RF-007** Webhook-SSRF-Validator
- [ ] **RF-019** IP-Spoof-Fix in Maintenance-Middleware
- [ ] **RF-017** Sudo-Mode `ImproperlyConfigured` in `prod.py`
- [ ] **RF-018** Passwort-Mindestlänge auf 12
- [ ] **RF-006** k-Anonymisierungs-Setting an Lebenszyklus anschließen ODER entfernen (User-Entscheidung verlangt)
- [ ] **RF-002** `Client.anonymize` → `services/clients.anonymize_client` (Tests aus absichern)
- [ ] **RF-014** `non_field_errors`-Block in 3 Form-Templates
- [ ] **RF-015** `tabindex` aus `events/create.html` entfernen

### — Strukturverbesserung (1–2 Sprints)

- [ ] **RF-004** `services/event.py` aufteilen (`events/{crud,context,deletion,fields}.py`)
- [ ] **RF-003** `services/retention.py` Strategy-Konsolidierung (1 Generator, 3 Konsumenten)
- [ ] **RF-010** AuditLog-Pruning ohne `DISABLE TRIGGER` (SECURITY-DEFINER-Funktion)
- [ ] **RF-008** HTMX-Partial-Mixin pro List-View aktivieren (4–6 Views in einem Sprint)
- [ ] **RF-009** Pagination-Mixin + WorkItem-Inbox-Cap + Feed-Slice-Konstante
- [ ] **RF-013** EventCreateView/UpdateView Service-Extraktion

### — Aufräumen und Dokumentieren (1 Sprint)

- [ ] **RF-016** `CACHES`-Backend (Redis) in `prod.py` + Service in Compose
- [ ] **RF-021** `scripts/initial-setup.sh` (`setup_facility` + `ALTER ROLE NOSUPERUSER` + Health-Check)
- [ ] **RF-022** Cron-Sidecar in `docker-compose.prod.yml` (Backup/Retention/Breach-Detection)
- [ ] **RF-023** dev-Compose `127.0.0.1:5432:5432` + Header-Warnung
- [ ] **RF-025** `.pre-commit-config.yaml`
- [ ] **RF-026** Ruff-Regelumfang erweitern (`B`, `UP`, `SIM`, `N`, `S`)
- [ ] **RF-027** CoC + DCO
- [ ] Drei fehlende ADRs nachziehen (File Vault, MFA, Volltextsuche)
- [ ] Setup-Anleitung-Drift (`cd anlaufstelle` → `cd app`)
- [ ] Health-Endpoint-Erweiterung (SMTP, Encryption-Key, Backup-Alter, Disk-Frei)

---

## 12. Konkrete erste Pull Requests

> 7 PRs, jeweils klein, sicher, gut testbar. Reihenfolge respektiert „erst absichern, dann refaktorieren".

---

### PR-1: `tests: safe_redirect_path Fuzz + zentraler Helper` ( + RF-001)

- **Ziel:** Open-Redirect-Lücke in `views/workitem_actions.py:62` schließen, gleichzeitig parametrisches Fuzz-Pattern als Vorlage etablieren.
- **Umfang:**
 - Neuer Test `src/tests/test_safe_redirect_helper.py` mit ~10 Cases (`/`, `/x`, `//evil`, `///evil`, `javascript:alert(1)`, `data:`, `http://x`, `ftp://x`, `/x/../../y`, `""`, `None`).
 - Helper `safe_redirect_path` in `src/core/views/utils.py` (Body aus `views/sudo_mode.py:25-32`).
 - `views/sudo_mode.py:25-32` und `views/workitem_actions.py:61-63` rufen Helper.
 - Architektur-Test `test_no_unchecked_next_redirect` in `src/tests/test_architecture.py`.
- **Dateien:** 5 (1 neu, 4 modifiziert).
- **Tests:** 10 neue Cases + 1 Architektur-Test.
- **Risiko:** Niedrig — engt nur Pfad-Akzeptanz ein. Worst Case: User mit fehlerhaftem `next`-Parameter wird auf `/` redirected.
- **Warum dieser PR zuerst:** P0-Security-Fix mit minimalem Surface-Area, etabliert Pattern für künftige Open-Redirect-Prävention.

---

### PR-2: `tests: Client.anonymize Charakterisierung` 

- **Ziel:** Sicherheitsnetz für RF-002 (Service-Extraktion).
- **Umfang:**
 - Neuer Test `src/tests/test_client_anonymize_characterization.py` mit 3 Szenarien:
 - „Plain anonymize": Client + 2 Cases + 5 Events + 0 Attachments → erwartete Side-Effects.
 - „With attachments": +3 EventAttachments mit Datei → File-Löschungen, EventHistory-Redaktion.
 - „With deletion request": existierende `DeletionRequest` → Status-Übergang.
 - Pro Szenario: Trigger-State-Snapshot vor und nach (`pg_get_triggers` o. ä.) — sicherstellen, dass `session_replication_role` nach Aufruf wieder auf `origin` ist.
- **Dateien:** 1 neu.
- **Tests:** 3 + 3 Trigger-State-Asserts.
- **Risiko:** Niedrig — keine Code-Änderung, nur Tests.
- **Warum nicht zuerst:** Hängt nicht von PR-1 ab, kann parallel gemerged werden.

---

### PR-3: `security: file vault default whitelist` ( + RF-005)

- **Ziel:** File-Upload fail-closed bei `Settings.DoesNotExist`.
- **Umfang:**
 - Test `test_file_vault_failclosed.py` (Settings null → Reject; Settings empty `allowed_file_types=""` → Reject; missing Settings-Row → Default-Whitelist).
 - `core/constants.py`: `DEFAULT_ALLOWED_FILE_TYPES = frozenset({"pdf","jpg","jpeg","png","docx","odt"})`, `DEFAULT_MAX_FILE_SIZE_MB = 10`.
 - `services/file_vault.py:131-145`: Default-Fallback statt `return`.
 - `forms/events.py:198-200`: identisches Pattern.
 - Post-migrate-Signal: jede `Facility` ohne Settings bekommt eine.
- **Dateien:** 5 (1 neu Test, 1 Constants, 2 modifiziert, 1 Signal).
- **Tests:** 4 neue Cases.
- **Risiko:** Niedrig — User mit bisheriger leerer Whitelist bekommt restriktiveres Verhalten als Default. Migrationen-Script-Notiz im Changelog.
- **Warum dieser PR zeitig:** P1-Security, einer von vier Bypass-Lücken im neu eingeführten Defense-Layer.

---

### PR-4: `security: SSRF protection in breach webhook` ( + RF-007)

- **Ziel:** Webhook-URL gegen private IPs / Schemas filtern.
- **Umfang:**
 - Test `test_breach_webhook_ssrf.py` parametrisch (`file://`, `http://127.0.0.1`, `http://169.254.169.254`, `http://10.0.0.1`, `http://192.168.0.1`, `gopher://x`, `ftp://x`, `https://valid.example/hook`).
 - Helper `_validate_webhook_url(url)` in `services/breach_detection.py` (oder `services/_url_safety.py` als Vorbild für-ähnliche Checks).
 - Validator in `_post_webhook` und in der Settings-Form.
- **Dateien:** 3 (1 neu, 2 modifiziert).
- **Tests:** 8 parametrische Cases.
- **Risiko:** Niedrig. DNS-Resolve in der Settings-Form macht Save ~50 ms langsamer.
- **Warum dieser PR früh:** Defense-Layer-Erosion in einem **pre-Auth-laufenden** Workflow.

---

### PR-5: `feat: k-anonymization wired into retention` ( + RF-006, Pfad A)

- **Ziel:** `Settings.retention_use_k_anonymization` an `anonymize_clients` anschließen — **nur wenn der User den Pfad bestätigt** (alternativ Pfad B: Setting entfernen).
- **Umfang (Pfad A):**
 - Test `test_retention_k_anonymization.py`:
 - Setting True, k=5, ≥5 Clients in Bucket → `client.k_anonymize` aufgerufen, `is_active=False`, `k_anonymized=True`.
 - Setting True, &lt;5 Clients → `k_anonymize_client` lehnt ab, Client bleibt aktiv (Negativtest).
 - Setting False → `client.anonymize` wie heute.
 - `services/retention.py:794`: Conditional einführen.
 - Top-level-Import `from core.services.k_anonymization import k_anonymize_client`.
 - Doku-Update: `docs/dsgvo-templates/verarbeitungsverzeichnis.md`, FAQ-Eintrag.
- **Dateien:** 4 (1 Test, 1 Service, 2 Doku).
- **Tests:** 3 neue Cases.
- **Risiko:** **Mittel** — verändert Retention-Verhalten. **Vor Merge ausdrückliches User-OK + Pilot-Konfig prüfen.**
- **Warum nicht zuerst:** Verlangt Tobias' Entscheidung Pfad A vs. B. Kein blinder Merge.

---

### PR-6: `refactor: Client.anonymize → services/clients.anonymize_client` (RF-002)

- **Ziel:** `Client.anonymize` (105 LOC im Model) als dünner Service-Aufruf.
- **Umfang:**
 - Voraussetzung: PR-2 gemerged (Charakterisierungs-Tests grün).
 - Neue Funktion `anonymize_client(client, *, user, request=None)` in `services/clients.py` — Body 1:1 aus Model.
 - ContextManager `with_replica_role` in `services/_db_admin.py` als Helper.
 - `Client.anonymize` ruft Service auf (1-Zeile-Delegation, deprecation-Decorator + `DeprecationWarning`).
 - Direkte Aufrufer (View, Retention) auf `anonymize_client` umstellen.
- **Dateien:** 4-5 (1 neu Service, 1 neu DB-Admin-Helper, 1 Model, 2-3 Aufrufer).
- **Tests:** PR-2-Tests müssen grün bleiben. Zusätzlich: Test, dass `Client.anonymize` (deprecated) noch funktioniert.
- **Risiko:** Mittel — Move-Refactor mit Trigger-Bypass. Tests aus PR-2 sind das Sicherheitsnetz.
- **Warum nicht in einem mit PR-2:** Sequentiell — Tests zuerst absichern, dann erst Refactoring.

---

### PR-7: `refactor: HTMXPartialMixin aktivieren — ClientListView + CaseListView (Pilot)` ( + RF-008)

- **Ziel:** Beweis, dass das Mixin-Pattern funktioniert — Vorlage für die übrigen 11 Views.
- **Umfang:**
 - Test `test_htmx_partial_mixin.py` mit 4 Cases (HX-Request true/false × ClientListView/CaseListView).
 - `views/mixins.py:HTMXPartialMixin` ggf. minimal erweitern (`partial_template_name` als Klassenattribut, `render_htmx_or_full(context)`-Methode).
 - `ClientListView` und `CaseListView` umstellen (`HTMXPartialMixin` als Basis, `template_name`/`partial_template_name` setzen, `if request.htmx … else …`-Branch entfernen).
- **Dateien:** 4 (1 Test, 1 Mixin, 2 Views).
- **Tests:** 4 neue Cases + bestehende View-Tests grün.
- **Risiko:** Niedrig — Mixin existiert bereits, nur Aktivierung.
- **Warum dieser PR aufnehmen:** Pilot zeigt Aufwand für die übrigen 11 Views — Schätzung wird belastbar.

---

**Optionaler PR-8 (P2, Quick-Win):** `docs: non_field_errors Block + Form-Errors-Partial` (RF-014) — 4 Templates, 1 neues `_form_errors.html`-Partial, 4 Includes. Aufwand 30 min.

**Optionaler PR-9 (P2):** `chore: pre-commit + Ruff B/UP/SIM/N/S per-file-ignores` (RF-025 + RF-026) — `.pre-commit-config.yaml`, `pyproject.toml`-Update, ggf. 5–20 inline `# noqa`-Marker als Übergangsstand.

---

## 13. Nicht anfassen / Vorsicht

> Bereiche, die trotz möglicher Optimierungspotenziale **jetzt** nicht refaktoriert werden sollten — entweder weil sie gut getestet, geschäftskritisch oder durch zu viele Seiteneffekte gefährdet sind.

### 13.1 Geschäftskritische Defense-Layer (nicht ohne sehr starke Begründung)

| Bereich | Begründung |
|---|---|
| **Append-only-Trigger auf `AuditLog`** (Migration `0024_auditlog_immutable_trigger.py`) | Gerichtsfestigkeit. Erweiterungen nur additiv via neue Migration. RF-010 ist dokumentiert, soll aber **die Trigger nicht ändern**, nur `DISABLE TRIGGER` durch `SECURITY DEFINER`-Funktion ersetzen — Einschränkung des Zustands, nicht der Trigger-Logik. |
| **Append-only-Trigger auf `EventHistory`** (Migration `0012_event_history.py`, `0074_redact_legacy_eventhistory_delete.py`) | Compliance-relevant. `0074` hat eine Lücke geschlossen — nicht erneut anfassen. |
| **PostgreSQL-RLS-Policies** (Migration `0047_postgres_rls_setup.py`, `EXPECTED_TABLES` in `test_rls.py`) | **Defense-Layer #4** der 4-Schicht-Auth. Jedes neue facility-gescopte Model muss in dieselbe Migration-Sentinel-Tabelle. Refactor-Versuche an `0047` lebensgefährlich für Mandantentrennung. |
| **`test_rls_functional.py`** (NOSUPERUSER-Cross-Tenant-Test) | Vorbildlich, einmalig in der Django-Welt. Tests dürfen erweitert, aber nicht durch Mock-Versionen ersetzt werden. |
| **`scripts/restore-drill.sh`** | Verifiziert RLS-Policies + AuditLog-Trigger im Restore-Image. Refactor nur, wenn E2E-Validierung den exakten Drill-Output prüft. |
| **`models/client.py:35-39` Pseudonym-Klartext-Defer** ([Issue #717](https://github.com/anlaufstelle/app/issues/717)) | **Bewusste Trade-off-Entscheidung** (Trigram-Suche pro Facility ⇒ Real-Risiko niedrig akzeptiert). Dokumentiert, kein Aktion-Item. Re-Open nur durch User-Beschluss. |

### 13.2 Bereiche, die bereits gut getestet und geschäftskritisch sind

- **`test_rbac_matrix.py`** — 33 Tests über 4 Rollen × 8 Views. Jede Erweiterung der Rollen-Matrix muss durch parametrisierte Erweiterung dieses Files erfolgen, nicht durch Parallel-Tests.
- **`services/encryption.py:145-206`** (MultiFernet-Layer) — Re-Encrypt-Pfad, Key-Rotation. Refactor nur mit dediziertem Drill (alte Keys + neue Keys + Migration).
- **`services/login_lockout.py`** — Lockout-Race kommentiert dokumentiert, `select_for_update` korrekt. Optimierung nur, wenn Pilot mit vielen Usern den Bedarf zeigt.
- **`services/feed.py`** — `[:200]`-Slice ist suboptimal (RF-009), aber das Modul rendert die meistbesuchte Seite (Zeitstrom). Aufmerksam refaktorieren.

### 13.3 Bereiche, die erst durch Tests abgesichert werden müssen (siehe §11)

- **`Client.anonymize`** (RF-002) — Tests in PR-2 vor Refactor in PR-6.
- **`services/event.py`** (RF-004) — Service-Unit-Tests vor Aufteilung.
- **`services/retention.py`** (RF-003, RF-010) — Strategy-Tests vor Konsolidierung. Cross-Strategy-Intersection besonders kritisch.

### 13.4 Bereiche, die bewusst nicht „Best-Practice-aktualisiert" werden

- **Single-App-Architektur (`core`):** Bei dieser Größe (22 Models, 35 Services) trägt die Aufteilung in Bounded-Context-Apps nicht. Service-Modul-Naming reicht (z. B. `services/events/`, `services/retention/`). Aufsplitten wird **erst** mit (Plugin-Architektur) zwingend — heute als Vorbereitungsschritt **kein Action-Item**.
- **Keine Generic-CBV-Migration:** `ListView`/`UpdateView` würden ~30 % Boilerplate sparen, aber 35 Views umzubauen kostet einen Sprint und bringt keinen funktionalen Nutzen. **Empfehlung:** für **neue** Views als Standard etablieren, bestehende lassen.
- **Kein `factory_boy` / `hypothesis`:** Die Tests sind heute lesbar und stabil. Beide Tools sind **mittel**, nicht dringend.
- **`document_type.py:32-41 SystemType` (BAN, CRISIS, NEEDLE_EXCHANGE):** Hartcodiert auf Streetwork. Plugin-fähig erst mit. **-Embargo respektieren** (siehe `CLAUDE.md`).

### 13.5 Aus Agent-Reports nicht aktionsreif (Falsifikation)

- **„Migration-Nummern-Dupletten brechen CI/CD"** — Nicht bestätigt. `0027_merge_*` und `0051_merge_20260417_2123.py` lösen die Konflikte. Django funktioniert. Maximal P3 (Konvention).
- **„`clamdcheck.sh` fehlt im offiziellen Image"** — Nicht bestätigt am Code. Das offizielle `clamav/clamav`-Image enthält `clamdcheck.sh` seit Version 1.0+. Vor Änderungen am Healthcheck unbedingt durch lokales Image-Test verifizieren.
- **„`EventUpdateView` baut Initial-Map zweimal"** — Nicht bestätigt. Die zwei Schleifen (`initial_data` Z.310-314, `existing_data` Z.365-369) bauen **unterschiedliche** Strukturen — bewusste Trennung, keine Duplikation.

---

## Anhang: Konsolidierte RF-Liste

| ID | Titel | Prio | Aufwand | Abschnitt |
|---|---|---|---|---|
| RF-001 | Open-Redirect-Helper zentralisieren | P0 | S | §3, §4, §6 |
| RF-002 | `Client.anonymize` → Service | P0 | M | §3, §5 |
| RF-003 | Retention-Strategien konsolidieren | P1 | L | §3, §4 |
| RF-004 | `services/event.py` aufteilen | P1 | M | §3, §6 |
| RF-005 | File-Upload Default-Whitelist | P1 | S | §3, §4 |
| RF-006 | k-Anonymisierung anschließen oder entfernen | P1 | S/M | §3, §4 |
| RF-007 | Webhook-SSRF-Validator | P1 | S | §3, §4 |
| RF-008 | HTMX-Partial-Mixin aktivieren | P2 | M | §3, §6 |
| RF-009 | Pagination zentralisieren + Caps | P2 | M | §3, §6, §8 |
| RF-010 | AuditLog-Pruning ohne DISABLE TRIGGER | P1 | M | §3, §4 |
| RF-011 | reencrypt_fields erweitern | P2 | M | §3, §4 |
| RF-012 | Soft-Delete-Mixin oder ADR | P2 | M | §3, §5 |
| RF-013 | EventCreate/UpdateView Service-Extraktion | P2 | S | §3, §6 |
| RF-014 | non_field_errors-Block | P2 | S | §3, §6 |
| RF-015 | tabindex aufräumen | P3 | S | §3, §6 |
| RF-016 | CACHES-Backend (Redis) | P1 | M | §3, §8 |
| RF-017 | Sudo-Mode-Bypass härten | P1 | S | §3, §4 |
| RF-018 | Passwort-Mindestlänge 12 | P2 | S | §3, §4 |
| RF-019 | IP-Spoof-Fix Maintenance | P1 | S | §3, §4 |
| RF-020 | CSP `style-src` engziehen | P3 | M | §3, §4 |
| RF-021 | initial-setup.sh + Health-Check | P1 | M | §3, §9 |
| RF-022 | Cron-Sidecar in Compose | P1 | M | §3, §9 |
| RF-023 | dev-Compose Postgres-Bind | P2 | S | §3, §9 |
| RF-024 | Inline-Imports in retention.py | P3 | S | §3 |
| RF-025 |.pre-commit-config.yaml | P2 | S | §3, §10 |
| RF-026 | Ruff-Regelumfang erweitern | P2 | M | §3 |
| RF-027 | DCO + Code of Conduct | P2 | S | §3, §10 |
|–T08 | 8 Charakterisierungstests | P0–P2 | S/M | §7, §11 |

---

*Audit-Ende. Erzeugt am 2026-04-30 durch Claude (Opus 4.7) im parallelen Multi-Agent-Modus über 5 Refactoring-Achsen (Services / Views+Forms+Templates / Models+Migrationen / Tests / Performance+Ops+Doku). Alle Refactoring-Vorschläge mit `path:line` am aktuellen Code belegt; drei Vor-Audit-Behauptungen explizit als nicht aktionsreif zurückgewiesen.*
