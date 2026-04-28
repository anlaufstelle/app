# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.10.2] - 2026-04-28

### Changed

- **CSP-Migration auf `@alpinejs/csp` finalisiert** — vendored Alpine-Build durch CSP-Variante ersetzt, alle Inline-`x-data="{..}"`-Objekte auf registrierte `Alpine.data()`-Komponenten umgestellt, komplexe Expressions in Component-Methoden ausgelagert. `script-src 'unsafe-eval'` ist damit endgültig aus der globalen CSP entfernt.

### Fixed

- **CSP-Folgefehler nach `@alpinejs/csp`-Migration** — Time-Filter-Tab-Highlight auf Zeitstrom-Feed (`hx-on::before-request` durch JS-Listener ersetzt), 11 Alpine-Expressions auf Computed-Getter / pre-formatierte Properties umgestellt (Toast-Farbe, Klientel-Autocomplete-Highlight, Aktivitätskarten-Pfeil, Offline-Toggle-Label, Konflikt-Diff-Tabelle, Offline-Detail-Sichtbarkeit). Architektur-Test verbietet zukünftig Ternaries, `||`/`&&`, Method-Calls und Object-Literale in `:class`/`x-text`/`x-show`/`x-if`/`x-bind:`/`x-model`-Direktiven sowie HTMX-Inline-Handler `hx-on::`.
- **Django-Admin Modal-Overlay blockt Action-Klicks** — django-unfold lädt seinen eigenen Alpine-Build, der für die Cmd+K-Suche-Modal `new AsyncFunction()`-basierte Expression-Auswertung nutzt. Globale CSP `script-src 'self'` (ohne `unsafe-eval`) blockt das, Component initialisiert nicht, `<div x-show="openCommandResults">` bleibt mit `display: flex` sichtbar und blockt Klicks. Neue `AdminCSPRelaxMiddleware` ergänzt `'unsafe-eval'` per-Request nur für `/admin-mgmt/*` (privilegierte Routes mit MFA-Gate).
- **Retention Bulk-Toolbar reagiert nicht auf Selektion** — Inline-`@change="$dispatch('retention-bulk-change')"` ist im `@alpinejs/csp`-Build verboten (Function-Calls mit String-Argumenten). Neue `notifyBulkChange()`-Method auf `proposalCard`-Component, Template nutzt `@change="notifyBulkChange"`.
- **`autosave-discard` Race-Condition** — `wait_for_url`-Test-Helper erkannte Same-URL-Reload nicht als Navigation; nachfolgende `page.evaluate` scheiterte mit "Execution context was destroyed". Test nutzt `expect_navigation`-Context-Manager, der auf `framenavigated`-Event auch bei identischer Target-URL synchronisiert.
- **`python-magic` fehlte in `requirements-dev.txt`** — `make deps-lock` regeneriert Lock-Files (drift gegenüber `.in`-Files); ohne `python-magic` scheiterten Test-Job (`ModuleNotFoundError`) und E2E-`seed --flush` (transitiv über `core.services.file_vault`).
- **CI `Test/check`-Job schlägt fehl** — Workflow-env setzte `SECRET_KEY`, `prod.py`/`base.py` lesen aber `DJANGO_SECRET_KEY`. Variable umbenannt.
- **CI `lock-check`-Job schlägt fehl** — siehe `python-magic`-Fix oben (regeneriertes Lock-File matcht jetzt `pip-compile`-Output).
- **E2E-Test-Helpers ignorieren xdist-Worker-DB** — vier Subprocess-Helper (`_seed_failed_logins_and_check_lock`, `_clear_lockout_for`, `_enable_totp_and_generate_codes`, `_cleanup_totp` in 3 Test-Files) nutzten `os.environ` als Subprocess-Env ohne `E2E_DATABASE_NAME`; Subprocess landete in default-DB `anlaufstelle_e2e` statt worker-spezifischer `anlaufstelle_e2e_1`. Helpers nehmen jetzt `e2e_env` aus der gleichnamigen Fixture als Parameter.
- **`TestZZAccountLockout` ohne Cleanup** — Tests sperrten `miriam`/`lena` per 10× LOGIN_FAILED-AuditLog ohne Cleanup; nachfolgende `_staff_storage_state`/`_assistant_storage_state`-Fixtures scheiterten weil User auf `/login/` hängenblieben. Autouse-Teardown-Fixture `_cleanup_lockout_state` ruft `login_lockout.unlock()` für miriam + lena nach jedem Test der Klasse auf (Cleanup über `LOGIN_UNLOCK`-AuditLog-Eintrag, weil `core_auditlog` einen `auditlog_immutable`-DB-Trigger hat).
- **`test_event_save_and_appears_in_detail` synchrones `is_visible()`** — `wait_for_url`-Match nach Server-Redirect kehrt sofort zurück, aber Detail-Template rendert `<dl>`/`<dd>`-Sektionen unter Last (xdist + 2 Worker auf gleicher VM) noch nicht im DOM. Asserts auf `wait_for(state="visible")` umgestellt.
- **`_ensure_proposals` Test-Helper unzuverlässig** — `RetentionProposal.objects.get_or_create(.. status__in=[..])` matched approved-Proposals als unique-Constraint-Konflikt; im parallelen Run scheiterte `assert n >= 2 false`. Helper neu geschrieben: zählt pending, holt fehlende Anzahl Events ohne existierende Proposal-Verknüpfung, legt frische pending-Proposals an.

