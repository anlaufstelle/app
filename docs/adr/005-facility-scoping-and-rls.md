# ADR-005: Facility-Scoping via Middleware + Row Level Security

- **Status:** Accepted
- **Date:** 2026-04-17
- **Deciders:** Tobias Nix

## Context

Anlaufstelle ist mandantenfähig: eine Installation hostet mehrere Einrichtungen (`Facility`). Daten einer Einrichtung dürfen unter keinen Umständen in einer anderen sichtbar werden — auch nicht bei Programmierfehlern (vergessenes `.filter(facility=…)`), bei manuell konstruierten Querysets im Admin oder bei künftigen Refactorings.

Eine reine Anwendungsschicht-Lösung („alle Querysets gehen über `FacilityScopedManager`") ist korrekt, aber einzeln-fehleranfällig. Ein einziges `Model.objects.all()` an der falschen Stelle bricht die Mandantengrenze.

## Decision

Zwei Schichten, beide aktiv:

1. **Anwendungsschicht:** Jedes facility-gescopte Model erbt einen `FacilityScopedManager`, der per Default nach `request.current_facility` filtert. Das Facility wird in [`src/core/middleware/facility_scope.py`](././src/core/middleware/facility_scope.py) je Request gesetzt.
2. **Datenbankschicht:** PostgreSQL Row Level Security (RLS) ist auf allen mandantenbezogenen Tabellen aktiv ([`0047_postgres_rls_setup.py`](././src/core/migrations/0047_postgres_rls_setup.py)). Die Middleware setzt eine Session-Variable, gegen die die RLS-Policy filtert. Der Datenbank-Rolle der Anwendung wird `NOSUPERUSER` zugewiesen, damit RLS nicht umgehbar ist.

Ergänzungen für neue facility-gescopte Models sind verbindlich:

- Migration ergänzt das Model in `DIRECT_TABLES` oder `JOIN_TABLES`.
- `EXPECTED_TABLES` in [`src/tests/test_rls.py`](././src/tests/test_rls.py) wird erweitert.
- Manager + `facility`-FK sind Pflicht.

## Consequences

- **+** Defense-in-Depth: ein vergessenes Filter in der Anwendungsschicht führt nicht zum Datenleck — die Datenbank verweigert die Zeile.
- **+** Funktionaler RLS-Cross-Tenant-Test ([`test_rls_functional.py`](././src/tests/test_rls_functional.py)) prüft die Garantie regressions-stabil.
- **+** Auch Admin-Queries und Management-Commands sind durch RLS geschützt, sofern sie als Anwendungsrolle laufen.
- **−** Migrations werden komplexer: jede neue Tabelle braucht zwei Stellen (Manager + RLS-Liste).
- **−** Wartungs-Tasks (Migrations selbst, RLS-Setup) müssen mit Superuser-Rolle laufen — der Wechsel ist explizit dokumentiert.

## Alternatives considered

- **Nur Manager-Filter:** Verworfen — siehe Context, einzelner Bug bricht die Garantie.
- **Eine DB pro Facility:** Verworfen — Operativer Aufwand (Migrations × N), Cross-Facility-Auswertungen unmöglich, Backup-Komplexität.
- **Schema-per-Tenant (`django-tenants`):** Verworfen — RLS gibt vergleichbare Isolation mit niedrigerer Komplexität, gemeinsame Auswertungen bleiben einfach.

## References

- [`docs/ops-runbook.md`](./ops-runbook.md) § 9 (RLS-Pflege)
- [CONTRIBUTING.md § Facility-Scoping & Row Level Security](././CONTRIBUTING.md)
- Commits: `f8ef338` (RLS-Einführung), `b2dbc6a` (session-weite Variable), `4f4273a` (Cross-Tenant-Test)

## Update 2026-05-09: 2-User-Modell für Operator-Tasks

Postgres macht den per `POSTGRES_USER` angelegten Login-User automatisch zum Bootstrap-Superuser. Der kann sich nicht selbst entrechten. Damit RLS als ehrliche Defense-in-Depth wirkt:

- Bootstrap-User bleibt `postgres` (Superuser, nur Init/Notfall — nicht für Runtime/Wartung).
- **App-User** `anlaufstelle` (`NOSUPERUSER NOBYPASSRLS`) — Django-Runtime; RLS-Policies greifen.
- **Admin-User** `anlaufstelle_admin` (`NOSUPERUSER BYPASSRLS`) — Operator-Tasks (`seed`, `migrate`, `retention-pruning`). Wird vom postgres-Init-Script angelegt.

Operator-Tasks laufen via `compose run` mit `POSTGRES_USER`/`POSTGRES_PASSWORD`-ENV-Override (siehe [`deploy/deploy-dev.sh`](././deploy/deploy-dev.sh) und [`Makefile`](././Makefile) `dev-seed`-Target). App-Code nutzt ausschließlich den Runtime-User; kein BYPASSRLS-Toggling am Runtime-User.

## Update 2026-05-10: Anwendungsrollen-Schicht für Superadmin

Mit Einführung des 5-Rollen-Modells ([ADR-018](018-rollenmodell-superadmin.md)) braucht der `super_admin` einen facility-übergreifenden Lese-Pfad — und zwar bewusst **ohne** dafür einen weiteren DB-User mit `BYPASSRLS` einzuführen, weil der Runtime-User die einzige Verbindung in den Connection-Pool von gunicorn ist.

**Lösung:** Eine **zweite Schicht oberhalb der DB-Rollen** (`anlaufstelle` / `anlaufstelle_admin`):

- Migration [0085](././src/core/migrations/0085_rls_super_admin_branch.py) erweitert jede `facility_isolation`-Policy um einen OR-Branch:

  ```sql
  USING (
      facility_id::text = current_setting('app.current_facility_id', true)
      OR current_setting('app.is_super_admin', true) = 'true'
  )
  ```

- Die [`FacilityScopeMiddleware`](././src/core/middleware/facility_scope.py) setzt **pro Request** entweder `app.current_facility_id` (für facility-gebundene User) oder `app.is_super_admin='true'` (für `super_admin`). Beides ist eine Postgres-Session-Variable, gesetzt via `SELECT set_config('app.is_super_admin', 'true', false)` (transaction-scoped via `false`-Flag, oder session-scoped — siehe Middleware).

- **Always-Reset-Pattern (Pool-Leak-Schutz):** Vor jedem Request wird `app.is_super_admin` explizit auf `'false'` zurückgesetzt — auch wenn der vorige Request auf der gleichen DB-Connection ein `super_admin`-Request war. Damit kann ein lecker Pool-Connection nicht ausversehen einem nachfolgenden facility-User Cross-Tenant-Sicht geben. Der Test [`test_rls_super_admin.py`](././src/tests/test_rls_super_admin.py) verifiziert das Reset-Verhalten.

**Architektur-Schichtung:**

| Schicht | Mechanismus | Wer steuert? |
|---|---|---|
| 1 (Anwendung) | `FacilityScopedManager.for_facility()` + Mixin-Gating | Django-Code |
| 2 (DB-User) | Runtime-User `anlaufstelle` ist `NOSUPERUSER NOBYPASSRLS` | Init-Script |
| 3 (Anwendungsrolle) | `app.is_super_admin`-Session-Var als OR-Branch in Policies | Middleware (per Request neu gesetzt) |

Schicht 3 ist die neue Ebene, Schichten 1+2 bleiben unverändert. Der Bootstrap-Superuser `postgres` und der Operator-User `anlaufstelle_admin` (BYPASSRLS) bleiben für Init/Wartung; sie sind kein Pfad für `super_admin`-Application-Logic.

**Abgrenzung gegenüber „eigener DB-User":** Der erwogene Alternativ-Weg „dritter DB-User mit `BYPASSRLS` für `super_admin`-Requests" wurde verworfen, weil (a) Django-Connection-Pools pro Worker einen festen User haben, (b) ein dynamischer User-Wechsel per Request einen kompletten neuen Connection-Pool für `super_admin`-Sessions bräuchte, (c) das DSGVO-Audit aufwändiger wird (mehr DB-User = mehr `pg_user`-Rotation), und (d) die Anwendungsrollen-Schicht den gleichen Schutz mit weniger Komplexität bringt.
