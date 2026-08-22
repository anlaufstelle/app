# ADR-045: Duplikat-Merge für Personen — gestaffelter Antrag, Tombstone, Undo bis Fristablauf

- **Status:** Accepted
- **Date:** 2026-08-22
- **Deciders:** Tobias Nix
- **Refs:** #1876 (RFC + Umsetzung), #1818/[ADR-044](044-fall-loeschpfad.md) (Staffelungs-Blaupause P1c), #1737 (Personen-Papierkorb), #1760 (Offline-Widerruf), #1368 (Massen-Destruktions-Erkennung), #1058 (dynamische Personen-Felder), [ADR-039](039-owner-generische-dateianhaenge.md) §7 (Marker-Invariante I-M1)

## Context

Doppelt angelegte Personen sind im Alltag kaum vermeidbar: Pseudonym leicht anders geschrieben, Neuanlage nach erfolgloser Suche. Die App **warnt** beim Anlegen per Trigram-Fuzzy-Match („Ähnliche Personen", `search_similar_clients`), bietet aber keinen Weg, zwei Personen nachträglich **zusammenzuführen**. Die Duplikate leben dauerhaft weiter: Ereignisse, Fälle und Statistik verteilen sich auf zwei Akten, Zählungen über eindeutige Personen sind verfälscht.

Das RFC #1876 hat die offenen Fragen gesammelt; diese ADR schreibt die Produktentscheide fest. Träger der Umsetzung ist der Service [`services/client/merge.py`](../../src/core/services/client/merge.py) (Migration 0160).

## Decision

### 1. Vier-Augen gestaffelt wie die Löschung (ADR-044 P1c)

Ein Merge ist faktisch die Löschung des einen Pseudonyms — er erbt darum die Löschschwelle der **höher geschützten** Seite: Steht EINE der beiden Personen auf der Stufe „Mit Zusatzangaben" (`QUALIFIED`), braucht der Merge einen Antrag und die Genehmigung durch eine Zweitperson mit dem Recht `can_confirm_deletion`. Sind beide `IDENTIFIED`, darf die **Leitung** direkt zusammenführen (Bestätigungsseite, keine Zweitperson).

Träger des Antrags ist der bestehende `DeletionRequest` mit einem neuen, nullbaren FK `merge_into` — **kein** eigenes Request-Modell. Der Vier-Augen-Apparat (PENDING-Unique je Ziel, Requester≠Reviewer-Constraint, Antragsliste, Idempotenz-Muster #932, TOCTOU-`select_for_update` aus #1376) ist mit dem Löschantrag identisch; die Quelle IST `target_id`, es fehlte allein das Ziel. Ein zweites Modell hätte denselben Apparat dupliziert, und die Review-Fläche (`DeletionRequestReviewView`, dispatcht auf `target_type`) kann den Merge-Zweig als vierte Weiche aufnehmen statt einen zweiten Einstieg zu brauchen. Ein `CheckConstraint` (`deletion_request_merge_target_differs`, analog `deletion_request_different_reviewer`) hält die Invariante „Ziel ≠ Quelle" auch in der DB fest. Die generischen Service-Wege sind gegeneinander verriegelt: `approve_client_deletion`/`reject_client_deletion` weisen Anträge mit gesetztem `merge_into` ab (sonst landete die Quelle im Papierkorb, ohne dass irgendetwas wandert), entschieden wird ausschließlich über `approve_client_merge`/`reject_client_merge`.

Der partielle Unique auf (facility, target_type, target_id, PENDING) sorgt nebenbei dafür, dass pro Person immer nur **ein** destruktiver Antrag läuft — Löschung ODER Merge, nie beides; `_validate_merge` weist zusätzlich jeden Merge ab, an dessen Quelle oder Ziel bereits ein Antrag hängt.

### 2. Die Quelle wird Tombstone im Papierkorb — Undo bis Fristablauf

Die Quelle wird **nicht** gelöscht, sondern soft-gelöscht und trägt einen neuen FK `Client.merged_into` auf das Ziel — beides in derselben Transaktion wie die Datenwanderung. Es gilt die normale Papierkorb-Frist (`client_trash_days`, keine zweite Frist — dieselbe Begründung wie ADR-044 §2); nach Ablauf redigiert der **bestehende** Retention-Lauf (`anonymize_eligible_soft_deleted_clients`) den Tombstone wie jede andere Papierkorb-Person. Der `merged_into`-Verweis überlebt die Redaktion: `anonymize_client` fasst ihn nicht an, und der Audit-Trail der Quelle (append-only, `target_id` = Quelle) bleibt über den Tombstone erreichbar — die Art.-15-Antwort „wohin sind die Daten dieses Pseudonyms gewandert" bleibt beantwortbar.

`on_delete=PROTECT` statt `SET_NULL`, begründet am Retention-Verhalten: der Lauf **redigiert** Personen, er löscht keine Zeilen — PROTECT kostet dort also nie etwas. Es verhindert aber, dass ein künftiger Hard-Delete-Pfad den einzigen Verbleibs-Nachweis still kappt (SET_NULL täte genau das, lautlos). Dasselbe Argument gilt für `DeletionRequest.merge_into`.

**Undo:** `unmerge_client` stellt die Zuordnungen wieder her, solange die Quelle unredigiert im Papierkorb liegt. Das Umkehr-Inventar (welche Ereignis-/Fall-/Aufgaben-/Anhang-UUIDs wanderten, welche Slugs übernommen wurden, welche Kontaktstufe das Ziel vorher hatte) liegt im `detail` der `CLIENT_MERGED`-Audit-Zeile — bewusst **derselbe** Ort wie die Rechenschafts-Spur: WAS wanderte und WAS zurückzunehmen ist, ist dieselbe Information; ein zweiter Speicherort könnte nur divergieren. Das Undo ist ein Minimal-Undo mit dokumentierten Grenzen: nach dem Merge am Ziel neu entstandene Ereignisse bleiben dort (außer sie hängen an einem zurückwandernden Fall — die Invariante `Event.client == Event.case.client` geht vor); die deduplizierte Merkliste (`RecentClientVisit`) wird nicht rekonstruiert; Ziel-Edits an übernommenen Feldern gehen mit deren Entfernen verloren. Nur die Leitung darf aufheben (die Stufen-Rückstufung des Ziels wäre sonst ein N2-Hebel), und `restore_client` weist Merge-Tombstones ab — ein bloßes Restore ließe die Quelle ohne ihre gewanderten Daten auferstehen (Duplikat UND Datenverlust in einem). Nach der Redaktion ist nichts mehr umkehrbar; der Versuch wird abgewiesen. **Ketten-Merges sind im Undo-Fenster gesperrt:** Wer selbst einen unredigierten Tombstone trägt (A→B liegt im Undo-Fenster), darf nicht Quelle eines weiteren Merges sein (B→C) — sonst liefe `unmerge_client(A)` fehlerfrei ins Leere (die Inventar-Updates filtern auf `client=target`, die Daten liegen aber längst bei C) und stellte A als leeres, aktives Duplikat wieder her. Erst A aufheben oder die Redaktion abwarten; das **Ziel** eines Merges darf dagegen Tombstones tragen (LIFO-Undo trägt nachweislich). `unmerge_client` prüft den Ziel-Zustand zusätzlich selbst und weist ein seinerseits zusammengeführtes Ziel ab (Abwehr in der Tiefe für Alt-/Reparaturzustände).

### 3. Kontaktstufe: höchste gewinnt; Zusatzangaben füllen nur Lücken

Das Ziel landet auf `QUALIFIED`, sobald eine der beiden Personen dort war. Alles andere wäre eine **verdeckte Herabstufung** — genau die verbietet `update_client` für Nicht-Leitung (Security N2), und sie höbe die Vier-Augen-Eintrittsbedingung der Ereignis-Löschung aus. Die Hochstufung wird als reguläre `STAGE_CHANGE`-Zeile am Ziel protokolliert.

Bei `data_json`-Konflikten behält das Ziel seine Werte; Quelle-Werte werden **nur für leere Ziel-Felder** übernommen (fehlender Dokumentationstyp zählt als leeres Feld und wird mit übernommen). Alles läuft durch die bestehende Whitelist (`_clean_client_dynamic_data`): Slugs, die der effektive Typ nicht kennt, fallen weg; verschlüsselte Werte wandern als Token (gleiche Einrichtung = gleicher Schlüssel). Das Audit-`detail` dokumentiert **welche Slugs** übernommen wurden — nie Werte. Für übernommene Datei-Slugs wandern die im Marker referenzierten `Attachment`-Zeilen mit (Marker-Invariante I-M1, ADR-039 §7); nicht übernommene Datei-Werte bleiben bei der Quelle und fallen mit deren Redaktion — „Ziel behält seine Werte", auf Dateien angewandt.

### 4. Datenwanderung: alle sechs eingehenden FKs, drei FK-lose Verweise

| Verweis | Verhalten | Warum |
|---|---|---|
| `Case.client` (PROTECT) | wandert | Fälle gehören der Person. ERST Fälle, DANN Ereignisse, Konsistenz-Check am Transaktionsende — die Invariante `Event.client == Event.case.client` (`services/events/crud.py`) darf nie halb committen. |
| `Event.client` (SET_NULL) | wandert | inklusive soft-gelöschter — der Papierkorb eines Ereignisses folgt seiner Person. |
| `WorkItem.client` (SET_NULL) | wandert | Aufgaben zur Person bleiben relevant. |
| `RecentClientVisit.client` (CASCADE) | wandert mit Dedup | `unique_together (user, client)`: hat dieselbe Nutzerin beide besucht, gewinnt das jüngste `visited_at`, `is_favorite` per OR; durchweg `QuerySet.update()`, weil `visited_at` `auto_now` ist. |
| `Attachment.client` (CASCADE) | teilweise | nur Anhänge übernommener Datei-Slugs, präzise über die Marker-Entry-IDs (§3). |
| `RectificationRequest.target_client` (CASCADE) | bleibt | Ein Art.-16-Antrag dokumentiert Aussagen über die Werte **genau dieses** Datensatzes; umgehängt trüge er Behauptungen an eine Akte, gegen die er nie gestellt wurde (zudem kollidierte der partielle Unique auf (facility, target_client, field_path, PENDING)). Der Löschpfad lässt sie identisch stehen; die Redaktion des Tombstones leert die Werte. |
| `OfflineClientRevocation.client_pk` (kein FK) | Quelle wird widerrufen | personen-genauer Widerruf (#1760): Geräte purgen das Quell-Bundle. |
| `DeletionRequest.target_id` / `LegalHold.target_id` / AuditLog-`target_id` (kein FK) | bleiben | append-only-Protokoll und Antrags-/Hold-Metadaten sind über den Tombstone erreichbar (§2). Konflikte werden stattdessen **vorab** blockiert: PENDING-Antrag oder aktiver Legal Hold auf einer der beiden Personen weist den Merge ab. |

Der Legal-Hold-Guard ist bewusst strenger als bei den Soft-Delete-Pfaden (die prüfen Holds erst bei der Tilgung): ein Hold friert eine Akte als Beweismittel ein, der Merge strukturiert **beide** Akten um — und auf der Quellseite verlöre ein Personen-Hold nach der Wanderung sogar still die Deckung über deren Ereignisse (`client_has_active_hold` findet sie nicht mehr). Spoliations-Schutz analog #1066.

### 5. Audit-Vokabular: `CLIENT_MERGED`, breach-neutral

Eine neue Aktion `CLIENT_MERGED` mit `target_obj` = **Quelle** (das Spiegelbild von `CLIENT_SOFT_DELETED`: die Zeile dokumentiert das Schicksal der Person, die aus dem Bestand geht). Zwingend **getrennt** von `CLIENT_SOFT_DELETED`, und die Genehmigung schreibt bewusst **kein** `DELETION_APPROVED`: beide Aktionen zählt `detect_mass_client_destruction` ab 20 im Kurzfenster — eine Aufräum-Kampagne über Alt-Duplikate ist Konsolidieren, kein Vernichten, und darf keinen Breach-Alarm auslösen. Der Merge-Soft-Delete der Quelle läuft über die Mixin-Methode und schreibt selbst keine Audit-Zeile; damit trifft nichts am Merge die Zählung — die spätere Tilgung des Tombstones läuft regulär als `CLIENT_ANONYMIZED` in den Zähler. Die Vier-Augen-Evidenz liegt in der `DeletionRequest`-Zeile plus `deletion_request` im `detail` (dasselbe Muster wie der Personen-Löschpfad, der ebenfalls nur die Domänen-Zeile schreibt). Antrag und Ablehnung nutzen die generischen `DELETION_REQUESTED`/`DELETION_REJECTED` (nicht gezählt). `CLIENT_MERGED` steht **nicht** auf der Kritisch-Liste von Compliance-Check #11 — derselbe Grund wie bei den Lösch-Trias-Aktionen: der vorgesehene Ablauf ist kein Vorfall.

Das Undo schreibt `CLIENT_RESTORED` an der Quelle mit `unmerged_from` im `detail` — kein neues Vokabular für einen Vorgang, der fachlich ein Wiederherstellen ist.

Kein Pseudonym im `detail` und keins der Quelle im Zeitstrom: die Aktivitäts-Zeile hängt am **Ziel** und nennt nur dessen Pseudonym — sie wird damit von der Redaktion des Ziels erfasst; das Quell-Pseudonym stünde dort länger als die Quelle selbst (Befund #1751).

### 6. Abgrenzung zu den bestehenden Guards

Der Merge-Service ist der **dritte** `soft_delete()`-Aufrufer im Produktionscode; der abgeleitete Guard in `test_cases_trash_invariant.py` pinnt ihn (die Soll-Menge wächst — exakt der Mechanismus, für den der Guard gebaut wurde). Unter den Redaktions-Paritäts-Guard (`TestKAnonBridgeRedactionParity`) fällt er dagegen **nicht**, obwohl er dieselbe Tabellenmenge berührt: der Merge redigiert nichts, er verschiebt Zuordnungen — die Tilgung des Tombstones läuft durch `anonymize_client` selbst, und genau dessen Helfer-Menge pinnt der Guard bereits.

## Bewusst in Kauf genommene Risiken

- **Statistik-Diskontinuität.** `Count('client', distinct=True)` sinkt nach einem Merge **rückwirkend** für Live-Abfragen; eingefrorene `StatisticsSnapshot`-Zeilen behalten die Vor-Merge-Zahl. Ein bereits abgegebener Sachbericht wird damit unreproduzierbar. Entscheid: Warnung im Merge-Dialog (folgt mit den Views), **keine** Rückrechnung — eine „korrigierte" Historie wäre eine zweite Wahrheit neben den Snapshots.
- **k-Anonymität.** Dieselbe Personenzahl speist `_suppress_small` — ein Merge kann eine Berichtszeile unter die Schwelle drücken. Das ist die richtige Richtung (weniger Personen ⇒ mehr Suppression), aber eine sichtbare Veränderung bestehender Berichte.
- **Offline-Randfall.** Bereits gequeuete Offline-Ereignisse zur Quelle laufen nach dem Merge in die Dead-Letter-UI (`CLIENT_WRITE_REVOKED`) — dasselbe Verhalten wie bei Löschungen. Bewusst **kein** Replay-Remap auf das Ziel: der Server schriebe dann im Namen der Nutzerin auf eine Person, die deren Gerät nie gesehen hat; die Dead-Letter-UI macht den Konflikt sichtbar statt ihn still aufzulösen.
- **Undo ist ein Minimal-Undo** (§2): Merkliste nicht rekonstruiert, Ziel-Edits an übernommenen Feldern gehen beim Aufheben verloren, nach der Redaktion ist nichts mehr umkehrbar.

## Alternatives considered

- **Eigenes MergeRequest-Modell** statt `DeletionRequest.merge_into`: dupliziert PENDING-Unique, Reviewer-Constraint, Antragsliste und Review-View für identische Semantik — verworfen (§1).
- **`SET_NULL` für `merged_into`**: einziger Unterschied zu PROTECT ist das Verhalten bei einem Hard-Delete des Ziels — und genau dort soll der Verbleibs-Nachweis **nicht** lautlos verschwinden; da Retention redigiert statt löscht, ist PROTECT kostenlos — verworfen (§2).
- **`CLIENT_SOFT_DELETED` für den Merge-Soft-Delete** (oder `DELETION_APPROVED` bei der Genehmigung): flutete `detect_mass_client_destruction` bei Aufräum-Kampagnen mit Fehlalarmen; die Trennung nach Handlungs-Semantik folgt dem Muster `OFFLINE_BUNDLE_READ` vs. `EXPORT` (#1410) — verworfen (§5).
- **Berichtigungsanträge mit umhängen**: trägt Wert-Behauptungen an eine fremde Akte und kollidiert mit dem partiellen Unique — verworfen (§4).
- **Replay-Remap gequeueter Offline-Ereignisse auf das Ziel**: stille Schreibzugriffe im Namen der Nutzerin auf eine ihr unbekannte Akte — verworfen (Risiken).
- **Voll-Undo mit eigenem Inventar-Modell**: zweiter Speicherort für Information, die die Audit-Zeile ohnehin tragen muss; Divergenz-Risiko ohne Zusatznutzen im 30-Tage-Fenster — verworfen zugunsten des Inventars im `CLIENT_MERGED`-`detail` (§2).
- **Minimalvariante „nur Ereignisse umhängen + Quelle in den Papierkorb"** (RFC-Frage 5): ließe Fälle, Aufgaben und Merkliste an der Quelle zurück und bräche die Invariante `Event.client == Event.case.client`, sobald ein Ereignis eines Quell-Falls wandert — der Voll-Merge ist mit dem Inventar-Ansatz kaum teurer und hinterlässt keinen halben Zustand.

## References

- Umsetzungs-Issue: #1876
- Service: [`services/client/merge.py`](../../src/core/services/client/merge.py); Modell/Migration: [`models/client.py`](../../src/core/models/client.py), [`models/workitem.py`](../../src/core/models/workitem.py), Migration 0160
- Blaupausen: [ADR-044](044-fall-loeschpfad.md) (Staffelung P1c), [`services/client/main.py`](../../src/core/services/client/main.py) (`request_client_deletion` … `approve_client_deletion`)
- Tests: `src/tests/test_client_merge_workflow.py`; Guard-Pins in `src/tests/test_cases_trash_invariant.py`
