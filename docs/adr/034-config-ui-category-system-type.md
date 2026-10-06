# ADR-034: category vs. system_type in der Konfigurations-UI

- **Status:** Teilweise abgelöst durch [ADR-040](040-documenttype-category-entfernt.md) und [ADR-042](042-report-key-berichtszuordnung.md)
- **Date:** 2026-08-02
- **Deciders:** Tobias Nix
- **Refs:** #1056 (M0, Entscheidung E11), #1058 (M2, `DocumentType.slug`), [anlaufstelle/app#38](https://github.com/anlaufstelle/app/issues/38) (M5, Reporting-Entscheid), [ADR-024](024-slug-based-identifiers.md) (Slug-basierte Identifikatoren)

## Context

`DocumentType` trägt zwei nebeneinanderstehende Klassifikations-Felder mit inhaltlicher Überlappung:

- **`category`** ([`document_type.py`](../../src/core/models/document_type.py), Z. 54–64) — eine `TextChoices`-Kategorie (`contact`/`service`/`admin`/`note`). Laut Feld-`help_text` eine *„reine UI-Gruppierung für Statistik-Anzeige und Admin-Filter"*, die *„weder Export noch Bann-/Krisen-Logik"* beeinflusst.
- **`system_type`** ([`document_type.py`](../../src/core/models/document_type.py), Z. 94–103) — die *„stabile Kennung für Bann-Logik, Krisen-Highlight und Jugendamt-Export"*, nach Erstellung unveränderbar (vgl. [ADR-024](024-slug-based-identifiers.md): immutabler `SystemType`-Enum als interner Identifier).

Die Werte-Räume überlappen sprachlich (`category="contact"` vs. `system_type="contact"`), aber ihre Rolle ist verschieden: `category` gruppiert nur die Anzeige, `system_type` steuert Systemverhalten. Die neue Konfigurations-UI (`/config/document-types/`, Teil von M0) muss entscheiden, **welches der beiden Felder** sie einer Einrichtung zur Bearbeitung anbietet — und ob überhaupt beide.

