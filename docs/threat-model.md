# Threat Model — Anlaufstelle (STRIDE-Lite)

**Version:** v0.16.x · **Letzte Revision:** 2026-07-07 · **Quelle:** [Issue #691](https://github.com/anlaufstelle/app/issues/691), Offline-Grenze aus [ADR-022](adr/022-offline-snapshot-keys.md) (#1411)

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
| **R** Insider-Aktion ohne Spur | AuditLog mit ~30 Action-Typen + DB-Immutable; alle State-Transitions sollten loggen | [`audit.py`](../src/core/models/audit.py) | WorkItem-Status-Übergänge jetzt append-only geloggt (#1467, inkl. offline-replaybar #1419); übrige State-Transitions intern getrackt |
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

### TB6 — Offline: Client-Gerät als Datenhalter

Grundlage: [ADR-022](adr/022-offline-snapshot-keys.md) (Krypto-/TTL-/Wipe-Kern), Snapshot-Build [`offline.py`](../src/core/services/system/offline.py), Client-Layer [`offline-store.js`](../src/static/js/offline-store.js) / [`crypto.js`](../src/static/js/crypto.js). Grundprinzip: **Server-seitige Sichtbarkeits- und Rechte-Entscheidungen (TB1–TB3) bleiben autoritativ** — die Browser-Crypto schützt gegen Gerätediebstahl, nicht gegen Rollen-Eskalation.

| Threat | Mitigation (Bestand) | Quelle | Offene Lücke / Restrisiko |
|---|---|---|---|
| **S** Gerätediebstahl → Offline-Zugriff auf den Bundle-Bestand | Login-abgeleiteter AES-GCM-256-Key (PBKDF2 600 k / SHA-256), **non-extractable** in separater IndexedDB — ohne aktive Session liefert das Gerät nur Chiffretext; Idle-Wipe von Key **und** Store, sobald länger als die Session-Dauer (Default 30 Min) untätig — geprüft bei Boot, im 60-s-Intervall und bei Tab-Rückkehr | [`crypto.js`](../src/static/js/crypto.js), [ADR-022](adr/022-offline-snapshot-keys.md) | **F-02** — Salt + Chiffretext liegen bei Diebstahl beide lokal → Offline-Brute-Force gegen PBKDF2 bei schwachem Passwort möglich; **F-01** — kein kürzeres Offline-Lock und **kein** `pagehide`-Wipe (bewusst, Streetwork; #1065/#1324) |
| **T** Manipulation lokaler IDB-Daten (Chiffretext / Index) | Server-Filter (`visible_to`/`user_can_see_field`) laufen **vor** der Serialisierung und sind autoritativ; Schreibzugriffe werden Server-gegen-Server neu gespielt; GCM-Auth-Tag erkennt jede Chiffretext-Manipulation → Auto-Discard | [`offline.py`](../src/core/services/system/offline.py), [`offline-store.js`](../src/static/js/offline-store.js) | Lokale Index-Metadaten sind schreibbar, aber nicht autoritativ — sie steuern nur die Client-Sicht; die maßgebliche Rechte-/Sichtbarkeits-Entscheidung fällt serverseitig |
| **I** Klartext-Index-Metadaten bei Gerätezugriff lesbar | Nur Metadaten unverschlüsselt (pk, `lastSynced`, `occurredAt`, `etag`, `expiresAt`); alle Werte-/Freitext-Inhalte AES-GCM-verschlüsselt; Datei-Inhalte gehen nie ins Bundle (nur „vorhanden"-Marker) | [`offline-store.js`](../src/static/js/offline-store.js), [ADR-022](adr/022-offline-snapshot-keys.md) | **Bewusster YAGNI-Verzicht:** Index-Metadaten (u. a. Kontaktzeitpunkte, UUIDs) unverschlüsselt — als Restrisiko akzeptiert; Eviction-Aspekt siehe **D** |
| **R** Abstreitbarkeit offline erzeugter Änderungen | Offline-Writes tragen Konflikt-Token + Idempotenz-Key und werden beim Reconnect gegen den Server gespielt — die **autoritative** AuditLog-Spur entsteht serverseitig beim Replay (TB2/TB3) | [`offline-queue.js`](../src/static/js/offline-queue.js), [ADR-022](adr/022-offline-snapshot-keys.md) | Kein clientseitiges Audit vor dem Replay (n. a. — die Aktion ist bis zum Sync nur lokal); permanente Decrypt-Fehler ungesyncter Zeilen werden als sichtbarer Dead-Letter markiert statt still verworfen (#1385) |
| **D** Browser-Eviction / QuotaExceeded → Datenverlust | `navigator.storage.persist()` einmalig angefragt (#1356); Quota-/Persist-Status live angezeigt (#1412); `QuotaExceededError` wird **sichtbar** gemeldet statt still zu scheitern (#1414); 48-h-TTL deckelt den Bestand (Data-Minimization) | [`offline-store.js`](../src/static/js/offline-store.js) | Ohne `persist()`-Grant darf der Browser die IndexedDB unter Speicherdruck evicten (Safari-ITP zusätzlich zeitbasiert) — ungesyncte Arbeit gilt als Datenverlustrisiko, daher die Sichtbarmachung |
| **E** Rechte-Eskalation über das Offline-Bundle | **Keine** — das Bundle kann nicht mehr enthalten, als der/die Nutzer:in online sähe; Sichtbarkeit wird serverseitig **vor** Bundle-Erzeugung entschieden; Non-Staff erhält keinen Zuweisungs-Roster und kann offline nicht mutieren | [`offline.py`](../src/core/services/system/offline.py), [ADR-022](adr/022-offline-snapshot-keys.md) | — (Server-Grenzen TB1–TB3 bleiben autoritativ) |

**Restrisiken (Offline), konsolidiert:** Die Klartext-Index-Metadaten sind ein bewusster **YAGNI-Verzicht** (Index-Verschlüsselung vertagt). **F-01** (kein `pagehide`-/Kurz-Lock) und **F-02** (Passwortstärke als Single-Point beim Offline-Brute-Force) sind in [ADR-022 § Akzeptierte Restrisiken](adr/022-offline-snapshot-keys.md#akzeptierte-restrisiken) angenommen. Ein **Passwortwechsel rotiert den abgeleiteten Schlüssel** (Salt-Rotation): alte Bundles werden per GCM-Auth-Tag-Mismatch permanent unlesbar und verworfen — eine **Pre-Submit-Warnung** weist vor dem Wechsel auf noch nicht synchronisierte Offline-Einträge hin (#1415). Out-of-App-Grenzen (Cold-Boot-/RAM-Imaging, fehlende OS-Festplattenverschlüsselung, MDM, kompromittiertes Gerät) sind in ADR-022 über die Native-App-Alternative eingeordnet. DSFA-/TOM-Abdeckung des Offline-Pfads: offen (#1343); Design-Trade-offs der Krypto-Schicht: [`security-notes.md`](security-notes.md).

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

Ergänzung **2026-07-07**: Offline-Vertrauensgrenze **TB6** (Client-Gerät als Datenhalter) + Asset **IndexedDB (Offline-Bundle)**, destilliert aus [ADR-022](adr/022-offline-snapshot-keys.md) (#1411).
