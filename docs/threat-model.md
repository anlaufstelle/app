# Threat Model — Anlaufstelle (STRIDE-Lite)

**Version:** v0.80.x · **Letzte Revision:** 2026-08-09 · **Quelle:** [Issue #691](https://github.com/anlaufstelle/app/issues/691), Offline-Grenze aus [ADR-022](adr/022-offline-snapshot-keys.md) (#1411)

Dieses Dokument macht das Sicherheitsmodell explizit. Es ergänzt — nicht ersetzt — die zeilengenauen internen Code-Audits (dev-only) und die Design-Entscheidungen in [`docs/security-notes.md`](security-notes.md).

> **Format:** STRIDE-Lite (Spoofing · Tampering · Repudiation · Information Disclosure · Denial of Service · Elevation of Privilege) je Vertrauensgrenze. Bewusst knapp; volle Befundliste in den Audit-Reports.

---

## 1. Scope und Annahmen

**In Scope:**

- Django-Applikation `core` (Models, Services, Views, Middleware, Templates)
- Lauf-Stack laut [`docker-compose.prod.yml`](../docker-compose.prod.yml): Caddy 2 → Django/Gunicorn → PostgreSQL 18; ClamAV als Sidecar
- Backup-/Restore-Pfade in [`scripts/`](../scripts/)

**Out of Scope (vertrauen wir):**

- Host-OS, Docker-Daemon, Kernel-Härtung
- Coolify/Deployment-Plattform und deren TLS-Terminierung
- Mail-Provider (SPF/DKIM/Spam-Filter) und Browser-Sandbox
- Physische Sicherheit von Backups jenseits der `BACKUP_ENCRYPTION_KEY`-Verschlüsselung

**Annahmen:**

- Postgres-DB-User ist **kein** Superuser ([`docs/ops-runbook.md` § 9](ops-runbook.md))
- `ENCRYPTION_KEYS` und `BACKUP_ENCRYPTION_KEY` werden über die Plattform-Geheimnisverwaltung bereitgestellt, nicht im Image
- Caddy terminiert TLS und setzt `X-Forwarded-Proto`; Django vertraut diesem Header (`SECURE_PROXY_SSL_HEADER` in [`prod.py`](../src/anlaufstelle/settings/prod.py))
- Admins befolgen das [Release-Runbook](https://github.com/anlaufstelle/app/issues/502) und die Release-Checkliste

---

## 2. Assets

| Asset | Schutzziel | Wo persistiert | Klassifikation |
|---|---|---|---|
| **Klientendaten** (Pseudonyme, Events mit `data_json`, Klartext-Notizen) | C, I | Postgres `core_client`, `core_event`, `core_case`, `core_episode`, `core_workitem` | Art.-9 DSGVO (sozial/medizinisch) |
| **AuditLog** | I, A (Repudiation-Schutz gegen nachträgliche Änderung/Löschung) | Postgres `core_auditlog` (UPDATE/DELETE per DB-Trigger blockiert; INSERT erlaubt; Retention-Pruning siehe TB2/TB3-Tabelle) | hoch — gerichtsfest |
| **Encryption-Keys** | C | `ENCRYPTION_KEYS` env, App-Speicher zur Laufzeit | kritisch — kompromittiert ⇒ Klartext |
| **File-Vault-Anhänge** — Dateien an **Ereignis, Person oder Fall** (genau ein Owner je Zeile, [ADR-039](adr/039-owner-generische-dateianhaenge.md)) | C, I | Postgres `core_attachment` (eigene `facility_id`, **direkte** RLS-Policy statt Join über `core_event`) + `MEDIA_ROOT=/data/media`, Fernet-verschlüsselt ([`src/core/services/file_vault/encryption.py`](../src/core/services/file_vault/encryption.py)); Originaldateiname verschlüsselt in der Zeile | Art.-9 DSGVO |
| **Backups** | C, I | `backups/`, AES-256 mit Encrypt-then-MAC (HMAC-SHA256-Sidecar) ([`backup.sh`](../scripts/ops/backup.sh), [`_backup_common.sh`](../scripts/ops/_backup_common.sh)) | kritisch — Offline-Kopie aller Klientendaten |
| **Sessions** | C, I | Postgres `django_session`, HTTPOnly+Secure+SameSite-Cookie; Inaktivitäts-Fenster (`session_timeout_minutes`, Default 30 Min) **plus** absolutes Maximum ab Login (`session_absolute_hours`, Default 12 h, #1868) | mittel — Session-Hijack ⇒ Account-Übernahme, aber **zeitlich hart begrenzt**: kein Dauerzugriff durch bloßes Weiterpingen |
| **MFA-Secrets / Backup-Codes** | C | Postgres `otp_*` (django-otp): TOTP-Secret `otp_totp_totpdevice.key` **Fernet-verschlüsselt** at rest ([ADR-031](adr/031-totp-secret-at-rest.md), Refs #1362); Backup-Codes nur SHA-256-gehasht (#790); zusätzlich optionale WebAuthn-Passkeys (Public-Key-Credentials, kein at-rest-Geheimnis) ([ADR-032](adr/032-webauthn-passkeys-2fa.md), #1492) | hoch — kompromittiert ⇒ MFA-Bypass |
| **Login-Lockout-State** | I, A | nicht persistiert — zur Laufzeit aus AuditLog abgeleitet ([`src/core/services/security/login_lockout.py`](../src/core/services/security/login_lockout.py)) | mittel — Integrität AuditLog-abhängig (TB2/TB3); Restrisiken intern getrackt |
| **IndexedDB (Offline-Bundle)** — server-vorgefilterte Klient:innen-Spiegel (AES-GCM-256-verschlüsselte Bundles **plus Klartext-Index-Metadaten**) | C, I | Browser-IndexedDB des Client-Geräts: verschlüsselte Envelopes in `anlaufstelle-offline` ([`offline-store.js`](../src/static/js/offline-store.js)), non-extractable Session-Key in `anlaufstelle-crypto` ([`crypto.js`](../src/static/js/crypto.js)); Datei-Inhalte nie im Bundle | Art.-9-DSGVO-Spiegel (verschlüsselt); Index-Metadaten (pk, `lastSynced`, `occurredAt`, `etag`, `expiresAt`) im **Klartext** — bewusstes Restrisiko (TB6), [ADR-022](adr/022-offline-snapshot-keys.md) |

C = Confidentiality, I = Integrity, A = Availability

> **Anmerkung zur Ableitung:** Wo eine Asset-Zeile als *abgeleitet* markiert ist, erbt sie nur die Schutzeigenschaften, die der Container im jeweiligen Scope tatsächlich liefert — der DB-Immutable-Trigger deckt nur die Operationen ab, die er erzwingt; darüber hinausgehende Restrisiken sind intern getrackt.

---

## 3. Akteure

| Akteur | Vertrauenslevel | Capabilities |
|---|---|---|
| **Anonymous** | niedrig | `/login/`, `/password-reset/`, `/health/`, statische Assets |
| **Assistent:in** | mittel | Lesen + eingeschränkter Schreibzugriff auf eigene WorkItems; keine HIGH-Sensitivity-Felder ([`mixins.py`](../src/core/views/mixins.py)) |
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
   ├─ Caddy 2 (TLS-Termination, Edge-Rate-Limit) ───────────┤
TB2│        ↕ HTTP intern (frontend-Netz)                   │
   ├─ Django / Gunicorn (App-Logik, Middleware-Stack) ──────┤
TB3│        ↕ Postgres-Protokoll (internal-Netz)            │
   ├─ PostgreSQL 18 (RLS-FORCE, immutable AuditLog-Trigger) ┤
TB4│        ↕ INSTREAM via TCP (internal-Netz)              │
   └─ ClamAV (Datei-Scan, fail-closed) ─────────────────────┘
```

Backups (`backup.sh`) verlassen das interne Netz nur **verschlüsselt** auf den Backup-Host (TB5: Off-Site).

**TB6 — Offline: Client-Gerät als Datenhalter** (orthogonal zur Server-Kette oben). Im Offline-Modus ([ADR-022](adr/022-offline-snapshot-keys.md)) landen server-vorgefilterte Snapshot-Bundles in der Browser-IndexedDB des Mitarbeiter:innen-Geräts. Das **Client-Gerät** wird damit zu einer eigenen Vertrauensgrenze — verschlüsselter Datenbestand *at rest* plus Klartext-Index-Metadaten, außerhalb der Reichweite aller Server-Mitigations (TB1–TB5). Bewusst **als TB6 angehängt** (nicht als „TB0" vor TB1), damit die Bestandsnummern TB1–TB5 und ihre Querverweise stabil bleiben; wie TB5 (Off-Site-Backups) ist auch TB6 keine Inline-Station der Request-Kette, sondern eine terminale Daten-*at-rest*-Grenze.

```
   ┌─ Client-Gerät (Browser-Storage: IndexedDB, Cache) ─────┐
TB6│   Snapshot-Bundle AES-GCM-256 (non-extractable Key)    │
   │   + Klartext-Index-Metadaten · Idle-Wipe an Session    │
   └─ ↕ derselbe Browser wie TB1 (Session-gebundener Key) ──┘
```

---

## 5. STRIDE pro Vertrauensgrenze

### TB1 — Browser ↔ Caddy/Django

| Threat | Mitigation (Bestand) | Quelle | Offene Lücke |
|---|---|---|---|
| **S** Session-Hijack via XSS | CSP `script-src 'self'` ohne `unsafe-eval`; Architektur-Test verbietet Inline-`<script>`/`x-data`-Objekte; `SESSION_COOKIE_HTTPONLY=True`; `report-uri /csp-report/` loggt Violations strukturiert ([CSPReportView](../src/core/views/csp_report.py)). **Bewusste Ausnahmen:** (1) `/admin-mgmt/` erhält `'unsafe-eval'` per [`AdminCSPRelaxMiddleware`](../src/core/middleware/admin_csp_relax.py) (django-unfold-Kompatibilität, [Trade-off in security-notes.md](security-notes.md#csp-unsafe-eval-auf-admin-mgmt-issue-695)); (2) auf `demo.anlaufstelle.app` gibt [`DemoUmamiCSPRelaxMiddleware`](../src/core/middleware/demo_umami_csp_relax.py) den Umami-Host in `script-src`/`connect-src` frei — **nur `DEMO_MODE` + Post-Login + HTML**, synthetische Seed-Daten (#1604, [Trade-off in security-notes.md](security-notes.md#eng-gescopte-ausnahme--umami-analytics-auf-der-demo-issue-1604)) | [`base.py:236-263`](../src/anlaufstelle/settings/base.py#L236-L263), `src/tests/test_architecture_guards_*.py`, `src/tests/test_demo_umami_csp_relax.py` | — (CSP-Reporting #684 geschlossen) |
| **S** Session-Hijack via entwendetes/zurückgelassenes Cookie (Gerätediebstahl, geteiltes Gerät, Cookie-Exfiltration) | Zwei unabhängige Zeitgrenzen: **Inaktivitäts-Fenster** (`session_timeout_minutes`, Default 30 Min, [`SessionIdleRefreshMiddleware`](../src/core/middleware/session_idle.py), #1858) **und absolutes Maximum ab Login** (`session_absolute_hours`, Default 12 h, je Einrichtung 1–168 h, #1868). Das Maximum wird beim Login eingefroren und von Aktivität **nicht** verschoben — auch ein dauersyncendes Gerät (Offline-Replay, Sync-API) erreicht es. Ablauf = `logout()` mit Session-Flush und `LOGOUT`-AuditLog. Zusätzlich: `Clear-Site-Data` beim Logout, Salt-Rotation + Session-Flush bei Rechteentzug ([`offline_invalidation.py`](../src/core/signals/offline_invalidation.py)) | [`session_idle.py`](../src/core/middleware/session_idle.py), [`auth.py`](../src/core/views/auth.py), [`test_middleware_session_idle.py`](../src/tests/test_middleware_session_idle.py), [Trade-off in security-notes.md](security-notes.md#absolutes-sitzungs-maximum-ab-login-issue-1868) | Innerhalb der Deadline hat ein gestohlenes Cookie vollen Zugriff — die Grenze verhindert nur den unbegrenzten Dauerzugriff; ein Settings-Wechsel wirkt erst ab dem nächsten Login (Deadline ist eingefroren) |
| **S** Login-Brute-Force | IP `5/m` + Username `10/h` Rate-Limit, Account-Lockout 10/15 Min **je Quell-IP** (#1372/N9), MFA optional/erzwingbar, Captcha-frei aber audit-getrackt; verteilte Bruteforce-/Victim-Lockout-Signatur löst Breach-Alarm aus (`detect_distributed_login_attack`), ebenso anonyme Login-Bursts gegen **unbekannte** Usernames und ein sekundäres 24-h-Langzeitfenster (#1368); der `super_admin`-Break-Glass-Account ist zentral im Lockout-Service ausgenommen (`is_locked`→`False`, `unlock` No-Op), damit er nicht per Fehlversuchs-Flut aussperrbar ist (DoS auf die Recovery-Rolle, #1375) | [`auth.py`](../src/core/views/auth.py), [`src/core/services/security/login_lockout.py`](../src/core/services/security/login_lockout.py), [`breach_detection.py`](../src/core/services/compliance/breach_detection.py) | Username-`10/h`-Achse: weiche Victim-Lockout-Restfläche bewusst Monitor-only ([Trade-off in security-notes.md](security-notes.md#username-ratelimit-bleibt-monitor-only-statt-hartem-fix-issue-1372)) |
| **S** Phishing / Replay des zweiten Faktors | TOTP (`django-otp`, Secret Fernet-at-rest [ADR-031](adr/031-totp-secret-at-rest.md)) als Standard-2FA; optional **Passkey/WebAuthn** (FIDO2, origin-gebunden ⇒ **phishing-resistent**) als zusätzlicher zweiter Faktor — nur **zusätzlich** zu einem TOTP-Gerät, kein passwordless Login, Passkey-Entfernen sudo-pflichtig; 2FA-Login mit Methoden-Chooser | [ADR-032](adr/032-webauthn-passkeys-2fa.md), #1492 | Neue Auth-Angriffsfläche: serverseitige WebAuthn-Registrierung/-Assertion-Verifikation |
| **T** CSRF / Replay | `CsrfViewMiddleware` + `CSRF_COOKIE_HTTPONLY=True` + `SAMESITE="Strict"` (CSRF) / `"Lax"` (Session, [siehe security-notes](security-notes.md)) | [`prod.py`](../src/anlaufstelle/settings/prod.py) ||
| **R** Password-Reset-Token-Replay | Django-Default-Tokens (signed); Reset-Link jetzt explizit kurzlebig (`PASSWORD_RESET_TIMEOUT` = 2 h statt 3-Tage-Default), Einladungen über eigenen `invite_token_generator`/`INVITE_TOKEN_TIMEOUT` entkoppelt (#1375); Token-Verbrauch loggt AuditLog | [`auth.py`](../src/core/views/auth.py) ||
| **I** Pseudonym-Leak via Suche | `services/search.py` filtert per Sensitivity + RLS | [`src/core/services/dashboard/search.py`](../src/core/services/dashboard/search.py) | `data_json__icontains` matcht verschlüsselte Tokens |
| **D** Login-Flood / Bot-Spam | Rate-Limit auf alle POST-Handler (Architektur-Test 100 % Coverage) | `src/tests/test_architecture_guards_*.py` | GET-Endpunkte ohne Rate-Limit (Bestandsentscheidung) |
| **E** Privilege-Escalation via Form-Tampering | Server-seitige Permission-Checks via Mixin-Hierarchie + `can_user_mutate_*`-Helper; `UserAdmin` entfernt Djangos native Permission-Felder (`is_staff`/`is_superuser`/`groups`/`user_permissions`) für **alle** Editoren vollständig aus Formular **und** Fieldset, sodass ein POST-Smuggling folgenlos bleibt (Autorisierung läuft ausschließlich über `role`) (#1371) | [`mixins.py`](../src/core/views/mixins.py), [`workitem_actions.py`](../src/core/views/workitem_actions.py), [`users.py`](../src/core/admin/users.py) | — (WorkItem-Race #129 Teil A geschlossen) |
| **R** Admin-Schreibpfad umgeht Domänen-Audit/Service-Invarianten; Cross-Facility-PII-Read unauditiert (`/admin-mgmt/`) | Fachobjekte (`Client`/`Case`/`Event`/`WorkItem`) im Django-Admin strikt read-only (`ReadOnlyDomainAdminMixin`) — kein Bypass von AuditLog/EventHistory/Vier-Augen; super_admin-/Cross-Facility-Reads werden als `SYSTEM_VIEW` auditiert (`AdminReadAuditMixin`, analog `/system/`); Architektur-Guard erzwingt read-only für neue Fach-Model-Admins | [`mixins.py`](../src/core/admin/mixins.py), [`test_admin_audit.py`](../src/tests/test_admin_audit.py), [`test_architecture_guards_audit.py`](../src/tests/test_architecture_guards_audit.py) | — (Refs #1341; Trade-off in [security-notes.md](security-notes.md#fachobjekte-im-admin-read-only--cross-facility-read-audit-issue-1341)) |

### TB2/TB3 — Django ↔ PostgreSQL

| Threat | Mitigation (Bestand) | Quelle | Offene Lücke |
|---|---|---|---|
| **S** Connection-Pool-Tenant-Leak | `FacilityScopeMiddleware` setzt `app.current_facility_id` pro Request — auch leer für anonyme Requests | [`facility_scope.py`](../src/core/middleware/facility_scope.py) — Refs #733 ||
| **T** AuditLog-Manipulation | DB-Trigger `auditlog_immutable` (BEFORE UPDATE/DELETE), seit #1026 `ENABLE ALWAYS` (feuert auch bei `session_replication_role=replica`) + Python-Override; Test [`test_audit_trigger.py`](../src/tests/test_audit_trigger.py) | Migration [`0024`](../src/core/migrations/0024_auditlog_immutable_trigger.py), [`0107`](../src/core/migrations/0107_append_only_triggers_enable_always.py) ||
| **E** Audit-Bypass via DB-Owner-DDL (`DISABLE TRIGGER`) | Runtime-/Owner-Rollentrennung (A1.4): Web-Runtime-Rolle ist **kein** DB-Owner mehr → kann Append-Only-Trigger nicht per `ALTER TABLE … DISABLE TRIGGER` abschalten; Owner ist die BYPASSRLS-Wartungs-Rolle. Test [`test_rls.py::TestAppendOnlyTriggerOwnership`](../src/tests/test_rls.py) | [`01-app-role.sh`](../deploy/postgres-init/01-app-role.sh), [`_db_admin.py`](../src/core/services/system/_db_admin.py) — #1026 | Prod-Rollout des Ownership-Umzugs für Bestandsinstallationen: [`ops-runbook.md` § 9.6](ops-runbook.md) |
| **T** Verschlüsselungs-Bypass via `bulk_create`/`update(data_json=...)` | Verschlüsselung in `Event.save()`/`encrypt_field`; Architektur-Test gegen `bulk_create`/`update(data_json=...)` außerhalb Service-Layer | | Guard-Härtung intern getrackt |
| **R** Insider-Aktion ohne Spur | AuditLog mit ~30 Action-Typen + DB-Immutable; alle State-Transitions sollten loggen; Privileg-Änderungs-Audits (Rollenwechsel/Deaktivierung/Vier-Augen-Recht) erfassen den handelnden Admin im `actor`-Feld tamper-evident in der HMAC-Kette (#1369); massenhafte Löschung von Personendaten (soft-delete/anonymize/deletion-approved) triggert die Breach-Detection (#1368) | [`audit.py`](../src/core/models/audit.py), [`chain.py`](../src/core/services/audit/chain.py) | WorkItem-Status-Übergänge jetzt append-only geloggt (#1467, inkl. offline-replaybar #1419); übrige State-Transitions intern getrackt |
| **I** Cross-Facility-Read | RLS FORCE-Modus auf 27 Tabellen + Manager-Layer + Mixin-Layer (Defense-in-Depth) | Migration [`0047`](../src/core/migrations/0047_postgres_rls_setup.py), ableitungsbasiert erzwungen in [`test_rls.py`](../src/tests/test_rls.py) | Statistik-MV bewusst ohne RLS — siehe [`security-notes.md`](security-notes.md) |
| **I** Sensitive Felder ohne Encryption | `is_encrypted=True` erzwungen für `Sensitivity=HIGH` — Refs #733; `Client.pseudonym` bleibt bis post-v1.0 im Klartext (UX-Trade-off, [Defer-Begründung](security-notes.md#clientpseudonym-bleibt-im-klartext-bis-post-v10-issue-717)) | [`document_type.py`](../src/core/models/document_type.py), [`client.py`](../src/core/models/client.py) | Klartext-Freitexte (`Client.notes`, `Case.description`) #716 |
| **I** TOTP-Secret-Leak at rest (Dump/Replica/Backup) | TOTP-Secret Fernet-verschlüsselt über das Proxy-Modell `EncryptedTOTPDevice` (vorhandenes MultiFernet, Key-Rotation) — ein DB-Dump liefert nur Chiffretext; konsistent zu den Hash-Backup-Codes (#790) | [ADR-031](adr/031-totp-secret-at-rest.md), [`mfa.py`](../src/core/models/mfa.py), [`totp.py`](../src/core/services/security/totp.py) | — (Refs #1362 geschlossen) |
| **D** AuditLog-Tabelle wächst unbegrenzt | Composite-Indexes + eigene AuditLog-Frist via `enforce_retention` (`Settings.auditlog_retention_months`, **Default 24 Monate, pro Einrichtung konfigurierbar, `0` = Pruning abgeschaltet**); `SECURITY_VIOLATION`/`RETENTION_RUN_COMPLETED`/`AUDIT_PRUNE_CHECKPOINT` sind prune-exempt, ebenso `EXPORT`-Einträge, die eine **Berichts-Freigabe** sind (Zeitraum + herausgegebenes Artefakt, #1770; Klienten-/DSGVO-Exporte und die Bildschirm-Ansichten des externen Berichts laufen unter derselben Aktion und bleiben der Frist unterworfen) | [`audit.py`](../src/core/models/audit.py), [`audit_pruning.py`](../src/core/retention/audit_pruning.py) | Bei `= 0` bzw. bei prune-exempten Aktionen kein Löschhorizont — siehe § 6 |
| **E** RLS-Bypass via Superuser-DB-Rolle | DB-User darf **kein** Superuser sein; FORCE-Modus aktiv; CI laedt ``rls_test_role`` (``NOSUPERUSER``) und verifiziert Cross-Tenant-0-Rows ([test_rls_functional.py](../src/tests/test_rls_functional.py)) | [`ops-runbook.md` § 9](ops-runbook.md) | — ( #718 geschlossen) |

#### A1.4 — Runtime-/Owner-Rollentrennung (Append-Only-Immutability)

Die Append-Only-Trigger (`auditlog_immutable`, `eventhistory_no_update`/`_no_delete`)
schützen die revisionssichere Historie gegen `UPDATE`/`DELETE`. Zwei Umgehungswege
sind zu betrachten:

1. **`session_replication_role = replica`** — ein Trigger im Default-Modus `ENABLE`
 (`tgenabled='O'`) feuert in diesem Modus nicht. Gegenmaßnahme: die Trigger stehen
 seit Migration [`0107`](../src/core/migrations/0107_append_only_triggers_enable_always.py)
 auf **`ENABLE ALWAYS`** (`tgenabled='A'`) und feuern damit auch im `replica`-Modus.
 Zusätzlich hat die Runtime-App-Rolle kein `GRANT SET ON PARAMETER
 session_replication_role` — sie kann `replica` gar nicht setzen.
2. **Owner-DDL `ALTER TABLE … DISABLE TRIGGER`** — der **Table-Owner** darf jeden
 Trigger abschalten; `ENABLE ALWAYS` ändert daran nichts. Gegenmaßnahme: Die
 Web-Runtime-Rolle (`POSTGRES_USER`) ist **nicht** mehr DB-/Objekt-Owner, sondern
 hat nur DML-Rechte; Owner ist die BYPASSRLS-Wartungs-Rolle (`POSTGRES_ADMIN_USER`),
 die migrate/seed/Retention ohnehin fährt ([`01-app-role.sh`](../deploy/postgres-init/01-app-role.sh),
 [ADR-033](adr/033-runtime-owner-role-separation.md)). Eine kompromittierte
 Runtime-Verbindung kann die Trigger daher weder DISABLE-n (kein Owner) noch per
 `replica` umgehen (kein GRANT).

Der legitime DSGVO-/Retention-Wartungspfad (`prune_auditlog`, `Client.anonymize()`,
`backfill_audit_chain`, `reencrypt_fields`) umgeht die Trigger als Owner-Rolle
transaktional per `ALTER TABLE … DISABLE TRIGGER` + Re-`ENABLE ALWAYS` in **einem**
`transaction.atomic()` — bei Prozessabbruch vor dem Commit rollt PostgreSQL das
DDL (inkl. DISABLE) mit zurück, der Schutz bleibt aktiv
([`_db_admin.py`](../src/core/services/system/_db_admin.py)).

### TB4 — Django ↔ ClamAV

| Threat | Mitigation (Bestand) | Quelle | Offene Lücke |
|---|---|---|---|
| **S** ClamAV-Spoofing (falscher Sidecar) | Internes Compose-Netz `internal: internal=true`; kein Public-Mapping | [`docker-compose.prod.yml`](../docker-compose.prod.yml) ||
| **T** Bypass durch ungescannte Datei | 4-stufige Pipeline: Extension-Whitelist → ClamAV → Magic-Bytes → Encryption; **fail-closed** wenn ClamAV nicht erreichbar | [`src/core/services/file_vault/encryption.py`](../src/core/services/file_vault/encryption.py), Healthcheck `/health/` ||
| **I** Klartext-Zwischenpuffer beim Upload (> `FILE_UPLOAD_MAX_MEMORY_SIZE`) | Django puffert grosse Uploads vor Scan/Verschluesselung als Tempdatei auf Platte; `FILE_UPLOAD_TEMP_DIR` zeigt in den mitgelieferten Compose-Dateien (prod/staging/demo/dev) explizit auf das bereits vorhandene `/tmp`-tmpfs (RAM, Refs #1374) — der Klartext existiert nur fluechtig im Arbeitsspeicher, nicht auf persistentem Storage. `core.W002`-Deploy-Check warnt, falls die Variable fehlt (#1661) | [`prod.py`](../src/anlaufstelle/settings/prod.py), [`docker-compose.prod.yml`](../docker-compose.prod.yml), [`ops-runbook.md` § 5.7a](ops-runbook.md) | Kurzes Zeitfenster bleibt bestehen (Framework-Standardverhalten, kein Vollspeicher-Buffering als Alternative — DoS-Trade-off); Deployments ohne tmpfs (Bare-Metal/eigene Orchestrierung) tragen das Restrisiko bewusst, falls sie kein eigenes tmpfs/verschluesseltes Volume nachziehen (Fallback dokumentiert in ops-runbook.md § 5.7a) |
| **D** ClamAV-Outage blockt Uploads | Fail-closed gewollt — Risiko: Service-Verfügbarkeit. Healthcheck flag in `/health/`; das Subsystem-Detail des Payloads (`database`/`clamav`/`encryption_key`) liegt jetzt hinter dem `X-Health-Token`-Gate — anonyme Caller erhalten nur `status`/HTTP-Code (Recon-Schutz, #1375) | Refs #524 | Offene Frage Healthcheck differenziert (`503` bei ClamAV-Ausfall) |

### TB5 — Off-Site-Backups

| Threat | Mitigation (Bestand) | Quelle | Offene Lücke |
|---|---|---|---|
| **C** Backup-Diebstahl | AES-256-CBC + PBKDF2 mit `BACKUP_ENCRYPTION_KEY`; DB + Medien gemeinsam ([ #720](https://github.com/anlaufstelle/app/issues/720)) | [`backup.sh`](../scripts/ops/backup.sh) | Off-Site-Hook (rclone/restic/S3) fehlt |
| **I** Backup-Tampering | Encrypt-then-MAC: detached HMAC-SHA256-Sidecar (`.hmac`, Key domain-separiert aus `BACKUP_ENCRYPTION_KEY` abgeleitet); `restore.sh` und `--verify` prüfen den HMAC **vor** der Entschlüsselung — fehlende Sidecar oder Mismatch bricht ab. Zusätzlich restored `--verify` die DB in eine Temp-DB + prüft Tabellen; Medien-tar wird auf Listbarkeit geprüft | [`_backup_common.sh`](../scripts/ops/_backup_common.sh), [`restore.sh`](../scripts/ops/restore.sh), [`backup.sh`](../scripts/ops/backup.sh) | Restore-Drill nur quartalsweise dokumentiert (Runbook § 6.6) |
| **A** Schlüsselverlust ⇒ Restore unmöglich | Key-Rollover-Runbook geplant || Runbook fehlt |

### TB6 — Offline: Client-Gerät als Datenhalter

Grundlage: [ADR-022](adr/022-offline-snapshot-keys.md) (Krypto-/TTL-/Wipe-Kern), Snapshot-Build [`offline.py`](../src/core/services/system/offline.py), Client-Layer [`offline-store.js`](../src/static/js/offline-store.js) / [`crypto.js`](../src/static/js/crypto.js). Grundprinzip: **Server-seitige Sichtbarkeits- und Rechte-Entscheidungen (TB1–TB3) bleiben autoritativ** — die Browser-Crypto schützt gegen Gerätediebstahl, nicht gegen Rollen-Eskalation.

| Threat | Mitigation (Bestand) | Quelle | Offene Lücke / Restrisiko |
|---|---|---|---|
| **S** Gerätediebstahl → Offline-Zugriff auf den Bundle-Bestand | Login-abgeleiteter AES-GCM-256-Key (PBKDF2 600 k / SHA-256), **non-extractable** in separater IndexedDB — ohne aktive Session liefert das Gerät nur Chiffretext; Idle-Wipe von Key **und** Store, sobald länger als die Session-Dauer (Default 30 Min) untätig — geprüft bei Boot, im 60-s-Intervall und bei Tab-Rückkehr | [`crypto.js`](../src/static/js/crypto.js), [ADR-022](adr/022-offline-snapshot-keys.md) | **F-02** — Salt + Chiffretext liegen bei Diebstahl beide lokal → Offline-Brute-Force gegen PBKDF2 bei schwachem Passwort möglich; **F-01** — kein kürzeres Offline-Lock und **kein** `pagehide`-Wipe (bewusst, Streetwork; #1065/#1324) |
| **T** Manipulation lokaler IDB-Daten (Chiffretext / Index) | Server-Filter (`visible_to`/`user_can_see_field`) laufen **vor** der Serialisierung und sind autoritativ; Schreibzugriffe werden Server-gegen-Server neu gespielt; GCM-Auth-Tag erkennt jede Chiffretext-Manipulation → Auto-Discard | [`offline.py`](../src/core/services/system/offline.py), [`offline-store.js`](../src/static/js/offline-store.js) | Lokale Index-Metadaten sind schreibbar, aber nicht autoritativ — sie steuern nur die Client-Sicht; die maßgebliche Rechte-/Sichtbarkeits-Entscheidung fällt serverseitig |
| **I** Klartext-Index-Metadaten bei Gerätezugriff lesbar | Nur Metadaten unverschlüsselt (pk, `lastSynced`, `occurredAt`, `etag`, `expiresAt`); alle Werte-/Freitext-Inhalte AES-GCM-verschlüsselt; Datei-Inhalte gehen nie ins Bundle (nur „vorhanden"-Marker) | [`offline-store.js`](../src/static/js/offline-store.js), [ADR-022](adr/022-offline-snapshot-keys.md) | **Bewusster YAGNI-Verzicht:** Index-Metadaten (u. a. Kontaktzeitpunkte, UUIDs) unverschlüsselt — als Restrisiko akzeptiert; Eviction-Aspekt siehe **D** |
| **I** Bundle überlebt die Löschung der Person | Server-geführte **Revoke-Liste**: jede Anonymisierung (harter Pfad **und** K-Anon-Bridge) schreibt einen klartextfreien, personen-genauen Widerruf; der Client holt ihn bei **jedem Sync** (erster Schritt, vor jedem Replay) und beim **Start** (höchstens 1×/Minute) und entfernt Bundle-Spiegel, ungesyncte Ereignisse, Queue-Einträge und Entwürfe, **die die Personen-UUID in URL oder Payload tragen** (#1760). Bis dahin griff nur der 404/410-Purge — eine anonymisierte Person liefert aber weiter **200** | [`offline_revocation.py`](../src/core/models/offline_revocation.py), [`offline-store.js`](../src/static/js/offline-store.js), [`security-notes.md`](security-notes.md) | Ein Gerät, das nie wieder online geht, erhält kein Signal — **48-h-Bundle-TTL bleibt die letzte Verteidigungslinie** (akzeptiert, #1493, #1476/R20); ohne Session-Key räumt der Widerruf nur die Bundle-Zeilen ab (Queue-/Entwurfs-Bezug steckt im Chiffretext, nächster Lauf holt nach); **nicht erfasst** sind gequeute `/events/<uuid>/edit/`-POSTs und der `/events/new/`-Autosave-Entwurf — beiden fehlt der Personenbezug im Umschlag (#1771) |
| **R** Abstreitbarkeit offline erzeugter Änderungen | Offline-Writes tragen Konflikt-Token + Idempotenz-Key und werden beim Reconnect gegen den Server gespielt — die **autoritative** AuditLog-Spur entsteht serverseitig beim Replay (TB2/TB3) | [`offline-queue.js`](../src/static/js/offline-queue.js), [ADR-022](adr/022-offline-snapshot-keys.md) | Kein clientseitiges Audit vor dem Replay (n. a. — die Aktion ist bis zum Sync nur lokal); permanente Decrypt-Fehler ungesyncter Zeilen werden als sichtbarer Dead-Letter markiert statt still verworfen (#1385) |
| **D** Browser-Eviction / QuotaExceeded → Datenverlust | `navigator.storage.persist()` einmalig angefragt (#1356); Quota-/Persist-Status live angezeigt (#1412); `QuotaExceededError` wird **sichtbar** gemeldet statt still zu scheitern (#1414); 48-h-TTL deckelt den Bestand (Data-Minimization) | [`offline-store.js`](../src/static/js/offline-store.js) | Ohne `persist()`-Grant darf der Browser die IndexedDB unter Speicherdruck evicten (Safari-ITP zusätzlich zeitbasiert) — ungesyncte Arbeit gilt als Datenverlustrisiko, daher die Sichtbarmachung |
| **E** Rechte-Eskalation über das Offline-Bundle | **Keine** — das Bundle kann nicht mehr enthalten, als der/die Nutzer:in online sähe; Sichtbarkeit wird serverseitig **vor** Bundle-Erzeugung entschieden; Non-Staff erhält keinen Zuweisungs-Roster und kann offline nicht mutieren; der Dokumentationstyp-Katalog wird jetzt auch auf **Typ**-Ebene nach Sensitivität gefiltert (`sensitivity__in=allowed_sensitivities_for_user`), sodass Name/Metadaten eines HIGH-/ELEVATED-Typs niedriger berechtigte Rollen offline nicht mehr erreichen (#1525) | [`offline.py`](../src/core/services/system/offline.py), [ADR-022](adr/022-offline-snapshot-keys.md) | — (Server-Grenzen TB1–TB3 bleiben autoritativ) |

**Restrisiken (Offline), konsolidiert:** Die Klartext-Index-Metadaten sind ein bewusster **YAGNI-Verzicht** (Index-Verschlüsselung vertagt). Die **48-h-Bundle-TTL** ist seit #1760 ausdrücklich die *letzte* Verteidigungslinie für ein dauerhaft offline gebliebenes Gerät — der reguläre Weg ist die Revoke-Liste, die jede Server→Gerät-Zustellung aber zwangsläufig an Konnektivität bindet. **F-01** (kein `pagehide`-/Kurz-Lock) und **F-02** (Passwortstärke als Single-Point beim Offline-Brute-Force) sind in [ADR-022 § Akzeptierte Restrisiken](adr/022-offline-snapshot-keys.md#akzeptierte-restrisiken) angenommen. Ein **Passwortwechsel rotiert den abgeleiteten Schlüssel** (Salt-Rotation): alte Bundles werden per GCM-Auth-Tag-Mismatch permanent unlesbar und verworfen — eine **Pre-Submit-Warnung** weist vor dem Wechsel auf noch nicht synchronisierte Offline-Einträge hin (#1415). Out-of-App-Grenzen (Cold-Boot-/RAM-Imaging, fehlende OS-Festplattenverschlüsselung, MDM, kompromittiertes Gerät) sind in ADR-022 über die Native-App-Alternative eingeordnet. DSFA-/TOM-Abdeckung des Offline-Pfads: abgedeckt in [ADR-022 § DSFA-/TOM-Einordnung](adr/022-offline-snapshot-keys.md#dsfa-tom-einordnung-des-offline-pfads-1343) (Art.-32/35-Bewertung von Server→Browser-Entschlüsselung der Art-9-Daten, Klartext-Index-Metadaten und `window.offlineStore`-Exposition) und der DSFA-Vorlage ([`dsfa.md`](../src/core/dsgvo_templates/dsfa.md), #1343); Design-Trade-offs der Krypto-Schicht: [`security-notes.md`](security-notes.md).

---

## 6. Bekannte offene Lücken (Verweis auf Audit-Reports)

Diese Threat-Model-Tabelle nennt Lücken nur als Stichwort. Vollständige Befunde, Belegstellen und Priorisierung in den internen Code-Audits (dev-only): (internes Code-Audit, dev-only) — konsolidiert, 50 priorisierte Maßnahmen, A.5 Blocker-Liste
- Sicherheitsbericht 2026-04-26 (internes Code-Audit, dev-only) — Verteidigungslinien-Inventar
- Vollanalyse 2026-04-25 (internes Code-Audit, dev-only) — Quer-Audit über Codequalität + Sicherheit
- Aktive Tracking-Issues: #681, #691, #684, #695

Ausdrücklich als Restrisiko akzeptiert und dokumentiert:

- **Audit-Log-Restdaten überleben die Art.-17-Löschung** — das Log ist append-only, die Anonymisierung einer Person redigiert es nicht. Neue Einträge sind seit #1751 PII-minimiert; Alt-Zeilen und die Beschäftigten-`username` bleiben bis zum Pruning stehen, bei `auditlog_retention_months = 0` oder prune-exempten Einträgen (`SECURITY_VIOLATION`; seit #1770 auch Berichts-Freigaben — dort ohne Klientenbezug, aber mit Empfänger-/Zweck-Freitext und Beschäftigten-`username`) dauerhaft. Details, Abwägung und Betriebsempfehlung: [`security-notes.md` § Audit-Log-Restdaten überleben die Art.-17-Löschung](security-notes.md#audit-log-restdaten-überleben-die-art-17-löschung-issue-1753) (#1753).
- **Disclosure aus Berichts-Artefakten** — die k-Anonymitäts-Zusicherung gilt **je Artefakt und je Zeitraum**. Nach #1770 bleiben genau zwei Kanäle offen: (a) die **Differenz mehrerer Grundgesamtheiten** — der Beispiel-Sachbericht zählt in „Gesamtkontakte" nur Ereignisse mit Berichtszuordnung, in der Altersverteilung nur Ereignisse mit Personenbezug (K3-A2, offener Produktentscheid); (b) die **Differenz mehrerer Freigaben** über verschachtelte Zeiträume (Monats- plus Jahresbericht) — die Freigabe-Übersicht macht sie sichtbar, verhindert sie aber nicht (K4a). Die zuvor hier als Fehler geführten Kanäle (Schwelle auf Ereignis- statt Personenzahlen, count-absteigende Zeilensortierung, Unterdrückungs-Entscheidung der Vollständigkeitsmeldungen, Randsummen-Residuen) sind geschlossen. Maßgeblich ist allein das Kanal-Inventar mit Verdikten, Aufwänden und akzeptierten Grenzen: [`security-notes.md` § Disclosure-Analyse der Berichts-Artefakte](security-notes.md#disclosure-analyse-der-berichts-artefakte-2026-08-03-issue-1742) (#1742) — hier bewusst keine Kopie der Tabelle.

---

## 7. Pflege

- **Review-Kadenz:** mit jedem Major-/Minor-Release (≥ 1× pro Quartal); ad hoc nach jedem neuen Audit-Report
- **Trigger für Update:**
 - neue Vertrauensgrenze (z.B. Worker, externer Webhook)
 - neuer Akteur (z.B. Maschinen-Account, externe API-Konsumenten)
 - neue Asset-Klasse (z.B. zusätzliche personenbezogene Daten)
 - Änderung an Defense-in-Depth-Schichten (z.B. RLS-Coverage, MFA-Pflicht)
- **Pflegeverantwortung:** Solo-Maintainer ([SECURITY.md](../SECURITY.md))

Letzte Revision: **2026-04-29** (initiale Fassung im Rahmen, Refs #691, #733).

Ergänzung **2026-07-07**: Offline-Vertrauensgrenze **TB6** (Client-Gerät als Datenhalter) + Asset **IndexedDB (Offline-Bundle)**, destilliert aus [ADR-022](adr/022-offline-snapshot-keys.md) (#1411).

Ergänzung **2026-07-14** (Release-0.22-Härtungswelle unter `[Unreleased]`): Breach-Detection um Massen-Löschung von Personendaten, anonyme Login-Bursts und ein 24-h-Langzeitfenster erweitert (#1368); Privileg-Änderungs-Audits erfassen den handelnden Admin (`actor`) tamper-evident in der HMAC-Kette (#1369); `UserAdmin` entfernt Djangos native Permission-Felder für alle Editoren (#1371); Offline-Personen-Bundle filtert Dokumentationstypen jetzt auch typ-ebene nach Sensitivität (#1525); Passkeys/WebAuthn als phishing-resistenter zweiter Faktor ([ADR-032](adr/032-webauthn-passkeys-2fa.md), #1492). Ergänzend eingearbeitet: `/health/`-Subsystemdetails token-gegated, kurzlebige Passwort-Reset-Links (2 h), zentrale super_admin-Lockout-Ausnahme (#1375). RLS-Tabellenzahl 21 → 22 an den ableitungsbasierten Ist-Stand angeglichen.

Ergänzung **2026-08-01**: **TB4** um den Klartext-Zwischenpuffer beim Datei-Upload (`FILE_UPLOAD_TEMP_DIR`) ergänzt — der Upload-Tempdatei-Puffer oberhalb `FILE_UPLOAD_MAX_MEMORY_SIZE` liegt in den mitgelieferten Compose-Dateien jetzt explizit auf einem `/tmp`-tmpfs (RAM) statt sich auf den impliziten `tempfile.gettempdir()`-Fallback zu verlassen; Deploy-Check `core.W002` warnt bei fehlender Variable (I7 aus #1376, #1661).

Ergänzung **2026-08-04** (Milestone 24, #1738, [ADR-039](adr/039-owner-generische-dateianhaenge.md)): Asset **File-Vault-Anhänge** auf die drei Owner (Ereignis, Person, Fall) erweitert. `EventAttachment` wurde zu `Attachment` mit eigener `facility_id`; die RLS-Policy auf `core_attachment` läuft seit Migration `0131` **direkt** über die facility-Spalte statt als Join über `core_event` (Defense-in-Depth-Gewinn: unter der Join-Policy wäre eine Zeile ohne Ereignis policy-**los** gewesen). Neue Fläche für **I/EoP**: die Facility einer Anhang-Zeile ist kein DB-Constraint, sondern Service-Layer-Konvention (`facility` wird immer aus `owner.facility` abgeleitet, Pin-Test) — die Composite-FK-Härtung `(event_id, facility_id)` bleibt Folgearbeit. Für **ID** kommt das fünfstufige Sichtbarkeits-Gate der Download-Routen hinzu (404 bei Typ-Sensitivity, 403 bei Feld-Sensitivity; Fall-Downloads sind für die Assistenz gesperrt, weil Fälle für sie unsichtbar sind). Die RLS-Tabellenzahl bleibt bei **25** — `core_eventattachment` wechselt nur den Namen und die Policy-Bauart, nicht die Menge.

Ergänzung **2026-08-04**: **TB6** um die Zeile „**I** Bundle überlebt die Löschung der Person" erweitert — eine Anonymisierung entwertet ausgelieferte Offline-Bundles jetzt über eine server-geführte, personen-genaue Revoke-Liste (angewandt bei jedem Sync und beim Boot-Check); die 48-h-Bundle-TTL ist damit ausdrücklich nur noch die *letzte* Verteidigungslinie für ein dauerhaft offline gebliebenes Gerät (#1760, Bezug #1493 und #1476/R20). Die neue Widerrufstabelle `core_offlineclientrevocation` fällt unter RLS (Migration `0127`); die RLS-Tabellenzahl steht damit bei **25** — die zuvor genannten „22" waren bereits seit M0/M1 (`core_fieldgroup`, `core_fieldcondition`) veraltet, die Menge selbst wird ableitungsbasiert in [`test_rls.py`](../src/tests/test_rls.py) erzwungen.
