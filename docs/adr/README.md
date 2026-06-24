# Architecture Decision Records (ADRs)

Dieser Ordner dokumentiert die wesentlichen Architektur- und Plattformentscheidungen von Anlaufstelle. ADRs erklären **warum** etwas so ist — der Code zeigt **was** und **wie**.

## Format

Schlankes [MADR](https://adr.github.io/madr/)-Light. Jede ADR-Datei hat:

- **Status:** `Proposed` · `Accepted` · `Superseded by ADR-NNN` · `Deprecated`
- **Date:** Datum der Entscheidung (ISO).
- **Context:** Was war die Ausgangslage / Problem.
- **Decision:** Was wurde entschieden.
- **Consequences:** Was folgt daraus (positiv und negativ).
- **Alternatives considered:** Welche Optionen wurden verworfen und warum.

## Wann eine neue ADR

Schreibe eine ADR, wenn eine Entscheidung:

- **Schwer reversibel** ist (Datenbank-Wechsel, Verschlüsselungs­schema, Auth-Modell).
- **Querschnittlich** wirkt (Settings-Vererbung, Service-Layer-Pflicht, RLS).
- **Nicht aus dem Code ablesbar** ist (warum *kein* Self-Service für DSGVO Art. 16, warum sync statt async).

Keine ADR für lokale Refactorings, Bugfixes, Renamings.

## Nummerierung & Status

- ADRs sind fortlaufend nummeriert (`ADR-001`, `ADR-002`, …) und werden **nie umnummeriert**.
- Eine überholte ADR wird auf `Superseded by ADR-NNN` gesetzt, **nicht gelöscht**. Die Historie bleibt nachvollziehbar.

## Index

| Nr | Titel | Status |
|----|-------|--------|
| [ADR-001](001-greenfield-rewrite.md) | Greenfield-Rewrite statt Prototyp-Weiterbau | Accepted |
| [ADR-002](002-cbvs-and-service-layer.md) | Class-Based Views + Service-Layer | Accepted |
| [ADR-003](003-htmx-alpine-tailwind.md) | HTMX + Alpine.js + Tailwind statt SPA | Accepted |
| [ADR-004](004-postgresql-only.md) | PostgreSQL als einzige Datenbank | Accepted |
| [ADR-005](005-facility-scoping-and-rls.md) | Facility-Scoping via Middleware + Row Level Security | Accepted |
| [ADR-006](006-fernet-field-encryption.md) | Fernet/MultiFernet für Feldverschlüsselung | Accepted |
| [ADR-007](007-auditlog-append-only.md) | AuditLog als Append-Only mit DB-Trigger | Accepted |
| [ADR-008](008-lockout-scope.md) | Login-Lockout-Scope: Username + IP | Accepted |
| [ADR-009](009-settings-inheritance.md) | Settings-Vererbung: E2E erbt von Dev, nicht Test | Accepted |
| [ADR-010](010-sync-pdf-generation.md) | Synchrone PDF-Generierung ohne Task-Queue | Accepted |
| [ADR-011](011-three-repo-release-pipeline.md) | 3-Repo-Release-Pipeline (dev → stage → app) | Accepted |
| [ADR-012](012-incremental-mypy.md) | Inkrementelles mypy mit Strict-Zone für Services | Accepted |
| [ADR-013](013-dsgvo-art16-no-selfservice.md) | DSGVO Art. 16 ohne App-Self-Service | Accepted |
| [ADR-014](014-encrypted-file-vault.md) | Encrypted File Vault | Accepted |
| [ADR-015](015-mfa-totp.md) | MFA-Verfahren — TOTP + Hash-Backup-Codes | Accepted |
| [ADR-016](016-search-postgres-only.md) | Volltextsuche bleibt in PostgreSQL | Accepted |
| [ADR-017](017-deployment-topology.md) | Deployment-Topologie — Plain Docker Compose primär, Multi-Stage als parallele Stacks | Accepted |
| [ADR-018](018-rollenmodell-superadmin.md) | 5-Rollen-Modell mit Super-Admin (Systemadministration / Anwendungsbetreuung / Leitung / Fachkraft / Assistenz) | Accepted |
| [ADR-019](019-custom-admin-site-sudo.md) | Custom AdminSite mit Rollen-Gate und Sudo-Mode | Accepted |
| [ADR-020](020-three-role-postgres-model.md) | Drei-Rollen-Postgres-Modell (Bootstrap / App / Admin) | Accepted |
| [ADR-021](021-retention-modell.md) | Retention-Modell (Fristen, Legal-Hold, AuditLog-Pruning) | Accepted |
| [ADR-022](022-offline-snapshot-keys.md) | Offline-Snapshot und Offline-Keys | Accepted |
| [ADR-023](023-k-anonymization-statistik.md) | K-Anonymisierung für externe Statistik | Accepted |
| [ADR-024](024-slug-based-identifiers.md) | Slug-basierte stabile Identifikatoren | Accepted |
| [ADR-025](025-csp-unsafe-eval-admin.md) | CSP-`unsafe-eval` nur auf `/admin-mgmt/` | Accepted |
| [ADR-026](026-rate-limiting.md) | Rate-Limiting-Strategie (Tarife, Pro-User-Keying, Shared-Cache) | Accepted |
| [ADR-027](027-i18n-strategy.md) | i18n-/Übersetzungs-Strategie (DE-Quelle, EN-Sync-Gate) | Accepted |
| [ADR-028](028-demo-release-versioning.md) | Demo-Instanz läuft nur auf getaggtem Release | Accepted |

## Backlog — geplante / noch zu schreibende ADRs

Entscheidungen, die im Code bereits sichtbar sind, deren Begründung aber noch nicht formal als ADR niedergeschrieben ist. Reihenfolge nach Priorität (oberste zuerst).

| Kandidat | Worum es geht | Warum noch keine ADR |
|----------|---------------|----------------------|
| _(aktuell keine offenen Kandidaten)_ | Die zuletzt offenen §E-Themen sind verschriftlicht: Slug-IDs ([ADR-024](024-slug-based-identifiers.md)), CSP-Ausnahme ([ADR-025](025-csp-unsafe-eval-admin.md)), Rate-Limiting ([ADR-026](026-rate-limiting.md)), i18n ([ADR-027](027-i18n-strategy.md)); ADR-022 ist seit #1100 `Accepted`. ||

**Vorgehen:** Wenn eines der Themen sich stabilisiert oder eine echte Entwicklungs­alternative ansteht, wird die nächste freie ADR-Nummer vergeben. Bei Verwerfen einer ADR-Idee bleibt der Eintrag hier kurz mit „verworfen, weil …" stehen, damit die Diskussion nicht verloren geht.

**Sicherheits-Härtung (v0.14.0, #1016):** Webhook-IP-Pinning, authentifizierte Backups (HMAC-SHA256), Shared-`DatabaseCache` und das Datei-Chunk-Format v2 sind bewusst **keine** eigenen ADRs — sie gehören als Mitigations in [`threat-model.md`](../threat-model.md) / [`security-notes.md`](../security-notes.md). `threat-model.md` ist auf die HMAC-SHA256-Backup-Integrität (Encrypt-then-MAC, Refs #1024) nachgezogen (#1099, erledigt).