## [0.10.1] - 2026-04-26

### Added

- **Visual Refresh** — Theme „Grün" mit DM Sans/Mono (self-hosted, kein Google-CDN), OKLCH-Akzentfarbe `#2d6a4f`, neuer Sidebar mit Logo-Box und „Neu erstellen"-Dropdown, Mobile-Bottom-Nav mit 5 Slots, 3 px farbige linke Kante an Feed-Cards, KPI-Cards mit Mono-Numbers, Card-Pattern flächig auf alle Templates.
- **Klientel-Liste responsive** — Single-Loop und CSS-Grid statt Doppel-Renderpfad für Desktop/Mobile.
- **MFA-Setup-Seite** zeigt Secret in Base32 (statt Hex) für Authenticator-Apps an.
- **MFA-Backup-Codes** als zweiten Faktor bei verlorenem Authenticator-Gerät, mit eigenem Limit pro Stunde und Audit-Log-Eintrag bei Verwendung.
- **Composite-Indexes** auf AuditLog (3×), Case, Event und WorkItem (Migration `0066`) für Listen-Filter mit Status + Datum.
- **Attachment-Versionierung Stufe B** — pro Datei-Feld eine Liste von Versionen statt Single-Slot, mit Vorversionen-Anzeige im Event-Detail.
- **Default-Werte für Feldvorlagen** (`FieldTemplate.default_value`) — Quick-Templates können Standardvorgaben befüllen.
- **FAQ-Erweiterungen** — `Hinweis` vs. `Aufgabe`, Bedeutung der `Wiedervorlage`, Grenzen des Wizards/Hausverbot-Flows.
- **Übergabe-Seite** mit 7 neuen E2E-Tests (Schicht-Wechsel, KPI-Cards, Highlights).
- **Audit-Tiefenanalysen** — drei systematische Code-Audits (`docs/audits/2026-04-{21,23,25,26}-*.md`) mit Belegscreenshots, vollständig adressiert.
- **`make ssl-cert` LAN-IP** — `SSL_HOST_IP=192.168.x.y make ssl-cert` für PWA-Tests von Mobilgeräten.

### Changed

