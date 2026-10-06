# Architecture Decision Records (ADRs)

Dieser Ordner dokumentiert die wesentlichen Architektur- und Plattformentscheidungen von Anlaufstelle. ADRs erklären **warum** etwas so ist — der Code zeigt **was** und **wie**.

## Format

Schlankes [MADR](https://adr.github.io/madr/)-Light. Jede ADR-Datei hat:

- **Status:** `Proposed` · `Accepted` · `Teilweise abgelöst durch ADR-NNN` · `Superseded by ADR-NNN` · `Deprecated`
  - `Teilweise abgelöst durch ADR-NNN`: Eine spätere ADR ersetzt einen abgegrenzten Teil der Entscheidung; der Rest gilt weiter. Ein datierter Nachtrag in der betroffenen ADR nennt den abgelösten Teil und verweist auf die neue ADR. Die ältere Schreibweise „Teilweise superseded durch“ (ADR-034, ADR-037) meint denselben Status.
- **Date:** Datum der Entscheidung (ISO).
- **Deciders:** Verantwortliche der Entscheidung.
- **Refs:** Optionale Ticket-Referenzen im Kopf.
- **References:** Optionale Belegsammlung am Ende.
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
- **Nachtrag statt Umschreiben:** Weicht der Code von einer angenommenen ADR ab, wird die ADR nicht umgeschrieben. Die Abweichung kommt als datierter Abschnitt `## Nachtrag JJJJ-MM-TT: … (Refs mit vollständiger Ticket-URL)` mit Ticket-Referenz ans Ende; Context und Decision bleiben stehen. Ältere Abschnitte heißen `## Update JJJJ-MM-TT: …` und haben dieselbe Funktion.

Die Index-Titel entsprechen der Titelzeile der Datei ohne `ADR-NNN:`. Erläuternde Zusätze stehen in der ADR, nicht im Index.

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
| [ADR-009](009-settings-inheritance.md) | Settings-Vererbung — E2E erbt von Dev, nicht Test | Accepted |
| [ADR-010](010-sync-pdf-generation.md) | Synchrone PDF-Generierung ohne Task-Queue | Accepted |
| [ADR-011](011-three-repo-release-pipeline.md) | 3-Repo-Release-Pipeline (dev → stage → app) | Accepted |
| [ADR-012](012-incremental-mypy.md) | Inkrementelles mypy mit Strict-Zone für Services | Accepted |
| [ADR-013](013-dsgvo-art16-no-selfservice.md) | DSGVO Art. 16 ohne App-Self-Service | Accepted |
| [ADR-014](014-encrypted-file-vault.md) | Encrypted File Vault | Accepted |
| [ADR-015](015-mfa-totp.md) | MFA-Verfahren — TOTP + Hash-gespeicherte Backup-Codes | Accepted |
| [ADR-016](016-search-postgres-only.md) | Volltextsuche bleibt in PostgreSQL | Accepted |
| [ADR-017](017-deployment-topology.md) | Deployment-Topologie — Plain Docker Compose primär, Multi-Stage als parallele Stacks | Accepted |
| [ADR-018](018-rollenmodell-superadmin.md) | 5-Rollen-Modell mit Super-Admin | Accepted |
| [ADR-019](019-custom-admin-site-sudo.md) | Custom AdminSite mit Rollen-Gate und Sudo-Mode | Accepted |
| [ADR-020](020-three-role-postgres-model.md) | Drei-Rollen-Postgres-Modell (Bootstrap / App / Admin) | Teilweise abgelöst durch ADR-033 |
| [ADR-021](021-retention-modell.md) | Retention-Modell (Fristen, Legal-Hold, AuditLog-Pruning) | Accepted |
| [ADR-022](022-offline-snapshot-keys.md) | Offline-Snapshot und Offline-Keys | Teilweise abgelöst durch ADR-030 |
| [ADR-023](023-k-anonymization-statistik.md) | K-Anonymisierung fuer externe Statistik | Accepted |
| [ADR-024](024-slug-based-identifiers.md) | Slug-basierte stabile Identifikatoren | Accepted |
| [ADR-025](025-csp-unsafe-eval-admin.md) | CSP-Lockerung (`unsafe-eval`, `style-src 'unsafe-inline'`) nur auf `/admin-mgmt/` | Accepted |
| [ADR-026](026-rate-limiting.md) | Rate-Limiting-Strategie | Accepted |
| [ADR-027](027-i18n-strategy.md) | i18n-/Übersetzungs-Strategie | Accepted |
| [ADR-028](028-demo-release-versioning.md) | Demo-Instanz läuft nur auf getaggtem Release | Accepted |
| [ADR-029](029-local-only-ci.md) | Quality-Gates lokal-only durchgesetzt (Dev-CI bewusst deaktiviert) | Accepted |
| [ADR-030](030-offline-sync-orchestration-replay-contract.md) | Offline-Sync-Orchestrierung, Replay-Contract & Dead-Letter-Semantik | Accepted |
| [ADR-031](031-totp-secret-at-rest.md) | TOTP-Secret at rest verschlüsseln (MultiFernet) | Accepted |
| [ADR-032](032-webauthn-passkeys-2fa.md) | Passkeys/WebAuthn als zusätzlicher zweiter Faktor | Accepted |
| [ADR-033](033-runtime-owner-role-separation.md) | Runtime-DB-Rolle von der DB-Ownership trennen (A1.4) | Accepted |
| [ADR-034](034-config-ui-category-system-type.md) | category vs. system_type in der Konfigurations-UI | Teilweise abgelöst durch ADR-040 |
| [ADR-035](035-evaluator-semantik-bedingte-feldlogik.md) | Evaluator-Semantik der bedingten Feldlogik | Accepted |
| [ADR-036](036-globale-block-sequenz-feldordnung.md) | Globale Block-Sequenz für Feldgruppen und ungruppierte Felder | Accepted |
| [ADR-037](037-konfigurationspakete-dynamische-felder.md) | Konfigurationspakete (YAML) und entitätsübergreifende dynamische Felder | Accepted; die Consequences-Punkte 1 und 3 (unten) sind teilweise superseded durch [ADR-040](040-documenttype-category-entfernt.md) (2026-08-04) — `category` ist als Paket-Property entfernt, die zugehörige Unique-Constraint ist seither `(facility, name)`. |
| [ADR-038](038-reporting-plugin-backends.md) | Reporting-Backends als Plugin-System | Teilweise abgelöst durch ADR-042 |
| [ADR-039](039-owner-generische-dateianhaenge.md) | Owner-generische Dateianhänge (Attachment an Ereignis, Person, Fall) | Teilweise abgelöst durch ADR-044 |
| [ADR-040](040-documenttype-category-entfernt.md) | `DocumentType.category` wird entfernt | Accepted |
| [ADR-041](041-berichtigungsworkflow-intern.md) | Interner Art.-16-Berichtigungsworkflow (App-gestützt, ohne Self-Service) | Accepted |
| [ADR-042](042-report-key-berichtszuordnung.md) | `report_key` als eigene Achse der Berichtszuordnung | Accepted |
| [ADR-043](043-suchraum-volltextsuche.md) | Suchraum der Volltextsuche — keine Such-Protokollierung, offener Fall-Rahmen | Teilweise abgelöst durch ADR-044 |
| [ADR-044](044-fall-loeschpfad.md) | Fall-Löschpfad — Papierkorb, gestaffelter Antrag, Frist und Redaktion | Accepted |
| [ADR-045](045-personen-merge.md) | Duplikat-Merge für Personen — gestaffelter Antrag, Tombstone, Undo bis Fristablauf | Accepted |
| [ADR-046](046-in-app-badges-statt-notification-modell.md) | In-App-Badges statt Notification-Modell | Accepted |
| [ADR-047](047-altersgruppen-systemweit-fix.md) | Altersgruppen bleiben systemweit fix | Accepted |
| [ADR-048](048-fachhoheit-zugaenge-hub.md) | Fachhoheit und Sicherheitsgrenzen des Zugänge-Hubs | Accepted |
| [ADR-049](049-one-command-local-bootstrap.md) | One-Command-Bootstrap für das lokale Setup | Accepted |
| [ADR-050](050-eventhistory-retention.md) | EventHistory-Retention — Redaktion nach Ereignis-Lebenszyklus statt Partitionierung | Accepted |
| [ADR-051](051-berichte-konfigurierbarkeit-zielbild.md) | Zielbild für Berichte-Konfigurierbarkeit und Kern-Konsolidierung | Accepted |

## Backlog — geplante / noch zu schreibende ADRs

Entscheidungen, die im Code bereits sichtbar sind, deren Begründung aber noch nicht formal als ADR niedergeschrieben ist. Reihenfolge nach Priorität (oberste zuerst).

| Kandidat | Worum es geht | Warum noch keine ADR |
|----------|---------------|----------------------|
| _(aktuell keine offenen Kandidaten)_ | Die zuletzt offenen §E-Themen sind verschriftlicht: Slug-IDs ([ADR-024](024-slug-based-identifiers.md)), CSP-Ausnahme ([ADR-025](025-csp-unsafe-eval-admin.md)), Rate-Limiting ([ADR-026](026-rate-limiting.md)), i18n ([ADR-027](027-i18n-strategy.md)); ADR-022 ist seit #1100 `Accepted`. | — |

**Vorgehen:** Wenn eines der Themen sich stabilisiert oder eine echte Entwicklungs­alternative ansteht, wird die nächste freie ADR-Nummer vergeben. Bei Verwerfen einer ADR-Idee bleibt der Eintrag hier kurz mit „verworfen, weil …" stehen, damit die Diskussion nicht verloren geht.

**Sicherheits-Härtung (v0.14.0, #1016):** Webhook-IP-Pinning, authentifizierte Backups (HMAC-SHA256), Shared-`DatabaseCache` und das Datei-Chunk-Format v2 sind bewusst **keine** eigenen ADRs — sie gehören als Mitigations in [`threat-model.md`](../threat-model.md) / [`security-notes.md`](../security-notes.md). `threat-model.md` ist auf die HMAC-SHA256-Backup-Integrität (Encrypt-then-MAC, Refs #1024) nachgezogen (#1099, erledigt).
