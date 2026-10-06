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
  - `identified` — identifizierte Klient-Eintraege werden nach Frist **anonymisiert** ([`anonymize_clients`](../../src/core/retention/anonymization.py)); *(Präzisiert im [Update 2026-10-06](#update-2026-10-06-ist-stand-der-löschwirkung).)*
  - `qualified` — fachlich qualifizierte Eintraege verlangen einen `RetentionProposal` mit Lead/Admin-Freigabe vor Soft-Delete; *(Präzisiert im [Update 2026-10-06](#update-2026-10-06-ist-stand-der-löschwirkung).)*
  - `document_type` — pro Dokumenttyp konfigurierbare Frist, ueberschreibt die globale Event-Frist. *(Präzisiert im [Update 2026-10-06](#update-2026-10-06-ist-stand-der-löschwirkung).)*
- **Legal-Hold als Einfrier-Marker.** [`LegalHold`](../../src/core/retention/legal_holds.py) setzt einen Marker pro Entitaet; `has_active_hold(target)` haelt jede Soft-Delete- und Anonymisierungs-Pipeline auf. Hold setzen darf nur Lead/Admin, Aufheben mit AuditLog-Spur.
- **`RetentionProposal`-Vorschlagspipeline** fuer qualifizierte Eintraege: Cron erzeugt Vorschlaege, Dashboard ([`build_retention_dashboard_context`](../../src/core/retention/proposals.py)) zeigt sie, Lead/Admin entscheidet `approve`/`defer`/`reject`. Damit bleibt das Pruning bei juristisch sensiblen Datensaetzen eine bewusste menschliche Entscheidung. *(Präzisiert im [Update 2026-10-06](#update-2026-10-06-ist-stand-der-löschwirkung).)*
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

## Update 2026-08-03: Realer Fristen-Zuschnitt — nicht jede Entitaet hat eine eigene Frist (Refs #1753)

Die Entscheidung oben formuliert *„Events, Cases, Clients, WorkItems und AuditLog haben je eigene Fristen"*. Das ist **ueberzeichnet** und wird hier praezisiert; die Umsetzung war nie anders, die Formulierung war es. **Reine Doku-Korrektur — kein neues Feature, keine Verhaltensaenderung.** Der tatsaechliche Zuschnitt:

| Entitaet | Eigene konfigurierbare Frist? | Mechanik |
|---|---|---|
| **Event** | **ja** — `retention_anonymous_days` (90), `retention_identified_days` (365), `retention_qualified_days` (3650), zusaetzlich `DocumentType.retention_days` als Typ-Override | Soft-Delete durch `enforce_retention`; `qualified` nur ueber `RetentionProposal` mit Lead/Admin-Freigabe |
| **Activity** (Zeitstrom) | **ja** — `retention_activities_days` (365) | **Hard**-Delete, kein Soft-Delete |
| **AuditLog** | **ja** — `auditlog_retention_months` (24, `0` = aus) | `prune_auditlog`; drei prune-exempte Aktionen plus `EXPORT`-**Berichts-Freigaben** (Zeitraum + herausgegebenes Artefakt, Refs #1770 — Klienten-/DSGVO-Exporte und Bildschirm-Ansichten bleiben der Frist unterworfen), Checkpoint-Tombstone fuer die HMAC-Kette |
| **Client** | **nein**, zwei *abgeleitete* Ausloeser | (a) `client_trash_days` (30) nach **manuellem** Soft-Delete → `anonymize_eligible_soft_deleted_clients`; (b) `anonymize_clients`, sobald **alle** Events der Person soft-geloescht sind. Beide Wege enden in `anonymize_client` |
| **Case, Episode, WorkItem, OutcomeGoal, Milestone** | **nein** | Kein eigener Cutoff und kein eigener Cron-Pfad. Sie haengen am **Owner-Lebenszyklus**: ihre Freitexte werden als Teil der Redaktionskaskade von `anonymize_client` getilgt (`_redact_cases_and_episodes`, `_redact_case_goals`, `_redact_workitems`) — seit #1751 inklusive Wirkungszielen und Meilensteinen. Die Zeilen selbst bleiben bestehen, nur die personenbeziehbaren Inhalte fallen |

Konsequenz fuer Betreiber und Auditor: „loeschen" heisst fuer Faelle/Episoden/Aufgaben/Ziele **Redaktion beim Anonymisieren der Person**, nicht Ablauf einer eigenen Frist. Wer fuer diese Entitaeten eine unabhaengige Frist braucht (z. B. „abgeschlossene Faelle nach X Jahren", auch wenn die Person aktiv bleibt), braucht dafuer ein eigenes Feature — es existiert heute nicht.

Beide Anonymisierungs-Pfade (hart und k-anonymisiert) fahren dieselbe Kaskade; die Paritaet ist seit #1750 durch einen ableitungsbasierten Architektur-Guard erzwungen (siehe [ADR-023 Update 2026-08-03](023-k-anonymization-statistik.md)). Legal Holds blockieren beide Bulk-Einstiege (#1756).

## Update 2026-10-06: Ist-Stand der Löschwirkung

Die Entscheidung oben und das Update vom 2026-08-03 beschreiben einzelne Punkte stärker, als der Aufbewahrungslauf sie umsetzt. **Reine Doku-Korrektur — keine Verhaltensänderung.** Maßgeblich ist der folgende Ist-Stand; die DSGVO-Vorlagen in [`src/core/dsgvo_templates/`](../../src/core/dsgvo_templates/) sind darauf abgeglichen.

### Dokumentationstyp-Frist ist zusätzlich, kein Override (Refs #2462)

`DocumentType.retention_days` ersetzt die Kontaktstufen-Frist nicht. [`process_facility_retention`](../../src/core/retention/enforcement.py) führt die Strategien `anonymous`, `identified`, `qualified` und `document_type` nacheinander aus; ein Ereignis fällt, sobald die erste greift. Es gilt also die zuerst ablaufende Frist: Eine kürzere Typ-Frist verkürzt, eine längere verlängert nichts. Die Formulierungen „ueberschreibt die globale Event-Frist" (Decision) und „Typ-Override" (Tabelle im Update 2026-08-03) sind in diesem Sinn zu lesen.

### Keine Freigabe im Standardlauf (Refs #2425)

Der ausgelieferte Zeitplan ([`deploy/crontab`](../../deploy/crontab)) ruft `enforce_retention` ohne `--propose` auf. Dieser Standardlauf setzt abgelaufene Ereignisse **aller** Strategien unmittelbar auf gelöscht, auch `qualified` — ohne `RetentionProposal` und ohne Freigabe. Er liest den Vorschlagsstatus nicht; nur ein aktiver Legal Hold (auf Ereignis, Person oder Fall) nimmt Daten aus. Nach der Löschung räumt er lediglich freigegebene Vorschläge der gelöschten Ereignisse auf.

- **Vorschlagslauf** (`enforce_retention --propose`): eigener, nicht eingeplanter Aufruf. Er löscht nichts, sondern legt für die Ereignisse, die die Strategien als abgelaufen erfassen, `RetentionProposal`-Einträge an — für alle vier Strategien, nicht nur `qualified` — und reaktiviert fällige Zurückstellungen.
- **Dashboard-Entscheidungen** (`approve`/`defer`/`reject`) ändern nur den Vorschlagsstatus. `reject` und `defer` schützen das Ereignis nicht vor dem Standardlauf; schützen kann nur ein Legal Hold, den die Oberfläche auf einer Vorschlagskarte setzt.
- **Vier-Augen-Prinzip** gilt für **manuelle** Löschungen (`DeletionRequest`): Personen immer, Ereignisse und Fälle bei Personen der Stufe `qualified`. Den Aufbewahrungslauf erfasst es nicht.

Damit sind „verlangen einen `RetentionProposal` mit Lead/Admin-Freigabe" (Decision, `qualified`), „bewusste menschliche Entscheidung" (Vorschlagspipeline) und „`qualified` nur über `RetentionProposal` mit Lead/Admin-Freigabe" (Tabelle im Update 2026-08-03) **nicht** der Ist-Stand. Ob der Standardlauf eine Freigabe verlangen soll, ist eine offene Produktentscheidung.

### Fristbeginn und Wirkung

- `anonymous` und `identified` messen die Frist **je Ereignis** ab `Event.occurred_at`. Ein neuer Kontakt verlängert ältere Ereignisse nicht; „nach letztem Kontakt" ist kein Fristbeginn der Anwendung.
- `qualified` misst ab `Case.closed_at` des verknüpften, geschlossenen Falls.
- `identified` löscht die Ereignisse (Soft-Delete), anonymisiert aber nicht unmittelbar die Person: [`anonymize_clients`](../../src/core/retention/anonymization.py) greift erst, wenn **alle** Ereignisse einer Person gelöscht sind (das Update 2026-08-03 beschreibt das in der Client-Zeile).
- Der Soft-Delete leert Inhalte und Suchtext sofort und löscht die Anhänge; die Ereigniszeile bleibt als gelöscht markiert.
- Vor der Löschung sichert der Lauf die betroffenen Monate als Statistik-Aggregate (`ensure_snapshots_for_months`). Diese haben keine Frist.

### Ereignisse der Stufe `qualified` ohne geschlossenen Fall (Refs #2463)

[`enforce_qualified`](../../src/core/retention/enforcement.py) erfasst nur Ereignisse, deren Fall geschlossen ist (`Case.Status.CLOSED`) und deren `closed_at` vor dem Stichtag liegt. `Event.case` ist optional. Ein Ereignis einer Person der Stufe `qualified` **ohne Fall** oder in einem **offenen Fall** verfällt über `retention_qualified_days` deshalb nie, sondern nur über eine gesetzte `DocumentType.retention_days`. Solange ein solches Ereignis besteht, greift auch `anonymize_clients` für die Person nicht. Gelöscht werden die Ereignisse, nicht der Fall. Ob diese Lücke fachlich gewollt ist oder ein eigener Fristbeginn für solche Ereignisse nötig ist, ist offen; die Vorlagen benennen sie, ohne eine Löschung zuzusagen.

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
