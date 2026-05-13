# Threat Model — Anlaufstelle (STRIDE-Lite)

**Version:** v0.11.x · **Letzte Revision:** 2026-05-05 · **Quelle:** Issue #691

Dieses Dokument macht das Sicherheitsmodell explizit. Es ergänzt — nicht ersetzt — die zeilengenauen Audits unter [`docs/audits/`](audits/) und die Design-Entscheidungen in [`docs/security-notes.md`](security-notes.md).

> **Format:** STRIDE-Lite (Spoofing · Tampering · Repudiation · Information Disclosure · Denial of Service · Elevation of Privilege) je Vertrauensgrenze. Bewusst knapp; volle Befundliste in den Audit-Reports.

---

## 1. Scope und Annahmen

**In Scope:**

- Django-Applikation `core` (Models, Services, Views, Middleware, Templates)
- Lauf-Stack laut [`docker-compose.prod.yml`](./docker-compose.prod.yml): Caddy 2 → Django/Gunicorn → PostgreSQL 16; ClamAV als Sidecar
- Backup-/Restore-Pfade in [`scripts/`](./scripts/)

**Out of Scope (vertrauen wir):**

- Host-OS, Docker-Daemon, Kernel-Härtung
- Coolify/Deployment-Plattform und deren TLS-Terminierung
- Mail-Provider (SPF/DKIM/Spam-Filter) und Browser-Sandbox
- Physische Sicherheit von Backups jenseits der `BACKUP_ENCRYPTION_KEY`-Verschlüsselung

**Annahmen:**

- Postgres-DB-User ist **kein** Superuser ([`docs/ops-runbook.md` § 9](ops-runbook.md))
- `ENCRYPTION_KEYS` und `BACKUP_ENCRYPTION_KEY` werden über die Plattform-Geheimnisverwaltung bereitgestellt, nicht im Image
- Caddy terminiert TLS und setzt `X-Forwarded-Proto`; Django vertraut diesem Header (`SECURE_PROXY_SSL_HEADER` in [`prod.py`](./src/anlaufstelle/settings/prod.py))
- Admins befolgen das Release-Runbook und die [Release-Checkliste](release-checklist.md)

---

## 2. Assets

| Asset | Schutzziel | Wo persistiert | Klassifikation |
|---|---|---|---|
| **Klientendaten** (Pseudonyme, Events mit `data_json`, Klartext-Notizen) | C, I | Postgres `core_client`, `core_event`, `core_case`, `core_episode`, `core_workitem` | Art.-9 DSGVO (sozial/medizinisch) |
| **AuditLog** | I, A (Repudiation-Schutz gegen nachträgliche Änderung/Löschung) | Postgres `core_auditlog` (UPDATE/DELETE per DB-Trigger blockiert; INSERT erlaubt; Retention-Pruning deaktiviert Trigger transaktional — siehe TB2/TB3-Tabelle) | hoch — gerichtsfest |
| **Encryption-Keys** | C | `ENCRYPTION_KEYS` env, App-Speicher zur Laufzeit | kritisch — kompromittiert ⇒ Klartext |
| **File-Vault-Anhänge** | C, I | `MEDIA_ROOT=/data/media`, Fernet-verschlüsselt ([`encryption.py`](./src/core/services/encryption.py)) | Art.-9 DSGVO |
| **Backups** | C, I | `backups/`, AES-256-CBC verschlüsselt ([`backup.sh`](./scripts/backup.sh)) | kritisch — Offline-Kopie aller Klientendaten |
| **Sessions** | C, I | Postgres `django_session`, HTTPOnly+Secure+SameSite-Cookie | mittel — Session-Hijack ⇒ Account-Übernahme |
| **MFA-Secrets / Backup-Codes** | C | Postgres `otp_*` (django-otp) | hoch — kompromittiert ⇒ MFA-Bypass |
| **Login-Lockout-State** | I, A | nicht persistiert — zur Laufzeit aus AuditLog abgeleitet ([`login_lockout.py`](./src/core/services/login_lockout.py)) | mittel — Bypass-Vektoren: gefälschter `LOGIN_UNLOCK`-INSERT, Race-Window (Threshold+1), Retention-Prune |

