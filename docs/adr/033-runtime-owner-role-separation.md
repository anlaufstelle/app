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
