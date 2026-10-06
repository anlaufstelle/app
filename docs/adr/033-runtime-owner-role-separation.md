# ADR-033: Runtime-DB-Rolle von der DB-Ownership trennen (A1.4)

- **Status:** Accepted
- **Date:** 2026-07-16
- **Deciders:** Tobias Nix
- **Refs:** #1026 (A1.4), Tracker #1016, baut auf [ADR-020](020-three-role-postgres-model.md) auf

## Context

Die revisionssichere Historie (AuditLog, EventHistory) ist auf DB-Ebene durch
Append-Only-Trigger geschützt: `auditlog_immutable` (Migration
[`0024`](../../src/core/migrations/0024_auditlog_immutable_trigger.py)) sowie
`eventhistory_no_update`/`eventhistory_no_delete` (Migration
[`0012`](../../src/core/migrations/0012_eventhistory_append_only_trigger.py))
werfen bei `UPDATE`/`DELETE` eine Exception.

[ADR-020](020-three-role-postgres-model.md) trennte drei Rollen (Bootstrap /
App / Admin), machte aber die **App-Rolle zum DB-Owner** (`ALTER DATABASE …
OWNER TO app_user`), damit die Runtime nach dem Migrate-als-Admin Zugriff auf
die Tabellen hat. Genau daraus entsteht ein Audit-Bypass (A1.4): Der
**Table-Owner** darf jeden Trigger per `ALTER TABLE … DISABLE TRIGGER`
abschalten. Eine kompromittierte Runtime-Verbindung (SQL-Injection,
Deserialisierung, geleaktes App-Passwort) konnte damit die Immutability
aushebeln, Audit-Zeilen ändern/löschen und den Trigger wieder aktivieren.

`ENABLE ALWAYS` allein schließt das **nicht**: es adressiert nur den
`session_replication_role = replica`-Vektor, nicht die Owner-DDL. Umgekehrt
brechen die legitimen Wartungspfade (`prune_auditlog`, `Client.anonymize()`
[DSGVO Art. 17], `backfill_audit_chain`, `reencrypt_fields`), wenn man den
Trigger auf `ENABLE ALWAYS` setzt, ohne den Bypass-Mechanismus umzubauen — sie
umgingen ihn bisher über `session_replication_role = replica`, was einen
`ENABLE ALWAYS`-Trigger nicht mehr stilllegt.

## Decision

1. **Die Runtime-App-Rolle (`POSTGRES_USER`) ist nicht mehr DB-/Objekt-Owner.**
   Sie erhält ausschließlich DML-Rechte (`SELECT/INSERT/UPDATE/DELETE`) plus
   Sequenz-/Schema-`USAGE` und bleibt `NOSUPERUSER NOBYPASSRLS` (RLS-Linchpin
   unverändert). Als Nicht-Owner kann sie keinen Trigger `DISABLE`-n.