C = Confidentiality, I = Integrity, A = Availability

> **Anmerkung zur Ableitung:** Wo eine Asset-Zeile als *abgeleitet* markiert ist, erbt sie nur die Schutzeigenschaften, die der Container im jeweiligen Scope tatsächlich liefert — d.h. der DB-Immutable-Trigger schützt die *Auswertung* nicht vor INSERT-Manipulation oder Pruning-Lücken.

---

## 3. Akteure

| Akteur | Vertrauenslevel | Capabilities |
|---|---|---|
| **Anonymous** | niedrig | `/login/`, `/password-reset/`, `/health/`, statische Assets |
| **Assistent:in** | mittel | Lesen + eingeschränkter Schreibzugriff auf eigene WorkItems; keine HIGH-Sensitivity-Felder ([`mixins.py`](./src/core/views/mixins.py)) |
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
TB1│ ↕ HTTPS, SameSite=Lax/Strict-Cookies │
   ├─ Caddy 2 (TLS-Termination, Edge-Rate-Limit) ───────────┤
TB2│ ↕ HTTP intern (frontend-Netz) │
   ├─ Django / Gunicorn (App-Logik, Middleware-Stack) ──────┤
TB3│ ↕ Postgres-Protokoll (internal-Netz) │
   ├─ PostgreSQL 16 (RLS-FORCE, immutable AuditLog-Trigger) ┤
TB4│ ↕ INSTREAM via TCP (internal-Netz) │
   └─ ClamAV (Datei-Scan, fail-closed) ─────────────────────┘
