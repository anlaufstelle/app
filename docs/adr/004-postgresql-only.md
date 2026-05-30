# ADR-004: PostgreSQL 16 als einzige Datenbank

- **Status:** Accepted
- **Date:** 2026-03-19
- **Deciders:** Tobias Nix

## Context

Django unterstützt mehrere Datenbank-Backends. Eine portable Lösung („läuft auch auf SQLite/MySQL") klingt zunächst niedrigschwelliger, kostet aber Funktionalität, die für ein DSGVO-relevantes Fachsystem zentral ist:

- **Row Level Security (RLS)** für Mandanten­trennung (ADR-005) — nur PostgreSQL.
- **JSONB** mit Indexen für strukturierte Zusatzdaten (z.B. Audit-Detail, Event-Felder).
- **Trigger** für AuditLog-Immutability (ADR-007).
- **Volltextsuche** mit Wörterbuch-Konfiguration und Trigram-Index.
- **Robuste Concurrency-Semantik** (`SELECT … FOR UPDATE`, Advisory Locks für Locking-Service).

Eine portable Lösung müsste den kleinsten gemeinsamen Nenner verwenden und alle Sicherheits­garantien in die Anwendungsschicht ziehen.

## Decision

PostgreSQL 16 ist die einzige unterstützte Datenbank — in Entwicklung, Test, E2E und Produktion. Migrations dürfen PostgreSQL-spezifische Features verwenden (`JSONB`, `GIN`, RLS-Policies, Trigger, `TSVECTOR`).

## Consequences

- **+** Defense-in-Depth durch RLS auch dann, wenn die Anwendungsschicht einen Bug hat.
- **+** AuditLog ist auf DB-Ebene gegen Manipulation geschützt, nicht nur per Convention.
- **+** Performante Volltextsuche und JSONB-Queries ohne externe Suchdienste.
- **−** Kein „Klon mit SQLite" für triviale lokale Setups — Entwickler brauchen Docker/Postgres.
- **−** Bei einem hypothetischen Wechsel auf einen anderen DBMS wären Migrations und Service-Layer-Annahmen zu refaktorieren.

## Alternatives considered

- **SQLite für Dev/Test, Postgres in Produktion:** Verworfen — Migrations und Tests müssten Postgres-Features umgehen, womit RLS und AuditLog-Trigger im Test gar nicht greifen würden. Dev/Prod-Drift wäre garantiert.
- **MySQL/MariaDB:** Verworfen — kein RLS, schwächere JSONB-Tooling-Unterstützung.

## References

- [`docs/ops-runbook.md`](../ops-runbook.md), [`src/core/migrations/0047_postgres_rls_setup.py`](../../src/core/migrations/0047_postgres_rls_setup.py)