2. **Owner ist die bestehende Admin-/Wartungs-Rolle (`POSTGRES_ADMIN_USER`,
   `NOSUPERUSER BYPASSRLS`).** Sie fährt migrate/seed/Retention ohnehin
   ([ADR-020](020-three-role-postgres-model.md), Refs
   #863); frisch
   migrierte Tabellen entstehen damit admin-owned. `ALTER DEFAULT PRIVILEGES FOR
   ROLE admin … TO app` grantet jeder danach erzeugten Tabelle/Sequenz
   automatisch die App-DML-Rechte — kein per-Tabelle-Owner-Fix mehr nötig.
   Der Bootstrap-Superuser (`postgres`) bleibt gemäß ADR-020 aus Runtime-/
   Operator-Tasks heraus.

3. **Der Wartungs-Bypass läuft über Owner-DDL statt `session_replication_role`.**
   `bypass_replication_triggers()` deaktiviert gezielt die drei Append-Only-
   Trigger per `ALTER TABLE … DISABLE TRIGGER` und stellt den Vorzustand per
   `ENABLE ALWAYS` wieder her — alles in **einem** `transaction.atomic()`
   (SIGKILL-sicher: Abbruch vor dem Commit rollt das DISABLE mit zurück). Ein
   vorangestelltes `SET CONSTRAINTS ALL IMMEDIATE` räumt Djangos deferred
   FK-Trigger-Events, sonst bräche `ALTER TABLE` mit `pending trigger events`.

4. **Die Trigger stehen auf `ENABLE ALWAYS`** (Migration
   [`0107`](../../src/core/migrations/0107_append_only_triggers_enable_always.py))
   — Defense-in-Depth gegen den `replica`-Vektor, jetzt gefahrlos möglich, weil
   der legitime Pfad nicht mehr auf `replica` angewiesen ist.

## Consequences

- **+** Eine kompromittierte Web-Runtime-Verbindung kann die Append-Only-Trigger
  weder `DISABLE`-n (kein Owner) noch per `replica` umgehen (kein
  `SET PARAMETER`-GRANT). Der Audit-Bypass A1.4 ist geschlossen.
- **+** Der Wartungs-Bypass ist enger als vorher: nur die drei benannten
  Append-Only-Trigger werden umgangen, nicht (wie bei `replica`) pauschal alle
  Trigger inkl. FK-/Constraint-Prüfungen.
- **+** SIGKILL-sicher ohne den früheren Nachteil von `ALTER TABLE`-Klammern
  (Refs #781): DISABLE
  und Re-ENABLE liegen in derselben Transaktion.
- **−** **Breaking für Bestandsinstallationen.** Auf einem bereits laufenden
  Cluster ist die App-Rolle noch Owner. Der Ownership-Umzug (Owner → Admin,
  DML-Grants an App, Entzug der App-Ownership) ist ein einmaliger,
  Maintainer-geführter Wartungsschritt mit kurzer Downtime (Ablauf:
  [`ops-runbook.md` § 9.6](../ops-runbook.md)). Frische Cluster ziehen das
  Init-Script automatisch auf.
- **−** Der Wartungs-Bypass erfordert Owner-Rechte; die entsprechenden
  Management-Commands (`enforce_retention`, `backfill_audit_chain`,
  `reencrypt_fields`) müssen als Admin-/Owner-Rolle laufen (Env-Override, wie
  bisher der Retention-Cron).
- **−** `SET CONSTRAINTS ALL IMMEDIATE` im Bypass stellt für den Rest der
  Transaktion Deferred-Constraints auf Immediate; für die betroffenen
  Wartungspfade ohne Nebenwirkung, aber als Verhalten dokumentiert.
- **−** **Tabellen-Lock-Regression.** `ALTER TABLE … DISABLE/ENABLE TRIGGER`
  nimmt einen `ShareRowExclusiveLock` auf die betroffene Tabelle und **hält ihn
  bis zum Commit** der umschließenden Transaktion. Der Lock konfligiert mit dem
  `RowExclusiveLock` gewöhnlicher `INSERT`s — solange ein Wartungslauf im Bypass
  offen ist, **blockieren parallele Audit-/EventHistory-`INSERT`s** (also nahezu
  jeder mutierende Request). Das frühere `session_replication_role = replica`
  nahm diesen Tabellen-Lock **nicht**. Bei `Client.anonymize()` besteht zusätzlich
  ein **Lock-Upgrade-Risiko**: der Pfad schreibt erst EventHistory-Zeilen
  (`RowExclusiveLock`) und fordert danach im selben `atomic()` den
  `ShareRowExclusiveLock` an — läuft parallel ein Request, der die Locks in
  umgekehrter Reihenfolge hält, kann PostgreSQL einen Deadlock erkennen und eine
  Seite abbrechen. **Konsequenz für den Betrieb** (Details
  [`ops-runbook.md` § 9.5](../ops-runbook.md)): Die Bypass-Commands
  (`enforce_retention`/Retention-Cron, `backfill_audit_chain`, `reencrypt_fields`,
  `Client.anonymize()`) gehören in **Wartungs-/Nebenzeiten** mit wenig
  Schreiblast; ein `lock_timeout` (per Rollen-/Session-Default) begrenzt die
  Wartezeit eines blockierten `ALTER`.
  *Entscheidung gegen ein hartkodiertes `SET LOCAL lock_timeout` im Bypass:* Ein
  belastbarer Regressionstest bräuchte zwei konkurrierende Sessions mit
  konfligierenden Locks — auf dem RAM-limitierten, xdist-parallelen CI inhärent
  flaky; und ein fester Wert würde ohne Betriebsprofil legitime (u. U. synchron
  im Request laufende) `anonymize`-Läufe in spurious Hard-Fails drehen. Der
  `lock_timeout` wird daher **operativ** (Rollen-Default) empfohlen statt im Code
  erzwungen.

## Alternatives considered

- **`SECURITY DEFINER`-Funktion für den Bypass.** Verworfen: Die Wartungs-Rolle
  ist bereits Owner — eine Definer-Funktion wäre ein *breiterer* Vektor (jeder
  Grantee könnte sie aufrufen). Owner-DDL mit benannten Triggern ist die engere
  Variante.
- **Owner bleibt Bootstrap-Superuser (`postgres`).** Verworfen: widerspricht
  ADR-020 (Bootstrap raus aus Runtime/Operator); der Retention-Cron liefe dann
  als Superuser und umginge auch RLS pauschal.
- **Trigger-Logik prüft eine Session-GUC (`app.bypass = on`).** Verworfen:
  Custom-GUCs kann jede Rolle setzen — die App-Rolle könnte sich selbst
  freischalten. Eine Kombination „GUC + `rolbypassrls`-Check" wäre zwar sicher,
  ändert aber die Trigger-Definitionen invasiver, ohne die Ownership-Trennung
  überflüssig zu machen (Owner könnte weiter `DISABLE`-n).
- **Nur `ENABLE ALWAYS`, App bleibt Owner.** Verworfen: schließt nur den
  `replica`-Vektor, nicht die Owner-DDL — der eigentliche A1.4-Bypass bliebe
  offen.

## References

- [`deploy/postgres-init/01-app-role.sh`](../../deploy/postgres-init/01-app-role.sh) — Rollen-/Ownership-Setup
- [`src/core/services/system/_db_admin.py`](../../src/core/services/system/_db_admin.py) — `bypass_replication_triggers()` (Owner-DDL)
- [`src/core/migrations/0107_append_only_triggers_enable_always.py`](../../src/core/migrations/0107_append_only_triggers_enable_always.py)
- [`docs/threat-model.md` § TB2/TB3 (A1.4)](../threat-model.md)
- [`docs/ops-runbook.md` § 9.6](../ops-runbook.md) — Rollout für Bestandsinstallationen
- [ADR-020](020-three-role-postgres-model.md) — Drei-Rollen-Postgres-Modell (Vorgänger)
- [ADR-005](005-facility-scoping-and-rls.md) — Facility-Scoping + RLS
- Issue #1026 — A1.4

## Nachtrag (2026-10-06): Folgeentscheidungen aus ADR-050 und der Webrequest-Pfad der Anonymisierung (Refs #2499)

Decision 3 und die Consequences beschreiben den Bypass als „immer alle drei Trigger“ und `Client.anonymize()` als Owner-Pfad. Beides hat [ADR-050](050-eventhistory-retention.md) (D5, M-6, M-8) fortgeschrieben. Der Ist-Stand in [`_db_admin.py`](../../src/core/services/system/_db_admin.py):

- **Tabellenauswahl:** `bypass_replication_triggers(tables=None)` öffnet ohne Argument weiter alle drei Append-Only-Trigger. Mit `tables=("core_eventhistory",)` öffnet es nur die Trigger dieser Tabelle und nimmt auf `core_auditlog` keine DDL-Sperre. So arbeiten der EventHistory-Sweep, die Historien-Reparatur, die Schlüsselrotation der Historie und die Datenmigration 0174. Alle drei Trigger öffnen nur noch `prune_auditlog`, `Client.anonymize()` und `backfill_audit_chain`.
- **Sperr-Reihenfolge:** `_APPEND_ONLY_TRIGGERS` steht in der Reihenfolge `core_eventhistory` vor `core_auditlog` und folgt damit der Schreibreihenfolge der Anwendung. Die frühere Reihenfolge (AuditLog zuerst) bildete mit einem gewöhnlichen Request einen ABBA-Zyklus, den PostgreSQL als Deadlock abbrach. Der Guard dazu ist `test_append_only_trigger_reihenfolge_ist_eventhistory_vor_auditlog`. Das in den Consequences beschriebene Lock-Upgrade-Risiko bei `Client.anonymize()` ist damit für diese Kollision geschlossen. Dass der Bypass die Tabellen bis zum Commit sperrt, bleibt; die Empfehlung für Wartungszeiten und `lock_timeout` gilt weiter.
- **Anonymisierung im Webrequest:** Die Art.-17-Sofortaktion läuft im Webprozess unter der App-Rolle, die kein Owner ist. `_redact_event_history` in [`services/client/main.py`](../../src/core/services/client/main.py) prüft deshalb vorher `current_connection_owns("core_eventhistory")` (katalogbasiert über `pg_has_role`). Ohne Owner-Verbindung **überspringt** es die Redaktion der früheren Historien-Fassungen und protokolliert `eventhistory_redaction_deferred`. Pseudonym, Notizen, Zusatzfelder, Fall- und Live-Ereignisinhalte fallen im Request trotzdem, weil sie gewöhnliches DML sind. Die Historie holt der owner-seitige Lauf (`core.retention.eventhistory`) nach; er findet die Person über `Client.anonymized_at`. Bis dahin vergeht bis zu ein Cron-Intervall (ADR-050 M-8). Die App-Rolle bekommt bewusst kein zusätzliches Recht; eine `SECURITY DEFINER`-Funktion wurde geprüft und verworfen.
