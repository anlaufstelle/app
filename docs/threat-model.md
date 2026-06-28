# Threat Model — Anlaufstelle (STRIDE-Lite)

**Version:** v0.16.x · **Letzte Revision:** 2026-06-25 · **Quelle:** [Issue #691](https://github.com/anlaufstelle/app/issues/691)

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
| **File-Vault-Anhänge** | C, I | `MEDIA_ROOT=/data/media`, Fernet-verschlüsselt ([`src/core/services/file_vault/encryption.py`](../src/core/services/file_vault/encryption.py)) | Art.-9 DSGVO |
| **Backups** | C, I | `backups/`, AES-256 mit Encrypt-then-MAC (HMAC-SHA256-Sidecar) ([`backup.sh`](../scripts/ops/backup.sh), [`_backup_common.sh`](../scripts/ops/_backup_common.sh)) | kritisch — Offline-Kopie aller Klientendaten |
| **Sessions** | C, I | Postgres `django_session`, HTTPOnly+Secure+SameSite-Cookie | mittel — Session-Hijack ⇒ Account-Übernahme |
| **MFA-Secrets / Backup-Codes** | C | Postgres `otp_*` (django-otp) | hoch — kompromittiert ⇒ MFA-Bypass |
| **Login-Lockout-State** | I, A | nicht persistiert — zur Laufzeit aus AuditLog abgeleitet ([`src/core/services/security/login_lockout.py`](../src/core/services/security/login_lockout.py)) | mittel — Integrität AuditLog-abhängig (TB2/TB3); Restrisiken intern getrackt |

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

---

## 5. STRIDE pro Vertrauensgrenze

### TB1 — Browser ↔ Caddy/Django

| Threat | Mitigation (Bestand) | Quelle | Offene Lücke |
|---|---|---|---|
| **S** Session-Hijack via XSS | CSP `script-src 'self'` ohne `unsafe-eval`; Architektur-Test verbietet Inline-`<script>`/`x-data`-Objekte; `SESSION_COOKIE_HTTPONLY=True`; `report-uri /csp-report/` loggt Violations strukturiert ([CSPReportView](../src/core/views/csp_report.py)). **Bewusste Ausnahme:** `/admin-mgmt/` erhält `'unsafe-eval'` per [`AdminCSPRelaxMiddleware`](../src/core/middleware/admin_csp_relax.py) (django-unfold-Kompatibilität, [Trade-off in security-notes.md](security-notes.md#csp-unsafe-eval-auf-admin-mgmt-issue-695)) | [`base.py:236-263`](../src/anlaufstelle/settings/base.py#L236-L263), `src/tests/test_architecture_guards_*.py` | — (CSP-Reporting #684 geschlossen) |
| **S** Login-Brute-Force | IP `5/m` + Username `10/h` Rate-Limit, Account-Lockout 10/15 Min, MFA optional/erzwingbar, Captcha-frei aber audit-getrackt | [`auth.py`](../src/core/views/auth.py), [`src/core/services/security/login_lockout.py`](../src/core/services/security/login_lockout.py) | Restrisiko intern getrackt |
| **T** CSRF / Replay | `CsrfViewMiddleware` + `CSRF_COOKIE_HTTPONLY=True` + `SAMESITE="Strict"` (CSRF) / `"Lax"` (Session, [siehe security-notes](security-notes.md)) | [`prod.py`](../src/anlaufstelle/settings/prod.py) ||
| **R** Password-Reset-Token-Replay | Django-Default-Tokens (signed, kurze TTL); Token-Verbrauch loggt AuditLog | [`auth.py`](../src/core/views/auth.py) ||
| **I** Pseudonym-Leak via Suche | `services/search.py` filtert per Sensitivity + RLS | [`src/core/services/dashboard/search.py`](../src/core/services/dashboard/search.py) | `data_json__icontains` matcht verschlüsselte Tokens |
| **D** Login-Flood / Bot-Spam | Rate-Limit auf alle POST-Handler (Architektur-Test 100 % Coverage) | `src/tests/test_architecture_guards_*.py` | GET-Endpunkte ohne Rate-Limit (Bestandsentscheidung) |
| **E** Privilege-Escalation via Form-Tampering | Server-seitige Permission-Checks via Mixin-Hierarchie + `can_user_mutate_*`-Helper | [`mixins.py`](../src/core/views/mixins.py), [`workitem_actions.py`](../src/core/views/workitem_actions.py) | — (WorkItem-Race #129 Teil A geschlossen) |

### TB2/TB3 — Django ↔ PostgreSQL

| Threat | Mitigation (Bestand) | Quelle | Offene Lücke |
|---|---|---|---|
| **S** Connection-Pool-Tenant-Leak | `FacilityScopeMiddleware` setzt `app.current_facility_id` pro Request — auch leer für anonyme Requests | [`facility_scope.py`](../src/core/middleware/facility_scope.py) — Refs #733 ||
| **T** AuditLog-Manipulation | DB-Trigger `auditlog_immutable` (BEFORE UPDATE/DELETE) + Python-Override; Test [`test_audit_trigger.py`](../src/tests/test_audit_trigger.py) | Migration [`0024`](../src/core/migrations/0024_auditlog_immutable_trigger.py) | Retention-Pruning: Restrisiko intern getrackt |
| **T** Verschlüsselungs-Bypass via `bulk_create`/`update(data_json=...)` | Verschlüsselung in `Event.save()`/`encrypt_field`; Architektur-Test gegen `bulk_create`/`update(data_json=...)` außerhalb Service-Layer | | Guard-Härtung intern getrackt |
| **R** Insider-Aktion ohne Spur | AuditLog mit ~30 Action-Typen + DB-Immutable; alle State-Transitions sollten loggen | [`audit.py`](../src/core/models/audit.py) | einzelne State-Transitions noch ohne AuditLog — intern getrackt |
| **I** Cross-Facility-Read | RLS FORCE-Modus auf 21 Tabellen + Manager-Layer + Mixin-Layer (Defense-in-Depth) | Migration [`0047`](../src/core/migrations/0047_postgres_rls_setup.py) | Statistik-MV bewusst ohne RLS — siehe [`security-notes.md`](security-notes.md) |
| **I** Sensitive Felder ohne Encryption | `is_encrypted=True` erzwungen für `Sensitivity=HIGH` — Refs #733; `Client.pseudonym` bleibt bis post-v1.0 im Klartext (UX-Trade-off, [Defer-Begründung](security-notes.md#clientpseudonym-bleibt-im-klartext-bis-post-v10-issue-717)) | [`document_type.py`](../src/core/models/document_type.py), [`client.py`](../src/core/models/client.py) | Klartext-Freitexte (`Client.notes`, `Case.description`) #716 |
| **D** AuditLog-Tabelle wächst unbegrenzt | Composite-Indexes + 24-Monat-Retention via `enforce_retention` | [`audit.py`](../src/core/models/audit.py), [`retention.py`](../src/core/services/retention.py) ||
| **E** RLS-Bypass via Superuser-DB-Rolle | DB-User darf **kein** Superuser sein; FORCE-Modus aktiv; CI laedt ``rls_test_role`` (``NOSUPERUSER``) und verifiziert Cross-Tenant-0-Rows ([test_rls_functional.py](../src/tests/test_rls_functional.py)) | [`ops-runbook.md` § 9](ops-runbook.md) | — ( #718 geschlossen) |

### TB4 — Django ↔ ClamAV

| Threat | Mitigation (Bestand) | Quelle | Offene Lücke |
|---|---|---|---|
| **S** ClamAV-Spoofing (falscher Sidecar) | Internes Compose-Netz `internal: internal=true`; kein Public-Mapping | [`docker-compose.prod.yml`](../docker-compose.prod.yml) ||
| **T** Bypass durch ungescannte Datei | 4-stufige Pipeline: Extension-Whitelist → ClamAV → Magic-Bytes → Encryption; **fail-closed** wenn ClamAV nicht erreichbar | [`src/core/services/file_vault/encryption.py`](../src/core/services/file_vault/encryption.py), Healthcheck `/health/` ||
| **D** ClamAV-Outage blockt Uploads | Fail-closed gewollt — Risiko: Service-Verfügbarkeit. Healthcheck flag in `/health/` | Refs #524 | Offene Frage Healthcheck differenziert (`503` bei ClamAV-Ausfall) |

### TB5 — Off-Site-Backups

| Threat | Mitigation (Bestand) | Quelle | Offene Lücke |
|---|---|---|---|
| **C** Backup-Diebstahl | AES-256-CBC + PBKDF2 mit `BACKUP_ENCRYPTION_KEY`; DB + Medien gemeinsam ([ #720](https://github.com/anlaufstelle/app/issues/720)) | [`backup.sh`](../scripts/ops/backup.sh) | Off-Site-Hook (rclone/restic/S3) fehlt |
| **I** Backup-Tampering | Encrypt-then-MAC: detached HMAC-SHA256-Sidecar (`.hmac`, Key domain-separiert aus `BACKUP_ENCRYPTION_KEY` abgeleitet); `restore.sh` und `--verify` prüfen den HMAC **vor** der Entschlüsselung — fehlende Sidecar oder Mismatch bricht ab. Zusätzlich restored `--verify` die DB in eine Temp-DB + prüft Tabellen; Medien-tar wird auf Listbarkeit geprüft | [`_backup_common.sh`](../scripts/ops/_backup_common.sh), [`restore.sh`](../scripts/ops/restore.sh), [`backup.sh`](../scripts/ops/backup.sh) | Restore-Drill nur quartalsweise dokumentiert (Runbook § 6.6) |
| **A** Schlüsselverlust ⇒ Restore unmöglich | Key-Rollover-Runbook geplant || Runbook fehlt |

---

## 6. Bekannte offene Lücken (Verweis auf Audit-Reports)

Diese Threat-Model-Tabelle nennt Lücken nur als Stichwort. Vollständige Befunde, Belegstellen und Priorisierung in den internen Code-Audits (dev-only): (internes Code-Audit, dev-only) — konsolidiert, 50 priorisierte Maßnahmen, A.5 Blocker-Liste
- Sicherheitsbericht 2026-04-26 (internes Code-Audit, dev-only) — Verteidigungslinien-Inventar
- Vollanalyse 2026-04-25 (internes Code-Audit, dev-only) — Quer-Audit über Codequalität + Sicherheit
- Aktive Tracking-Issues: #681, #691, #684, #695

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
