# ADR-007: AuditLog als Append-Only mit DB-Trigger

- **Status:** Accepted
- **Date:** 2026-03-22
- **Deciders:** Tobias Nix

## Context

Das AuditLog ist die einzige nachhaltige Spur über sensitive Operationen (Datenzugriff, Export, Anonymisierung, Berechtigungs­änderungen, Sicherheits­ereignisse). Damit es als Beleg gegenüber Datenschutz­behörden und Aufsichtsorganen tauglich ist, muss es:

- **Vollständig** sein — jeder Schreibpfad protokolliert (siehe ADR-002, Service-Layer als Single-Source-of-Truth).
- **Manipulations­sicher** sein — auch ein Admin mit DB-Zugang darf Einträge nicht still ändern oder löschen.
- **Strukturiert** sein — Detail-Felder als JSONB, damit Auswertungen ohne Reparsing möglich sind.

## Decision

- AuditLog wird ausschließlich aus dem Service-Layer geschrieben (`core/services/audit.py`). Inline-Schreibvorgänge in Views sind verboten und werden im Code-Review zurückgewiesen (Refactoring-Commit `ef4fddc`).
- **Datenbank-Trigger** verhindert `UPDATE` und `DELETE` auf der `core_auditlog`-Tabelle ([`0024_auditlog_immutable_trigger.py`](../../src/core/migrations/0024_auditlog_immutable_trigger.py)). Versuche werfen einen Error, kein Stillschweigen.
- **Ausnahme Retention:** Die Retention-Pruning-Logik (`enforce_retention`, Refs commit `444ed8f`) darf alte AuditLog-Einträge nach Ablauf der Aufbewahrungsfrist löschen — über einen explizit privilegierten Pfad mit eigenem Audit-Eintrag über die Löschung selbst.
- `detail` ist `JSONField` — strukturierte Daten, keine `repr()`-Strings ([`0022_audit_detail_jsonfield.py`](../../src/core/migrations/0022_audit_detail_jsonfield.py)).

## Consequences

