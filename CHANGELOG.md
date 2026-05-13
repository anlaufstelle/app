# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.12.0] - 2026-05-12

Minor-Release. Schwerpunkte: 5-Rollen-Modell mit Superadmin und cross-facility `/system/`-Bereich; produktive `dev.anlaufstelle.app`-Topologie auf Hetzner als Coolify-Ablöse; RLS-Hardening rund um Bootstrap und Pre-Auth-AuditLogs. Zusätzlich vier CVE-Fixes (Django 6.0.5, urllib3 2.7.0, plus cryptography-Hardening).

### Security

- **Django 6.0.4 → 6.0.5** — drei CVE-Fixes:
  - CVE-2026-6907 — Caching von Requests bei gesetztem `Vary`-Header
  - CVE-2026-35192 — `Vary`-Header beim Setzen einer Session
  - CVE-2026-5766 — `DATA_UPLOAD_MAX_MEMORY_SIZE`-Enforcement im `MemoryUploadHandler`
  - Im Django-Stack mit aktualisiert: `django-unfold` 0.91.0 → 0.92.0, `django-stubs` 6.0.3 → 6.0.4.
- **`urllib3` 2.6.3 → 2.7.0** — CVE-2026-44431, CVE-2026-44432. Transitive Dependency über `sentry-sdk`, jetzt explizit in `requirements.in` gepinnt.
- **`cryptography` 47.0.0 → 48.0.0** — Hardening: strikte X.509-CRL-Validierung (Mismatch zwischen `TBSCertList.signature` und `signatureAlgorithm` löst jetzt `ValueError`). Post-Quantum-Support (ML-KEM/ML-DSA) via OpenSSL 3.5+, AWS-LC, BoringSSL.

### Added

