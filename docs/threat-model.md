# Threat Model — Anlaufstelle (STRIDE-Lite)

**Version:** v0.99.x · **Letzte Revision:** 2026-10-23 · **Quelle:** Issue #691, Offline-Grenze aus [ADR-022](adr/022-offline-snapshot-keys.md) (#1411)

Dieses Dokument macht das Sicherheitsmodell explizit. Es ergänzt — nicht ersetzt — die zeilengenauen internen Code-Audits (dev-only) und die Design-Entscheidungen in [`docs/security-notes.md`](security-notes.md).

> **Format:** STRIDE-Lite (Spoofing · Tampering · Repudiation · Information Disclosure · Denial of Service · Elevation of Privilege) je Vertrauensgrenze. Bewusst knapp; Abwägungen und Belege der einzelnen Entscheidungen stehen in [`security-notes.md`](security-notes.md).

---

## 1. Scope und Annahmen

**In Scope:**

- Django-Applikation `core` (Models, Services, Views, Middleware, Templates)
- Lauf-Stack laut [`docker-compose.prod.yml`](../docker-compose.prod.yml): Caddy 2 → Django/Gunicorn → PostgreSQL 18; ClamAV als Sidecar
- Backup-/Restore-Pfade in [`scripts/`](../scripts/)

**Out of Scope (vertrauen wir):**

- Host-OS, Docker-Daemon, Kernel-Härtung
- Deployment-Plattform und deren TLS-Terminierung
- Mail-Provider (SPF/DKIM/Spam-Filter) und Browser-Sandbox
- Physische Sicherheit von Backups jenseits der `BACKUP_ENCRYPTION_KEY`-Verschlüsselung

**Annahmen:**

- Postgres-DB-User ist **kein** Superuser ([`docs/ops-runbook.md` § 9](ops-runbook.md))
- `ENCRYPTION_KEYS` und `BACKUP_ENCRYPTION_KEY` werden über die Plattform-Geheimnisverwaltung bereitgestellt, nicht im Image
- Caddy terminiert TLS und setzt `X-Forwarded-Proto`; Django vertraut diesem Header (`SECURE_PROXY_SSL_HEADER` in [`prod.py`](../src/anlaufstelle/settings/prod.py))
- Betreiber:innen folgen dem [Ops-Runbook](ops-runbook.md) (Updates, Backups, DB-Rollen) und spielen nur veröffentlichte Releases ein

---

## 2. Assets