```

Backups (`backup.sh`) verlassen das interne Netz nur **verschlüsselt** auf den Backup-Host (TB5: Off-Site).

---

## 5. STRIDE pro Vertrauensgrenze

### TB1 — Browser ↔ Caddy/Django

| Threat | Mitigation (Bestand) | Quelle | Offene Lücke |
|---|---|---|---|
| **S** Session-Hijack via XSS | CSP `script-src 'self'` ohne `unsafe-eval`; Architektur-Test verbietet Inline-`<script>`/`x-data`-Objekte; `SESSION_COOKIE_HTTPONLY=True`; `report-uri /csp-report/` loggt Violations strukturiert ([CSPReportView](./src/core/views/csp_report.py)). **Bewusste Ausnahme:** `/admin-mgmt/` erhält `'unsafe-eval'` per [`AdminCSPRelaxMiddleware`](./src/core/middleware/admin_csp_relax.py) (django-unfold-Kompatibilität, [Trade-off in security-notes.md](security-notes.md#csp-unsafe-eval-auf-admin-mgmt-issue-695)) | [`base.py:236-263`](./src/anlaufstelle/settings/base.py#L236-L263), [`test_architecture.py`](./src/tests/test_architecture.py) | — (CSP-Reporting geschlossen) |
| **S** Login-Brute-Force | IP `5/m` + Username `10/h` Rate-Limit, Account-Lockout 10/15 Min, MFA optional/erzwingbar, Captcha-frei aber audit-getrackt | [`auth.py`](./src/core/views/auth.py), [`login_lockout.py`](./src/core/services/login_lockout.py) | Lockout race-anfällig — Tier-2-Audit Maßnahme #13 |
| **T** CSRF / Replay | `CsrfViewMiddleware` + `CSRF_COOKIE_HTTPONLY=True` + `SAMESITE="Strict"` (CSRF) / `"Lax"` (Session, [siehe security-notes](security-notes.md)) | [`prod.py`](./src/anlaufstelle/settings/prod.py) | — |
| **R** Password-Reset-Token-Replay | Django-Default-Tokens (signed, kurze TTL); Token-Verbrauch loggt AuditLog | [`auth.py`](./src/core/views/auth.py) | — |
| **I** Pseudonym-Leak via Suche | `services/search.py` filtert per Sensitivity + RLS | [`search.py`](./src/core/services/search.py) | `data_json__icontains` matcht verschlüsselte Tokens — Master-Audit B.2.2 |
| **D** Login-Flood / Bot-Spam | Rate-Limit auf alle POST-Handler (Architektur-Test 100 % Coverage) | [`test_architecture.py`](./src/tests/test_architecture.py) | GET-Endpunkte ohne Rate-Limit (Bestandsentscheidung) |
| **E** Privilege-Escalation via Form-Tampering | Server-seitige Permission-Checks via Mixin-Hierarchie + `can_user_mutate_*`-Helper | [`mixins.py`](./src/core/views/mixins.py), [`workitem_actions.py`](./src/core/views/workitem_actions.py) | — (WorkItem-Race Teil A geschlossen) |

### TB2/TB3 — Django ↔ PostgreSQL

| Threat | Mitigation (Bestand) | Quelle | Offene Lücke |
|---|---|---|---|
| **S** Connection-Pool-Tenant-Leak | `FacilityScopeMiddleware` setzt `app.current_facility_id` pro Request — auch leer für anonyme Requests | [`facility_scope.py`](./src/core/middleware/facility_scope.py) — Tier-2 | — |
| **T** AuditLog-Manipulation | DB-Trigger `auditlog_immutable` (BEFORE UPDATE/DELETE) + Python-Override; Test [`test_audit_trigger.py`](./src/tests/test_audit_trigger.py) | Migration [`0024`](./src/core/migrations/0024_auditlog_immutable_trigger.py) | Pruning via `enforce_retention` deaktiviert Trigger transaktional — siehe [`prune_auditlog`](./src/core/services/retention.py) |
| **T** Verschlüsselungs-Bypass via `bulk_create`/`update(data_json=..)` | Verschlüsselung in `Event.save()`/`encrypt_field`; Architektur-Test gegen `bulk_create`/`update(data_json=..)` außerhalb Service-Layer | Master-Audit B.2.2 | Architektur-Test fehlt noch — Audit-Maßnahme #11 |
| **R** Insider-Aktion ohne Spur | AuditLog mit ~30 Action-Typen + DB-Immutable; alle State-Transitions sollten loggen | [`audit.py`](./src/core/models/audit.py) | Lücken in `assign_event_to_case`/`remove_event_from_case` — Master-Audit B.2.4 |
| **I** Cross-Facility-Read | RLS FORCE-Modus auf 21 Tabellen + Manager-Layer + Mixin-Layer (Defense-in-Depth) | Migration [`0047`](./src/core/migrations/0047_postgres_rls_setup.py) | Statistik-MV bewusst ohne RLS — siehe [`security-notes.md`](security-notes.md) |
| **I** Sensitive Felder ohne Encryption | `is_encrypted=True` erzwungen für `Sensitivity=HIGH` — Tier-2; `Client.pseudonym` bleibt bis post-v1.0 im Klartext (UX-Trade-off, [Defer-Begründung](security-notes.md#clientpseudonym-bleibt-im-klartext-bis-post-v10-issue-717)) | [`document_type.py`](./src/core/models/document_type.py), [`client.py`](./src/core/models/client.py) | Klartext-Freitexte (`Client.notes`, `Case.description`) — Master-Audit Blocker 3 |
| **D** AuditLog-Tabelle wächst unbegrenzt | Composite-Indexes + 24-Monat-Retention via `enforce_retention` | [`audit.py`](./src/core/models/audit.py), [`retention.py`](./src/core/services/retention.py) | — |
| **E** RLS-Bypass via Superuser-DB-Rolle | DB-User darf **kein** Superuser sein; FORCE-Modus aktiv; CI laedt ``rls_test_role`` (``NOSUPERUSER``) und verifiziert Cross-Tenant-0-Rows ([test_rls_functional.py](./src/tests/test_rls_functional.py)) | [`ops-runbook.md` § 9](ops-runbook.md) | — (Master-Audit Blocker 5 geschlossen) |

### TB4 — Django ↔ ClamAV

| Threat | Mitigation (Bestand) | Quelle | Offene Lücke |
|---|---|---|---|
| **S** ClamAV-Spoofing (falscher Sidecar) | Internes Compose-Netz `internal: internal=true`; kein Public-Mapping | [`docker-compose.prod.yml`](./docker-compose.prod.yml) | — |
| **T** Bypass durch ungescannte Datei | 4-stufige Pipeline: Extension-Whitelist → ClamAV → Magic-Bytes → Encryption; **fail-closed** wenn ClamAV nicht erreichbar | [`encryption.py`](./src/core/services/encryption.py), Healthcheck `/health/` | — |
| **D** ClamAV-Outage blockt Uploads | Fail-closed gewollt — Risiko: Service-Verfügbarkeit. Healthcheck flag in `/health/` | | Offene Frage Healthcheck differenziert (`503` bei ClamAV-Ausfall) — Master-Audit Maßnahme #24 |

### TB5 — Off-Site-Backups

| Threat | Mitigation (Bestand) | Quelle | Offene Lücke |
|---|---|---|---|
| **C** Backup-Diebstahl | AES-256-CBC + PBKDF2 mit `BACKUP_ENCRYPTION_KEY`; DB + Medien gemeinsam (Tier-3 #720) | [`backup.sh`](./scripts/backup.sh) | Off-Site-Hook (rclone/restic/S3) fehlt — Master-Audit Maßnahme #22 |
| **I** Backup-Tampering | `--verify` restored DB in Temp-DB + prüft Tabellen; Medien-tar wird auf Listbarkeit geprüft | [`backup.sh`](./scripts/backup.sh) | Restore-Drill nur quartalsweise dokumentiert (Runbook § 6.6) |
| **A** Schlüsselverlust ⇒ Restore unmöglich | Key-Rollover-Runbook geplant — Master-Audit Maßnahme #26 | — | Runbook fehlt |

---

## 6. Bekannte offene Lücken (Verweis auf Audit-Reports)

Diese Threat-Model-Tabelle nennt Lücken nur als Stichwort. Vollständige Befunde, Belegstellen und Priorisierung in:

- [`docs/audits/anlaufstelle-audit-master.md`](audits/anlaufstelle-audit-master.md) — Konsolidiertes Master-Audit (50 priorisierte Maßnahmen, A.5 Blocker-Liste)
- [`docs/audits/2026-04-26-security-bestand.md`](audits/2026-04-26-security-bestand.md) — Sicherheitsbericht mit Verteidigungslinien-Inventar
- [`docs/audits/2026-04-25-vollanalyse.md`](audits/2026-04-25-vollanalyse.md) — Quer-Audit über Codequalität + Sicherheit
- Aktive Tracking-Issues: 

---

## 7. Pflege

- **Review-Kadenz:** mit jedem Major-/Minor-Release (≥ 1× pro Quartal); ad hoc nach jedem neuen Audit-Report
- **Trigger für Update:**
  - neue Vertrauensgrenze (z.B. Worker, externer Webhook)
  - neuer Akteur (z.B. Maschinen-Account, externe API-Konsumenten)
  - neue Asset-Klasse (z.B. zusätzliche personenbezogene Daten)
  - Änderung an Defense-in-Depth-Schichten (z.B. RLS-Coverage, MFA-Pflicht)
- **Pflegeverantwortung:** Solo-Maintainer ([SECURITY.md](./SECURITY.md))

Letzte Revision: **2026-04-29** (initiale Fassung im Rahmen Tier-3-Sprint).
