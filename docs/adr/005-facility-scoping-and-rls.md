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