| Asset | Schutzziel | Wo persistiert | Klassifikation |
|---|---|---|---|
| **Klientendaten** (Pseudonyme, Events mit `data_json`, Klartext-Notizen) | C, I | Postgres `core_client`, `core_event`, `core_case`, `core_episode`, `core_workitem` | Art.-9 DSGVO (sozial/medizinisch) |
| **AuditLog** | I, A (Repudiation-Schutz gegen nachträgliche Änderung/Löschung) | Postgres `core_auditlog` (UPDATE/DELETE per DB-Trigger blockiert; INSERT erlaubt; Retention-Pruning siehe TB2/TB3-Tabelle) | hoch — gerichtsfest |
| **Encryption-Keys** | C | `ENCRYPTION_KEYS` env, App-Speicher zur Laufzeit | kritisch — kompromittiert ⇒ Klartext |
| **File-Vault-Anhänge** — Dateien an **Ereignis, Person oder Fall** (genau ein Owner je Zeile, [ADR-039](adr/039-owner-generische-dateianhaenge.md)) | C, I | Postgres `core_attachment` (eigene `facility_id`, **direkte** RLS-Policy statt Join über `core_event`) + `MEDIA_ROOT=/media/media`, Fernet-verschlüsselt ([`src/core/services/file_vault/encryption.py`](../src/core/services/file_vault/encryption.py)); Originaldateiname verschlüsselt in der Zeile | Art.-9 DSGVO |
| **Backups** | C, I | `backups/`, AES-256 mit Encrypt-then-MAC (HMAC-SHA256-Sidecar) ([`backup.sh`](../scripts/ops/backup.sh), [`_backup_common.sh`](../scripts/ops/_backup_common.sh)) | kritisch — Offline-Kopie aller Klientendaten |
| **Sessions** | C, I | Postgres `django_session`, HTTPOnly+Secure+SameSite-Cookie; Inaktivitäts-Fenster (`session_timeout_minutes`, Default 30 Min) **plus** absolutes Maximum ab Login (`session_absolute_hours`, Default 12 h, #1868) | mittel — Session-Hijack ⇒ Account-Übernahme, aber **zeitlich hart begrenzt**: kein Dauerzugriff durch bloßes Weiterpingen |
| **MFA-Secrets / Backup-Codes** | C | Postgres `otp_*` (django-otp): TOTP-Secret `otp_totp_totpdevice.key` **Fernet-verschlüsselt** at rest ([ADR-031](adr/031-totp-secret-at-rest.md), Refs #1362); Backup-Codes als zweckgebundene 96-Bit-HMAC-Präfixe mit Schlüssel aus `SECRET_KEY` gespeichert (#790); zusätzlich optionale WebAuthn-Passkeys (Public-Key-Credentials, kein at-rest-Geheimnis) ([ADR-032](adr/032-webauthn-passkeys-2fa.md), #1492) | hoch — kompromittiert ⇒ MFA-Bypass |
| **Login-Lockout-State** | I, A | nicht persistiert — zur Laufzeit aus AuditLog abgeleitet ([`src/core/services/security/login_lockout.py`](../src/core/services/security/login_lockout.py)) | mittel — Integrität AuditLog-abhängig (TB2/TB3); Restrisiken intern getrackt |
| **IndexedDB (Offline-Bundle)** — server-vorgefilterte Klient:innen-Spiegel (AES-GCM-256-verschlüsselte Bundles **plus Klartext-Index-Metadaten**) | C, I | Browser-IndexedDB des Client-Geräts: verschlüsselte Envelopes in `anlaufstelle-offline` ([`offline-store.js`](../src/static/js/offline-store.js)), non-extractable Session-Key in `anlaufstelle-crypto` ([`crypto.js`](../src/static/js/crypto.js)); Datei-Inhalte nie im Bundle | Art.-9-DSGVO-Spiegel (verschlüsselt); Ordnungsmerkmale im **Klartext** (`pk`, `clientPk`, `lastSynced`, `etag`, `localStatus`; Sende-Warteschlange `url`, `createdAt`, `retryAfter`; Entwürfe `formKey`, `updatedAt`), `expiresAt` liegt im verschlüsselten Umschlag — bewusstes Restrisiko (TB6), [ADR-022](adr/022-offline-snapshot-keys.md) |

C = Confidentiality, I = Integrity, A = Availability

> **Anmerkung zur Ableitung:** Wo eine Asset-Zeile als *abgeleitet* markiert ist, erbt sie nur die Schutzeigenschaften, die der Container im jeweiligen Scope tatsächlich liefert — der DB-Immutable-Trigger deckt nur die Operationen ab, die er erzwingt; darüber hinausgehende Restrisiken sind intern getrackt.

---

## 3. Akteure

| Akteur | Vertrauenslevel | Capabilities |
|---|---|---|
| **Anonymous** | niedrig | `/login/`, `/password-reset/`, `/health/`, statische Assets |
| **Assistent:in** | mittel | Lesen + eingeschränkter Schreibzugriff auf eigene WorkItems; sieht nur Sensitivity NORMAL — keine ELEVATED- und keine HIGH-Inhalte (`ROLE_MAX_SENSITIVITY` in [`sensitivity.py`](../src/core/services/compliance/sensitivity.py), Rollen-Mixins in [`mixins.py`](../src/core/views/mixins.py)) |
| **Fachkraft (Staff)** | mittel | Klient-CRUD, Event-Erfassung, Case-Bearbeitung; Sensitivity NORMAL+ELEVATED |
| **Leitung (Lead)** | hoch | Wie Staff + DSGVO-Workflows, Retention-Approvals, HIGH-Sensitivity |
| **Admin** | hoch | Cross-Facility-User-Management, Settings, Schema-Migrationen via Deploy |
| **Externer Angreifer** | feindlich | beliebige HTTP-Requests; ohne valide Session |
| **Kompromittierter DB-User** | feindlich | direkter SQL-Zugriff (Read+Write), aber **kein** Superuser |
| **Malicious Insider** | feindlich, mit valider Session | erweiterte Rechte missbrauchen, Audit-Spuren zu vermeiden |
| **Kompromittierter Admin-Account** | feindlich, Vollzugriff | Worst-Case — Mitigation primär organisatorisch + 4-Augen-Prinzip |

---

## 4. Vertrauensgrenzen

```
   ┌─ Browser (Anonymous / authentifizierter User) ─────────┐
TB1│        ↕ HTTPS, SameSite=Lax/Strict-Cookies            │
   ├─ Caddy 2 (TLS-Termination, Reverse-Proxy) ─────────────┤
TB2│        ↕ HTTP intern (frontend-Netz)                   │
   ├─ Django / Gunicorn (App-Logik, Middleware-Stack) ──────┤
TB3│        ↕ Postgres-Protokoll (internal-Netz)            │
   ├─ PostgreSQL 18 (RLS-FORCE, immutable AuditLog-Trigger) ┤
TB4│        ↕ INSTREAM via TCP (internal-Netz)              │
   └─ ClamAV (Datei-Scan, fail-closed) ─────────────────────┘
```

Rate-Limits setzt die Anwendung (`django-ratelimit`), nicht Caddy: das mitgelieferte [`Caddyfile`](../Caddyfile) enthält den Rate-Limit-Block nur auskommentiert, er bräuchte das Plugin `caddy-ratelimit`.

ClamAV hängt außer am `internal`-Netz am eigenen Netz `clamav-egress` (`internal: false`), damit `freshclam` Signaturen per DNS/HTTPS aus dem Internet laden kann; das Netz verbindet keinen weiteren App-Service ([`docker-compose.prod.yml`](../docker-compose.prod.yml)). Der Scanner sieht jeden Upload im Klartext — TB4 hat damit eine zweite, ausgehende Seite (Zeile **I** in der TB4-Tabelle).

Backups (`backup.sh`) verlassen den Host nur **verschlüsselt** und nur, wenn `BACKUP_OFFSITE_TARGET` gesetzt ist (TB5: Off-Site).

**TB6 — Offline: Client-Gerät als Datenhalter** (orthogonal zur Server-Kette oben). Im Offline-Modus ([ADR-022](adr/022-offline-snapshot-keys.md)) landen server-vorgefilterte Snapshot-Bundles in der Browser-IndexedDB des Mitarbeiter:innen-Geräts. Das **Client-Gerät** wird damit zu einer eigenen Vertrauensgrenze — verschlüsselter Datenbestand *at rest* plus Klartext-Index-Metadaten, außerhalb der Reichweite aller Server-Mitigations (TB1–TB5). Wie TB5 (Off-Site-Backups) ist TB6 keine Inline-Station der Request-Kette, sondern eine terminale Daten-*at-rest*-Grenze.

```
   ┌─ Client-Gerät (Browser-Storage: IndexedDB, Cache) ─────┐
TB6│   Snapshot-Bundle AES-GCM-256 (non-extractable Key)    │
   │   + Klartext-Index-Metadaten · Idle-Sperre/-Wipe       │
   └─ ↕ derselbe Browser wie TB1 (Session-gebundener Key) ──┘
```

---

## 5. STRIDE pro Vertrauensgrenze

### TB1 — Browser ↔ Caddy/Django

| Threat | Mitigation (Bestand) | Quelle | Offene Lücke |
|---|---|---|---|
| **S** Session-Hijack via XSS | CSP `script-src 'self'` ohne `unsafe-eval`; Architektur-Test verbietet Inline-`<script>`/`x-data`-Objekte; `\|safe`, `{% autoescape off %}`, `mark_safe()` und `csrf_exempt` nur mit begründetem Allowlist-Eintrag (#2468, eigene Templates/Python-Code sowie installierte Django-Admin-/Unfold-Pakettemplates im Markup-Guard und Paket-Python im CSRF-Guard; konkrete Ausnahmen mit Einzelbegründungen, Inline-/Alpine-Guard nur für eigene Templates); `SESSION_COOKIE_HTTPONLY=True`; `report-uri /csp-report/` loggt Violations strukturiert ([CSPReportView](../src/core/views/csp_report.py)); **auswertbarer Sink ist die Server-Log-Datei (Logger `security.csp`), nicht Sentry** — kein `event_level` für diesen Logger, bewusste Noise-/PII-Abwägung ([Begründung in security-notes.md](security-notes.md#csp-reporting-issue-684), #1949). Was umgekehrt doch beim Tracker landet, trägt keine URL-Pfad-Tokens und keinen Query-String aus `Referer`/`HX-Current-Url` (Route-Schema statt konkreter URL, #2198; Einzelheiten im Abschnitt „Error-Tracker-Scrubbing“ unten). **Bewusste Ausnahmen:** (1) jede HTML-Antwort unter `/admin-mgmt/`, **auch die anonyme Admin-Login-Seite**, erhält per [`AdminCSPRelaxMiddleware`](../src/core/middleware/admin_csp_relax.py) `'unsafe-eval'` in `script-src` und `'unsafe-inline'` (ohne Hashes) in `style-src` (django-unfold-Kompatibilität, [ADR-025](adr/025-csp-unsafe-eval-admin.md), [Trade-off in security-notes.md](security-notes.md#csp-unsafe-eval-auf-admin-mgmt-issue-695)); (2) auf `demo.anlaufstelle.app` gibt [`DemoUmamiCSPRelaxMiddleware`](../src/core/middleware/demo_umami_csp_relax.py) den Umami-Host in `script-src`/`connect-src` frei — **nur `DEMO_MODE` + Post-Login + HTML**, synthetische Seed-Daten (#1604, [Trade-off in security-notes.md](security-notes.md#eng-gescopte-ausnahme--umami-analytics-auf-der-demo-issue-1604)); (3) `settings/devlive.py` erweitert `script-src`/`connect-src` global um den Umami-Host, sobald `UMAMI_SCRIPT_SRC` gesetzt ist | [`settings/base.py`](../src/anlaufstelle/settings/base.py) (`CONTENT_SECURITY_POLICY`), `src/tests/test_architecture_guards_*.py`, `src/tests/test_demo_umami_csp_relax.py` | — (CSP-Reporting #684 geschlossen) |
| **S** Session-Hijack via entwendetes/zurückgelassenes Cookie (Gerätediebstahl, geteiltes Gerät, Cookie-Exfiltration) | Zwei unabhängige Zeitgrenzen: **Inaktivitäts-Fenster** (`session_timeout_minutes`, Default 30 Min, [`SessionIdleRefreshMiddleware`](../src/core/middleware/session_idle.py), #1858) **und absolutes Maximum ab Login** (`session_absolute_hours`, Default 12 h, je Einrichtung 1–168 h, #1868). Das Maximum wird beim Login eingefroren und von Aktivität **nicht** verschoben — auch ein dauersyncendes Gerät (Offline-Replay, Sync-API) erreicht es. Ablauf = `logout()` mit Session-Flush und `LOGOUT`-AuditLog. Zusätzlich: `Clear-Site-Data` beim Logout, Salt-Rotation + Session-Flush bei Rechteentzug ([`offline_invalidation.py`](../src/core/signals/offline_invalidation.py)). **Rotation der Django-Session-ID (Cookie-Wert `sessionid`) an den nachfolgend genannten Rechteanhebungen innerhalb der Sitzung** (#2034): MFA-Verify per TOTP, MFA-Verify per Backup-Code, der Verify-Schritt der 2FA-Einrichtung, deren Abschluss (Backup-Code-Quittung), die Neuerzeugung von Backup-Codes, eine erfolgreiche Passkey-Assertion, der Sudo-Grant und die Übernahme eines gültigen Passwort-Reset-/Invite-Tokens in die Sitzung erneuern die ID und löschen die bisherige Sitzungszeile — Django rotiert von sich aus beim Login und beim eigenen Passwortwechsel (`update_session_auth_hash`), **nicht** aber an diesen Übergängen — ein davor entwendetes Cookie erbte dort sonst die angehobene Fähigkeit ([`session_rotation.py`](../src/core/services/security/session_rotation.py)). Gemeint ist ausschließlich Djangos Session-ID, **nicht** der gleichnamige „Session-Key“ der Offline-Verschlüsselung (TB6) | [`session_idle.py`](../src/core/middleware/session_idle.py), [`auth.py`](../src/core/views/auth.py), [`test_middleware_session_idle.py`](../src/tests/test_middleware_session_idle.py), [`test_session_rotation.py`](../src/tests/test_session_rotation.py), [Trade-off in security-notes.md](security-notes.md#absolutes-sitzungs-maximum-ab-login-issue-1868) | Innerhalb der Deadline hat ein gestohlenes Cookie vollen Zugriff — die Grenze verhindert nur den unbegrenzten Dauerzugriff; ein Settings-Wechsel wirkt erst ab dem nächsten Login (Deadline ist eingefroren). Die Rotation entwertet nur, was **vor** einer Rechteanhebung entwendet wurde — ein nach dem letzten Step-up abgegriffenes Cookie bleibt bis zu einer der beiden Zeitgrenzen gültig |
| **S** Login-Brute-Force | IP `5/m` + Username `10/h` Rate-Limit, Account-Lockout 10/15 Min **je Quell-IP** (#1372), MFA optional/erzwingbar, Captcha-frei aber audit-getrackt; verteilte Bruteforce-/Victim-Lockout-Signatur löst Breach-Alarm aus (`detect_distributed_login_attack`), ebenso anonyme Login-Bursts gegen **unbekannte** Usernames und ein sekundäres 24-h-Langzeitfenster (#1368); der `super_admin`-Break-Glass-Account ist zentral im Lockout-Service ausgenommen (`is_locked`→`False`, `unlock` No-Op), damit er nicht per Fehlversuchs-Flut aussperrbar ist (DoS auf die Recovery-Rolle, #1375) | [`auth.py`](../src/core/views/auth.py), [`src/core/services/security/login_lockout.py`](../src/core/services/security/login_lockout.py), [`breach_detection.py`](../src/core/services/compliance/breach_detection.py) | Username-`10/h`-Achse: weiche Victim-Lockout-Restfläche bewusst Monitor-only ([Trade-off in security-notes.md](security-notes.md#username-ratelimit-bleibt-monitor-only-statt-hartem-fix-issue-1372)) |
| **S** Phishing / Replay des zweiten Faktors | TOTP (`django-otp`, Secret Fernet-at-rest [ADR-031](adr/031-totp-secret-at-rest.md)) als Standard-2FA; optional **Passkey/WebAuthn** (FIDO2, origin-gebunden ⇒ **phishing-resistent**) als zusätzlicher zweiter Faktor — nur **zusätzlich** zu einem TOTP-Gerät, kein passwordless Login, Passkey-Entfernen sudo-pflichtig; 2FA-Login mit Methoden-Chooser | [ADR-032](adr/032-webauthn-passkeys-2fa.md), #1492 | Neue Auth-Angriffsfläche: serverseitige WebAuthn-Registrierung/-Assertion-Verifikation |
| **T** CSRF / Replay | `CsrfViewMiddleware` + `CSRF_COOKIE_HTTPONLY=True` + `SAMESITE="Strict"` (CSRF) / `"Lax"` (Session, [siehe security-notes](security-notes.md)) | [`prod.py`](../src/anlaufstelle/settings/prod.py) | — |
| **R** Password-Reset-Token-Replay | Django-Default-Tokens (signed); Reset-Link explizit kurzlebig (`PASSWORD_RESET_TIMEOUT` = 2 h statt 3-Tage-Default), Einladungen über eigenen `invite_token_generator`/`INVITE_TOKEN_TIMEOUT` entkoppelt (#1375); Token-Verbrauch loggt AuditLog | [`auth.py`](../src/core/views/auth.py) | — |
| **I** Mitschauen am Arbeitsplatz / physischer Zugriff auf die offene Sitzung (Empfangsplatz, Frauenhaus, Dokumentation am oeffentlichen Ort, BYOD) | **Quick-Exit + Sichtschutz** (#2136): Knopf „Schnell verlassen“ auf jeder angemeldeten Seite (erstes Tab-Ziel nach dem Skip-Link) plus `Strg`/`Cmd`+`Umschalt`+`X` und `Umschalt`+`Esc`, auch aus Eingabefeldern heraus — verdeckt den Inhalt sofort (`visibility:hidden` + Blende), beendet die Sitzung serverseitig (`POST /logout/` per `sendBeacon`, ohne auf die Antwort zu warten) und ersetzt die Seite per `location.replace` durch die einrichtungsweit konfigurierte neutrale Adresse (`Settings.quick_exit_url`); ein bfcache-Restore beim Zurück verdeckt und lädt neu (`pageshow`/`persisted`, ergänzend zu `Cache-Control: no-store` aus #1342). Opt-in-Sichtschutz bei `blur`/`visibilitychange` (`Settings.privacy_screen_on_blur`), Freigabe nur per Klick/Taste (kein Timer, WCAG 2.2.1). Offline dieselbe Geste ohne Server-Logout: der Sitzungsschlüssel wird verworfen, der verschlüsselte Offline-Bestand bleibt auf dem Gerät (TB6). Flankiert vom Inaktivitäts-Fenster und dem absoluten Sitzungs-Maximum (Zeile oben) | [`quick-exit.js`](../src/static/js/quick-exit.js), [`base.html`](../src/templates/base.html), [`settings.py`](../src/core/models/settings.py), [`test_quick_exit.py`](../src/tests/test_quick_exit.py) + [E2E](../src/tests/e2e/test_quick_exit.py), `tests/js/quick-exit.test.mjs` | Der Schulterblick VOR dem Auslösen bleibt unadressiert (kein Blickschutzfilter-Ersatz); ungespeicherte Eingaben gehen mit der Abmeldung verloren — eine „Sperre statt Logout“ (Re-Auth ohne Sitzungsende) ist bewusst ein Folgeschritt, weil sie die Sitzung serverseitig gerade NICHT beendet; die Zieladresse steht danach im Geräteverlauf und muss deshalb unverfänglich gewählt sein (Betreiber-Entscheidung, Admin-Guide § 2.3); drei weitere benannte Restrisiken siehe Absatz unten |
| **I** Pseudonym-Leak via Suche | Suche über `Client.pseudonym` und den Suchindex `Event.search_text` (`ilike`), nicht über `data_json`; der Index enthält schreibseitig nur unverschlüsselte, nicht erhöhte Feldwerte (#827); lesend zusätzlich Sensitivity-Filter über den Dokumentationstyp, Papierkorb-Ausschluss und RLS | [`src/core/services/dashboard/search.py`](../src/core/services/dashboard/search.py) | — |
| **D** Login-Flood / Bot-Spam | Anwendungs-Limits (`django-ratelimit`) auf Login und Passwort-Reset; Konfigurations-POSTs und Passwortänderung 60/h je Benutzer, anonyme Passwort-/Einladungseinrichtung 5/min je IP, Sprachwahl 60/h je Benutzer oder IP. `TestRateLimitOnAllMutations` prüft in den erkannten Deklarationsformen fail-closed zur Laufzeit rekursiv den aktiven URL-Resolver für POST, PUT, PATCH und DELETE, einschließlich Admin-/WebAuthn-Routen, Funktionen mit `require_POST`/`require_http_methods`, geerbten Mutationshandlern und Formularviews. Er erkennt die üblichen Deklarationsformen (geerbte Handler, Formularviews, Methodenfilter und Projekt-Limit-Wrapper). Dabei zählen nur effektive Handler-/Dispatch-Bindungen mit prüfbarer Markierung von `core.utils.ratelimit`, endlicher Rate, passender Methode und `block=True` oder ein nachgewiesener Methodenfilter; verdeckte Eltern-Dispatches, kopierte Attribute, eigene Dispatches/Instanzbindungen und dynamische Raten ohne nachgewiesenen äußeren Limiter oder Methodenfilter werden in den erkannten Deklarationsformen abgelehnt. Login-/Passwort-/Recovery-Formularviews teilen Schreibquoten; beide Passkey-Beginnpfade sind vor dem Parser auf 60/h begrenzt (Registrierung je Benutzer, Anmeldung je Benutzer oder IP). Verwaltungsrouten erhalten 60/h je URL und Benutzer oder IP; Admin-Login 5/min je IP und 10/h je normalisiertem Benutzernamen für schreibende Methoden. Ausnahmeliste nach URL-Name, POST-Methode und bekannter Zielklasse bzw. Zielfunktion: `logout` (`CustomLogoutView.post`, Sitzungsende einschließlich Schnell-verlassen), `admin:logout` (`AdminSite.logout`, Verwaltungs-Abmeldung) und `core:client_rename_warning` (`ClientRenameWarningView.post`, lesende Pseudonym-Warnprobe). Caddy hat kein Edge-Limit | [`test_architecture_guards_views.py`](../src/tests/test_architecture_guards_views.py), [`ratelimit.py`](../src/core/utils/ratelimit.py), [`test_resolved_endpoint_rate_limits.py`](../src/tests/test_resolved_endpoint_rate_limits.py), [`Caddyfile`](../Caddyfile) | Nicht erkannt werden u. a. eigene Prüf-Hooks von Auth-Mixins (`LoginRequiredMixin`, `PermissionRequiredMixin`, `UserPassesTestMixin`) vor der Drosselung und per `as_view(...)` ersetzte Dispatches unter dem URL-Namen `logout`; die Klassenausnahme belegt dort nicht den tatsächlich ausgeführten Dispatch. Verteilte Last kann vor dem Anwendungs-Limit Ressourcen binden; der Guard prüft importierte Bindungen und konfigurierte Schranken, keine Lastmessung, GET-Schreibsemantik oder nachträgliche Bindungsänderungen |
| **E** Privilege-Escalation via Form-Tampering | Server-seitige Permission-Checks via Mixin-Hierarchie + `can_user_mutate_*`-Helper; `UserAdmin` entfernt Djangos native Permission-Felder (`is_staff`/`is_superuser`/`groups`/`user_permissions`) für **alle** Editoren vollständig aus Formular **und** Fieldset, sodass ein POST-Smuggling folgenlos bleibt (Autorisierung läuft ausschließlich über `role`) (#1371) | [`mixins.py`](../src/core/views/mixins.py), [`workitem_actions.py`](../src/core/views/workitem_actions.py), [`users.py`](../src/core/admin/users.py) | — (WorkItem-Race #129 Teil A geschlossen) |
| **R** Admin-Schreibpfad umgeht Domänen-Audit/Service-Invarianten; Cross-Facility-PII-Read unauditiert (`/admin-mgmt/`) | Fachobjekte (`Client`/`Case`/`Event`/`WorkItem`) im Django-Admin strikt read-only (`ReadOnlyDomainAdminMixin`) — kein Bypass von AuditLog/EventHistory/Vier-Augen; super_admin-/Cross-Facility-Reads werden als `SYSTEM_VIEW` auditiert (`AdminReadAuditMixin`, analog `/system/`); Architektur-Guard erzwingt read-only für neue Fach-Model-Admins | [`mixins.py`](../src/core/admin/mixins.py), [`test_admin_audit.py`](../src/tests/test_admin_audit.py), [`test_architecture_guards_audit.py`](../src/tests/test_architecture_guards_audit.py) | — (Refs #1341; Trade-off in [security-notes.md](security-notes.md#fachobjekte-im-admin-read-only--cross-facility-read-audit-issue-1341)) |

#### Restrisiken des Mitschauen-Schutzes (Refs #2136)

Der Quick-Exit leistet zuverlässig das, wofür er da ist: Der Inhalt des auslösenden
Fensters ist weg, bevor irgendetwas anderes passiert. Alles Weitere ist **Best Effort mit
benannten Grenzen** — vier Restflächen, die bewusst nicht geschlossen sind und hier
stehen, statt in der Zeile oben unterzugehen.

1. **Frühere Verlaufseinträge derselben Sitzung.** `location.replace`
   ([`quick-exit.js`](../src/static/js/quick-exit.js)) ersetzt nur den *letzten*
   Verlaufseintrag. Alle vorher geöffneten App-Seiten bleiben in der Browser-Chronik —
   mit Pfad (`/clients/<pk>/`) **und Seitentitel**, und der Titel einer Personenseite
   trägt das Pseudonym ([`core/clients/detail.html`](../src/templates/core/clients/detail.html)).
   Der Browser-Verlauf ist aus JavaScript nicht löschbar (kein Web-API dafür), die
   Restfläche ist also technisch inhärent und nicht wegzuprogrammieren. **Gegenmaßnahme
   organisatorisch:** Verlauf am Schichtende löschen bzw. auf geteilten Geräten im
   privaten Fenster arbeiten — so im Nutzerhandbuch benannt; der Admin-Guide weist auf
   die Pseudonyme im Seitentitel hin.
2. **Zweiter offener Tab.** Der Quick-Exit wirkt pro Fenster. Ein zweiter Tab behält
   seinen bereits gerenderten DOM, bis dort etwas navigiert (dann greift die
   serverseitig beendete Sitzung mit 302 auf `/login/`) — ein statischer DOM verlangt
   keinen Request. Sein **Memory-Krypto-Schlüssel** wird entwertet, **sofern die
   Löschung im auslösenden Tab nachweislich gelungen ist** (nachweislich heißt:
   Erfolgsmeldung von `clearSessionKey()` **und** leere Rückleseprobe, siehe
   Punkt 4): nur dann sendet Quick-Exit
   `syncOrchestrator.broadcast({type:"key-cleared"})` — denselben Kanal, den
   [`crypto.js`](../src/static/js/crypto.js) nach dem Idle-Wipe nutzt. Der Empfänger
   verwirft seinen Schlüssel, wenn die IndexedDB-Grundlage fehlt, und **verankert ihn
   sonst neu**; genau deshalb wird nach Timeout oder Fehler (Punkt 4) **nicht**
   gesendet — ein Broadcast wäre dort schädlicher als keiner. Ohne
   `BroadcastChannel`/Sync-Orchestrator ist der Kanal ein stiller No-op. Im Ergebnis:
   der zweite Tab behält im Regelfall keinen brauchbaren Schlüssel mehr, sein bereits
   gerendertes Markup aber in **jedem** Fall. **Gegenmaßnahme organisatorisch:** zweite
   Fenster schließen und das Gerät nicht unbeaufsichtigt lassen.
3. **Verlorener Logout-Beacon.** Der Logout ist bewusst fire-and-forget: Es
   wird nicht auf die Antwort gewartet, damit die Navigation nicht am Netz hängt. Reißt
   die Verbindung genau in diesem Moment (`sendBeacon` liefert `false` **und** der
   `fetch`-Fallback stirbt), überlebt die **Server-Sitzung**; ein späterer, neuer Aufruf
   einer App-Seite von diesem Gerät aus liefert dann wieder Inhalt. Zeitlich gedeckelt
   ist das durch das Inaktivitäts-Fenster (Vorgabe 30 Min,
   #1858) und das absolute
   Sitzungs-Maximum (Vorgabe 12 h,
   #1868) — dieselbe Lage wie
   bei einem Netzabriss während eines normalen Abmeldens. Das auslösende Fenster ist
   unabhängig davon leer und weg navigiert.
4. **Löschung des lokalen Schlüssels innerhalb der Frist.** Der persistente
   Offline-Sitzungsschlüssel wird **abgewartet** gelöscht, bevor navigiert
   wird — aber mit Deckel (500 ms). Der Regelfall ist die abgeschlossene Löschung; hängt
   IndexedDB länger oder schlägt der Delete fehl, wird trotzdem verlassen (die Blende
   liegt zu diesem Zeitpunkt bereits, der Bildschirm ist geschützt), und der Schlüssel
   **bleibt dann liegen** — bis zum nächsten regulären Abmelden
   (`Clear-Site-Data: "storage"`), bis die Leerlaufprüfung beim nächsten Öffnen der App greift
   (#1324); die 48-h-Bundle-TTL
   räumt abgelaufene Spiegel ebenfalls erst beim nächsten Start mit geladenem Schlüssel ab (TB6). Bis dahin sind die lokal liegenden,
   verschlüsselten Offline-Bestände auf diesem Gerät wieder lesbar, sobald jemand die
   App erneut öffnet. Der Fall ist unterscheidbar gemacht statt stillschweigend als
   Erfolg gewertet — in zwei Schritten:
   * `clearSessionKey()` in [`crypto.js`](../src/static/js/crypto.js) **meldet** den
     Ausgang (`{persisted: true|false}`) statt einen gescheiterten
     IndexedDB-Delete zu verschlucken. Es wirft nicht — jeder
     Aufrufer (`wipeOfflineState`, Idle-Wipe, Passwortwechsel) `await`et die Funktion
     und würde von einer Rejection unvermittelt abbrechen; beim Logout wäre ein
     abgebrochener Wipe schlimmer als ein stiller. Der Memory-Schlüssel fällt in jedem
     Fall.
   * `_dropSessionKey()` in [`quick-exit.js`](../src/static/js/quick-exit.js) wertet nur
     als `cleared`, was **beide** Belege hat: die Erfolgsmeldung UND eine leere
     Rückleseprobe (`hasPersistedSessionKey()`). Eine synchrone Rückkehr, ein fehlendes
     Ergebnisobjekt, ein Lesefehler oder eine noch stehende Zeile zählen als `error`;
     der Deckel als `timeout`. Nur `cleared` löst den Broadcast aus Punkt 2 aus,
     `timeout`/`error` schreiben eine Konsolenwarnung.

   **Nicht messbar im Betrieb** (kein Telemetriekanal, und ein Audit-Ereignis ist hier
   bewusst ausgeschlossen) — deshalb die organisatorische Empfehlung, das Gerät nach dem
   Verlassen nicht unbeaufsichtigt zu lassen.

**Wie die Grenzen geprüft werden:** `location.replace` würde eine noch laufende
IndexedDB-Transaktion abreißen — deshalb wartet der Quick-Exit die Löschung ab (Punkt 4), und der
E2E-Fall liest die Schlüsselzeile **nach** dem Dokumentwechsel. Ein gescheiterter Delete darf
nicht als Erfolg durchgehen: ein `key-cleared`-Broadcast würde den Schlüssel in einem Fremd-Tab
über `_applyKeyCleared` sogar **wieder verankern**; das schließt der Melde- und Rücklesepfad aus
Punkt 4 aus. Der Sprung hängt außerdem unmittelbar vor `location.replace` ein
`<meta name="referrer" content="no-referrer">` in den `<head>`: Das Sprungziel —
per Vorgabe ein Dritter — erfährt die Herkunft nicht mehr über den `Referer`, und die
`Referrer-Policy` aller übrigen Seiten bleibt unverändert (#2179).

#### Error-Tracker-Scrubbing (Refs #2198)

Gilt nur bei gesetztem `SENTRY_DSN` (opt-in). Der Detection-Sink für Security-Ereignisse ist die Server-Log-Datei, nicht Sentry (CSP-Zeile oben, #1949). Was an Sentry geht, läuft durch `before_send` bzw. `before_send_transaction` ([`_sentry.py`](../src/anlaufstelle/settings/_sentry.py)): `request.url` (das SDK baut ihn aus `scheme://host` + `SCRIPT_NAME` + rohem `PATH_INFO`), `Referer`, `HX-Current-Url` und der Transaktionsname im rohen URL-Fallback (`transaction_info.source == "url"`) verlieren den Query-String, der Pfad wird gegen `django.urls.resolve(...).route` auf das Route-Schema zurückgeführt; für nicht auflösende Pfade greift eine Heuristik, die auch signierte Wiederherstellungs-Token (`TimestampSigner`) in klarer und prozentkodierter Schreibweise erfasst. Ohne diese Maskierung stünde ein Token der Routen `invite/<uidb64>/<token>/`, `password-reset/<uidb64>/<token>/` oder `account/recovery/confirm/<str:token>/` bis zu seinem Ablauf nutzbar im Tracker. Die Log-Felder `logentry.formatted`/`logentry.params` laufen durch denselben Scrub; personenbezogene Schlüssel-Wert-Paare (`user=`, `name=` …) und `remote_addr` werden maskiert. **Nicht abgedeckt**, weil sie am Hook vorbeilaufen: Sentrys eigene Log-Übertragung (`enable_logs`) und Span-Streaming — beide in [`prod.py`](../src/anlaufstelle/settings/prod.py) nicht gesetzt.

### TB2/TB3 — Django ↔ PostgreSQL

| Threat | Mitigation (Bestand) | Quelle | Offene Lücke |
|---|---|---|---|
| **S** Connection-Pool-Tenant-Leak | `FacilityScopeMiddleware` setzt `app.current_facility_id` pro Request — auch leer für anonyme Requests. Dieselbe Middleware beendet Requests **angemeldeter Konten ohne Einrichtung** (außer `super_admin`) mit einer erklärenden 403 (#2090); die GUC wird auch im Sperrfall geleert | [`facility_scope.py`](../src/core/middleware/facility_scope.py) — Refs #733, #2090 | — |
| **T** AuditLog-Manipulation | DB-Trigger `auditlog_immutable` (BEFORE UPDATE/DELETE), `ENABLE ALWAYS` (feuert auch bei `session_replication_role=replica`, #1026) + Python-Override; Test [`test_audit_trigger.py`](../src/tests/test_audit_trigger.py) | Migration [`0024`](../src/core/migrations/0024_auditlog_immutable_trigger.py), [`0107`](../src/core/migrations/0107_append_only_triggers_enable_always.py) | — |
| **E** Audit-Bypass via DB-Owner-DDL (`DISABLE TRIGGER`) | Runtime-/Owner-Rollentrennung (A1.4): Web-Runtime-Rolle ist **kein** DB-Owner → kann Append-Only-Trigger nicht per `ALTER TABLE … DISABLE TRIGGER` abschalten; Owner ist die BYPASSRLS-Wartungs-Rolle. Test [`test_rls.py::TestAppendOnlyTriggerOwnership`](../src/tests/test_rls.py) | [`01-app-role.sh`](../deploy/postgres-init/01-app-role.sh), [`_db_admin.py`](../src/core/services/system/_db_admin.py) — #1026 | Fehlkonfigurierte Rollen (z. B. Cluster ohne Init-Script, Runtime-Rolle doch Owner) heben den Schutz auf, bis sie repariert sind: [`ops-runbook.md` § 9.6](ops-runbook.md) |
| **T** Verschlüsselungs-Bypass via `bulk_create`/`update(data_json=...)` | Verschlüsselung in `Event.save()`/`encrypt_field`; Architektur-Test gegen `bulk_create`/`update(data_json=...)` außerhalb Service-Layer | [`test_architecture_guards_services.py::TestDynamicDataEncryptionBypassGuard`](../src/tests/test_architecture_guards_services.py) | Der Guard ist ein Quelltext-Scan: Raw-SQL oder dynamisch gebaute Aufrufe erkennt er nicht |
| **R** Insider-Aktion ohne Spur | AuditLog mit festem Aktionskatalog (`AuditLog.Action`) + DB-Immutable; Privileg-Änderungs-Audits (Rollenwechsel/Deaktivierung/Vier-Augen-Recht) erfassen den handelnden Admin im `actor`-Feld tamper-evident in der HMAC-Kette (#1369); massenhafte Löschung von Personendaten (soft-delete/anonymize/deletion-approved) triggert die Breach-Detection (#1368) | [`audit.py`](../src/core/models/audit.py), [`chain.py`](../src/core/services/audit/chain.py) | Nicht jede Zustandsänderung erzeugt einen eigenen AuditLog-Eintrag; WorkItem-Status-Übergänge laufen append-only (#1467, auch offline-replaybar #1419) |
| **I** Cross-Facility-Read | RLS FORCE-Modus auf allen facility-gebundenen Tabellen (Menge `EXPECTED_TABLES`) + Manager-Layer + Mixin-Layer (Defense-in-Depth) | Migration [`0047`](../src/core/migrations/0047_postgres_rls_setup.py), ableitungsbasiert erzwungen in [`test_rls.py`](../src/tests/test_rls.py) | Statistik-MV bewusst ohne RLS — siehe [`security-notes.md`](security-notes.md); `core_user`, `core_facility` und `core_organization` liegen außerhalb von RLS ([ADR-005](adr/005-facility-scoping-and-rls.md)) |
| **I** Sensitive Felder ohne Encryption | `is_encrypted=True` erzwungen für `Sensitivity=HIGH` — Refs #733; `Client.pseudonym` bleibt bis post-v1.0 im Klartext (UX-Trade-off, [Defer-Begründung](security-notes.md#clientpseudonym-bleibt-im-klartext-bis-post-v10-issue-717)) | [`document_type.py`](../src/core/models/document_type.py), [`client.py`](../src/core/models/client.py) | Klartext-Freitexte (`Client.notes`, `Case.description`) — nur Formular-Hinweis „keine Klarnamen“, keine Verschlüsselung (#716) |
| **I** TOTP-Secret-Leak at rest (Dump/Replica/Backup) | TOTP-Secret Fernet-verschlüsselt über das Proxy-Modell `EncryptedTOTPDevice` (vorhandenes MultiFernet, Key-Rotation) — ein DB-Dump liefert nur Chiffretext; konsistent zu den Hash-Backup-Codes (#790) | [ADR-031](adr/031-totp-secret-at-rest.md), [`mfa.py`](../src/core/models/mfa.py), [`totp.py`](../src/core/services/security/totp.py) | — (Refs #1362 geschlossen) |
| **D** AuditLog-Tabelle wächst unbegrenzt | Composite-Indexes + eigene AuditLog-Frist via `enforce_retention` (`Settings.auditlog_retention_months`, **Default 24 Monate, pro Einrichtung konfigurierbar, `0` = Pruning abgeschaltet**); `SECURITY_VIOLATION`/`RETENTION_RUN_COMPLETED`/`AUDIT_PRUNE_CHECKPOINT`/`EVENT_HISTORY_REDACTED` sind prune-exempt (`PRUNE_EXEMPT_ACTIONS`), ebenso `EXPORT`-Einträge, die eine **Berichts-Freigabe** sind (Zeitraum + herausgegebenes Artefakt, #1770; Klienten-/DSGVO-Exporte und die Bildschirm-Ansichten des externen Berichts laufen unter derselben Aktion und bleiben der Frist unterworfen) | [`audit.py`](../src/core/models/audit.py), [`audit_pruning.py`](../src/core/retention/audit_pruning.py) | Bei `= 0` bzw. bei prune-exempten Aktionen kein Löschhorizont — siehe § 6 |
| **E** RLS-Bypass via Superuser-DB-Rolle | DB-User darf **kein** Superuser sein; FORCE-Modus aktiv; CI laedt ``rls_test_role`` (``NOSUPERUSER``, kein Tabellen-Eigentum) und verifiziert Cross-Tenant-0-Rows; unter derselben Rolle laeuft auch der Deferral-Zweig der Art.-17-Historien-Redaktion real ab (ADR-050 M-8): der aufgeschobene Schritt und seine Ops-Zeile sind damit ohne Test-Stub belegt ([test_rls_functional.py](../src/tests/test_rls_functional.py), #2159) | [`ops-runbook.md` § 9](ops-runbook.md) | — |

#### A1.4 — Runtime-/Owner-Rollentrennung (Append-Only-Immutability)

Die Append-Only-Trigger (`auditlog_immutable`, `eventhistory_no_update`/`_no_delete`)
schützen die revisionssichere Historie gegen `UPDATE`/`DELETE`. Zwei Umgehungswege
sind zu betrachten:

1. **`session_replication_role = replica`** — ein Trigger im Default-Modus `ENABLE`
   (`tgenabled='O'`) feuert in diesem Modus nicht. Gegenmaßnahme: die Trigger stehen
   per Migration [`0107`](../src/core/migrations/0107_append_only_triggers_enable_always.py)
   auf **`ENABLE ALWAYS`** (`tgenabled='A'`) und feuern damit auch im `replica`-Modus.
   Zusätzlich hat die Runtime-App-Rolle kein `GRANT SET ON PARAMETER
   session_replication_role` — sie kann `replica` gar nicht setzen.
2. **Owner-DDL `ALTER TABLE … DISABLE TRIGGER`** — der **Table-Owner** darf jeden
   Trigger abschalten; `ENABLE ALWAYS` ändert daran nichts. Gegenmaßnahme: Die
   Web-Runtime-Rolle (`POSTGRES_USER`) ist **nicht** DB-/Objekt-Owner, sondern
   hat nur DML-Rechte; Owner ist die BYPASSRLS-Wartungs-Rolle (`POSTGRES_ADMIN_USER`),
   die migrate/seed/Retention ohnehin fährt ([`01-app-role.sh`](../deploy/postgres-init/01-app-role.sh),
   [ADR-033](adr/033-runtime-owner-role-separation.md)). Eine kompromittierte
   Runtime-Verbindung kann die Trigger daher weder DISABLE-n (kein Owner) noch per
   `replica` umgehen (kein GRANT).

**Die Trennung gilt ausnahmslos — auch für den Art.-17-Sofortpfad** (Refs
#2085). Die
Papierkorb-Aktion „Jetzt endgültig anonymisieren" läuft im Webprozess unter der
App-Rolle und brach deshalb an genau dieser Grenze ab (HTTP 500, Löschverlangen
unerfüllt). Der naheliegende Ausweg — eine eng gefasste
`SECURITY DEFINER`-Funktion mit `GRANT EXECUTE` an die App-Rolle — wurde gebaut
und nach zwei gegen PostgreSQL reproduzierten Missbrauchswegen **verworfen**:

1. Der Funktionsrumpf musste die Einrichtung aus `app.current_facility_id`
   lesen. Diese Custom-GUC kann genau die Rolle setzen, die `EXECUTE` bekommt —
   damit war die Historie **jeder** Einrichtung tilgbar. Im Definer-Kontext gilt
   zudem das `rolbypassrls` des Owners; RLS hätte den Fehlgriff nicht abgefangen.
2. Der Rumpf kannte den Spoliationsschutz aus `anonymize_client_now` nicht und
   tilgte Beweisinhalte trotz **aktivem Legal Hold**.

Ausschlaggebend war nicht die fälschbare GUC an sich (RLS nutzt dieselbe),
sondern dass die App-Rolle die **append-only**-Historie überhaupt hätte
zerstören können — genau das verhindert die Ownership-Trennung sonst
vollständig. Der Sofortpfad tilgt deshalb im Request nur, was gewöhnliches DML
ist, und überlässt die älteren Historien-Versionen dem owner-seitigen Sweep
([ADR-050](adr/050-eventhistory-retention.md), Menge
`_anonymized_client_rows`). **Restrisiko, ausdrücklich benannt:** zwischen
Aktion und nächtlichem Lauf stehen diese Fassungen noch mit ihren Werten in der
Datenbank — höchstens bis zum nächsten Lauf, in der Voreinstellung 03:00 Uhr.
Oberfläche und Handbuch sagen das; wer sofort abschließen muss, fährt
`enforce_retention` von Hand (`ops-runbook.md` § 9.5). Guard gegen einen
stillen Rückfall: `test_rls.py::TestSecurityDefinerInventory` pinnt, dass es im
Repo **keine** `SECURITY DEFINER`-Funktion gibt und das Init-Skript der
App-Rolle keine Ausführungsrechte auf Funktionen erteilt.

Der legitime DSGVO-/Retention-Wartungspfad (`prune_auditlog`, `Client.anonymize()`,
`backfill_audit_chain`, `reencrypt_fields`) umgeht die Trigger als Owner-Rolle
transaktional per `ALTER TABLE … DISABLE TRIGGER` + Re-`ENABLE ALWAYS` in **einem**
`transaction.atomic()` — bei Prozessabbruch vor dem Commit rollt PostgreSQL das
DDL (inkl. DISABLE) mit zurück, der Schutz bleibt aktiv
([`_db_admin.py`](../src/core/services/system/_db_admin.py)).

### TB4 — Django ↔ ClamAV

| Threat | Mitigation (Bestand) | Quelle | Offene Lücke |
|---|---|---|---|
| **S** ClamAV-Spoofing (falscher Sidecar) | Internes Compose-Netz `internal: internal=true`; kein Public-Mapping | [`docker-compose.prod.yml`](../docker-compose.prod.yml) | — |
| **I** Klartext-Abfluss über den Scanner | Der Scanner bekommt jeden Upload im Klartext (INSTREAM vor der Verschlüsselung). Für `freshclam` hängt der ClamAV-Container zusätzlich am Netz `clamav-egress` (`internal: false`, DNS/HTTPS ins Internet); dieses Netz verbindet keinen weiteren App-Service. Container-Härtung: Image per Digest gepinnt, `no-new-privileges`, `cap_drop: ALL` mit fünf zurückgegebenen Capabilities, `pids_limit`/`mem_limit` | [`docker-compose.prod.yml`](../docker-compose.prod.yml) (Services `clamav`, Netz `clamav-egress`) | **Restrisiko:** Ein kompromittierter Scanner (etwa über eine Parser-Lücke in einer präparierten Datei oder ein manipuliertes Signatur-Update) hätte Upload-Klartext und einen Weg ins Internet; es gibt keine Egress-Filterung auf die Update-Spiegel |
| **T** Bypass durch ungescannte Datei | Mehrstufige Pipeline in fester Reihenfolge: Dateityp-Whitelist → Größen- und Kontingentgrenzen → ClamAV → Magic-Bytes-Abgleich → Dekompressions-Guards → Metadaten-Bereinigung (Bilder) → Fernet-Verschlüsselung; **fail-closed** wenn ClamAV nicht erreichbar | [`src/core/services/file_vault/storage.py`](../src/core/services/file_vault/storage.py) (`prepare_encrypted_upload`), Healthcheck `/health/` | — |
| **I** Klartext-Zwischenpuffer beim Upload (> `FILE_UPLOAD_MAX_MEMORY_SIZE`) | Django puffert grosse Uploads vor Scan/Verschluesselung als Tempdatei auf Platte; `FILE_UPLOAD_TEMP_DIR` zeigt in den mitgelieferten Compose-Dateien (prod/staging/demo/dev) explizit auf das bereits vorhandene `/tmp`-tmpfs (RAM, Refs #1374) — der Klartext existiert nur fluechtig im Arbeitsspeicher, nicht auf persistentem Storage. `core.W002`-Deploy-Check warnt, falls die Variable fehlt (#1661) | [`prod.py`](../src/anlaufstelle/settings/prod.py), [`docker-compose.prod.yml`](../docker-compose.prod.yml), [`ops-runbook.md` § 5.7a](ops-runbook.md) | Kurzes Zeitfenster bleibt bestehen (Framework-Standardverhalten, kein Vollspeicher-Buffering als Alternative — DoS-Trade-off); Deployments ohne tmpfs (Bare-Metal/eigene Orchestrierung) tragen das Restrisiko bewusst, falls sie kein eigenes tmpfs/verschluesseltes Volume nachziehen (Fallback dokumentiert in ops-runbook.md § 5.7a) |
| **D** ClamAV-Outage blockt Uploads | Fail-closed gewollt — Risiko: Service-Verfügbarkeit. Healthcheck flag in `/health/`; das Subsystem-Detail des Payloads (`database`/`clamav`/`encryption_key`) liegt hinter dem `X-Health-Token`-Gate — anonyme Caller erhalten nur `status`/HTTP-Code (Recon-Schutz, #1375) | Refs #524, [`health.py`](../src/core/views/health.py) | Ein ClamAV-Ausfall allein meldet `/health/` als `degraded` mit HTTP 200; HTTP 503 (`error`) kommt nur bei DB-Ausfall oder fehlgeschlagenem Encryption-Key-Roundtrip — Monitoring muss den Status-Wert auswerten, nicht nur den HTTP-Code |

### TB5 — Off-Site-Backups

**Schlüsselrotation und Vertrauensgrenze:** `ENCRYPTION_KEYS` ist ein Ring für die gesamte Installation, kein Ring je Einrichtung. `reencrypt_fields` rotiert Datenbankfelder, `reencrypt_files` referenzierte Dateianhänge; eine ungefilterte `--verify`-Prüfung authentifiziert alle referenzierten Versionen mit dem Primärschlüssel allein. Die Rotation verändert ältere Backup-Sätze nicht: Ein kompromittierter Altschlüssel entschlüsselt deren alte innere Chiffrate weiterhin, sofern der Angreifer auch an den entschlüsselten Backup-Inhalt gelangt. Lokale und Off-Site-Aufbewahrungsfristen sowie Schlüssel-Eskrow bleiben organisatorische Voraussetzungen der Entfernung; bei unbegrenzter Off-Site-Aufbewahrung ist kein endlicher Entfernungszeitpunkt nachgewiesen. ImportJob-Zwischendateien und Staging-Reste ohne DB-Zeile sind durch die Attachment-Prüfung nicht abgedeckt. Ablauf: [Ops-Runbook § 9.5](ops-runbook.md#dateirotation-und-altschlüssel-entfernen).

| Threat | Mitigation (Bestand) | Quelle | Offene Lücke |
|---|---|---|---|
| **C** Backup-Diebstahl | AES-256-CBC + PBKDF2 mit `BACKUP_ENCRYPTION_KEY`; DB + Medien gemeinsam (#720) | [`backup.sh`](../scripts/ops/backup.sh) (Off-Site-Sync über `BACKUP_OFFSITE_TARGET`: rclone, S3 oder SCP, #738) | Off-Site ist optional: ohne `BACKUP_OFFSITE_TARGET` liegen alle Backups nur auf dem Prod-Host |
| **I** Backup-Tampering | Encrypt-then-MAC: detached HMAC-SHA256-Sidecar (`.hmac`, Key domain-separiert aus `BACKUP_ENCRYPTION_KEY` abgeleitet); `restore.sh` prüft beide übergebenen Sidecars **vor** Bestätigung, Entschlüsselung und erster destruktiver Aktion sowie erneut unmittelbar vor der jeweiligen Nutzung, `--verify` vor der Entschlüsselung — fehlende Sidecar oder Mismatch bricht ab. Zusätzlich restored `--verify` die DB in eine Temp-DB + prüft Tabellen; der Drill prüft das Medien-Tar per HMAC und Probeentpackung | [`_backup_common.sh`](../scripts/ops/_backup_common.sh), [`restore.sh`](../scripts/ops/restore.sh), [`backup.sh`](../scripts/ops/backup.sh) | Restore-Drill nur quartalsweise dokumentiert (Runbook § 6.6) |
| **A** Schlüsselverlust ⇒ Restore unmöglich | Eskrow-Vorgabe: `BACKUP_ENCRYPTION_KEY`, die vollständige `ENCRYPTION_KEYS`-Historie, `DJANGO_AUDIT_HASH_KEY` und die übrigen Recovery-Secrets getrennt vom Prod-Host verwahren | [`disaster-recovery.md` § 4](disaster-recovery.md#4-schlüssel--und-secret-wiederbeschaffung) | Eskrow ist rein organisatorisch, kein automatisches Key-Escrow im Code |

### TB6 — Offline: Client-Gerät als Datenhalter

**Offline-Schlüssel und Widerrufsprüfung** (#2528). Der aus Passwort und Server-Salt abgeleitete non-extractable `CryptoKey` bleibt in der Browser-IndexedDB gespeichert und übersteht Tab-, Browser- und Geräteneustart. „Non-extractable“ verhindert den Export über WebCrypto; der Schutz des gespeicherten Schlüssels gegen Zugriff auf das Browserprofil hängt an **Geräteverschlüsselung und Bildschirmsperre**, die für Offline-Geräte verpflichtend sind. Beim Ableiten wird eine absolute Frist eingefroren: `session_absolute_hours` der Einrichtung (Vorgabe 12 Stunden). Aktivität und Navigation verlängern sie nicht. Die Anwendung prüft beim Laden, vor Entschlüsselung/Speicherung/Replay, bei Tab-Rückkehr und im 60-Sekunden-Intervall. Am Ablauf entzieht sie den Schlüssel und zeigt „Offline-Schlüssel abgelaufen — bitte online anmelden“; ungesendete Arbeit bleibt verschlüsselt erhalten und wird nach erneutem Online-Login mit unverändertem Passwort und Salt wieder lesbar. Das Leerlauffenster gilt zusätzlich. Fehlende oder ungültige Ablaufdaten sperren den Zugriff; Bundles ohne gültiges `expiresAt` gelten als abgelaufen. Ein beobachteter Rücksprung der Geräteuhr sperrt den Schlüssel; eine manipulierbare Geräteuhr bleibt dennoch ein Restrisiko. Bei ausgeschalteter Anwendung erfolgt kein Schlüsselentzug per Fernlöschung.

Meldet die Widerrufsliste einen HTTP-/Netz-/Verarbeitungsfehler (auch 500/429) oder liefert sie eine ungültige Antwortstruktur, bleiben Queue- und Edit-Replay ausgesetzt: „Synchronisation wartet auf Widerrufsprüfung“. Der Sperrzustand wird in der lokalen IndexedDB gespeichert. Zusätzlich braucht jeder Replay-Lauf eine eigene frische, strukturell gültige, erfolgreich angewandte und persistierte Widerrufsprüfung; eine bereits erteilte Freigabe erlaubt keinen weiteren Lauf und keinen anderen Tab, auch wenn Metadatenschreiben und Ersatzlöschung gleichzeitig scheitern. Fehlende oder unlesbare Metadaten sperren den Replay; auch nach einem Lesefehler ist eine neue erfolgreiche Prüfung nötig. Der nächste Sync-Lauf prüft erneut, ebenso ein direktes Speichern aus der Offline-Akte. Bei fehlgeschlagener Prüfung bleibt ungesendete Arbeit verschlüsselt erhalten. Die bestehenden Idempotenz-Schlüssel bleiben erhalten, und die Server-Gates (422 für widerrufene Personen, 409 für Statuskonflikte) bleiben verbindlich. Geräteverschlüsselung ersetzt diese Server-Prüfungen nicht.

Grundlage: [ADR-022](adr/022-offline-snapshot-keys.md) (Krypto-/TTL-/Wipe-Kern), Snapshot-Build [`offline.py`](../src/core/services/system/offline.py), Client-Layer [`offline-store.js`](../src/static/js/offline-store.js) / [`crypto.js`](../src/static/js/crypto.js). Grundprinzip: **Server-seitige Sichtbarkeits- und Rechte-Entscheidungen (TB1–TB3) bleiben autoritativ** — die Browser-Crypto schützt gegen Gerätediebstahl, nicht gegen Rollen-Eskalation.

| Threat | Mitigation (Bestand) | Quelle | Offene Lücke / Restrisiko |
|---|---|---|---|
| **S** Gerätediebstahl → Offline-Zugriff auf den Bundle-Bestand | Login-abgeleiteter AES-GCM-256-Key (PBKDF2 600 k / SHA-256), **non-extractable** in separater IndexedDB — non-extractable verhindert nur den Export der Schlüsselbytes; innerhalb der Leerlaufzeit entschlüsselt der gespeicherte Key im Browser des Geräts weiter. Nach Ablauf (Session-Dauer, Default 30 Min) beim nächsten Ausführen der App — bei Boot, im 60-s-Intervall und bei Tab-Rückkehr — Entzug des Keys; der Store wird nur ohne ungesendete Arbeit mitgelöscht, sonst bleibt das Chiffrat bis zum Re-Login erhalten. Ein nicht wieder geöffnetes Gerät behält Key und Chiffrat | [`crypto.js`](../src/static/js/crypto.js), [ADR-022](adr/022-offline-snapshot-keys.md) | **F-02** — Salt + Chiffretext liegen bei Diebstahl beide lokal → Offline-Brute-Force gegen PBKDF2 bei schwachem Passwort möglich; **F-01** — kein kürzeres Offline-Lock und **kein** `pagehide`-Wipe (bewusst, Streetwork; #1065/#1324) |
| **T** Manipulation lokaler IDB-Daten (Chiffretext / Index) | Server-Filter (`visible_to`/`user_can_see_field`) laufen **vor** der Serialisierung und sind autoritativ; Schreibzugriffe werden Server-gegen-Server neu gespielt; GCM-Auth-Tag erkennt jede Chiffretext-Manipulation → Auto-Discard | [`offline.py`](../src/core/services/system/offline.py), [`offline-store.js`](../src/static/js/offline-store.js) | Lokale Index-Metadaten sind schreibbar, aber nicht autoritativ — sie steuern nur die Client-Sicht; die maßgebliche Rechte-/Sichtbarkeits-Entscheidung fällt serverseitig |
| **I** Klartext-Index-Metadaten bei Gerätezugriff lesbar | Nur Ordnungsmerkmale unverschlüsselt (`pk`, `clientPk`, `lastSynced`, `etag`, `localStatus`; Sende-Warteschlange `url`, `createdAt`, `retryAfter`; Entwürfe `formKey`, `updatedAt`); alle Werte-/Freitext-Inhalte AES-GCM-verschlüsselt; Datei-Inhalte gehen nie ins Bundle (nur „vorhanden"-Marker); Runtime-Cache des Service Workers enthält nur statische Assets mit Asset-Content-Type, keine HTML-/HTMX-Responses (Refs #2201) | [`offline-db.js`](../src/static/js/offline-db.js) (Dexie-Schema, `putEncrypted`), [`offline-store.js`](../src/static/js/offline-store.js), [`sw.js`](../src/static/js/sw.js), [ADR-022](adr/022-offline-snapshot-keys.md) | **Bewusster YAGNI-Verzicht:** Ordnungsmerkmale (u. a. UUIDs, Sync-Zeitstempel, Warteschlangen-Adressen) unverschlüsselt — als Restrisiko akzeptiert; Eviction-Aspekt siehe **D** |
| **I** Bundle überlebt die Löschung der Person | Server-geführte **Revoke-Liste**: jede Anonymisierung (harter Pfad **und** K-Anon-Bridge) schreibt einen klartextfreien, personen-genauen Widerruf; der Client holt ihn bei **jedem Sync** (erster Schritt, vor jedem Replay) und beim **Start** (höchstens 1×/Minute) und entfernt Bundle-Spiegel, ungesyncte Ereignisse, Queue-Einträge und Entwürfe, **die die Personen-UUID in URL oder Payload tragen** (#1760). Der 404/410-Purge allein reicht nicht — eine anonymisierte Person liefert weiter **200** | [`offline_revocation.py`](../src/core/models/offline_revocation.py), [`offline-store.js`](../src/static/js/offline-store.js), [`security-notes.md`](security-notes.md) | Ein Gerät, das nie wieder online geht, erhält kein Signal — **48-h-Bundle-TTL bleibt die letzte Verteidigungslinie** (akzeptiert, #1493, #1476) — und auch sie greift erst, wenn die App auf dem Gerät mit geladenem Schlüssel wieder startet; ohne Session-Key räumt der Widerruf nur die Bundle-Zeilen ab (Queue-/Entwurfs-Bezug steckt im Chiffretext, nächster Lauf holt nach); **nicht erfasst** sind gequeute `/events/<uuid>/edit/`-POSTs und der `/events/new/`-Autosave-Entwurf — beiden fehlt der Personenbezug im Umschlag (#1771) |
| **R** Abstreitbarkeit offline erzeugter Änderungen | Offline-Writes tragen Konflikt-Token + Idempotenz-Key und werden beim Reconnect gegen den Server gespielt — die **autoritative** AuditLog-Spur entsteht serverseitig beim Replay (TB2/TB3) | [`offline-queue.js`](../src/static/js/offline-queue.js), [ADR-022](adr/022-offline-snapshot-keys.md) | Kein clientseitiges Audit vor dem Replay (n. a. — die Aktion ist bis zum Sync nur lokal); permanente Decrypt-Fehler ungesyncter Zeilen werden als sichtbarer Dead-Letter markiert statt still verworfen (#1385) |
| **D** Browser-Eviction / QuotaExceeded → Datenverlust | `navigator.storage.persist()` einmalig angefragt (#1356); Quota-/Persist-Status live angezeigt (#1412); `QuotaExceededError` wird **sichtbar** gemeldet statt still zu scheitern (#1414); 48-h-TTL deckelt den Bestand (Data-Minimization) | [`offline-store.js`](../src/static/js/offline-store.js) | Ohne `persist()`-Grant darf der Browser die IndexedDB unter Speicherdruck evicten (Safari-ITP zusätzlich zeitbasiert) — ungesyncte Arbeit gilt als Datenverlustrisiko, daher die Sichtbarmachung |
| **E** Rechte-Eskalation über das Offline-Bundle | **Keine** — das Bundle kann nicht mehr enthalten, als der/die Nutzer:in online sähe; Sichtbarkeit wird serverseitig **vor** Bundle-Erzeugung entschieden; Non-Staff erhält keinen Zuweisungs-Roster und kann offline nicht mutieren; der Dokumentationstyp-Katalog wird auch auf **Typ**-Ebene nach Sensitivität gefiltert (`sensitivity__in=allowed_sensitivities_for_user`), sodass Name/Metadaten eines HIGH-/ELEVATED-Typs niedriger berechtigte Rollen offline nicht erreichen (#1525) | [`offline.py`](../src/core/services/system/offline.py), [ADR-022](adr/022-offline-snapshot-keys.md) | — (Server-Grenzen TB1–TB3 bleiben autoritativ) |

**Restrisiken (Offline), konsolidiert:** Die Klartext-Index-Metadaten sind ein bewusster **YAGNI-Verzicht** (Index-Verschlüsselung vertagt). Die **48-h-Bundle-TTL** ist ausdrücklich die *letzte* Verteidigungslinie (#1760) für ein dauerhaft offline gebliebenes Gerät — der reguläre Weg ist die Revoke-Liste, die jede Server→Gerät-Zustellung aber zwangsläufig an Konnektivität bindet. **F-01** (kein `pagehide`-/Kurz-Lock) und **F-02** (Passwortstärke als Single-Point beim Offline-Brute-Force) sind in [ADR-022 § Akzeptierte Restrisiken](adr/022-offline-snapshot-keys.md#akzeptierte-restrisiken) angenommen. Ein **Passwortwechsel rotiert den abgeleiteten Schlüssel** (Salt-Rotation): alte Bundles werden per GCM-Auth-Tag-Mismatch permanent unlesbar und verworfen — eine **Pre-Submit-Warnung** weist vor dem Wechsel auf noch nicht synchronisierte Offline-Einträge hin (#1415). Out-of-App-Grenzen (Cold-Boot-/RAM-Imaging, fehlende OS-Festplattenverschlüsselung, MDM, kompromittiertes Gerät) sind in ADR-022 über die Native-App-Alternative eingeordnet. DSFA-/TOM-Abdeckung des Offline-Pfads: abgedeckt in [ADR-022 § DSFA-/TOM-Einordnung](adr/022-offline-snapshot-keys.md#dsfa-tom-einordnung-des-offline-pfads-1343) (Art.-32/35-Bewertung von Server→Browser-Entschlüsselung der Art-9-Daten, Klartext-Index-Metadaten und `window.offlineStore`-Exposition) und der DSFA-Vorlage ([`dsfa.md`](../src/core/dsgvo_templates/dsfa.md), #1343); Design-Trade-offs der Krypto-Schicht: [`security-notes.md`](security-notes.md).

---

## 6. Bekannte offene Lücken

Die Tabellen oben nennen offene Lücken nur als Stichwort in der Spalte „Offene Lücke“; Abwägungen und Belege stehen in [`security-notes.md`](security-notes.md). Neue Befunde werden als GitHub-Issue mit dem Label `security` geführt; Meldungen von außen laufen über [SECURITY.md](../SECURITY.md).

Ausdrücklich als Restrisiko akzeptiert und dokumentiert:

- **Dependabot ignoriert Django-Minor- und -Major-Sprünge** — Patch-Bumps der gepinnten Linie bleiben automatisch, doch der Ignore darf ihr Security-Support-Ende nicht still überdauern; Kompensation und Ablösung vor Dezember 2027: [`security-notes.md` § Dependabot-Ignore für Django-Updates](security-notes.md#dependabot-ignore-für-django-updates-issue-2264) (#2264).
- **Medien-Backups überleben das ersatzlose Löschen einer Datei** — das nutzer-getriggerte Löschen eines Dateianhangs (#1874) entfernt Zeilen, Marker und `.enc` aus der laufenden Instanz, nicht aber aus den Sicherungskopien (`*_media.tar.gz.enc`). Bis zum Ablauf der Backup-Aufbewahrung ist die Datei über einen Restore wiederherstellbar; Bestätigungsseite und Handbücher sagen das ausdrücklich, statt eine unbedingte Löschzusage zu geben. Abwägung und Betriebsempfehlung: [`security-notes.md` § Medien-Backups überleben das ersatzlose Löschen einer Datei](security-notes.md#medien-backups-überleben-das-ersatzlose-löschen-einer-datei-issue-1874).
- **Audit-Log-Restdaten überleben die Art.-17-Löschung** — das Log ist append-only, die Anonymisierung einer Person redigiert es nicht. Einträge sind PII-minimiert (#1751); die Beschäftigten-`username` bleibt bis zum Pruning stehen, bei `auditlog_retention_months = 0` oder prune-exempten Einträgen (`SECURITY_VIOLATION`; ebenso Berichts-Freigaben, #1770 — dort ohne Klientenbezug, aber mit Empfänger-/Zweck-Freitext und Beschäftigten-`username`) dauerhaft. Details, Abwägung und Betriebsempfehlung: [`security-notes.md` § Audit-Log-Restdaten überleben die Art.-17-Löschung](security-notes.md#audit-log-restdaten-überleben-die-art-17-löschung-issue-1753) (#1753).
- **Disclosure aus Berichts-Artefakten** — die k-Anonymitäts-Zusicherung gilt **je Artefakt und je Zeitraum**. Genau zwei Kanäle bleiben offen (#1770): (a) die **Differenz mehrerer Grundgesamtheiten** — der Beispiel-Sachbericht zählt in „Gesamtkontakte" nur Ereignisse mit Berichtszuordnung, in der Altersverteilung nur Ereignisse mit Personenbezug (K3-A2, offener Produktentscheid); (b) die **Differenz mehrerer Freigaben** über verschachtelte Zeiträume (Monats- plus Jahresbericht) — die Freigabe-Übersicht macht sie sichtbar, verhindert sie aber nicht (K4a). Die Kanäle Schwelle auf Ereignis- statt Personenzahlen, count-absteigende Zeilensortierung, Unterdrückungs-Entscheidung der Vollständigkeitsmeldungen und Randsummen-Residuen sind geschlossen. Maßgeblich ist allein das Kanal-Inventar mit Verdikten, Aufwänden und akzeptierten Grenzen: [`security-notes.md` § Disclosure-Analyse der Berichts-Artefakte](security-notes.md#disclosure-analyse-der-berichts-artefakte-2026-08-03-issue-1742) (#1742) — hier bewusst keine Kopie der Tabelle.
- **`Event.idempotency_payload_hash` überlebt die Klienten-Anonymisierung** — der SHA-256-Fingerabdruck des Anlage-Payloads (persistenter DB-Backstop des Offline-Replay-Schutzes, #1424) wird weder von `anonymize_client()` noch von der K-Anon-Bridge geleert — anders als `data_json`/`search_text` derselben Zeile. Begründung (Zweck, Unlinkability-/Kollisionsbewertung, Retention, Exportausschluss): [`security-notes.md` § `Event.idempotency_payload_hash` überlebt die Klienten-Anonymisierung](security-notes.md#eventidempotency_payload_hash-überlebt-die-klienten-anonymisierung-issue-1935) (#1935).
- **`lastActivity`-Stempel in `anlaufstelle-crypto.meta` nach dem Abmelden** — nach dem Logout liegt in der Krypto-IndexedDB ein Eintrag `{key: "lastActivity", ts: <epoch ms>}`. Er ist **kein** Überbleibsel des Wipes (der löscht ihn zusammen mit dem Schlüssel, #1065), sondern wird von der Anmeldeseite neu angelegt, auf der der Browser danach landet — dieselbe Spur entsteht durch den bloßen Besuch von `/login/` oder `/offline/` ohne jede Anmeldung. PII-frei; ein Stempel-Gate wird bewusst nicht gebaut. Begründung, Beweisführung und Re-Evaluations-Trigger: [`security-notes.md` § `lastActivity`-Stempel in `anlaufstelle-crypto.meta` nach dem Abmelden](security-notes.md#lastactivity-stempel-in-anlaufstelle-cryptometa-nach-dem-abmelden-issue-1946) (#1946).

---

## 7. Pflege

- **Review-Kadenz:** mit jedem Major-/Minor-Release (≥ 1× pro Quartal); ad hoc nach jedem sicherheitsrelevanten Befund
- **Trigger für Update:**
  - neue Vertrauensgrenze (z.B. Worker, externer Webhook)
  - neuer Akteur (z.B. Maschinen-Account, externe API-Konsumenten)
  - neue Asset-Klasse (z.B. zusätzliche personenbezogene Daten)
  - Änderung an Defense-in-Depth-Schichten (z.B. RLS-Coverage, MFA-Pflicht)
- **Pflegeregel:** Das Dokument beschreibt den Ist-Stand. Zahlen, die sich aus dem Code ableiten lassen (RLS-Tabellen, Audit-Aktionen), stehen hier nicht fest, sondern als Verweis auf ihre Quelle. Das Revisionsdatum steht im Kopf; der Änderungsverlauf steht im [CHANGELOG](../CHANGELOG.md).
- **Pflegeverantwortung:** Solo-Maintainer ([SECURITY.md](../SECURITY.md))