- **+** Selbst kompromittierte Anwendungs­zugänge können das AuditLog nicht nachträglich säubern — sie bräuchten DB-Superuser-Rechte und müssten den Trigger entfernen.
- **+** JSONB-Detail erlaubt gezielte SQL-Abfragen für Auditierung („alle Exporte einer bestimmten Klientel").
- **+** Regression-Tests gegen den Trigger verhindern das versehentliche Entfernen.
- **−** Schema-Änderungen am AuditLog brauchen Trigger-Awareness in der Migration.
- **−** Bug-Fixes in alten Einträgen sind nicht mehr „eben in der DB korrigieren" — falsche Einträge werden mit einem Korrektur-Eintrag ergänzt, nicht überschrieben.

## Alternatives considered

- **Application-only Append-Only-Konvention:** Verworfen — keine Garantie gegen direkten DB-Zugriff.
- **Append-Only über separates Log-System (Loki/SIEM):** Ergänzend möglich, ersetzt aber nicht die DB-eigene Spur — der Service-Layer braucht synchrone Bestätigung, dass das Audit geschrieben wurde, bevor die Geschäftstransaktion committet.

## References

- [`src/core/services/audit/`](../../src/core/services/audit/)
- [`src/core/migrations/0024_auditlog_immutable_trigger.py`](../../src/core/migrations/0024_auditlog_immutable_trigger.py)
- [`docs/threat-model.md`](../threat-model.md)

## Update 2026-05-09: NULL-Facility-Semantik (Refs #863)

`AuditLog.facility` ist `nullable`. NULL-Einträge repräsentieren **system-wide events** ohne oder vor Auth-Kontext:

- `LOGIN_FAILED` bei unbekanntem Username (siehe [`core/signals/audit.py`](../../src/core/signals/audit.py))
- Breach-Detection-Events ohne Facility-Bezug
- Künftige Maintenance-Tasks, die DB-Änderungen ohne Trägerbezug protokollieren

Diese Einträge sind:

- via RLS-USING-Policy für reguläre App-User unsichtbar (kein NULL-Match)
- via RLS-WITH-CHECK explizit als gültiger INSERT erlaubt (Migration [0083](../../src/core/migrations/0083_auditlog_rls_with_check.py))
- durch den Append-Only-Trigger (Migration 0024) gegen UPDATE/DELETE geschützt
- nur über Admin-User (`anlaufstelle_admin`, BYPASSRLS) oder Bootstrap-Superuser (`postgres`) auswertbar — Forensik-Pfad

## Update 2026-05-10: Pre-Auth-AuditLog-Sichtbarkeit über /system/ (Refs #867)

Die in der vorigen Iteration als „nur über psql sichtbar" deklarierten NULL-Facility-Audit-Einträge (`LOGIN_FAILED` ohne Facility-Bezug, Breach-Detection-Events, frühe Maintenance-Tasks) waren ein operativer Schmerzpunkt: Eine DSGVO-Auskunftsanfrage zu „Wer hat sich erfolglos mit meinem Username angemeldet?" verlangt einen Forensik-Pfad, der nicht mehr per psql-Login auf den Postgres-Container läuft. Issue #866 verlangt explizit ein UI für diese Auswertung.

**Entscheidung:** Mit dem 5-Rollen-Modell aus [ADR-018](018-rollenmodell-superadmin.md) erhält der `super_admin` einen `/system/`-Bereich mit AuditLog-Browser für **NULL-Facility-Einträge sowie facility-übergreifende Sicht aller Audit-Logs**:

- Die View [`SystemAuditLogListView`](../../src/core/views/system/audit.py) zeigt AuditLog-Einträge inklusive `facility IS NULL`-Treffer. Sie ist gekapselt durch `SuperAdminRequiredMixin` und nutzt RLS-Bypass via `app.is_super_admin`-Session-Variable (siehe [ADR-005 Update 2026-05-10](005-facility-scoping-and-rls.md)).
- **Neue Action `SYSTEM_VIEW`:** Jeder Aufruf einer beliebigen `/system/`-View schreibt einen `SYSTEM_VIEW`-AuditLog-Eintrag mit Detail (`view_name`, Filter-Parameter, Anzahl angezeigter Einträge). Damit ist DSGVO-Rechenschaftspflicht (Art. 5(2) DSGVO) erfüllt: Auch der hochprivilegierte `super_admin` hinterlässt eine Spur, wenn er auf nicht-eigene Facility-Daten schaut.
- **Banner im UI:** Permanenter Hinweis im `/system/`-Layout: „Sie greifen auf Daten facility-übergreifend zu — dieser Zugriff wird im Audit-Log protokolliert." Übersetzbar via `django.po`.

**Was unverändert bleibt:**

- DB-Trigger gegen UPDATE/DELETE (Migration 0024) greift auch für `SYSTEM_VIEW`-Einträge — ein Super-Admin kann seinen eigenen Zugriff nicht verstecken.
- `anlaufstelle_admin` (BYPASSRLS, CLI-only) bleibt für Forensik-Tasks ohne UI bestehen — kein Verlust des direkten DB-Pfads.
- NULL-Facility-Einträge sind weiterhin für reguläre App-User unsichtbar (RLS-USING-Policy unverändert).

**Konsequenzen:**

- **+** Issue #866 gelöst: Forensik im UI statt nur in psql.
- **+** DSGVO-Rechenschaftspflicht über `SYSTEM_VIEW` durchgängig erfüllt.
- **−** `super_admin` ist jetzt eine *aktive* Rolle mit eigener UI-Surface — Wartungsaufwand für `/system/`-Views steigt.
- **−** Ein kompromittierter `super_admin`-Account hat direkten UI-Zugriff auf alle Audit-Daten der Installation. Mitigation: MFA-Pflicht auf `super_admin` durch [ADR-015](015-mfa-totp.md), Lockout-Recovery nur via CLI (`manage.py unlock`).

## Update 2026-08-25: Fachliche Schreibpfade im Audit-Log (Refs #1860)

Sieben zuvor nur teilweise oder ausschließlich in der objektnahen Historie sichtbare
Schreibpfade sind jetzt als eigene `AuditLog.Action`-Werte eingeschrieben:

- `EVENT_UPDATE`, `EPISODE_UPDATE`, `GOAL_UPDATE`
- `CASE_EVENT_ASSIGNED`, `CASE_EVENT_REMOVED`
- `ATTACHMENT_UPLOAD`, `ATTACHMENT_DELETE`

Das Audit-Log und die Ereignishistorie erfüllen dabei zwei verschiedene Verträge:
`EventHistory` bewahrt die Vorher-/Nachher-Stände eines einzelnen Ereignisses für
die Änderungshistorie am Objekt. `AuditLog` beantwortet einrichtungsweit, **dass**
eine sicherheitsrelevante Änderung stattgefunden hat. `EVENT_UPDATE` ersetzt die
Historienzeile deshalb nicht; beide Zeilen werden im selben fachlichen Vorgang
geschrieben. Im Audit-Detail stehen nur Dokumentationstyp und geänderte
Feld-Slugs, niemals Vorher-/Nachher-Werte oder Freitext.

Für Episoden und Wirkungsziele enthält `detail` nur `case_id` und
`changed_fields`; für Fall-Zuordnungen nur die betroffenen UUIDs. Anhang-Aktionen
werden an den zentralen File-Vault-Choke-Points protokolliert: erfolgreicher
Upload/Replace bei `commit_staged_upload`, erfolgreiche Löschung einer
Versionskette bei `soft_delete_attachment_chain`. Der Klartext-Dateiname wird
nicht gespeichert; der Upload trägt nur HMAC-Fingerprint, sichere Dateiendung,
Größe und erkannten MIME-Typ. Beim Dokumentationstyp-Wechsel werden mehrere
gelöschte Ketten zu genau einer `ATTACHMENT_DELETE`-Zeile mit
`bulk=true`, Anzahl und Grund zusammengefasst. Physischer Cleanup nach einem
bereits auditierten Owner-Löschpfad erzeugt keine zweite Spur.

Jeder Audit-Write nimmt den transaktionsgebundenen Advisory Lock der
einrichtungsspezifischen HMAC-Kette. Virenscan und Verschlüsselungs-Staging liegen
daher bei Anlage und Bearbeitung **vor** der schreibenden Transaktion; der Lock
wird nicht über langsame Datei-I/O gehalten. Die sieben Routineaktionen sind
nicht von der regulären Audit-Aufbewahrungsfrist ausgenommen: Für sie gilt
`auditlog_retention_months` (Standard 24 Monate).

## Nachtrag (2026-10-06): Privilegierter Pfad, HMAC-Kette, Tombstones und Lockout-Recovery (Refs #2449)

Die Decision und die Consequences oben beschreiben drei Punkte enger oder älter als der Code. Der Ist-Stand:

- **Pfad:** Der Schreibpfad ist das Paket [`src/core/services/audit/`](../../src/core/services/audit/) (`audit_event`, `log_audit_event`), nicht `core/services/audit.py`. Direkte `AuditLog.objects.create(...)` außerhalb einer Allowlist blockt `TestAuditLogCreationAllowlist` in [`test_architecture_guards_audit.py`](../../src/tests/test_architecture_guards_audit.py).
- **Wer den Trigger umgehen kann:** Dafür braucht es keine Superuser-Rechte. Es genügt die **Owner-Rolle** der Tabelle, also die Wartungs-Rolle (`POSTGRES_ADMIN_USER`, BYPASSRLS, DB-Owner nach [ADR-033](033-runtime-owner-role-separation.md)). Der Table-Owner darf `ALTER TABLE core_auditlog DISABLE TRIGGER auditlog_immutable` ausführen. Genau so arbeitet der legitime Wartungspfad: `bypass_replication_triggers` in [`_db_admin.py`](../../src/core/services/system/_db_admin.py) schaltet die Append-Only-Trigger in **einer** Transaktion ab und wieder an (`ENABLE ALWAYS`). Für `core_auditlog` rufen ihn `prune_auditlog`, die Anonymisierung (`Client.anonymize`) und `backfill_audit_chain` auf; EventHistory-Sweep und Schlüsselrotation öffnen nur `core_eventhistory`. Die Web-Runtime-Rolle ist kein Owner und kann das nicht. Der Satz „bräuchten DB-Superuser-Rechte“ in den Consequences gilt deshalb nur für die Runtime-Verbindung; für einen Angreifer mit den Zugangsdaten der Wartungs-Rolle ist der Trigger keine Hürde.
- **Tamper-Evidenz über die HMAC-Kette** (#1070): Jede Zeile trägt `entry_hash = HMAC(AUDIT_HASH_KEY, prev_hash || canonical(row))`. Die Kette läuft je Einrichtung; Zeilen ohne Einrichtung bilden eine gemeinsame System-Kette ([`chain.py`](../../src/core/services/audit/chain.py)). Ein Postgres-Advisory-Lock je Kette serialisiert die Schreiber. `manage.py verify_audit_chain` (täglich per [`deploy/crontab`](../../deploy/crontab)) erkennt In-Place-Änderungen und Löschungen oder Einschübe in der Mitte. Das Abschneiden am Ketten-Ende und das Leeren einer ganzen Kette erkennt es nur mit dem externen High-Water-Mark ([`hwm.py`](../../src/core/services/audit/hwm.py), `AUDIT_HWM_PATH`; leer = aus), den `--require-anchor` erzwingt. Die Kette verhindert keine Manipulation durch die Owner-Rolle, sie macht sie nachweisbar, solange `AUDIT_HASH_KEY` nicht mit kompromittiert ist.
- **Tombstones beim Pruning:** `prune_auditlog` ([`audit_pruning.py`](../../src/core/retention/audit_pruning.py)) schreibt die Kette nie um. Nach dem Löschen legt es über den normalen verketteten Pfad einen `AUDIT_PRUNE_CHECKPOINT` an, der die Grenzen der gelöschten Zeilen (`boundary_hash`, `boundary_hashes`) festhält. `verify_audit_chain` toleriert eine Lücke nur, wenn sie ein authentifizierter Checkpoint deckt. Der Checkpoint selbst ist von der Frist ausgenommen (`PRUNE_EXEMPT_Q`), ebenso `SECURITY_VIOLATION`, `RETENTION_RUN_COMPLETED`, `EVENT_HISTORY_REDACTED` und Berichts-Exporte mit Zeitraum (`PRUNE_EXEMPT_ACTIONS`, `RELEASE_EXPORT_Q`). „Mit eigenem Audit-Eintrag über die Löschung selbst“ aus der Decision ist damit dieser Checkpoint.
- **Lockout-Recovery** (Update 2026-05-10 oben, „nur via CLI“): Das gilt nicht mehr. Entsperrt wird über `/system/lockouts/unlock/` (`super_admin`, Sudo), `/verwaltung/zugaenge/<pk>/entsperren/` (Einrichtungsverwaltung, Sudo), den Self-Service per Mail-Token oder MFA-Backup-Code (#869), Passwort-Reset und Einladungs-Setup sowie `manage.py unlock`. `super_admin` selbst ist vom Lockout ausgenommen (#1375); für die Rolle ist jeder dieser Wege ein No-op. Den Deckel bildet das Username-Ratelimit (`10/h`). Details: [ADR-008 Nachtrag (2026-10-06)](008-lockout-scope.md#nachtrag-2026-10-06-ist-stand-von-schwelle-achse-audit-und-ausnahmen-refs-2449).
