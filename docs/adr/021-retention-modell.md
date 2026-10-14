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
  - `identified` — identifizierte Klient-Eintraege werden nach Frist **anonymisiert** ([`anonymize_clients`](../../src/core/retention/anonymization.py)); *(Präzisiert im [Nachtrag 2026-10-10](#nachtrag-2026-10-10-freigabe-und-geschlossene-fristlücken).)*
  - `qualified` — fachlich qualifizierte Eintraege verlangen einen `RetentionProposal` mit manuelle Freigabe durch zwei verschiedene berechtigte Personen mit „Löschbestätigung“ vor Soft-Delete (Auto-Freigabe nur als Opt-in-Ausnahme, standardmäßig aus); *(Präzisiert im [Nachtrag 2026-10-10](#nachtrag-2026-10-10-freigabe-und-geschlossene-fristlücken).)*
  - `document_type` — pro Dokumenttyp konfigurierbare Frist, ueberschreibt die globale Event-Frist. *(Präzisiert im [Nachtrag 2026-10-10](#nachtrag-2026-10-10-freigabe-und-geschlossene-fristlücken).)*
- **Legal-Hold als Einfrier-Marker.** [`LegalHold`](../../src/core/retention/legal_holds.py) setzt einen Marker pro Entitaet; `has_active_hold(target)` haelt jede Soft-Delete- und Anonymisierungs-Pipeline auf. Hold setzen darf nur Lead/Admin, Aufheben mit AuditLog-Spur.
- **`RetentionProposal`-Vorschlagspipeline** fuer qualifizierte Eintraege: Cron erzeugt Vorschlaege, Dashboard ([`build_retention_dashboard_context`](../../src/core/retention/proposals.py)) zeigt sie, Lead/Admin entscheidet `approve`/`defer`/`reject`. Damit bleibt das Pruning bei juristisch sensiblen Datensaetzen eine bewusste menschliche Entscheidung. *(Präzisiert im [Nachtrag 2026-10-10](#nachtrag-2026-10-10-freigabe-und-geschlossene-fristlücken).)*
- **AuditLog-Pruning auf eigener, laengerer Frist** ([`prune_auditlog`](../../src/core/retention/audit_pruning.py)). Die Frist ist absichtlich laenger als alle Fach-Fristen, damit Forensik nach einem Vorfall noch Spuren findet, wenn die Sachdaten bereits anonymisiert sind. *(Berichtigt im [Nachtrag 2026-10-10](#nachtrag-2026-10-10-freigabe-und-geschlossene-fristlücken): die Frist ist unabhängig von den Fach-Fristen und kann kürzer sein.)* Pruning ist append-only-kompatibel — keine `UPDATE`s, nur `DELETE` strikt nach Frist.
- **Default-Reihenfolge im Cron:** Backup (02:00) → `enforce_retention` (03:00) → Snapshots (04:00). Backup laeuft **vor** Retention, damit gepruente Daten in einem Backup-Stand wiederherstellbar bleiben (siehe [`docs/ops-runbook.md` § 3 Cron-Jobs](../ops-runbook.md#3-cron-jobs)).

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
| **Event** | **ja** — `retention_anonymous_days` (90), `retention_identified_days` (365), `retention_qualified_days` (3650), zusaetzlich `DocumentType.retention_days` als Typ-Override | Soft-Delete durch `enforce_retention`; `qualified` nur ueber `RetentionProposal` mit manuelle Freigabe durch zwei verschiedene berechtigte Personen mit „Löschbestätigung“ (Auto-Freigabe nur als Opt-in-Ausnahme, standardmäßig aus) |
| **Activity** (Zeitstrom) | **ja** — `retention_activities_days` (365) | **Hard**-Delete, kein Soft-Delete |
| **AuditLog** | **ja** — `auditlog_retention_months` (24, `0` = aus) | `prune_auditlog`; drei prune-exempte Aktionen plus `EXPORT`-**Berichts-Freigaben** (Zeitraum + herausgegebenes Artefakt, Refs #1770 — Klienten-/DSGVO-Exporte und Bildschirm-Ansichten bleiben der Frist unterworfen), Checkpoint-Tombstone fuer die HMAC-Kette |
| **Client** | **nein**, zwei *abgeleitete* Ausloeser | (a) `client_trash_days` (30) nach **manuellem** Soft-Delete → `anonymize_eligible_soft_deleted_clients`; (b) `anonymize_clients`, sobald **alle** Events der Person soft-geloescht sind. Beide Wege enden in `anonymize_client` |
| **Case, Episode, WorkItem, OutcomeGoal, Milestone** | **nein** | Kein eigener Cutoff und kein eigener Cron-Pfad. Sie haengen am **Owner-Lebenszyklus**: ihre Freitexte werden als Teil der Redaktionskaskade von `anonymize_client` getilgt (`_redact_cases_and_episodes`, `_redact_case_goals`, `_redact_workitems`) — seit #1751 inklusive Wirkungszielen und Meilensteinen. Die Zeilen selbst bleiben bestehen, nur die personenbeziehbaren Inhalte fallen |

Konsequenz fuer Betreiber und Auditor: „loeschen" heisst fuer Faelle/Episoden/Aufgaben/Ziele **Redaktion beim Anonymisieren der Person**, nicht Ablauf einer eigenen Frist. Wer fuer diese Entitaeten eine unabhaengige Frist braucht (z. B. „abgeschlossene Faelle nach X Jahren", auch wenn die Person aktiv bleibt), braucht dafuer ein eigenes Feature — es existiert heute nicht.

Beide Anonymisierungs-Pfade (hart und k-anonymisiert) fahren dieselbe Kaskade; die Paritaet ist seit #1750 durch einen ableitungsbasierten Architektur-Guard erzwungen (siehe [ADR-023 Update 2026-08-03](023-k-anonymization-statistik.md)). Legal Holds blockieren beide Bulk-Einstiege (#1756).

## Nachtrag 2026-10-10: Freigabe und geschlossene Fristlücken

Die ursprüngliche Decision zur Freigabe von `qualified` gilt im Standardlauf
wieder (#2524). Dieser
Nachtrag ersetzt die Ist-Beschreibung vom 2026-10-06 und präzisiert die Audit-
Fristkorrektur vom 2026-10-07:

- „Mit Zusatzangaben“: Standardlauf erzeugt Vorschläge und löscht erst nach
  manuelle Freigabe durch zwei verschiedene berechtigte Personen mit „Löschbestätigung“. Zurückstellen
  schützt bis zum Datum, Ablehnen erlaubt frühestens nach 365 Tagen
  (konfigurierbar) eine neue Vorlage. Auto-Freigabe bleibt Opt-in.

Den Vorschlag legt der Lauf an. Die manuelle Freigabe braucht zwei verschiedene aktive Personen der eigenen Einrichtung, jeweils mit Rolle Leitung oder Anwendungsbetreuung und dem Recht „Löschbestätigung“. Die erste Freigabe lässt den Vorschlag ausstehend (PENDING, „1 von 2 Freigaben“); erst die zweite andere Person gibt ihn endgültig frei (APPROVED). Mit nur einem berechtigten Konto bleibt es bei „1 von 2“; die erste Freigabe allein erlaubt keine Löschung. Zurückstellen, Legal Hold (auch aus der Personen-, Fall- oder Ereignisakte) und Ablehnung verwerfen die erste Freigabe mit Audit; bei einem erneuten manuellen Freigabeweg sind wieder zwei Freigaben nötig. Die Einrichtung benennt mindestens zwei berechtigte Konten. Die Compliance-Karte „Genehmiger für den Aufbewahrungslauf“ prüft Rolle und Anzahl (0 = kritisch, 1 = Warnung, mindestens 2 = ok). Die Karte „Genehmiger für Löschanträge“ zählt weiterhin aktive Löschantragsberechtigte unabhängig von ihrer Rolle. Ausnahme: Die Einrichtung kann die Auto-Freigabe nach mehrfacher Zurückstellung ausdrücklich einschalten (Opt-in, standardmäßig aus); nach der konfigurierten maximalen Zahl von Zurückstellungen und Ablauf der Wiedervorlage genehmigt der Lauf ohne zweite Person. Hold und Art. 18 schützen auch diesen Weg.
- Fristanker: Fallabschluss; ohne Fall Ereignisdatum; offen letzte fachliche
  Fallaktivität (Fallback Anlage). Fachliche Änderungen an Fall, Ereignissen,
  Aufgaben und Verlauf zählen, Umschlüsselung nicht.
- Dokumentationstyp: **Höchstfrist (verkürzt nur)**, der früheste Stichtag
  gewinnt. Kein Umgehen der Freigabe durch Typ-Frist oder einseitige
  Herabstufung bei vorhandenen Ereignissen.
- Legal Hold aus Personen-, Fall- und Ereignisakte mit Audit-Spur;
  Systemliste bietet Aufheben. Hold und Art. 18 schützen im Lauf Ereignisse,
  Personen-Anonymisierung und Historien-Redaktion. Audit-Ziele mit Einrichtung unter Hold
  bleiben erhalten, mit authentifizierten HMAC-Checkpoint-Grenzen.
- Audit-Fristen bleiben unabhängig von Fach-Fristen: 24 Monate je Einrichtung
  und 24 Monate installationsweit ohne Einrichtung, separat konfigurierbar;
  jeweils 0 = aus. Dauerhafte Ausnahmen bleiben unverändert. Lückenlose
  Historie ohne Routinebereinigung verlangt weiterhin 0; ein Hold schützt
  nur seine Zielmenge. Ein eingeschränkter Einrichtungslauf bereinigt die
  Systemkette nicht.
- Ohne Ereignis gilt die Inaktivitätsfrist `retention_identified_days` nur für „Nur Pseudonym“ ohne offene Fälle. Der Anker ist der jüngste Zeitpunkt aus Personenänderung (`updated_at`), fachlicher Fallaktivität (`last_activity_at`), Aufgabenänderung und Anhangsänderung an Person oder Fall (Anlage, Ersetzung oder Entfernung). Personen „Mit Zusatzangaben“ ohne Ereignis sind von der automatischen Anonymisierung ausgenommen; der Vorschlagsweg unterstützt bisher nur Ereignisse, für diese Personen fehlt eine automatische Fristvollstreckung. Personen „Nur Pseudonym“ ohne Ereignis mit offenem Fall sind vom Inaktivitätszweig ausgenommen. Die Einrichtung prüft ereignislose Personen „Mit Zusatzangaben“ regelmäßig und stellt bei Bedarf einen Löschantrag; das Prüfintervall hält sie im Löschkonzept fest. Maßgeblich ist [`anonymization.py`](../../src/core/retention/anonymization.py). Abgeschlossene
  Lösch-/Zusammenführungs-/Herabstufungsanträge: 1095 Tage ab Abschluss,
  konfigurierbar. Keine Bestandsmigration oder nachträgliche Altdatenbereinigung.
- Automatische und manuelle Ereignislöschung übertragen Monatszählwerte in
  derselben Transaktion. Aggregate bleiben unbefristet als statistischer
  Restbestand; sie sind keine unveränderlichen Archivkopien. Personenstatus
  und Papierkorb weisen noch offene Historienfassungen nach Anonymisierung aus.
- Keine zusätzliche Fall-, Datei- oder Übergabenotiz-Frist; Datei-Sofortlöschung
  bleibt die einzelne berechtigte Entscheidung mit Hold-/Art.-18-Prüfung.

## References

- [`src/core/retention/`](../../src/core/retention/) — Submodule (`anonymization`, `audit_pruning`, `enforcement`, `eventhistory`, `eventhistory_repair`, `legal_holds`, `proposals`, `redaction_repair`, `strategies`)
- [`src/core/services/retention.py`](../../src/core/services/retention.py) — Re-Export-Stub fuer Bestands-Aufrufer
- [`src/core/management/commands/enforce_retention.py`](../../src/core/management/commands/enforce_retention.py)
- [`src/core/dsgvo_templates/`](../../src/core/dsgvo_templates/) — Verarbeitungs-Verzeichnis und Loeschkonzept
- [`docs/ops-runbook.md` § 3 Cron-Jobs](../ops-runbook.md#3-cron-jobs)
- [ADR-007](007-auditlog-append-only.md) — AuditLog Append-Only
- [ADR-013](013-dsgvo-art16-no-selfservice.md) — DSGVO Art. 16 ohne Self-Service
- [ADR-023](023-k-anonymization-statistik.md) — k-Anonymisierung fuer externe Statistik
- [ADR-044](044-fall-loeschpfad.md) — Fall-Löschpfad und Fall-Papierkorb
- [ADR-050](050-eventhistory-retention.md) — EventHistory-Retention
- Issue #744 — Retention-Refactor
