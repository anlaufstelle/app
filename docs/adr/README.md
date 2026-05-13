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
| [ADR-004](004-postgresql-only.md) | PostgreSQL 16 als einzige Datenbank | Accepted |
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

## Backlog — geplante / noch zu schreibende ADRs

Entscheidungen, die im Code bereits sichtbar sind, deren Begründung aber noch nicht formal als ADR niedergeschrieben ist. Reihenfolge nach Priorität (oberste zuerst).

| Kandidat | Worum es geht | Warum noch keine ADR |
|----------|---------------|----------------------|
| Retention-Modell | Retention-Fristen, Legal-Hold, AuditLog-Pruning ([`src/core/services/retention.py`](././src/core/services/retention.py)) — wer setzt welche Frist, wer darf Hold setzen, was passiert mit Historie. | Modell ist im Code, aber die Begründung der Default-Fristen sollte noch mit Datenschutz­vorlagen abgeglichen werden. |
| Offline-Snapshot / Offline-Keys | Offline-Bundle für Außeneinsätze ([`src/core/services/offline.py`](././src/core/services/offline.py), [`offline_keys.py`](././src/core/services/offline_keys.py)) — Bedrohungsmodell, Schlüsselableitung, Synchronisations­semantik. | Feature ist noch nicht stabil genug, um Annahmen festzuschreiben. |
| K-Anonymisierung für Statistik | Schwellenwerte und Fallback-Bucketing in der Statistik­auswertung ([`src/core/services/k_anonymization.py`](././src/core/services/k_anonymization.py)). | Schwellen­wahl ist eher Policy als Architektur — wird ggf. lieber im Fachkonzept verankert als als ADR. |

**Vorgehen:** Wenn eines der Themen sich stabilisiert oder eine echte Entwicklungs­alternative ansteht, wird die nächste freie ADR-Nummer vergeben. Bei Verwerfen einer ADR-Idee bleibt der Eintrag hier kurz mit „verworfen, weil …" stehen, damit die Diskussion nicht verloren geht.
