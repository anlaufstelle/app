# ADR-021: Retention-Modell (Fristen, Legal-Hold, AuditLog-Pruning)

- **Status:** Accepted
- **Date:** 2026-05-28
- **Deciders:** Tobias Nix
- **Refs:** #744

## Context

DSGVO Art. 5(1)(e) — Speicherbegrenzung — verlangt, dass personenbezogene Daten nicht laenger aufbewahrt werden, als es der Zweck erfordert. Im niedrigschwelligen Beratungskontext steht dieser Grundsatz in Spannung zu drei realen fachlichen Beduerfnissen:

- **Aktenfuehrung ueber Jahre:** Fachkraefte muessen Verlauf, Massnahmen und Abschluss eines Falls nachvollziehen koennen, auch wenn der letzte Kontakt Jahre zurueckliegt. Ein hart-loeschendes Modell zerstoert fachliche Kontinuitaet.
- **Behoerdliche / juristische Pflichten:** Laufende Verfahren, Sozialgerichtsstreite und behoerdliche Anfragen verlangen, dass Daten **trotz** abgelaufener fachlicher Frist unveraendert bleiben, bis das Verfahren endet.
- **Audit-Forensik:** Sicherheitsvorfaelle werden oft erst Wochen oder Monate nach dem auslösenden Login bemerkt. AuditLog-Eintraege ([ADR-007](007-auditlog-append-only.md)) duerfen nicht synchron mit den Fachdaten gepruned werden — sonst verliert die Forensik genau die Spur, die sie braucht.

Die Defaults muessen mit den Datenschutzvorlagen in [`src/core/dsgvo_templates/`](../../src/core/dsgvo_templates/) konsistent sein, damit Verarbeitungs-Verzeichnis (VVT) und tatsaechliches App-Verhalten nicht auseinanderlaufen.

## Decision

Anlaufstelle implementiert ein **mehrstufiges Retention-Modell** mit getrennten Fristen pro Entitaetstyp, expliziter Legal-Hold-Schicht und eigener AuditLog-Pruning-Politik. Die Logik lebt im Submodul [`core.retention`](../../src/core/retention/), re-exportiert via [`src/core/services/retention.py`](../../src/core/services/retention.py).

- **Fristen pro Entitaet, nicht global.** Events, Cases, Clients, WorkItems und AuditLog haben je eigene Fristen, die in den Einrichtungs-Settings konfigurierbar sind. Default-Werte sind mit den DSGVO-Templates (Verarbeitungs-Verzeichnis, Aufbewahrungs- und Loeschkonzept) abgeglichen — eine Aenderung im Code zwingt zu einem Abgleich der Templates.
- **Vier Soft-Delete-Strategien** statt Hard-Delete:
 - `anonymous` — datensparsame Eintraege werden sofort nach Frist soft-geloescht;
 - `identified` — identifizierte Klient-Eintraege werden nach Frist **anonymisiert** ([`anonymize_clients`](../../src/core/retention/anonymization.py));
 - `qualified` — fachlich qualifizierte Eintraege verlangen einen `RetentionProposal` mit Lead/Admin-Freigabe vor Soft-Delete;
 - `document_type` — pro Dokumenttyp konfigurierbare Frist, ueberschreibt die globale Event-Frist.
- **Legal-Hold als Einfrier-Marker.** [`LegalHold`](../../src/core/retention/legal_holds.py) setzt einen Marker pro Entitaet; `has_active_hold(target)` haelt jede Soft-Delete- und Anonymisierungs-Pipeline auf. Hold setzen darf nur Lead/Admin, Aufheben mit AuditLog-Spur.
- **`RetentionProposal`-Vorschlagspipeline** fuer qualifizierte Eintraege: Cron erzeugt Vorschlaege, Dashboard ([`build_retention_dashboard_context`](../../src/core/retention/proposals.py)) zeigt sie, Lead/Admin entscheidet `approve`/`defer`/`reject`. Damit bleibt das Pruning bei juristisch sensiblen Datensaetzen eine bewusste menschliche Entscheidung.
- **AuditLog-Pruning auf eigener, laengerer Frist** ([`prune_auditlog`](../../src/core/retention/audit_pruning.py)). Die Frist ist absichtlich laenger als alle Fach-Fristen, damit Forensik nach einem Vorfall noch Spuren findet, wenn die Sachdaten bereits anonymisiert sind. Pruning ist append-only-kompatibel — keine `UPDATE`s, nur `DELETE` strikt nach Frist.
- **Default-Reihenfolge im Cron:** Backup (02:00) → `enforce_retention` (03:00) → Snapshots (04:00). Backup laeuft **vor** Retention, damit gepruente Daten in einem Backup-Stand wiederherstellbar bleiben (siehe [`docs/ops-runbook.md` § Retention](../ops-runbook.md)).