Die eng verwandte Reporting-Frage ist bereits andernorts entschieden: Für die Statistik-/Export-Aggregation gilt seit M5 (anlaufstelle/app#38) die Priorität **`system_type` zuerst, `slug` zweitens, `category` ungenutzt**. Diese ADR trifft *keine* neue Reporting-Entscheidung — sie klärt ausschließlich die **UI-Darstellung** in der Config-Oberfläche.

## Decision

Die Config-UI bietet **beide** Felder an, aber mit unterschiedlichem Editier-Regime:

1. **`category` bleibt frei editierbar.** Es ist eine reine Statistik-/Anzeige-Gruppierung ohne Verhaltensbezug; eine Einrichtung darf ihre Dokumentationstypen jederzeit umgruppieren.
2. **`system_type` ist nur bei der *Erstellung* wählbar und danach read-only.** Nach dem ersten Speichern zeigt das Bearbeiten-Formular den Systemtyp nur an, lässt ihn aber nicht mehr ändern.

Die Read-only-Regel für `system_type` ist keine reine UI-Konvention, sondern auf **Model-Ebene** hart durchgesetzt: Der `save()`-Guard in [`DocumentType.save()`](../../src/core/models/document_type.py) (Z. 127–132) wirft eine `ValidationError` (*„system_type kann nach Erstellung nicht geändert werden."*), sobald ein bestehender, gesetzter `system_type` abweicht. Die UI spiegelt diese Invariante lediglich; sie ist selbst kein Verlass-Punkt. (Eine DB-`CheckConstraint` kann diese Immutabilität nicht abbilden — sie vergleicht keine Alt-/Neu-Werte —; der `save()`-Guard ist daher die maßgebliche Durchsetzung. Die einzige `CheckConstraint` auf dem Model, Z. 121–124, betrifft `retention_days`, nicht `system_type`.)

## Consequences

- **+** Klare Rollentrennung in der UI: `category` = editierbare Gruppierung, `system_type` = stabile, geschützte Systemkennung. Der jeweilige `help_text` erklärt den Unterschied direkt am Feld.
- **+** Die M0-UI führt für `system_type` keine neue Umgehung ein — die bestehende Model-Invariante gilt unverändert; die UI setzt sie nur sichtbar um.
- **−** `category` ist Teil des Natural Key `(facility, name, category)`, mit dem das Statistik-**Snapshot-Lookup** archivierte Kennzahlen wieder ihrem `DocumentType` zuordnet: `UniqueConstraint(facility, name, category)` in [`document_type.py`](../../src/core/models/document_type.py) (Z. 115–118, mit erklärendem Kommentar Z. 110–114) und der Composite-Key-Lookup `(name, category)` in [`dashboard/snapshot.py`](../../src/core/services/dashboard/snapshot.py) (Z. 47–60). Wird `category` nach Anlage einer Snapshot-Zeile geändert, kann die Zuordnung eines historischen Snapshots ins Leere laufen — ein **vorbestehendes** Risiko: Das Custom-Admin erlaubt den `category`-Edit heute bereits. Die Config-UI macht dieselbe Bearbeitung nur bequemer zugänglich, führt aber **kein neues Risiko** ein.
- **−** Der eigentliche Fix des Natural-Key-Problems ist ein stabiler, immutabler `DocumentType.slug` (statt `category` im Schlüssel) — geplant für M2 (#1058). Bis dahin bleibt die `category`-Editierbarkeit ein akzeptiertes, dokumentiertes Restrisiko; M0 verschiebt es nicht und verschärft es nicht.

## Alternatives considered

- **Nur `category` anbieten, `system_type` in der UI verstecken.** Verworfen: `system_type` steuert Bann-/Krisen-/Export-Logik und muss *bei der Erstellung* eines Dokumentationstyps wählbar sein — sonst könnte eine Einrichtung keinen system-relevanten Typ anlegen.
- **Nur `system_type` anbieten, `category` weglassen.** Verworfen: `category` ist die Statistik-Gruppierung der Anzeige und legitimer, verhaltensfreier Konfigurationswert; ihn zu entfernen wäre ein separater Datenmodell-Eingriff außerhalb von M0.
- **`category` in der Config-UI ebenfalls read-only sperren (um das Natural-Key-Risiko zu umgehen).** Verworfen: verschöbe das Problem nur und nähme Einrichtungen eine legitime Gruppierungs-Freiheit, ohne das eigentliche Leck (der schwache Natural Key) zu schließen. Die echte Lösung ist `DocumentType.slug` in M2 (#1058), nicht eine UI-Sperre in M0.

## References

- [`src/core/models/document_type.py`](../../src/core/models/document_type.py) — `category` (Z. 54–64), `system_type` (Z. 94–103), Natural-Key-`UniqueConstraint` `(facility, name, category)` (Z. 115–118, Kommentar Z. 110–114), `save()`-Immutabilitäts-Guard für `system_type` (Z. 127–132)
- [`src/core/services/dashboard/snapshot.py`](../../src/core/services/dashboard/snapshot.py) — Snapshot-Lookup über den Composite-Key `(name, category)` (Z. 47–60)
- [ADR-024](024-slug-based-identifiers.md) — Slug-basierte, immutable Identifikatoren (`system_type` als immutabler Enum; `slug`-Konzept, das M2 auf `DocumentType` überträgt)
- Issue #1056 — M0, Entscheidung E11 (diese ADR)
- Issue #1058 — M2, `DocumentType.slug` (echte Lösung des Natural-Key-Risikos)
- anlaufstelle/app#38 — M5-Reporting-Entscheid (`system_type` → `slug` → `category` ungenutzt); Grundlage, dass hier nur noch die UI-Darstellung offen war

## Nachtrag 2026-10-06: Berichtsachse und Immutabilitätsprüfung (Refs #2500)

Neben der Entfernung von `category` durch [ADR-040](040-documenttype-category-entfernt.md) ersetzt [ADR-042](042-report-key-berichtszuordnung.md) die frühere Berichtsrolle von `system_type`: Nur `ban`/`crisis` bleiben als Verhaltensschlüssel; Berichtstypen werden über `report_key` zugeordnet. Beleg: `src/core/models/document_type.py`, `DocumentType.SystemType` und `_check_immutable_fields`; `src/core/models/report_key.py`, `ReportKeyMixin`. Die alten Zeilennummern sind keine gültige Schnittstellenreferenz; maßgeblich ist die genannte Methode, die `save()` aufruft.
