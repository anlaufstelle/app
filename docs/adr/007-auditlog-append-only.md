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
- **Tamper-Evidenz über die HMAC-Kette** (#1070): Jede über den Schreibpfad angelegte Zeile trägt `entry_hash = HMAC(AUDIT_HASH_KEY, prev_hash || canonical(row))`. Die Kette läuft je Einrichtung; Zeilen ohne Einrichtung bilden eine gemeinsame System-Kette ([`chain.py`](../../src/core/services/audit/chain.py)). Ein Postgres-Advisory-Lock je Kette serialisiert die Schreiber. `manage.py verify_audit_chain` (täglich per [`deploy/crontab`](../../deploy/crontab)) erkennt In-Place-Änderungen an Zeilen, die ihren Hash behalten, sowie Löschungen oder Einschübe zwischen gehashten Zeilen, soweit die Lücke keine authentifizierte Prune-Grenze ist (Einschränkungen im nächsten Punkt). Das Abschneiden am Ketten-Ende und das Leeren einer ganzen Kette erkennt es nur mit dem externen High-Water-Mark ([`hwm.py`](../../src/core/services/audit/hwm.py), `AUDIT_HWM_PATH`; leer = aus), den `--require-anchor` erzwingt.
- **Was die Prüfung erkennt und was nicht:** `prev_hash` und `entry_hash` sind nullable (`AuditLog` in [`models/audit.py`](../../src/core/models/audit.py)), damit Zeilen aus der Zeit vor der Kette lesbar bleiben und `backfill_audit_chain` sie nachziehen kann. Neue Zeilen müssen beide tragen: Der Trigger `auditlog_require_chain_hash` (Migration [`0184_auditlog_require_chain_hash`](../../src/core/migrations/0184_auditlog_require_chain_hash.py), `ENABLE ALWAYS`) lehnt jedes INSERT ohne `entry_hash` oder mit `prev_hash` NULL ab, auch für die Laufzeit-Rolle; abschalten kann ihn nur die Owner-Rolle. Für neue Einträge zählt nur die jüngste gehashte Zeile als Vorgänger (`_latest_link`, `_system_chain_latest_link_raw`). `verify_chain` in [`chain.py`](../../src/core/services/audit/chain.py) meldet genau vier Fälle: (1) eine gehashte Zeile, deren Inhalt nicht zu ihrem `entry_hash` passt; (2) eine gehashte Zeile, deren `prev_hash` weder zum `entry_hash` der vorigen gehashten Zeile passt (an der ersten gehashten Zeile: nicht leer ist) noch eine Grenze aus einem authentifizierten `AUDIT_PRUNE_CHECKPOINT` ist (Grenzen aus `_checkpoint_boundaries`); (3) eine Zeile ohne `entry_hash` nach der ersten gehashten Zeile derselben Kette, in der Mitte wie am Kettenende (`unhashed row after chain start`); (4) mit High-Water-Mark das Fehlen des zuletzt verankerten Hashes (`HighWaterMarkStore.check` in [`hwm.py`](../../src/core/services/audit/hwm.py)). Zeilen ohne `entry_hash` vor der ersten gehashten Zeile gelten als Altbestand und werden übersprungen. Für die Owner-Rolle bleiben Erkennungslücken: Sie kann den Trigger abschalten. Leert sie den `entry_hash` der ältesten Zeilen einer Kette und ändert deren Inhalt, gelten diese Zeilen als Altbestand, sobald die erste verbleibende gehashte Zeile als `prev_hash` eine authentifizierte Prune-Grenze trägt; Prune-Grenzen werden nicht an eine Position gebunden, sondern überall in der Kette toleriert. Leert sie alle Hashes einer Kette, meldet das nur der High-Water-Mark. Der High-Water-Mark belegt nur, dass sein Anker-Hash noch vorhanden ist, nicht, dass die Zeilen davor unverändert und vollständig sind; Zeilen nach dem Anker deckt er gar nicht. Die Kette verhindert keine Manipulation durch die Owner-Rolle. Nachweisbar macht sie, solange `AUDIT_HASH_KEY` nicht mit kompromittiert ist, nur die Fälle (1) bis (4).
- **Tombstones beim Pruning:** `prune_auditlog` ([`audit_pruning.py`](../../src/core/retention/audit_pruning.py)) schreibt die Kette nie um. Nach dem Löschen legt es über den normalen verketteten Pfad einen `AUDIT_PRUNE_CHECKPOINT` an, der die Grenzen der gelöschten Zeilen (`boundary_hash`, `boundary_hashes`) festhält. `verify_audit_chain` toleriert eine Lücke nur, wenn sie ein authentifizierter Checkpoint deckt. Der Checkpoint selbst ist von der Frist ausgenommen (`PRUNE_EXEMPT_Q`), ebenso `SECURITY_VIOLATION`, `RETENTION_RUN_COMPLETED`, `EVENT_HISTORY_REDACTED` und Berichts-Exporte mit Zeitraum (`PRUNE_EXEMPT_ACTIONS`, `RELEASE_EXPORT_Q`). „Mit eigenem Audit-Eintrag über die Löschung selbst“ aus der Decision ist damit dieser Checkpoint.
- **Lockout-Recovery** (Update 2026-05-10 oben, „nur via CLI“): Das gilt nicht mehr. Entsperrt wird über `/system/lockouts/unlock/` (`super_admin`, Sudo), `/verwaltung/zugaenge/<pk>/entsperren/` (Einrichtungsverwaltung, Sudo), den Self-Service per Mail-Token oder MFA-Backup-Code (#869), Passwort-Reset und Einladungs-Setup sowie `manage.py unlock`. `super_admin` selbst ist vom Lockout ausgenommen (#1375); für die Rolle ist jeder dieser Wege ein No-op. Den Deckel bildet das Username-Ratelimit (`10/h`). Details: [ADR-008 Nachtrag (2026-10-06)](008-lockout-scope.md#nachtrag-2026-10-06-ist-stand-von-schwelle-achse-audit-und-ausnahmen-refs-2449).


## Nachtrag 2026-10-09: Audit-Vollständigkeit (#2527)

Bekannte, einrichtungslose `super_admin`-Konten erhalten ab zehn Fehlanmeldungen in 15 Minuten einen `SECURITY_VIOLATION`-Alarm mit `reason=super_admin_bruteforce`, sofort im Fehlanmeldepfad und als Heuristik im vollständigen `detect_breaches`-Lauf (planmäßig stündlich). Derselbe Alarm wird je Konto 24 Stunden dedupliziert. Es entsteht keine Kontosperre. Am normalen Login (`/login/`) erhalten weitere falsche Passwörter ab der Schwelle HTTP 429 mit einer progressiven Retry-Empfehlung von 1, 2, 4 und höchstens 8 Sekunden (`Retry-After`); der Server schläft nicht, und diese Empfehlung verhindert keinen Folgeversuch mit korrektem Passwort. Die bestehenden IP-/Benutzernamen-Ratelimits bleiben wirksam und können weiterhin auch korrekte Anmeldungen abweisen.

Erfolgreiche TOTP- und Passkey-Verifikation erzeugt `MFA_VERIFIED` mit der festen Methode `totp` oder `passkey`; Fehlgeschlagene Faktorprüfungen behalten `MFA_FAILED` bzw. `WEBAUTHN_FAILED`. Der Backup-Code-Verbrauch behält genau seine bestehende `BACKUP_CODES_USED`-Zeile. Für angemeldete Konten erzeugt eine wegen ungültigem TOTP, unverifizierter Sitzung oder fehlendem TOTP-Gerät abgewiesene POST-Anfrage zum Erneuern der Backup-Codes `MFA_BACKUP_REGENERATE_DENIED` mit dem festen Grund `invalid_totp`, `unverified_session` oder `missing_totp`. Codes, TOTP-Secrets und Passkey-Assertions stehen nicht in diesen Audit-Details.

HTTP-429-Abweisungen schreiben `RATE_LIMITED` mit maskierter Pfadklasse und festem Grund (`request_rate` bzw. `super_admin_backoff`) sowie Nutzer und Einrichtung, soweit bereits bekannt. Unaufgelöste Pfade werden nur als `unresolved` erfasst; Body, Query und konkrete URL-Tokens bleiben aus den Details. Der Cache begrenzt die Spur auf eine Zeile je Pfadklasse und bekanntem Konto/Einrichtung in 60 Sekunden (anonym gemeinsam über alle Quell-IPs). Scheitert das Audit-Schreiben, bleibt die Antwort 429 und eine ebenso gedrosselte, inhaltsfreie Sicherheitszeile nennt Pfadklasse, Grund und bekannte Kennungen. Passkey-Fehler und abgewiesene Backup-Erneuerungen zählen zusätzlich als kritische Audit-Ereignisse; erfolgreiche Verifikation und gewöhnliche Drosselung lösen allein keine Compliance-Warnung aus.

Alle registrierten Admin-Modelle auditieren systemadministrative Ausgabewege; Fehler beim Leseaudit sperren Inhalte mit HTTP 403. Konfigurationsmutationen erhalten eine Audit-Spur mit Actor, Ereignis- und Historien-Admin bleiben auf Metadaten beschränkt. Das Anker-Volume ist für web nur lesend, für cron/maintenance beschreibbar; ein konfigurierter fehlender, leerer oder unlesbarer Anker bricht das Backup vor Veröffentlichung ab. Die Host-/Owner-Vertrauensgrenze bleibt bestehen.

**English summary.** Known `super_admin` accounts without a facility raise a `SECURITY_VIOLATION` alarm with `reason=super_admin_bruteforce` after ten failed logins in fifteen minutes, immediately in the failed-login path and as a heuristic in a full `detect_breaches` scan (scheduled hourly). The same alarm is deduplicated per account for 24 hours. No account lock is introduced. At the regular `/login/` endpoint, failed passwords at or above the threshold receive HTTP 429 with progressive retry advice of 1, 2, 4 and at most 8 seconds (`Retry-After`); the server does not sleep and this advice does not reject a subsequent correct password. The existing IP/username rate limits remain effective and can still reject valid logins.

Successful TOTP and passkey verification records `MFA_VERIFIED` with the fixed method `totp` or `passkey`; failed factor checks retain `MFA_FAILED` or `WEBAUTHN_FAILED`. Consuming a backup code retains exactly its existing `BACKUP_CODES_USED` row. For signed-in accounts, a POST request to regenerate backup codes rejected for invalid TOTP, an unverified session or a missing TOTP device records `MFA_BACKUP_REGENERATE_DENIED` with the fixed reason `invalid_totp`, `unverified_session` or `missing_totp`. These audit details contain no codes, TOTP secrets or passkey assertions.

HTTP 429 rejections record `RATE_LIMITED` with a masked route class, a fixed reason (`request_rate` or `super_admin_backoff`), and the user/facility where already known. Unresolved paths are recorded only as `unresolved`; bodies, queries and actual URL tokens stay out of the details. The cache caps the trail at one row per route class and known account/facility in sixty seconds (anonymous requests share a bucket across all source IPs). If the audit write fails, the response stays 429 and an equally capped security log line names the route class, reason and known identifiers without request contents. Passkey failures and denied backup regeneration also count as critical audit events; successful verification and ordinary throttling alone do not trigger a compliance warning.

All registered admin models audit system-administrator output paths; read-audit failures deny content with HTTP 403. Configuration mutations carry an attributed audit record, while event/history admin pages show only metadata. The anchor volume is read-only for web and writable for cron/maintenance; a configured missing, empty or unreadable anchor aborts backup before publication. The host/owner trust boundary remains.
