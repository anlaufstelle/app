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
- **+** Regression-Tests gegen den Trigger ([`29ad3ef`](https://github.com/tobiasnix/anlaufstelle/commit/29ad3ef)) verhindern das versehentliche Entfernen.
- **−** Schema-Änderungen am AuditLog brauchen Trigger-Awareness in der Migration.
- **−** Bug-Fixes in alten Einträgen sind nicht mehr „eben in der DB korrigieren" — falsche Einträge werden mit einem Korrektur-Eintrag ergänzt, nicht überschrieben.

## Alternatives considered

- **Application-only Append-Only-Konvention:** Verworfen — keine Garantie gegen direkten DB-Zugriff.
- **Append-Only über separates Log-System (Loki/SIEM):** Ergänzend möglich, ersetzt aber nicht die DB-eigene Spur — der Service-Layer braucht synchrone Bestätigung, dass das Audit geschrieben wurde, bevor die Geschäftstransaktion committet.

## References

- [`src/core/services/audit.py`](../../src/core/services/audit.py)
- [`src/core/migrations/0024_auditlog_immutable_trigger.py`](../../src/core/migrations/0024_auditlog_immutable_trigger.py)
- [`docs/threat-model.md`](../threat-model.md)