- **5-Rollen-Modell mit Superadmin** — Neue Hierarchie `SUPER_ADMIN > FACILITY_ADMIN > LEAD > STAFF > ASSISTANT`. Superadmin ist facility-übergreifend.
  - Bestehende `ADMIN`-User werden zu `FACILITY_ADMIN` migriert (Rename).
  - RLS-Bypass läuft über ein Postgres-Session-Setting, **nicht** über die `BYPASSRLS`-Role — Migrationen [`0084_user_role_super_admin.py`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0084_user_role_super_admin.py) und [`0085_rls_superadmin_bypass.py`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0085_rls_superadmin_bypass.py).
  - Superadmin wird per `manage.py create_super_admin`-CLI angelegt (interaktiv, kein Seed-Default in Production).
  - Hintergrund und Trade-offs: [ADR-018](https://github.com/anlaufstelle/app/blob/main/docs/adr/018-rollenmodell-superadmin.md), Fachkonzept v1.5, FAQ.

- **`/system/`-Bereich für Superadmin** — Login-Redirect, eigene Sidebar, facility-übergreifendes Dashboard. Facility-gescopte Menü-Einträge werden in `/system/` ausgeblendet.
  - **Tier 1:** System-Health-Card, Sperrkonten-Liste mit Unlock-Button, AuditLog-Export CSV/JSON, Maintenance-Mode-Toggle.
  - **Tier 2:** Retention-Übersicht, Verzeichnis Verarbeitungstätigkeiten (Art. 30, read-only), Legal-Hold-Übersicht.
  - `manage.py unlock <username>`-CLI als Recovery-Pfad, falls kein Superadmin verfügbar ist.

- **`dev.anlaufstelle.app` Live-Deployment** — Plain Docker Compose auf Hetzner CX22 als Coolify-Ablöse ([ADR-017](https://github.com/anlaufstelle/app/blob/main/docs/adr/017-deployment-topology.md)). Coolify-Runbook ist deprecated.
  - Compose-Stack: [`docker-compose.dev.yml`](https://github.com/anlaufstelle/app/blob/main/docker-compose.dev.yml), [`Caddyfile.dev`](https://github.com/anlaufstelle/app/blob/main/Caddyfile.dev), [`.env.dev.example`](https://github.com/anlaufstelle/app/blob/main/.env.dev.example).
  - Deploy-Skripte: [`deploy/bootstrap.sh`](https://github.com/anlaufstelle/app/blob/main/deploy/bootstrap.sh) (UFW als letzter Schritt, damit die SSH-Session nicht abreißt), [`deploy/deploy-dev.sh`](https://github.com/anlaufstelle/app/blob/main/deploy/deploy-dev.sh), [`deploy/backup.sh`](https://github.com/anlaufstelle/app/blob/main/deploy/backup.sh).
  - Settings-Modul [`devlive`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/devlive.py) und Make-Target `make deploy-dev`.
  - [`dev-image`-Workflow](https://github.com/anlaufstelle/app/blob/main/.github/workflows/dev-image.yml) baut bei jedem `main`-Push ein `:main`-Image.
  - `RobotsTxtView` mit `Disallow: /` — Dev-Instanz wird nicht indexiert.

- **Manuelle Test-Matrix** — [`docs/testing/manual-test-matrix.md`](https://github.com/anlaufstelle/app/blob/main/docs/testing/manual-test-matrix.md) mit drei Sektionen (Anwender, Entwickler, Auditor) für Funktionalität, DSGVO und Sicherheit. Setup einmalig pro Test-Tag gegen `dev.anlaufstelle.app`.

### Changed

- **2-User-DB-Modell für RLS-Bootstrap** — Postgres-Init legt einen separaten Admin-User mit `BYPASSRLS` an; Migrationen und Seed verbinden als `POSTGRES_ADMIN_USER`. App-Worker laufen weiterhin auf einem nicht-bypass-fähigen App-User. Self-Hoster brauchen die neuen Env-Vars (siehe [`.env.dev.example`](https://github.com/anlaufstelle/app/blob/main/.env.dev.example)). Das Pattern ist in [`docs/dev-deployment.md`](https://github.com/anlaufstelle/app/blob/main/docs/dev-deployment.md) und ADRs [005](https://github.com/anlaufstelle/app/blob/main/docs/adr/005-facility-scoping-and-rls.md) + [007](https://github.com/anlaufstelle/app/blob/main/docs/adr/007-auditlog-append-only.md) dokumentiert.
- **i18n** — `/system/`-Bereich und Tier-1/2-Funktionen vollständig DE + EN übersetzt.
- **CSP-Hygiene** — `data-confirm`/`data-action`-Attribute ersetzen verbliebene inline-`onclick`/`onsubmit`-Handler. Wireup in neuem [`confirm-action.js`](https://github.com/anlaufstelle/app/blob/main/src/static/js/confirm-action.js).
- **Routine-Dependency-Bumps** — `sentry-sdk` 2.58.0 → 2.59.0, `docker/setup-qemu-action` v3 → v4 in `release.yml`.

### Fixed

- **Pre-Auth-AuditLogs unter RLS** — Login-Versuche, Lockout-Trigger und anonyme Reset-Anfragen schreiben AuditLogs, bevor `FacilityScopeMiddleware` die Session-Variable setzt; die `WITH CHECK`-Policy lehnte sie ab. Zwei Eingriffe lösen das:
  - Migration [`0083`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0083_auditlog_rls_with_check.py) erlaubt NULL-Facility-INSERTs auf `core_auditlog`.
  - `user_logged_in` und `user_login_failed` setzen `app.current_facility_id` jetzt selbst, bevor sie auditieren.
  - Sichtbar sind diese Logs nur für Superadmin im `/system/`-Bereich.
- **Seed unter `FORCE ROW LEVEL SECURITY`** — `make seed` lief am Bootstrap-Henne-Ei vorbei (App-User kann die eigenen Policies nicht umgehen); Seed verbindet jetzt als `POSTGRES_ADMIN_USER` (BYPASSRLS).
- **Bootstrap-UFW-Reihenfolge** — UFW-Aktivierung im [`bootstrap.sh`](https://github.com/anlaufstelle/app/blob/main/deploy/bootstrap.sh) ist der letzte Schritt, sonst kappte das Skript die laufende SSH-Session, bevor `ufw allow OpenSSH` durch war.
- **Health-Check ohne TLS-Termination im Container** — `SECURE_REDIRECT_EXEMPT` für `/health/` gesetzt (Caddy macht TLS davor); `migrate` läuft via [`docker-migrate.sh`](https://github.com/anlaufstelle/app/blob/main/deploy/docker-migrate.sh) als Admin-User.

## [0.11.1] - 2026-05-05

Patch-Release: Dependency-Bumps und CI-Hardening als Folge zum v0.11.0 Stage-CI Lock-Drift-Befund. Keine Code-Änderungen am App-Verhalten.

### Changed

- **Dependencies aktualisiert** — `psycopg` 3.3.3 → 3.3.4, `mypy` ≥1.20 → ≥1.20.2.
- **GitHub-Actions aktualisiert** — `actions/setup-python` 5 → 6, `actions/setup-node` 4 → 6, `github/codeql-action` 3 → 4, `docker/build-push-action` 6 → 7, `peter-evans/create-issue-from-file` 5 → 6.
- **`make lint`-Scope** auf `scripts/` erweitert — die `check_*.py`-Helfer waren bisher nur im pre-commit-Hook erfasst, nicht in `make ci`. Drei Format-Drifts in `scripts/` mit gefixt; `pyproject.toml` hat per-file-ignores für Subprocess-Aufrufe in Dev-/CI-Tools.

### Fixed

- **Lock-File-Drift-Schutz** — `make ci` ruft `deps-check` auf, `.pre-commit-config.yaml` hat `pip-compile`-Hooks für `requirements*.in` und einen `pre-push-fast-ci`-Hook (`make lint && make deps-check && make check`). Ersetzt Branch Protection mit Required Status Checks, die bei direktem `git push` auf `main` nicht greifen würden.
- **Workflow-Health-Check als Pre-Flight-Schritt** in [`docs/release-checklist.md`](https://github.com/anlaufstelle/app/blob/main/docs/release-checklist.md) — verhindert, dass deaktivierte Workflows unbemerkt bleiben. Hintergrund: Test/E2E/Lint/CodeQL/Release auf `anlaufstelle/app` waren von 2026-04-29 bis 2026-05-05 manuell deaktiviert, sodass v0.11.0 ohne CI auf `main` durchging und der Lock-Drift erst auf Stage-CI auffiel.

## [0.11.0] - 2026-05-05

Großer Sicherheits- und Hardening-Release. Hauptthemen: Wechsel auf Django 6.0 inkl. fünf CVE-Fixes, Sudo-Mode-Re-Auth für sensible Aktionen, DSGVO-Art.-33/34-Breach-Detection, Vier-Augen-Lösch-Workflow, Maintenance-Mode, neue Health-Checks, sowie ein A11y- und i18n-Sweep, der die Sprachleitlinie „Person" flächig durchzieht.

### Security

- **Django 5.1 → 6.0 Migration** — Wechsel von Django 5.1.15 auf 6.0.4. Django 5.1 ist EOL. Mit dem Sprung kommen die Sicherheits-Fixes CVE-2026-33034 (`DATA_UPLOAD_MAX_MEMORY_SIZE` enforcement), CVE-2026-33033 (`MultiPartParser`-DoS), CVE-2026-4292 (`ModelAdmin.list_editable`), CVE-2026-4277 (`GenericInlineModelAdmin`) und CVE-2026-3902 (Header mit Underscores in `ASGIRequest`). `django-unfold` auf 0.91.0 gehoben (6.0-Kompatibilität). Plugin-Stack (`django-csp`, `django-htmx`, `django-otp`, `django-ratelimit`, `sentry-sdk`) unverändert kompatibel. `django.contrib.postgres` zu `INSTALLED_APPS` hinzugefügt (in 6.0 strikt für `GinIndex` auf `Client.pseudonym` erforderlich, postgres.E005).
- **Sudo-Mode Re-Auth für sensible Aktionen** — Zeitlich begrenztes Re-Authentifizierungs-Fenster (15 min) vor besonders sensiblen Aktionen wie MFA-Disable, Passwort-Änderung, Daten-Export. `RequireSudoModeMixin` + neue Form mit Rate-Limit. Details in [`docs/faq.md` § 13a](https://github.com/anlaufstelle/app/blob/main/docs/faq.md#13a-was-ist-sudo-mode-re-auth-fenster).
- **DSGVO Art. 33/34 Breach-Detection** — Heuristik-basiertes `detect_breaches`-Cron-Kommando (stündlich:30) für Failed-Login-Burst, Mass-Export und Mass-Delete. Schreibt `SECURITY_VIOLATION`-AuditLog und liefert optional einen Webhook für SIEM/Pager. Runbook-Eintrag in [`docs/ops-runbook.md` § 6.5b](https://github.com/anlaufstelle/app/blob/main/docs/ops-runbook.md).
- **Klartext-Freitexte: UI-Warnung + Inventar** — `Client.notes`, `Case.description`, `Episode.description` sind weiterhin nicht feldverschlüsselt. Sicht- und Editfelder zeigen jetzt eine UI-Warnung, dass dort keine Klarnamen oder Art-9-Daten gehören. Klartext-Inventar dokumentiert in [`docs/security-notes.md`](https://github.com/anlaufstelle/app/blob/main/docs/security-notes.md).
- **CSP-Reporting via `report-uri`** — neuer lokaler `/csp-report/`-Endpoint speichert Browser-CSP-Verstöße als `AuditLog` (Typ `CSP_VIOLATION`). Trade-off-Diskussion zu `report-to` vs. `report-uri` in [`docs/security-notes.md`](https://github.com/anlaufstelle/app/blob/main/docs/security-notes.md).
- **MFA-Backup-Codes auf 128 Bit + Hash-Storage** — Codes werden mit `secrets.token_urlsafe(16)` (128 Bit Entropie) erzeugt und nur als HMAC-SHA-256-Hash gespeichert. Vorher: 80 Bit, Klartext in DB. Bestandsdaten werden beim nächsten Login pro User automatisch migriert.
- **Passwort-Reset-AuditLog: E-Mail durch HMAC-Hash ersetzt** — Anonyme Reset-Anfragen schrieben die E-Mail-Adresse im Klartext ins AuditLog. Jetzt landet nur noch ein HMAC-SHA-256-Hash dort — Wiederbenutzbarkeit bleibt für die Forensik (Burst-Erkennung), Klartext-Leak ist weg.
- **Passwort-Mindestlänge auf 12 Zeichen** — `MinimumLengthValidator` von 8 auf 12 angehoben. Bestandsuser werden beim nächsten Login zur Änderung gezwungen.
- **CSV-Export auf `Event.objects.visible_to(user)`** — Der CSV-Export ging am Rollen-Filter vorbei und exportierte Events, die der User in der UI nicht sehen durfte. Jetzt einheitlich über den `visible_to`-Manager.
- **q-Suchbegriffe nicht mehr in `sessionStorage`** — `data-filter-persist` enthielt das `q`-Feld, sodass eingegebene Pseudonyme nach Logout im Browser-`sessionStorage` zurückblieben. Filter-Persistenz schließt `q` jetzt explizit aus.
- **DSGVO-Top-Pseudonyme aus Standard-PDFs entfernt** — Standard-Auswertungen listeten die häufigsten Pseudonyme. Mit Internal-Mode-Banner getrennt: nur Admin-Internal-PDFs zeigen Pseudonyme, alle anderen aggregieren.
- **CSV-Formula-Injection neutralisiert** — `services/export.py` prefixt führende `=`, `+`, `-`, `@`, `\t`, `\r` mit `'`, damit Excel/LibreOffice die Felder nicht als Formel auswertet.
- **Retention löscht jetzt wirklich** — `EventHistory`-DELETE wurde im Retention-Pfad nicht mitgenommen, sodass „gelöschte" Events über die History weiter rekonstruierbar waren. Pfad ist jetzt durchgängig redaktiert; `audit_pruning` läuft ohne `DISABLE TRIGGER`.
- **`Client.anonymize()` schließt zugehörige Daten ein** — bei k-Anonymisierung wurden `EventHistory`, `EventAttachment` und `DeletionRequest` nicht mitgewandert. Jetzt atomar in einer Transaktion.
- **Login-Lockout `select_for_update` + Autocomplete `block=True`** — Race zwischen parallelen Failed-Login-Threads konnte den 10-Versuche-Trigger umgehen; `select_for_update` macht den Counter monoton. Autocomplete-Endpoint blockt unauthentifizierte Requests jetzt explizit, neuer Architektur-Test verbietet künftige Sensible-GETs ohne Auth-Check.
- **`WorkItemUpdateView`-Permission-Check** — die Edit-View verließ sich auf den Form-Layer für die Permission-Prüfung; jetzt zentral über `can_user_mutate_workitem`.
- **`FacilityScopeMiddleware` leert `app.current_facility_id` für anonyme Requests** — Login-, Health- und statische Requests sahen je nach Worker-Zustand das `facility_id` des vorherigen Requests im Connection-Pool, was RLS-relevant ist. Anonyme Requests schreiben jetzt explizit `NULL`.
- **Service-Layer-Konsistenz-Sweep** — vier Stellen aus Audit B.2.2 (RLS-Lücke bei Bulk-Aktionen, fehlende `select_for_update` auf zwei Counter-Updates) abgeräumt.
- **Validator erzwingt `is_encrypted=True` für FieldTemplate-Sensitivity HIGH** — bisher nur Form-Hint, jetzt Schema-Constraint im Save-Pfad.
- **SBOM (CycloneDX) als CI-Artefakt** — `release.yml` veröffentlicht jetzt eine `cyclonedx-bom.json` als Build-Asset; SCA-Scanner können den Stand pro Release direkt vom GitHub-Release ziehen.
- **CodeQL-Workflow** — neuer `codeql.yml` mit Python + JavaScript-Sprache, Cron + PR-Trigger; Sichtbarkeit Dev/Stage/App in [`docs/release-checklist.md`](https://github.com/anlaufstelle/app/blob/main/docs/release-checklist.md) dokumentiert.
- **Dev-Postgres an `127.0.0.1`** — `docker-compose.yml` band Postgres an `0.0.0.0:5432`, was lokal auf Multi-User-Maschinen beobachtbar war. Jetzt `127.0.0.1:5432`.

### Added

- **Vier-Augen-Lösch-Workflow für Personen mit Papierkorb-Frist** — Lösch-Anträge gehen erst nach Genehmigung durch Leitung/Admin in den Papierkorb, dort konfigurierbare Frist bis zur Hard-Deletion. Vor Ablauf ist Restore möglich.
- **Maintenance-Mode mit 503-Page** — Admin-toggelbarer Wartungsmodus mit IP-Allowlist; 503-Template im Design-System. Runbook: [`docs/ops-runbook.md` § 6.5a](https://github.com/anlaufstelle/app/blob/main/docs/ops-runbook.md).
- **Custom CSRF-Failure-Page** — Eigene 403-CSRF-Seite im App-Layout statt Django-Default; klare Handlungsanweisung („Bitte neu laden, Cookies prüfen").
- **PWA Offline-Fallback-Page für Navigation-Requests** — Service-Worker liefert eine eigene Offline-Seite für Navigation-Fetches statt der Default-Browser-Fehlerseite.
- **Aufklappbare Event-Cards im Zeitstrom** — Event-Cards lassen sich per Chevron inline aufklappen; alle Felder inkl. Textarea-Notizen direkt sichtbar. Generische `expandableCard`-Alpine-Komponente, identisches Pattern für Übergabe-Highlights.
- **Health-Checks SMTP / Encryption-Key / Backup-Alter / Disk-Frei** — `/health/` prüft jetzt zusätzlich SMTP-Erreichbarkeit, Encryption-Key-Verfügbarkeit, Backup-Alter und Disk-Frei. Kompatibler Health-Vertrag (clamav-Alias, `status`-Feld), Container-Healthcheck liest direkt das `status`-Feld.
- **DSGVO-Versionsstempel + AGPL-Footer in Templates** — DSGVO-Paket-Footer trägt App-Version, Generierungszeitpunkt und AGPL-Hinweis; in DSGVO-Template-Sektion versioniert.
- **Threat Model (STRIDE-Lite)** — neues [`docs/threat-model.md`](https://github.com/anlaufstelle/app/blob/main/docs/threat-model.md) mit Assets, Akteuren, Vertrauensgrenzen und STRIDE-Tabellen je Boundary inkl. Mitigation und offenen Lücken.
- **Architecture Decision Records (ADRs)** — drei ADRs nachgezogen: File Vault, MFA, Suche.
- **`reencrypt_fields` rotiert auch EventHistory + EventAttachment** — Schlüssel-Rotation deckt jetzt den vollständigen Daten-Pfad ab, nicht nur die Live-Events.
- **Off-Site-Backup-Hook in `scripts/backup.sh`** — optionaler Sync-Hook nach erfolgreichem Backup; State-File und Exit-Code für wiederholte Fehlversuche.
- **Backup-Restore-Drill als ausführbares Skript** — `scripts/restore-drill.sh` führt den 7-Schritt-Drill aus, prüft RLS und AuditLog-Trigger.
- **Übergabe-Highlights aufklappbar** — gleiches Toggle-Pattern wie Event-Cards in der Schichtübergabe.

### Changed

- **Sprachleitlinie „Klientel" → „Person"** — flächig durchgezogen: UI-Strings, Form-Labels, Fehlermeldungen, Handbuch, FAQ, admin-guide, README + Screenshots, Übersetzungs-Coverage-Wachhund in CI. Datums-/Zeitformate auf Django-L10N umgestellt (langes Format mit Wochentag).
- **Migrationen als One-Shot-Job vor Rolling-Restart** — `docker-compose.prod.yml` führt Migrationen jetzt in einem Init-Container aus, der vor dem Web-Service läuft. Lange RunPython-Migrationen blockieren keine Worker mehr.
- **Caddy: www-Redirect, Access-Log, Rate-Limit-Hinweis** — `www.anlaufstelle.app` redirected jetzt 301 auf Apex; Access-Log JSON-formatiert, Rate-Limit-Header dokumentiert.
- **DSGVO-Vorlagen ins App-Paket verschoben** — Templates wandern aus dem Repo-Root in `src/core/templates/dsgvo/`, sind im Paket und beim Deployment automatisch dabei.
- **Persistentes `media:`-Volume in `docker-compose.prod.yml`** — vorher Bind-Mount, das bei Coolify-Deploys verloren ging. Jetzt named volume mit Backup-/Restore-Pfad.
- **Service-Aufteilungen** — `services/event.py` in `services/events/` zerlegt (`crud.py`, `context.py`, `fields.py`, `attachments.py`). Retention-Strategien in `core/retention/strategies.py` konsolidiert. Statistik-Periodenparser extrahiert. `audit_pruning` ohne `DISABLE TRIGGER`.
- **`PaginatedListMixin` + `FEED_MAX_PER_TYPE` konsolidiert** — Pagination-Logik aus drei Listen-Views zusammengezogen, Feed-Maximum zentral.
- **`log_audit_event` in 8 View-Callsites** — direkter `AuditLog.objects.create`-Aufruf durch zentralen Service ersetzt; einheitliche Felder + IP-Hashing.
- **Audit-Plan-1 Quickwins R-001/R-005/R-006/R-007/R-008** — fünf kleine Refactorings aus dem Audit-Plan.
- **Inline-Imports an Modulkopf** — Retention-Hot-Path bekam Imports zentral, Modul-Lade-Zeit deterministisch.
- **`DocumentType.UniqueConstraint(facility, name, category)`** — vorher nur `(facility, name)`; jetzt erlaubt eine Einrichtung denselben Namen in unterschiedlichen Kategorien.

### Fixed

- **Datums-/Zeit-Tooltips in App-Sprache** — HTML5-Validation-Tooltip an `<input type="date">` folgte der Browser-Locale; jetzt lokalisierte Meldungen aus den App-Translations.
- **WorkItem Quick-Date-Buttons + Min-Date-Validierung** — „Heute / Morgen / Nächste Woche / In 2 Wochen" funktionieren wieder unter `@alpinejs/csp`.
- **WorkItem-Datumsvalidierung max. 31.12. Folgejahr** — `due_date`/`remind_at` müssen ≥ heute und ≤ 31.12. Folgejahr sein, sowohl HTML5 als auch Server-Side; verhindert versehentliche „Aufgabe verschwindet im Jahr 3345".
- **A11y-Cluster** (–) — `aria-invalid` + `aria-describedby` in fünf Form-Templates, Touch-Target ≥ 44 px für Sidebar + Datum-Arrows, stabile `aria-live`-Region für HTMX-Erfolge, `role=table/row/columnheader/cell` auf Personen-Liste, `html lang` dynamisch aus `LANGUAGE_CODE`, sekundärer Text ≥ 12 px, `non_field_errors`-Block in clients/cases/workitems-Forms, `tabindex`-Anti-Pattern aus Event-Create entfernt.
- **EN-Übersetzungen entfuzzt + fehlende msgids** — 27 fuzzy-markierte Übersetzungen finalisiert, fehlende msgids für neue Features ergänzt.
- **Datei-Download nicht mehr durch Service-Worker als Offline-Fallback abgefangen** — Download-Routes werden im SW-Match jetzt explizit ausgeschlossen.
- **Download liefert 404 statt Connection-Reset bei fehlender Datei** — vorher reset-by-peer, jetzt sauberer 404 mit AuditLog-Eintrag.
- **Zeitstrom-Dienstübersicht zeigt Kennzahlen statt leerer Karten** — KPI-Berechnung griff vor Time-Filter, Cards waren leer; Reihenfolge korrigiert.
- **Aufgaben-Bearbeiten-Button nur bei Berechtigung** — Button war für alle Rollen sichtbar, scheiterte aber serverseitig; jetzt im Template gegated.
- **Auswahlwerte in „Erfasste Daten" als Labels** — Multi-Select-Werte wurden als Python-Listen-Repr gerendert; jetzt komma-separierte Labels.
- **Fälle müssen einer Person zugeordnet sein** — `Case.client` von `null=True` auf `null=False, on_delete=PROTECT`; Bestandsdaten in Migration `0080` gegen Anonym-Marker referenziert.
- **Aufgaben-Quickbutton „Nächste Woche" = heute+7** — vorher der nächste Freitag, jetzt konsistent zu Buchungssystem-Konventionen.
- **Statistik-Begriffe verständlicher + PDF-Export Halbjahresbericht** — Labels „Eindeutige Personen / Top-Personen / Verlauf"; PDF-Export liefert jetzt einen Halbjahresbericht statt nur Jahres-Summen.
- **Login-Footer mit Anlaufstelle-Marke** — Footer fehlte auf der Login-Seite, jetzt konsistent.
- **WorkItem-Status-Race + Idempotenz-Guard** — paralleles Toggle „Done"/“Reopen" konnte zwei AuditLog-Einträge schreiben; `select_for_update` + Idempotency-Token.
- **Healthcheck-Vertrag stabilisiert** — `clamav`-Alias und Container-Healthcheck lesen jetzt das `status`-Feld einheitlich; vorher Inkompatibilität bei Coolify-Deploys.
- **Off-Site-Sync State-File + Exit-Code bei wiederholtem Fehler** — wiederholtes Sync-Scheitern bricht jetzt die Backup-Rotation hart ab statt silent zu loggen.
- **Offline `escapejs` aus `data-pk`-Attributen entfernt** — `escapejs` ist für JS-Strings, nicht HTML-Attributwerte; PK-Vergleich scheiterte bei UUIDs mit `-`.
- **Offline-Marker `__files__` minimieren** — vorher kompletter Filename + Mime im Marker, jetzt nur Anzahl.

### Performance

- **Explizites `Event.search_text` + GIN-trgm-Index** — Volltextsuche aus dem Trigger generierten Index zieht jetzt auf eine pre-computed `search_text`-Spalte; Fuzzy-Suche skaliert deutlich besser bei großen Facilities.
- **Locust-Last-Tests + Nightly-Workflow + Budgets** — `locustfile.py` mit realen Routes, Nightly-CI führt Last-Tests gegen ein E2E-Setup, Budgets in der Pipeline schlagen Alarm.
- **Query-Count-Schutz für 4 Detail-Views** — `assertNumQueries` für Klient-, Fall-, Episode-, Event-Detail; verhindert N+1-Regressionen bei künftigen Refactorings.
- **`apply_attachment_changes` Bulk-Load** — pro Event-Speicherung eine Query statt N (eine pro Anhang).
- **`SESSION_SAVE_EVERY_REQUEST=False`** — HTMX-Polls und statische Routes schreiben keine Session-Update mehr; reduziert DB-Writes spürbar.
- **Pagination-Cap auf 500** — `cases`/`clients`/`audit` haben jetzt einen Server-Side-Cap, verhindert versehentliche `?page_size=10000`-Requests.
- **`AuditLog`-Retention-Pruning in `enforce_retention`** — eigener Pruning-Pfad fürs Audit-Log; Tabelle wuchs vorher unbegrenzt.
- **`select_related` für Zeitstrom-Sidebar-Workitems** — sechs Queries pro Render → eine.

### Tooling / Internal

- **pre-commit-Config** — `.pre-commit-config.yaml` mit Ruff (Lint+Format), mypy, end-of-file-fixer, trailing-whitespace.
- **Ruff-Regelsatz erweitert** — `B` (bugbear), `UP` (pyupgrade), `SIM` (simplify), `N` (naming), `S` (security) aktiviert; Bestand auf 0 gebracht.
- **mypy-Strict-Zone für `core/forms`** — strikt typisierte Zone wächst weiter; Forms-Layer komplett getypt.
- **pip-licenses Allowlist als CI-Lint** — Build bricht bei nicht-allowlisted Lizenzen; verhindert versehentliche AGPL-fremde Dependencies.
- **Translation-Coverage-Wachhund** — CI prüft, dass alle msgids übersetzt sind; verhindert untranslated-strings-Drift.
- **Coverage-Gate `--cov-fail-under=93`** — Coverage darf nicht mehr unter 93 % fallen.
- **CHANGELOG-relevant: Tag-Signing verbindlich** — Release-Tags müssen GPG-signiert sein, in der Release-Checkliste festgeschrieben.

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
- **WorkItemInbox-Pagination** auf 50 Einträge pro Liste begrenzt; Querysets nicht mehr pauschal in Templates evaluiert.

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