## Consequences

- **+** DSGVO Art. 5(1)(e) ist umsetzbar, ohne fachliche Kontinuitaet zu opfern — Soft-Delete plus Anonymisierung sind belastbarer als Hard-Delete und bleiben statistik-freundlich.
- **+** Legal-Hold liefert eine saubere Ausnahme-Schicht fuer laufende Verfahren, ohne dass Fristen pro Fall manuell ueberschrieben werden muessen.
- **+** Eigene AuditLog-Frist sichert Forensik-Faehigkeit auch nach Fach-Pruning; passt zur Append-Only-Politik aus [ADR-007](007-auditlog-append-only.md).
- **+** Defaults sind an die Datenschutzvorlagen gekoppelt — VVT, Loeschkonzept und tatsaechliches Verhalten driften nicht stillschweigend auseinander.
- **+** Vorschlagspipeline macht `qualified`-Loeschungen pruefbar (RetentionProposal-Historie + AuditLog).
- **−** Zusaetzliche Komplexitaet im Datenmodell (vier Strategien, Legal-Hold-Marker, Proposal-Tabelle). Wartungsaufwand steigt.
- **−** Soft-Delete-Eintraege bleiben physisch in der DB — Backup-Groessen waechst, bis die zweite Pruning-Stufe greift.
- **−** Default-Fristen sind eine Mischung aus juristischer Empfehlung und Erfahrungswerten. Aenderungsdruck aus echten Traegerfaellen ist absehbar; das Modell muss konfigurierbar bleiben.

## Alternatives considered

- **Hard-Delete strikt nach Frist.** Verworfen: zerstoert fachliche Verlaufslinie und macht spaetere Sozialgerichtsstreite blind. DSGVO verlangt nicht Loeschung um jeden Preis — Anonymisierung ist gleichwertig.
- **Globale Retention-Frist statt pro Entitaet.** Verworfen: AuditLog und Klient-Stammdaten haben fundamental unterschiedliche Aufbewahrungs-Logiken. Eine gemeinsame Frist trifft fuer beide das falsche Mass.
- **Loeschen ohne Legal-Hold-Schicht (nur Vorschlaege).** Verworfen: ein vergessener `defer`-Klick auf einen RetentionProposal waere ein juristisches Eigentor. Hold als unabhaengige Einfrier-Schicht ist robuster.
- **Differential-Privacy statt k-Anonymisierung als Standard-Anonymisierung.** Vertagt: Bibliotheks-Abhaengigkeit + neuer Bedrohungs-Modell-Aufwand stehen aktuell nicht im Verhaeltnis zum Mehrwert fuer kleine Traeger. Re-Evaluation, wenn ein externer Statistik-Bedarf entsteht (siehe [ADR-023](023-k-anonymization-statistik.md)).

## References

- [`src/core/retention/`](../../src/core/retention/) — Submodule (`anonymization`, `audit_pruning`, `enforcement`, `legal_holds`, `proposals`)
- [`src/core/services/retention.py`](../../src/core/services/retention.py) — Re-Export-Stub fuer Bestands-Aufrufer
- [`src/core/management/commands/enforce_retention.py`](../../src/core/management/commands/enforce_retention.py)
- [`src/core/dsgvo_templates/`](../../src/core/dsgvo_templates/) — Verarbeitungs-Verzeichnis und Loeschkonzept
- [`docs/ops-runbook.md` § Retention](../ops-runbook.md)
- [ADR-007](007-auditlog-append-only.md) — AuditLog Append-Only
- [ADR-013](013-dsgvo-art16-no-selfservice.md) — DSGVO Art. 16 ohne Self-Service
- [ADR-023](023-k-anonymization-statistik.md) — k-Anonymisierung fuer externe Statistik
- Issue #744 — Retention-Refactor