- **Alpine-Komponenten registriert** — alle 26 inline `x-data="{.. }"` zu `Alpine.data()`-Komponenten in [`src/static/js/alpine-components.js`](https://github.com/anlaufstelle/app/blob/main/src/static/js/alpine-components.js) extrahiert; Architektur-Test verbietet neue Inline-Verstöße. Vorbereitung für späteren Wechsel auf den `@alpinejs/csp`-Build.
- **EventUpdateView/EventCreateView** schlanker — neue Service-Funktionen (`apply_attachment_changes`, `attach_files_to_new_event`, `split_file_and_text_data`, `build_field_template_lookup`); `build_event_detail_context` mit `select_related` (kein N+1).
- **Magic Numbers in `core.constants`** — Pagination-Defaults, Rate-Limit-Konstanten, Cache-TTLs zentralisiert.
- **`seed.py` modularisiert** in 15 Domänen-Module (Clients, Events, Audit, Retention etc.) — vorher monolithisch.
- **Anonyme Pages auf Default-Locale forcieren** — Login/Password-Reset/MFA-Login rendern unabhängig von `Accept-Language` immer in `LANGUAGE_CODE` (de). Authentifizierte User behalten ihre Profil-Sprache als Override ( FND-13).
- **Feed-Card-Preview** für File-Marker — `__file__` und `__files__` werden als „[Datei]"/„[N Dateien]" angezeigt statt als rohes Dict-Repr ( FND-12).
- **WorkItem.item_type help_text** korrigiert — passt jetzt zu den tatsächlichen Choices (`hint`/`task`).

### Fixed

- **Alpine-Komponenten-Bootstrap** — `alpine-components.js` lädt vor `alpine.min.js`, sodass `alpine:init` die `Alpine.data()`-Registrierungen sieht; behebt 27–43 `ReferenceError`s pro Seite ( FND-11).
- **CSP-Inline-Handler** — Attachment-Entfernung im Event-Edit nutzt eigenen JS-Listener statt `onchange`-Attribut ( FND-01).
- **Offline-Queue ACK-Protokoll** — `MessageChannel`-basiertes ACK/NACK statt naivem Success-Banner; korrekte Rückmeldung an die UI bei IndexedDB-Fehlern ( FND-02).
- **File-Vault Cleanup** — Direct-Cleanup bei DB-Exception plus periodischer Orphan-Cleanup-Command ( FND-03).
- **MIME-Validierung für DOCX/OOXML** — Container-Formate werden als äquivalent zu `application/zip` erkannt und nicht mehr fälschlich als unsicher abgelehnt ( FND-04).
- **i18n f-Strings** — alle `_(f"..")` durch `_("..%(name)s..") % {"name": value}` ersetzt; Architektur-Test verbietet Rückfälle ( FND-07).
- **AuditLog für Case-Aktionen** — `close_case`, `reopen_case` und `delete_milestone` schreiben jetzt einen `AuditLog`-Eintrag (vorher silent).
- **Vorlage-entfernen-Link** löscht den Autosave-Draft mit, sonst bestand der alte Draft-Stand weiter.
- **Seed-Coverage-Pin** — `coverage.json` ignoriert; Ruff in CI auf `0.15.11` gepinnt.
- **Service-Worker Offline-POST-Handling** — Multipart-Antwort beginnt jetzt konsistent mit „Offline — Datei-Uploads erfordern eine Internetverbindung" (Präfix-Konsistenz mit Standard-Queue-Pfad). Cache-Version v6→v7. xfail-Test in [`test_pwa_offline.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/e2e/test_pwa_offline.py) durch echten URL-encoded-POST-Test ersetzt; Multipart-Pipeline
- **E2E-Browser-State-Cleanup** — neue `_cleanup_browser_state(page)`-Hilfsfunktion in [`src/tests/e2e/conftest.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/e2e/conftest.py) leert vor `context.close()` Service-Worker, IndexedDB, Cache-API und Storage, um Cross-Test-State-Pollution in der parallelen Suite zu reduzieren.

### Security

- **Rate-Limits flächig** — 19 fehlende `@ratelimit`-Decorators auf POST-Handlern ergänzt (Cases, Clients, Episodes, Events, Retention, MFA-Disable, WorkItem-Update, Bulk-Aktionen). Architektur-Test `TestRateLimitOnAllMutations` verbietet neue ungeschützte Mutationen ( FND-14).
- **Account-Lockout** nach 10 Login-Fehlversuchen, Admin-Unlock im Profil.
- **MFA-Backup-Codes** als zweiter Faktor mit eigenem 5/m-Limit, Audit-Log bei Verwendung.

### Accessibility

- **`aria-hidden="true"` auf 90 dekorativen SVG-Icons** in 23 Templates — Screen Reader liest keine Path-Daten mehr vor (WCAG 2.1 SC 1.1.1). Architektur-Test `TestSvgAccessibilityGuard` verbietet künftige Verstöße ( FND-15).

### Performance

- **Doppel-Rendering Klientel-Liste** auf Single-Loop reduziert (responsive Grid statt Desktop+Mobile-Render).
- **`enrich_events_with_preview`** N+1 entfernt — `select_related("field_template")` statt pro Event eigene Query ( FND-05).
- **WorkItemInbox-Pagination** auf 50 Einträge pro Liste begrenzt; Querysets nicht mehr pauschal in Templates evaluiert (, ).

## [0.10.0] - 2026-04-19

### Added

- **Encrypted File Vault** — verschlüsselte Datei-Anhänge an Events (AES-GCM, RFC-5987 Content-Disposition, zentraler `safe_download_response`).
- **ClamAV-Virenscan** für Datei-Uploads vor der Verschlüsselung — fail-closed, Healthcheck integriert.
- **Sicherer Offline-Modus (M6A)** — Offline-erfasste Events und Autosave-Drafts werden client-seitig mit AES-GCM-256 verschlüsselt in IndexedDB gespeichert. Der Schlüssel wird beim Login per PBKDF2 (600 000 Iterationen, SHA-256) aus dem Passwort + User-Salt abgeleitet, lebt nur in memory und ist `extractable: false`. Logout, Password-Change und Tab-Close machen alle Offline-Daten unlesbar.
- **Offline-Queue Multipart-Schutz** — Events mit File-Anhängen werden offline mit explizitem UI-Hinweis abgelehnt statt naiv als Text zu queuen.
- **Service-Worker UUID-Pattern** — Event-/WorkItem-Edit-Routen werden jetzt korrekt mit UUID-Regex statt `\d+` gematcht.
- **Offline-Queue Replay-Sicherheit** — `response.ok`-Check verhindert stilles Löschen von Queue-Einträgen bei 4xx/5xx; exponentielles Backoff bei 5xx.
- **Streetwork-Offline Stufen 2+3** — Read-Cache für mitgenommene Klientel + Offline-Edit mit Konfliktauflösung (Side-by-Side-Diff, 3 Resolve-Actions).
- **TOTP-2FA** via django-otp.
- **Token-basierter Invite-Flow** — kein Klartext-Initialpasswort mehr.
- **Retention Dashboard & Legal Hold** — DSGVO-Löschfristen-UI mit Bulk-Approve/Defer/Reject und Defer-Folgeverhalten.
- **K-Anonymisierung** als Alternative zu Hard-Delete.
- **Optimistic Locking** für Client, Case, Workitem, Settings (vorher nur Event) — gemeinsamer Helper `core.services.locking.check_version_conflict`.
- **Workitems:** „Mir zugewiesen"-Filter, Bulk-Edit für Status/Priorität/Zuweisung, `remind_at` getrennt von `due_date`, wiederkehrende Fristen mit Auto-Duplizierung bei Done.
- **Quick-Templates** für vorbefüllte Event-Eingaben.
- **Fuzzy Search** via PostgreSQL `pg_trgm` (Tippfehler-tolerant), Threshold pro Facility konfigurierbar.
- **FAQ** unter `docs/faq.md`.
- **Coolify-Deployment-Leitfaden** + GHCR-Image (`ghcr.io/anlaufstelle/app`).
- **Vendored: Dexie.js 4.2.0** als `src/static/js/dexie.min.js` (Apache-2.0). Wrapper für IndexedDB-Operationen im Offline-Modus.
- **Dateianhänge im Seed-Command** für realistische E2E-Demos.

### Changed

- **FieldTemplate.sensitivity** — Sichtbarkeit von Verschlüsselung entkoppelt (Daten-Migration), inkl. Löschschutz bei vorhandenen Daten.
- **Update-Views** (`ClientUpdate`, `WorkItemUpdate`) laufen jetzt über die Service-Schicht.
- **Statistik-Aggregate** als Materialized View.
- **CSP-Header** in Django konsolidiert (Caddyfile-CSP entfernt, Inline-Skripte externalisiert).
- **Pip-Tools Lock-Files** — `requirements.txt` / `requirements-dev.txt` werden aus `.in`-Files generiert; CI prüft Drift.
- **Coverage-Report** als CI-Artifact (14 Tage Retention).
- **README** ergänzt um Sektion „Unterstützung bei der Einführung".

### Security

- **PostgreSQL Row Level Security** als Defense-in-Depth auf 16 facility-scoped Tabellen, fail-closed bei fehlender Session-Variable.
- **RLS-Variable session-weit** statt per `SET LOCAL` gesetzt — ohne `ATOMIC_REQUESTS` lief die Variable bisher nur in der Middleware-Cursor-Transaktion und die RLS-Policies griffen gegen NULL. Pro Request neu gesetzt, für anonyme/facility-lose Requests explizit geleert.
- **Bulk-WorkItem-Endpoints** prüfen Ownership pro Item — verhindert, dass Assistenz-Rollen fremde Aufgaben bulk-mutieren konnten; gemischter Batch ohne Berechtigung wird komplett mit 403 abgelehnt.
- **Django-Admin** (`/admin-mgmt/`) unterliegt MFA- und Force-Password-Change-Gates.
- **Prod-Settings fail-closed** für SECRET_KEY, ALLOWED_HOSTS, ENCRYPTION_KEYS.
- **Zentraler Event-Access-Loader** mit 404-Semantik statt Permission-Leak.
- **Service-Invarianten** für `create_event` und `assign_event_to_case`.
- **PasswordResetView** rate-limited.
- **EventHistory-Interpretationsstabilität** — Feldmetadaten eingefroren.
- **Sensitivity-Filter** in Suche, Aktivitätsfeed, `compute_diff`, Profilseite, Attachments-Übersicht (Slicing-Reihenfolge korrigiert).

### Fixed

- **Atomare Event+Attachment-Persistierung** — Create- und Update-Flows in `transaction.atomic`; alte Datei wird im Update erst per `transaction.on_commit` gelöscht, sonst Datenverlust bei fehlschlagendem Upload.
- **Fuzzy-Suche:** alle `icontains`-Treffer (auch Display-Cap-Overflow) werden aus der Similar-Sektion ausgeschlossen, damit echte Fuzzy-Kandidaten nicht verdrängt werden.
- **`search_trigram_threshold`** validiert auf `0.0–1.0` per Validator + DB-Constraint.
- **Retention:** `create_proposal` erkennt `DEFERRED` als aktiven Status, vermeidet `IntegrityError`-Fallback nach Re-Run.
- **RLS-Middleware** öffnet DB-Cursor nur bei authentifizierten Requests — Anonymous-Routes (Login, Health, Static) brauchen den Hit nicht.
- `Client.anonymize()` deckt Cases, Episodes und alle Workitems ab.
- `SETTINGS_CHANGE` + Update-Actions werden im Audit-Log geschrieben.
- `TRUSTED_PROXY_HOPS` — `get_client_ip` korrekt für Multi-Proxy.
- `password_change`-Middleware exemptet `/admin-mgmt/` statt `/admin/`.
- `dexie.min.js` sourceMappingURL für `collectstatic` entfernt.
- E2E-Test `test_inbox_shows_sections` härter selektiert (vermied falschen Match auf neues Bulk-Status-Dropdown).
- CI: E2E-Ordner vom Unit-Test-Collection ausgeschlossen, `filelock` als Dev-Dependency.

## [0.9.1] - 2026-04-05

### Added

- **Standardsprache** persistent im Nutzerprofil speichern (DE/EN)
- **Analytics Charts** — Trend-Diagramme im Statistik-Dashboard mit monatlicher Aufschlüsselung nach Dokumentationstyp, inkl. User-Guide (DE + EN)
- **Sentry-Integration** — automatische Fehlererfassung in Produktion
- **JSON-Logging** — strukturiertes Logging für Produktionsumgebung
- **Coverage-Infrastruktur** — pytest-cov mit CI-Gates für Testabdeckung
- **Test-Parallelisierung** — pytest-xdist mit Worker-Isolation + Smoke-Marker

### Fixed

- CSP `unsafe-eval` für Alpine.js — behebt kaputtes Frontend
- Kontakt ohne Klientel wird automatisch als anonym markiert
- Anonym-Checkbox entfernt — Anonymität aus fehlender Klientel ableiten
- Chart.js Registry-Konflikt bei HTMX-Swap behoben
- E2E-Tests für xdist-Parallelisierung stabilisiert
- Autocomplete-E2E-Tests: Debounce-Race-Condition & nicht-deterministische Seed-Reihenfolge behoben

### Changed

- **Produktionshärtung** — CSP-Header, Docker-Konfiguration
- **Go-Live-Vorbereitung** — Runbook, Checkliste, Staging-Pipeline, E2E-Workflow
- **Testabdeckung** erweitert: Scope, RBAC-Matrix, Deletion-Requests, Management-Commands
- **Seed-Daten** finalisiert: realistische Tagesverteilung, Heute-Logik, Mitarbeiter-Zuordnung

## [0.9.0] - 2026-03-28

Initial public release.
