# ADR-050: EventHistory-Retention — Redaktion nach Ereignis-Lebenszyklus statt Partitionierung

- **Status:** Accepted
- **Date:** 2026-08-27 (entschieden 2026-08-28)
- **Deciders:** Tobias Nix, 2026-08-28
- **Refs:** #2065 (dieses Design), #2000 (Sammel-Analyse,), [ADR-021](021-retention-modell.md) (Fristen, Legal Hold, AuditLog-Pruning), [ADR-007](007-auditlog-append-only.md) (Append-only-Politik), [ADR-033](033-runtime-owner-role-separation.md) (Runtime-/Owner-Trennung — begrenzt, wer redigieren darf), [ADR-044](044-fall-loeschpfad.md) §3 (Redaktion statt Hard-Delete als Hausmuster), #714 (redigierte Lösch-Historie), #905 (Art.-17-Redaktion der Historie), #1026 (`ENABLE ALWAYS`, Owner-DDL-Bypass), #783 (Key-Rotation der Historie)

## Context

[`EventHistory`](../../src/core/models/event_history.py) hat **keine Frist**: keinen Cutoff, keinen Cron-Pfad und keinen Owner-Lebenszyklus im Sinne von [ADR-021](021-retention-modell.md). Sie wächst mit jeder Speicherung eines Ereignisses und schrumpft nie. Sie ist damit nicht die einzige fristlos wachsende Tabelle — `StatisticsSnapshot` (monatlich je Einrichtung) und abgeschlossene `ImportJob`-Zeilen kennen ebenfalls kein Pruning; beide tragen aber Aggregate bzw. Vorgangsmetadaten. `EventHistory` ist die einzige fristlose Tabelle, deren Zeilen **Beratungsinhalte im Volltext** führen. Die folgende Bestandsaufnahme ist am Code erhoben, nicht geschätzt.

### 1. Was pro Speicherung geschrieben wird

Drei Schreiber, alle in [`services/events/crud.py`](../../src/core/services/events/crud.py) — `create_event` (Zeile 183), `update_event` (Zeile 264), `soft_delete_event` (Zeile 373) — plus ein vierter im Retention-Lauf ([`retention/enforcement.py:99`](../../src/core/retention/enforcement.py)). Jede Zeile trägt:

| Spalte | Inhalt | Größe (Beispielrechnung §2) |
|---|---|---|
| `data_before` | bei `update` das **vollständige alte** `data_json`, bei `delete` nur der redigierte Marker `{"_redacted": true, "fields": [...]}` ([`build_redacted_delete_history`](../../src/core/services/events/fields.py)) | 0 / ~1,3 KB / ~0,3 KB |
| `data_after` | bei `create` und `update` das **vollständige neue** `data_json` | ~1,3 KB |
| `field_metadata` | `_snapshot_field_metadata(document_type)` — Name, Sensitivität und Verschlüsselungsstatus **aller** Felder des Dokumenttyps, unabhängig davon, welches Feld sich geändert hat | ~1,3 KB |

Zwei Eigenschaften dieses Formats treiben die Kosten stärker als die Zeilenzahl:

- **Der Aufwand ist von der Größe der Änderung entkoppelt.** Die Korrektur eines Tippfehlers in einem von fünfzehn Feldern schreibt beide Fassungen des gesamten Datensatzes plus den vollen Feld-Snapshot. Es gibt kein Feld-Delta — der Diff wird erst bei der *Anzeige* berechnet ([`history_diff.py`](../../src/core/services/events/history_diff.py)).
- **Die Zeile fällt auch ohne Delta an.** `update_event` schreibt die `EventHistory`-Zeile **unbedingt**; nur der AuditLog-Eintrag ist an `changed_slugs` gebunden (Kommentar in `crud.py`, Refs #1958). Ein Formular-Submit ohne Änderung erzeugt also eine volle Historienzeile und **keinen** Audit-Eintrag. Das ist bewusst so (die Historie ist der fachliche Bearbeitungsverlauf, das AuditLog der minimierte Rechenschaftsnachweis) — es heißt aber, dass die Historie die schlechter kontrollierte der beiden Mengen ist.

### 2. Quantifizierung

Rechnung für eine mittlere Beratungsstelle, Annahmen offengelegt: 40 Ereignisse je Werktag (10.000/Jahr), Dokumenttyp mit 15 Feldern, davon 8 verschlüsselt, mittlere Klartextlänge 40 Zeichen, im Mittel 0,5 Bearbeitungen je Ereignis über dessen Lebenszeit.

Ein Fernet-Token ist `base64(1 + 8 + 16 + ciphertext + 32)` Byte; 40 Zeichen Klartext ergeben mit CBC-Padding 48 Byte Ciphertext, also 105 Byte Token → **140 Byte base64** je verschlüsseltem Feld (Faktor 3,5 gegenüber dem Klartext). Damit:

- `data_json` ≈ 8 × 140 B + 7 × 30 B ≈ **1,3 KB**
- `field_metadata` ≈ 15 × ~85 B ≈ **1,3 KB** — der Metadaten-Snapshot ist so groß wie die Nutzlast selbst
- CREATE-Zeile ≈ 2,6 KB · UPDATE-Zeile ≈ 4,0 KB · DELETE-Zeile ≈ 1,6 KB

**Zeilen.** Je Ereignis 1 (CREATE) + *u* (UPDATE) + 1 (DELETE, sobald die Retention-Frist greift). Vor dem ersten Fristablauf ist das die im Ticket genannte Spanne von 1,3–2× der Ereigniszahl; **danach ist die Untergrenze 2× und der Erwartungswert hier 2,5×** — die Löschung *erhöht* die Zeilenzahl. Bei 10.000 Ereignissen/Jahr sind das ~25.000 Historienzeilen/Jahr, nach fünf Jahren ~125.000.

**Bytes.** 2,6 + 0,5 × 4,0 + 1,6 ≈ **6,2 KB je Ereignis** gegen 1,3 KB im `Event` selbst — Faktor ~4,7, am oberen Rand der Ticket-Schätzung (2–4×). Nach fünf Jahren ~310 MB in `core_eventhistory`. **Kompression rettet das nicht:** jeder Fernet-Token ist bei jeder Verschlüsselung neu (eigener IV), identischer Klartext ergibt also verschiedene Bytes. Weder pglz/lz4 innerhalb der Zeile (`data_before` vs. `data_after`) noch über Zeilen hinweg kann die Wiederholung zusammenfalten; komprimierbar ist praktisch nur `field_metadata`.

**Der ehrliche Befund:** 310 MB nach fünf Jahren sind für keinen Selbsthoster ein Kostenproblem. Der Speicherplatz ist **nicht** die Dringlichkeit — die Ticket-Prämisse „Kosten vor Ende Jahr 2 real" trägt in dieser Form nicht. Dringlich sind zwei andere Größen, die mit derselben Zeilenzahl skalieren: die Aufbewahrung von Inhalten **über die Frist des Ereignisses hinaus** (§4) und die Kosten der Schlüsselrotation (§5). Diese Verschiebung bestimmt die Optionswahl: Partitionierung beantwortet das Problem, das wir *nicht* haben.

### 3. Append-only ist doppelt verriegelt — und hat einen legitimen Wartungspfad

Die Unveränderlichkeit ist Sicherheitsarchitektur, nicht Konvention: ORM-seitig verweigern `save()`/`delete()` ([`event_history.py:59-67`](../../src/core/models/event_history.py)) jedes Update und jede Löschung, DB-seitig blocken die Trigger `eventhistory_no_update` und `eventhistory_no_delete` aus Migration [`0012`](../../src/core/migrations/0012_eventhistory_append_only_trigger.py), seit Migration [`0107`](../../src/core/migrations/0107_append_only_triggers_enable_always.py) im Modus `ENABLE ALWAYS` — sie feuern auch unter `session_replication_role = replica`.

Es existiert genau **ein** legitimer Umgehungspfad: [`bypass_replication_triggers`](../../src/core/services/system/_db_admin.py) — Owner-DDL `ALTER TABLE … DISABLE TRIGGER` plus exakte Wiederherstellung des Vorzustands aus `pg_trigger.tgenabled`, alles in **einer** `transaction.atomic()`, sodass ein Prozessabbruch das DISABLE mit zurückrollt. Vier Aufrufer nutzen ihn heute: `prune_auditlog`, `Client.anonymize()`, `backfill_audit_chain`, `reencrypt_fields`. Vier Eigenschaften sind für jede Retention-Option tragend:

- Der Helfer schaltet **alle drei** Append-only-Trigger ab (`_APPEND_ONLY_TRIGGERS`), also auch `auditlog_immutable` — wer die Historie anfasst, öffnet für die Dauer des Blocks auch das AuditLog.
- `ALTER TABLE … DISABLE/ENABLE TRIGGER` nimmt einen **`SHARE ROW EXCLUSIVE`**-Lock (PostgreSQL 18): Leser laufen weiter, konkurrierende **Schreiber** auf `core_eventhistory` und `core_auditlog` warten bis zum Commit des Blocks. Die Blockgröße entscheidet also über die Zeit, in der die Anwendung keine Audit- und Historien-Zeilen schreiben kann.
- **Die Sperr-Reihenfolge des Helfers ist der Schreib-Reihenfolge der Anwendung entgegengesetzt.** `_APPEND_ONLY_TRIGGERS` führt `core_auditlog` **zuerst** und `core_eventhistory` danach; ein gewöhnlicher Ereignis-Schreiber macht es umgekehrt — `create_event`/`update_event` schreiben in derselben Transaktion erst die `EventHistory`-Zeile und (ausdrücklich als letzten Schritt, Refs #1345/#1956) den AuditLog-Eintrag. Da `SHARE ROW EXCLUSIVE` mit dem `ROW EXCLUSIVE` der Schreiber kollidiert, ist das ein klassischer ABBA-Zyklus: Bypass hält `core_auditlog` und wartet auf `core_eventhistory`, der Request hält `core_eventhistory` und wartet auf `core_auditlog`. PostgreSQL erkennt das und bricht **eine** der beiden Seiten mit `deadlock detected` ab — es ist also **kein bloßes Warten**. Der Befund gilt schon heute für `prune_auditlog` und `reencrypt_fields`; ein nächtlicher Historien-Sweep macht ihn zur Routine (D5.5).
- Der Helfer stellt `tgenabled` **exakt** wieder her (`'A'` → `ENABLE ALWAYS`). Jeder Pfad, der stattdessen ein pauschales `ENABLE TRIGGER` schreibt, degradiert den gehärteten Zustand still auf den Origin-Modus und öffnet den `replica`-Vektor aus #1026 wieder.

Die Frage für jede Option ist damit nicht „darf man Zeilen ändern" — das tun heute schon zwei DSGVO-Pfade, sondern: **wer, wie oft, wie lange und mit welchem Nachweis** die Garantie öffnet.

### 4. Wer den Bypass überhaupt ausführen darf

Diese Randbedingung begrenzt den Entwurf stärker als alles andere und ist deshalb eigens festgehalten. Die Runtime-App-Rolle ist **nicht Owner** der Tabellen (`NOSUPERUSER NOBYPASSRLS`, nur DML — [`deploy/postgres-init/01-app-role.sh`](../../deploy/postgres-init/01-app-role.sh), [ADR-033](033-runtime-owner-role-separation.md)); ein Postgres-Test pinnt, dass sie die Append-only-Trigger nicht abschalten kann (`test_non_owner_runtime_role_cannot_disable_append_only_triggers` in [`src/tests/test_rls.py`](../../src/tests/test_rls.py), Assertion auf `must be owner`). Der [`ops-runbook`](../ops-runbook.md) § 9.5 hält dieselbe Konsequenz betrieblich fest: `enforce_retention`, `backfill_audit_chain`, `reencrypt_fields` und `load_package` **müssen** als Admin-/Owner-Rolle laufen, sonst brechen sie fail-loud ab.

**Daraus folgt: keine Redaktion im Request-Pfad.** Ein Aufruf des Bypasses aus einer View würde unter dem Drei-Rollen-Modell mit `must be owner` scheitern und — weil er in der Transaktion des Vorgangs läge — den Vorgang selbst zurückrollen. Jede Redaktion muss deshalb dort ausgeführt werden, wo heute schon `enforce_retention` läuft: im owner-seitigen `maintenance`-Service.

**Bestandsbefund am Rande, aber vor D2 zu klären:** `ClientAnonymizeView` ([`views/client_deletion.py`](../../src/core/views/client_deletion.py)) ruft `anonymize_client_now` → `anonymize_client` → `_redact_event_history` → `bypass_replication_triggers` **aus dem Webprozess**. Entweder ist die sofortige Admin-Tilgung unter dem Drei-Rollen-Modell schon heute defekt, oder Deployments fahren den Webprozess entgegen der Rollen-Tabelle als Admin-Rolle. Beides gehört geprüft und verticketet; diese ADR setzt keinen der beiden Fälle voraus, sondern baut so, dass sie ohne Bypass im Request auskommt.

### 5. Was heute redigiert wird — und die Lücke, die bleibt

Zwei Redaktionen existieren, beide inhaltlich richtig, zusammen aber nicht deckend:

| Pfad | Wirkung | Marker |
|---|---|---|
| Löschung eines Ereignisses (`soft_delete_event` **und** `_soft_delete_events`) | die **neue** DELETE-Zeile trägt nur Feldnamen | `{"_redacted": true, "fields": [...]}` (Refs #714, Bestand nachgezogen von Migration [`0074`](../../src/core/migrations/0074_redact_legacy_eventhistory_delete.py)) |
| Anonymisierung der Person (`anonymize_client` → [`_redact_event_history`](../../src/core/services/client/main.py)) | **alle** Zeilen aller Ereignisse dieser Person werden inhaltsleer | `{"_redacted": true, "anonymized": true}` |

Der zweite Pfad hat drei Einstiege: `anonymize_clients` (alle Ereignisse der Person sind soft-gelöscht), `anonymize_eligible_soft_deleted_clients` (Papierkorb-Frist `client_trash_days`) und `anonymize_client_now` (sofortige Admin-Tilgung aus dem Papierkorb). Die letzten beiden greifen **auch bei noch lebenden Ereignissen** — die Person muss dafür aber im Papierkorb liegen. Dazwischen liegt die Lücke, und sie ist der eigentliche Anlass dieser ADR:

> **Der Fristablauf eines Ereignisses leert das Ereignis, nicht seine Historie.** `_soft_delete_events` setzt `data_json = {}` und schreibt eine redigierte DELETE-Zeile — die **vorherigen** CREATE- und UPDATE-Zeilen desselben Ereignisses behalten ihre vollständigen Inhalte. Redigiert werden sie nur, wenn die **ganze Person** anonymisiert wird, und dafür muss sie entweder gar keine lebenden Ereignisse mehr haben oder selbst im Papierkorb liegen.

Konkret: eine Person in laufender Beratung, deren drei Jahre alte Ereignisse die `retention_identified_days`-Frist gerissen haben, hat für diese Ereignisse eine leere `Event`-Zeile — und in `EventHistory` weiterhin jede jemals gespeicherte Fassung. Die Frist ist formal vollzogen und faktisch unterlaufen. Genau dieses Muster war bei #714 schon einmal der Blocker; es ist damals nur für die *neu geschriebene* Zeile geschlossen worden.

**Zweiter Zweig derselben Lücke:** Ereignisse ohne Person (`is_anonymous`, `client_id IS NULL`) haben **überhaupt keinen** Redaktionspfad — es gibt keine Person, die anonymisiert werden könnte. Ihre CREATE-Zeile überlebt die Löschung des Ereignisses dauerhaft. Die Nutzlast ist über `min_contact_stage` datenminimiert, aber nicht leer (Freitextfelder sind möglich).

### 6. Key-Rotation: die Kosten, die wirklich weh tun

[`_reencrypt_event_history`](../../src/core/services/file_vault/rotation.py) iteriert **jede** Zeile der Tabelle, entschlüsselt und verschlüsselt jeden Wert in `data_before` und `data_after` neu und schreibt sie — **je Zeile** — in einer eigenen `transaction.atomic()` mit `bypass_replication_triggers()` zurück. Pro geänderter Zeile sind das zwei Katalogabfragen, drei `DISABLE TRIGGER` und drei `ENABLE …TRIGGER`.

Die Schleife läuft über **alle** Zeilen, öffnet den Bypass aber nur für die, die tatsächlich Ciphertext tragen: liefert `_reencrypt_data_json` für beide Spalten `changed = False`, wird die Zeile übersprungen. Von den 125.000 Zeilen aus §2 sind das die 50.000 DELETE-Zeilen, die schon heute nur den redigierten Marker führen. Es bleiben ~75.000 zu rotierende Zeilen und damit **~450.000 DDL-Statements und ~75.000 Sperrintervalle je Tabelle** — je rotierter Zeile eines auf `core_eventhistory` und `core_auditlog`, gehalten bis zum Commit der Zeilen-Transaktion. Jedes einzelne ist kurz; in Summe blockiert der Lauf die schreibenden Pfade beider Tabellen wiederholt und riskiert dabei den Deadlock aus §3. Wie lang das Wartungsfenster tatsächlich wird, ist zu **messen** und nicht aus der Statementzahl abzuleiten; belastbar ist die Skalierung: Aufwand und Sperrintervalle wachsen **linear mit der Zahl der Zeilen, die noch Ciphertext tragen**. Redigierte Zeilen fallen aus der Schleife heraus, weil `_reencrypt_data_json` ohne verschlüsselte Werte `changed = False` liefert und gar kein UPDATE ausgelöst wird.

### 7. Wer die Historie liest

- **Ereignis-Detail:** [`_build_history_with_slug_info`](../../src/core/services/events/context.py) lädt `event.history` **vollständig und ungebremst** (kein Limit, keine Paginierung) und rendert jede Fassung über `compute_diff`.
- **Auskunft nach Art. 15:** [`_gather_event_history`](../../src/core/services/client/export.py) serialisiert alle Zeilen aller Ereignisse der Person; `_serialize_history_payload` kennt den `_redacted`-Marker bereits und gibt dann `{slug, label, value: None}` aus (Refs #1752). Das Top-Level-Merkmal `redacted` des Eintrags wird allerdings **ausschließlich aus `data_before`** abgeleitet — siehe D1.
- **Django-Admin (lesend):** `EventHistoryAdmin` ([`admin/events.py`](../../src/core/admin/events.py)) zeigt `data_before`/`data_after` als `readonly_fields` an — ohne Diff-Logik, ohne Sensitivitäts-Maskierung, protokolliert über `AdminReadAuditMixin`. Eine Redaktion wirkt hier automatisch mit; eine Anzeige-Sonderbehandlung braucht es nicht.
- **Nicht** gelesen wird die Historie von Statistik, Reporting, Offline-Bundle oder Datenmitnahme. Es gibt **keinen** eingehenden Fremdschlüssel auf `core_eventhistory`.
- RLS läuft über ein Join-Subselect auf `core_event` (Migration [`0047`](../../src/core/migrations/0047_postgres_rls_setup.py)), nicht über eine eigene `facility_id`-Spalte.
- Es gibt **keinen Index auf `changed_at`** (Migration [`0006`](../../src/core/migrations/0006_event_eventhistory.py) legt Indizes nur auf `core_event`). Jede altersbasierte Abfrage ist heute ein Seq-Scan.

## Abwägung der drei Optionen

| | (a) CREATE + letzte N behalten | (b) Alternde Zeilen redigieren | (c) Zeitpartitionierung + Cold Storage |
|---|---|---|---|
| Mechanik | `DELETE` alter UPDATE-Zeilen | `UPDATE` auf den `_redacted`-Marker | `DROP`/`DETACH PARTITION` |
| Append-only | Trigger-Bypass, Zeile **verschwindet** | Trigger-Bypass, Zeile **bleibt** | **kein** Bypass nötig |
| Byte-Ersparnis je UPDATE-Zeile | 4,0 KB (100 %) | ~2,1 KB (~53 %) | 100 % der Partition |
| Schließt §5-Lücke | nur teilweise | ja, direkt | nein |
| Senkt Rotationskosten | ja | ja | nur für ausgelagerte Partitionen |
| Aufwand Selbsthoster | keiner (Cron) | keiner (Cron) | **hoch** (Partitionspflege, externer Ablageort) |
| Art. 15 / Art. 17 danach | Zeile fehlt in der Auskunft | Zeile bleibt, Wert `None` | ausgelagerte Daten **außerhalb** beider Pfade |

### (a) CREATE + die letzten N Bearbeitungen behalten, ältere Zeilen löschen

**Audit-Wirkung.** Die Zeile verschwindet — mit ihr die Tatsache, *dass* an diesem Tag von dieser Person gespeichert wurde. Für die Dauer von `auditlog_retention_months` (Default 24, `0` schaltet das Pruning ab) fängt das AuditLog diese Tatsache noch (`EVENT_UPDATE` mit `changed_fields`), danach ist sie fort. Und für Speicherungen **ohne** Delta gibt es überhaupt keinen Audit-Eintrag (§1) — deren Spur wäre sofort und vollständig weg. Das Löschen entfernt also mehr als die Nutzdaten: es entfernt die Chronologie.

**Ertrag — ehrlich gerechnet.** Option (b) behält Zeilenkopf, Slug-Listen **und** den vollen `field_metadata`-Snapshot. Nach den Größen aus §2 bleiben je redigierter UPDATE-Zeile ~1,9 KB stehen (zwei Slug-Listen à ~0,3 KB plus 1,3 KB Metadaten) und je CREATE-Zeile ~1,6 KB. Löschen spart diesen Rest zusätzlich — also **~47 % einer UPDATE- und ~62 % einer CREATE-Zeile** gegenüber der Redaktion, in der Größenordnung einiger zehn MB pro Jahr bei 25.000 Zeilen/Jahr. Der Mehrertrag ist real und deutlich; er ist nach §2 nur **nicht die bindende Größe** — und er wird mit der Chronologie bezahlt, die nach Ablauf der Audit-Frist nirgendwo sonst mehr steht und für delta-freie Speicherungen von vornherein nirgends stand. Der frühere Entwurf dieser ADR hat den Rest fälschlich auf den Zeilenkopf verkürzt und den Vorsprung mit „unter 5 %" beziffert; das war falsch, und die Empfehlung stützt sich ausdrücklich **nicht** auf dieses Argument.

**Betrieb/Migration.** „Alle außer den letzten N je Ereignis" braucht eine Fensterfunktion je Ereignis über eine Tabelle unter RLS-Join-Subselect; ein `changed_at`-Cutoff wäre ein einfacher Bereichsscan. N ist außerdem die falsche Achse: eine 2019 zweimal bearbeitete Akte behält ihre Werte für immer, weil sie nie ein drittes Mal bearbeitet wurde. N kennt keine Frist — und Fristen sind das, was Art. 5 Abs. 1 lit. e verlangt.

**Wechselwirkungen.** Art. 15: die Auskunft würde stillschweigend unvollständig; heute weist sie über den Marker aus, dass eine Fassung existierte. Art. 17: unproblematisch (gelöscht ist gelöscht). Key-Rotation: entlastet.

### (b) `data_before`/`data_after` alternder Zeilen redigieren

**Audit-Wirkung.** Die Zeile bleibt mit `changed_by`, `changed_at`, `action` und den Feldnamen erhalten; nur die Werte fallen. Das ist exakt das Hausmuster, das bereits dreimal gewählt wurde: für die Lösch-Historie (#714), für die Art.-17-Kaskade (#905) und für den Fall-Löschpfad ([ADR-044](044-fall-loeschpfad.md) §3: „Nach Fristablauf wird redigiert, nicht gelöscht"). Die Append-only-Garantie wird nicht aufgehoben, sondern auf dieselbe Weise kontrolliert geöffnet wie heute schon — mit dem Unterschied, dass es künftig **nächtlich** geschieht (D5) und dass die **Chronologie unbefristet stehen bleibt**, was eine eigene Entscheidung verlangt (D8).

**Ertrag.** Es fallen die Fernet-Tokens — der einzige nennenswerte und der einzige unkomprimierbare Anteil. Von 4,0 KB bleiben ~1,9 KB, von 2,6 KB bleiben ~1,6 KB. Der Rest ist zu zwei Dritteln `field_metadata`; wie viel davon Entscheid M-3 noch abträgt, hängt davon ab, wie viele Felder des Dokumenttyps tatsächlich befüllt sind (bei voll ausgefülltem Typ: wenig).

**Betrieb.** Ein Cutoff-Vergleich auf `changed_at` plus ein Index; keine neue Ops-Fläche, kein neuer Ablageort, keine neue Wiederherstellungs-Prozedur. Läuft im bestehenden owner-seitigen `maintenance`-Service (§4) — die einzige Option, die das Betriebshandbuch der Selbsthoster nicht verlängert.

**Key-Rotation.** Der stärkste Nebeneffekt: redigierte Zeilen enthalten keinen Ciphertext, `_reencrypt_data_json` meldet `changed = False`, die Zeile löst **kein** UPDATE und **keinen** Trigger-Bypass aus. Die Rotationskosten werden proportional zum *unredigierten Fenster* statt zur Tabelle — sie werden konstant statt linear wachsend.

**Migrationspfad.** Vorbild existiert: Migration [`0074`](../../src/core/migrations/0074_redact_legacy_eventhistory_delete.py) hat schon einmal Bestandszeilen auf denselben Marker gesetzt, idempotent und mit `reverse_code = noop`. Sie darf aber **nicht kopiert** werden: sie stammt aus der Zeit vor #1026 und restauriert mit einem pauschalen `ENABLE TRIGGER`, was den seit Migration `0107` gehärteten Zustand `ENABLE ALWAYS` still degradieren würde (§3). Und für 100.000+ Zeilen braucht sie echte Chargen statt eines `bulk_update` (§Migrationsskizze).

**Wechselwirkungen.** Art.-17-Ereignis-Redaktion: unberührt; eine bereits altersredigierte Zeile wird von `_redact_event_history` **im Ergebnis** nicht verändert, aber trotzdem mitgeschrieben — der Bulk-Update läuft ohne Filter über alle Treffer. Art. 15: `_serialize_history_payload` kennt den Marker und braucht **keine** Änderung; das Top-Level-Merkmal `redacted` in `_gather_event_history` wird jedoch nur aus `data_before` abgeleitet und meldet für eine redigierte CREATE-Zeile (deren `data_before` `NULL` ist und bleibt, D1) fälschlich `false` — das ist eine **Pflicht-Codeänderung**, kein Nulltarif. **Anzeige:** `_diff_delete` kennt den `_redacted`-Zweig, `_diff_create` und `_diff_update` in [`history_diff.py`](../../src/core/services/events/history_diff.py) **nicht**. Eine redigierte CREATE-Zeile würde heute die Marker-Schlüssel als Feld-Labels rendern. Das fällt bisher nicht auf, weil Art.-17-redigierte Ereignisse gelöscht und ihre Detailseiten unerreichbar sind — bei Option (b) betrifft es **lebende, sichtbare** Ereignisse und muss zwingend mitgebaut werden.

### (c) Zeitpartitionierung nach `changed_at` mit Cold Storage

**Audit-Wirkung im Normalbetrieb: die beste der drei.** Eine Partition wird per `DROP TABLE` entfernt; Zeilentrigger blocken das nicht. Die Append-only-Trigger müssten für die Aufräumarbeit **nie** abgeschaltet werden — der Punkt, der (a) und (b) sicherheitsrelevant macht, entfällt.

**Aber die Frist passt nicht zur Partition.** Der Löschanspruch ist ereignis- und strategiebezogen: `retention_anonymous_days` (90), `retention_identified_days` (365), `retention_qualified_days` (**3650**), dazu `DocumentType.retention_days` als Typ-Override und Legal Hold als Einfrier-Schicht ([ADR-021](021-retention-modell.md)). Eine Partition darf erst fallen, wenn **keine** ihrer Zeilen mehr gebraucht wird — also frühestens nach der längsten Frist, praktisch nach zehn Jahren, und auch dann nur, wenn kein Legal Hold hineinragt. Die Grobkörnigkeit des Verfahrens kollidiert mit der Feinkörnigkeit des Anspruchs; Option (c) räumt genau die Zeilen **nicht** ab, um die es in §5 geht.

**Betriebsaufwand.** Partitionspflege (Vorab-Anlage künftiger Partitionen, sonst schlagen Inserts fehl), ein zweiter Ablageort, dessen Verschlüsselung, Backup und Wiederherstellung der Selbsthoster selbst verantwortet, und eine erweiterte Restore-Prozedur. Für die Zielgruppe kleiner Träger — dieselbe, für die [ADR-049](049-one-command-local-bootstrap.md) das Setup auf ein Kommando zusammengezogen hat — ist das die teuerste Option, und sie ist der einzige Vorschlag, der die Betriebsanleitung verlängert.

**DSGVO — der Ausschlussgrund.** Ausgelagerte Zeilen liegen außerhalb der ORM-Reichweite: die Auskunft nach Art. 15 (`_gather_event_history`) sähe sie nicht mehr, und die Art.-17-Kaskade (`_redact_event_history`) erreichte sie nicht. Ein Löschverlangen würde damit zu einer **manuellen Prozedur am Cold Storage** — außerhalb von RLS, außerhalb des Audit-Trails, außerhalb jeder Testabdeckung. Eine Betroffenenrechts-Umsetzung, die von einer Handanleitung abhängt, ist keine.

**Technische Zusatzlasten**, falls (c) je gebaut wird: der Partitionsschlüssel muss in den Primärschlüssel (`id` → `(id, changed_at)`), was Djangos Ein-Spalten-PK-Annahme durchbricht; die RLS-Policy und die beiden Append-only-Trigger müssen auf der partitionierten Struktur neu etabliert und ihr `tgenabled = 'A'` je Partition geprüft werden; `bypass_replication_triggers` liest `pg_trigger` gezielt über `tgrelid = 'core_eventhistory'::regclass`, also nur den Elternteil — ob `ALTER TABLE … DISABLE TRIGGER` unter PostgreSQL 18 auf die Klon-Trigger der Partitionen durchgreift, wäre zu verifizieren und zu pinnen, sonst brechen Art.-17-Redaktion und Key-Rotation still. Immerhin: es gibt keinen eingehenden Fremdschlüssel auf die Tabelle, die Umstellung bliebe also lokal.

## Decision

Entschieden wird **(b), zugeschnitten auf den Ereignis-Lebenszyklus**, ausgeführt ausschließlich owner-seitig, mit (a) als reduziertem Schutzrand und (c) als ausdrücklich offengehaltener, später Skalierungsreserve.

### D1 — Redaktion statt Löschung, spaltengenau

`EventHistory`-Zeilen werden **nicht gelöscht** (zur Frist der verbleibenden Chronologie siehe D8). Was altert, verliert seine Werte, nicht seine Existenz. Drei Präzisierungen, ohne die der Marker falsche Aussagen erzeugt:

1. **Nur belegte Spalten werden redigiert.** `NULL` bleibt `NULL` — eine CREATE-Zeile bekommt kein `data_before`, eine DELETE-Zeile kein `data_after`. Sonst behauptete die Auskunft ein „Vorher", das es nie gab.
2. **Je Spalte die eigene Slug-Liste.** `{"_redacted": true, "fields": [<Schlüssel genau dieser Spalte>]}` — ein gemeinsamer Marker für beide Spalten verlöre bei UPDATE die Unterscheidung, welche Felder vorher und welche nachher existierten. Die Form entspricht dem Lösch-Pfad und wird von `_serialize_history_payload` unverändert korrekt gerendert.
3. **Das Top-Level-Merkmal `redacted` der Auskunft muss mit.** `_gather_event_history` leitet es heute nur aus `data_before` ab; es muss künftig „mindestens eine Spalte redigiert" bedeuten, sonst meldet eine redigierte CREATE-Zeile `redacted: false`.
4. **Ein Zeitstempel `EventHistory.redacted_at`** (nullable) hält fest, *wann* redigiert wurde. Er trägt drei Aufgaben zugleich: Idempotenz-Filter des Sweeps, Prädikat des Teilindex (D6) und Fristanker für die zweite Stufe (D8) — die Frage „wie lange steht diese Zeile schon leer" lässt sich aus `changed_at` nicht beantworten.

### D2 — Erster Auslöser: der Zustand „Ereignis gelöscht" (schließt die §5-Lücke)

Sobald ein Ereignis `is_deleted = True` trägt, dürfen seine Historienzeilen keine Werte mehr führen. **Auslöser ist der Zustand, nicht der Klick** — die Ausführung liegt im owner-seitigen Sweep (D5), nicht im Löschpfad selbst: die Redaktion braucht den Trigger-Bypass, und der ist der App-Rolle im Request verwehrt (§4). Der Sweep erfasst damit beide Löschwege (`soft_delete_event` und `_soft_delete_events`) über dieselbe Bedingung und zusätzlich jeden Bestand, der vor Einführung gelöscht wurde.

Das kostet ein Zeitfenster von bis zu einem Cron-Intervall zwischen Löschung und Redaktion. Gemessen am Ist-Zustand — die Werte bleiben **dauerhaft** — ist das kein Rückschritt; als Alternative bleibt eine eng gefasste `SECURITY DEFINER`-Funktion, die genau „redigiere die Historie eines soft-gelöschten Ereignisses" kann und der App-Rolle per `GRANT EXECUTE` offensteht (Entscheid M-8). Sie wäre synchron, fügte aber eine privilegierte DB-Primitive hinzu, deren Missbrauchsfläche eigens zu prüfen wäre.

Dieser Teil ist **keine Wachstumsmaßnahme, sondern eine Korrektur**: er ist auch dann richtig, wenn der Maintainer D3 verwirft. Er braucht keine neue Einstellung und keinen neuen Frist-Begriff und deckt den personenlosen Zweig (`is_anonymous`) mit ab, für den es heute überhaupt keine Redaktion gibt.

**Legal Hold: an beiden Stellen, und im Sweep unter der Chargen-Transaktion.** Der Löschpfad allein reicht nicht. Zwar prüft der **manuelle** Löschpfad heute **überhaupt keinen** Hold (`soft_delete_event` leert Payload und Anhänge unmittelbar; nur die Retention-Query schließt gehaltene Ereignisse aus, [`enforcement.py`](../../src/core/retention/enforcement.py)) — diese Lücke gehört geschlossen (Entscheid M-9). Sie macht den Sweep aber **nicht** hold-frei, denn zwischen Löschung und Sweep liegt ein Fenster, in dem ein Hold noch entstehen kann:

- `RetentionHoldView` ([`views/retention.py`](../../src/core/views/retention.py)) setzt den Hold über einen `RetentionProposal` und prüft dabei **nicht**, ob das Ziel-Ereignis bereits gelöscht ist;
- veraltete Vorschläge zu gelöschten Ereignissen werden erst im nächsten Retention-Lauf entfernt (`cleanup_stale_proposals`, [`retention/proposals.py`](../../src/core/retention/proposals.py)) — bis dahin ist ein solcher Vorschlag ein gültiger Aufhänger;
- dazu kommt der vorschlagsfreie Einstieg `create_hold_for_target` aus [ADR-044](044-fall-loeschpfad.md) §4.

Ein Hold, der nach dem Löschen eintrifft, ist gerade der interessante Fall: er ordnet an, den Zustand **zu erhalten**, und trifft auf eine Historie, die noch alles enthält. Deshalb: **der Sweep prüft den Hold selbst, je Charge und innerhalb der Chargen-Transaktion**, die auch redigiert — eine Prüfung davor wäre erneut ein Zeitfenster. Gehaltene Ereignisse werden **aufgeschoben, nicht übersprungen**: sie bleiben in der Menge, ihr Zurückstellen wird gezählt und in der Lauf-Zusammenfassung ausgewiesen (nicht still, sonst entsteht genau der Zustand „gelöscht, aber unredigiert", den niemand mehr sieht), und der nächste Lauf nach Ablauf des Holds holt sie nach. Das ist dieselbe Aufschub-Semantik, die `anonymize_clients` und `anonymize_eligible_soft_deleted_clients` für Holds schon fahren (#1756).

**Die Prüfung allein genügt nicht — beide Seiten brauchen dasselbe Sperrobjekt.** Eine Hold-Prüfung *innerhalb* der Chargen-Transaktion schließt das Rennen noch nicht: unter `READ COMMITTED` kann ein Hold nach dem `SELECT` des Sweeps und vor dessen `COMMIT` entstehen und bliebe für diesen Lauf unsichtbar. Und heute gibt es überhaupt keinen gemeinsamen Berührungspunkt: `create_hold_for_target` ([`retention/legal_holds.py`](../../src/core/retention/legal_holds.py)) läuft **ohne** `transaction.atomic`, legt nur die `LegalHold`-Zeile an und fasst das Ziel-Ereignis nie an — `LegalHold.target_id` ist ein freies UUID-Feld ohne Fremdschlüssel, die Datenbank koordiniert hier also von sich aus nichts.

Festgelegt wird deshalb ein **expliziter Koordinationspunkt: die Zeile des Ereignisses in `core_event`.** Beide Seiten sperren sie, bevor sie handeln:

- **Sweep**, je Charge und **vor** dem Öffnen des Trigger-Bypasses: `Event.objects.filter(pk__in=<Ereignisse der Charge>).order_by("pk").select_for_update(skip_locked=True)`. Erst danach wird der Hold-Zustand gelesen, dann redigiert, dann der Nachweis geschrieben, dann committet. Die Sperre **vor** dem Bypass zu nehmen ist wesentlich: andernfalls hielte der Sweep die Tabellensperre auf `core_eventhistory`, während er auf eine fremde Zeilensperre wartet, und blockierte jeden Schreiber. `order_by("pk")` verhindert, dass zwei parallele Läufe einander kreuzen; `skip_locked` verhindert, dass die Charge hinter der offenen Transaktion einer Fachkraft hängt — ein übersprungenes Ereignis wird wie ein gehaltenes gezählt und im nächsten Lauf erledigt.
- **Hold-Anlage**, für `target_type="Event"`: dieselbe Zeile per `select_for_update` sperren, in einer eigenen `transaction.atomic()`, **bevor** die `LegalHold`-Zeile geschrieben wird. Das ist die Änderung, die `create_hold_for_target` dafür braucht.

Damit sind beide Richtungen des Rennens definiert entschieden:

| Wer zuerst committet | Was passiert |
|---|---|
| **Hold** | Der Sweep blockiert an der Zeilensperre bzw. überspringt sie (`skip_locked`); erhält er sie, liest er den Hold-Zustand **nach** der Sperre und sieht den frisch committeten Hold. Das Ereignis wird zurückgestellt und gezählt — die Historie bleibt vollständig, genau wie der Hold es anordnet. |
| **Redaktion** | Die Hold-Anlage blockiert an derselben Zeile und sieht anschließend den redigierten Zustand (`redacted_at IS NOT NULL`). Sie wird **nicht abgelehnt** — der Hold bindet weiterhin Ereigniszeile, Anhänge und die Anonymisierung der Person, aber sie wird **als verspätet gekennzeichnet**: die Rückmeldung an die anordnende Person sagt ausdrücklich, dass die Inhalte früherer Fassungen bereits getilgt sind und nicht wiederherstellbar sind, und die `LEGAL_HOLD`-Audit-Zeile trägt dieselbe Aussage als Merkmal. Ein stiller Hold, der Vollständigkeit suggeriert, wäre die schlechtere Antwort: die Spoliationsfrage muss später beantwortbar sein. |

Die Reihenfolge bleibt mit D5.5 verträglich und erweitert sie nur nach vorn: **`core_event` → `core_eventhistory` → `core_auditlog`**. Das ist exakt die Ordnung, in der die Anwendung ohnehin schreibt — `update_event` liest das Ereignis unter `select_for_update`, speichert es, schreibt dann die `EventHistory`-Zeile und zuletzt den Audit-Eintrag; `create_event` legt das Ereignis an, dann die Historie, dann den Audit. Ein neuer ABBA-Pfad entsteht dadurch nicht.

### D3 — Zweiter Auslöser: eine eigene Frist für Zeilen lebender Ereignisse

Für Ereignisse, die noch leben, bekommt die Historie eine eigene, konfigurierbare Frist: `Settings.eventhistory_detail_months`, Vorschlag **24**, `0` = aus — als Anlehnung an den Default von `auditlog_retention_months`, damit Betreiber sich nur einen Zeitraum merken müssen. Der nächtliche `enforce_retention`-Lauf redigiert Zeilen mit `changed_at < cutoff`.

Fachlich tragfähig, weil die Historie zwei Zwecke hat, die unterschiedlich schnell verfallen: die **Nachvollziehbarkeit der laufenden Beratung** („was habe ich letzte Woche geändert") lebt Wochen, die **Rechenschaft** („wer hat wann etwas geändert") lebt Jahre — und die zweite überlebt die Redaktion vollständig. Sie überlebt sie allerdings **unbefristet**; die beiden Schichten altern also gerade nicht gemeinsam (D8).

### D4 — Was nie redigiert wird

Die **jüngste** Historienzeile eines lebenden Ereignisses behält ihre Werte, unabhängig vom Alter. So bleibt auf der Detailseite immer mindestens eine lesbare letzte Änderung stehen, auch bei einer Akte, die seit Jahren unangetastet ist. Das ist der einzige Rest von Option (a): „letzte N" als **Schutzrand**, nicht als Aufräumregel — mit N = 1 (offener Entscheid M-2).

### D5 — Wie und von wem die Integritätsgarantie geöffnet wird

Sicherheitsrelevanter Kern der Umsetzung; fünf Auflagen:

1. **Owner-seitig, nie im Request.** Der Sweep läuft im `maintenance`-Service als Admin-/Owner-Rolle, wie `enforce_retention` heute (§4), mit dem bestehenden RLS-Bypass-Guard (Muster #1016) — ohne ihn liefe er über 0 Zeilen und meldete Erfolg.
2. **Gebatcht, nicht je Zeile.** Ein `bypass_replication_triggers()`-Block je Charge (Vorschlag 500 Zeilen), nicht je Zeile wie in der Key-Rotation. Die Zahl der `SHARE ROW EXCLUSIVE`-Intervalle auf `core_auditlog`/`core_eventhistory` sinkt damit um Größenordnungen.
3. **Nachweis je Charge, nicht je Lauf.** Der Nachweis muss mit derselben Transaktion committen wie die Redaktion, die er belegt — ein einzelner Abschluss-Eintrag am Lauf-Ende würde bei einem Abbruch nach der dritten Charge bereits veränderte Append-only-Zeilen ohne Beleg zurücklassen. Vorschlag: je Charge ein AuditLog-Eintrag (neue Aktion `EVENT_HISTORY_REDACTED`, mit Anzahl und Cutoff-/Chargengrenze) **innerhalb** des Chargen-Blocks, plus optional eine Zusammenfassung am Lauf-Ende. Muster ist der Checkpoint-Tombstone aus #1070, der genauso die Legitimität einer Lücke transportiert.
4. **Vorzustand exakt wiederherstellen.** Ausschließlich über `bypass_replication_triggers` (liest `tgenabled` zurück), nie über ein pauschales `ENABLE TRIGGER` (§3).
5. **Kanonische Sperr-Reihenfolge: `core_event` → `core_eventhistory` → `core_auditlog`.** Sie ist die Reihenfolge, in der die Anwendung ohnehin schreibt (Ereignis unter `select_for_update`, dann Historie, Audit zuletzt) — und damit die einzige, die den ABBA-Deadlock aus §3 ausschließt. Die erste Stufe ist zugleich der Koordinationspunkt gegen die Hold-Anlage (D2); sie wird **vor** dem Trigger-Bypass genommen, nie danach. Konkret heißt das: die Reihenfolge in `_APPEND_ONLY_TRIGGERS` **umdrehen** (heute `core_auditlog` zuerst) und den Nachweis-Eintrag der Charge (D5.3) nach der Redaktion schreiben, nicht davor. Die Umkehr wirkt auf alle vier Bestandsaufrufer und ist für sie unschädlich: `prune_auditlog` fasst nur eine Tabelle an, `Client.anonymize()` und `reencrypt_fields` folgen derselben Historie-vor-Audit-Ordnung. Noch sauberer löst es Entscheid **M-6**: öffnet der Sweep nur die beiden `eventhistory`-Trigger, nimmt er auf `core_auditlog` gar keine DDL-Sperre mehr, sondern nur das `ROW EXCLUSIVE` seines eigenen Nachweis-Inserts — dieselbe Ordnung wie jeder Request, kein Zyklus mehr möglich. Ergänzend: eine Charge muss einen `deadlock detected`-Abbruch überleben dürfen — sie ist idempotent (D1.4) und wird mit Backoff wiederholt.

Die Trigger-Verengung ist damit nicht mehr Kür: **M-6 ist die empfohlene Lösung des Deadlock-Befunds aus §3**, die Umkehr der Reihenfolge die Mindestmaßnahme.

### D6 — Index und Anzeige

`changed_at` bekommt einen Index (heute existiert keiner). Empfohlen als **Teilindex** `(changed_at) WHERE redacted_at IS NULL` — mit dem Zeitstempel aus D1.4 ist das Prädikat unstrittig immutable, und der idempotente Sweep scannt nicht dauerhaft über bereits erledigte Zeilen; der Index schrumpft mit jedem Lauf. `_diff_create` und `_diff_update` bekommen den `_redacted`-Zweig, den `_diff_delete` schon hat; `_gather_event_history` bekommt die korrigierte `redacted`-Ableitung (D1.3). Der Admin (`EventHistoryAdmin`) braucht nichts — er zeigt die Rohspalten und damit automatisch den Marker.

### D7 — Partitionierung wird nicht verworfen, sondern vertagt

Keine Partitionierung, kein Cold Storage in 0.89. Der Auslöser für eine Neubewertung wird hier festgehalten, damit die Frage nicht beliebig oft neu aufgemacht wird: **wenn die Zahl der Zeilen selbst zum Problem wird** — Richtwert eine Million Zeilen je Einrichtung, bei der auch der redigierte Rest Index- und Vacuum-Kosten treibt. Das ist bei den Größen aus §2 frühestens jenseits von zwanzig Jahren der Fall. Redaktion und Partitionierung schließen einander nicht aus; (b) macht (c) nur unnötig.

### D8 — Die Chronologie bekommt eine eigene Frist (Stufe 2, mit M-7 angenommen)

Die Redaktion schließt die **Inhalts**-Lücke, nicht die Retention-Lücke. Dauerhaft stehen bleiben: der Fremdschlüssel auf das Ereignis (und über dieses auf die Person), `changed_by`, `changed_at`, `action` sowie sämtliche Feldnamen, Sensitivitäten und Verschlüsselungsmerkmale aus `field_metadata`. Das sind personenbezogene Daten auf zwei Seiten — der beratenen Person **und** der Beschäftigten; für Letztere führt [`audit_pruning.py`](../../src/core/retention/audit_pruning.py) § 26 BDSG selbst als Grund an, warum das AuditLog nicht dauerhaft aufbewahrt wird. Dass die Historie dieselbe Chronologie unbefristet hält, während das AuditLog sie nach Vorgabefrist verliert, ist eine Entscheidung, die getroffen und begründet werden muss — nicht eine, die aus dem Schema folgen darf.

Die Frist ist eine Zweckentscheidung des Verantwortlichen, keine technische Ableitung; sie war deshalb dem Maintainer vorbehalten (M-7). Damit sie mit „ja" oder „nein" beantwortbar war statt selbst entworfen werden zu müssen, lag hier ein fertiger Vorschlag mit Vorgabewerten vor — **er ist am 2026-08-28 angenommen worden** und gilt in genau dieser Form:

> **Stufe 2 — angenommen (M-7).** Eine bereits redigierte Zeile wird **gelöscht**, wenn ihre Redaktion `eventhistory_chronicle_months` zurückliegt. Neue Einstellung `Settings.eventhistory_chronicle_months`, **Default 24**, `0` = aus. Gemessen ab `redacted_at` (D1.4), nicht ab `changed_at`: die Frage lautet „wie lange steht die Zeile schon leer", und nur so gilt derselbe Zeitraum für beide Auslöser — die Zeile eines gelöschten Ereignisses (D2, sofort redigiert) und die eines lebenden (D3, nach Ablauf der Detailfrist). Mechanik, Rolle, Chargen, Nachweis und Sperr-Reihenfolge exakt wie in D5; inhaltlich fällt zu diesem Zeitpunkt nichts mehr, nur noch Metadaten und der Zeitstempel.
>
> **Begründung des Vorgabewerts:** 24 Monate ist der Default von `auditlog_retention_months`. Die Chronologie der Historie beantwortet dieselbe Frage wie das AuditLog („wer hat wann etwas geändert") und trägt dieselbe Doppelbetroffenheit; sie soll deshalb mit derselben Frist enden statt sie zu überleben. Mit den Vorschlagswerten aus D3 ergibt sich eine Gesamtlebensdauer von 24 Monaten Werte + 24 Monaten Chronologie für die Zeile eines lebenden Ereignisses und 24 Monaten Chronologie ab Löschung für die eines gelöschten.
>
> **Ein „nein" wäre** eine Entscheidung für die unbefristete Aufbewahrung der Chronologie gewesen — dann hätte diese Zweckbindung ausdrücklich in Verarbeitungsverzeichnis und Löschkonzept gehört, nicht ins Schweigen. Beide Antworten waren vertretbar; unentschieden war die einzige Variante, die es nicht war. Dieselbe Pflicht trifft jetzt jeden Betreiber, der `eventhistory_chronicle_months` auf `0` stellt.

## Migrationsskizze (Umsetzung ist 0.89-Arbeit, nicht Teil dieser ADR)

1. **Vorklärung (blockierend):** den Rollenbefund aus §4 auflösen — läuft der Webprozess irgendwo als Owner-Rolle, und ist `ClientAnonymizeView` unter dem Drei-Rollen-Modell heute defekt? Ergebnis verticketen. D2 setzt keinen Bypass im Request voraus, die Frage bleibt davon unabhängig offen.
2. **Schema:** Migration `Settings.eventhistory_detail_months` (Default 24, `0` = aus), `EventHistory.redacted_at` (nullable, D1.4) und der Teilindex `(changed_at) WHERE redacted_at IS NULL` (D6) — bei Annahme von D8 zusätzlich `Settings.eventhistory_chronicle_months`. Alles deterministisch; das Hinzufügen einer Spalte ist DDL und wird von den Append-only-Zeilentriggern nicht berührt.
3. **Anzeige und Auskunft zuerst:** `_diff_create`/`_diff_update` um den `_redacted`-Zweig, `_gather_event_history` um die korrigierte `redacted`-Ableitung ergänzen — mit Tests **vor** jeder Datenänderung, sonst erzeugt der erste Sweep kaputte Detailseiten und falsche Auskünfte.
4. **Service:** `redact_event_history_rows(pks)` als **einziger** Schreiber — owner-seitig, gebatcht, idempotent (`redacted_at IS NOT NULL` überspringen), spaltengenau nach D1, mit Hold-Prüfung, Nachweis und Sperr-Reihenfolge je Charge nach D2/D5. Beide Auslöser (D2, D3) rufen ihn auf; eine zweite Implementierung wäre genau die Drift, die ADR-044 §3 benennt.
5. **Auslöser verdrahten:** im `enforce_retention`-Lauf je Einrichtung zwei Mengen — (i) Zeilen von Ereignissen mit `is_deleted = True` und `redacted_at IS NULL` (D2), (ii) Zeilen lebender Ereignisse älter als der Cutoff, ohne die jüngste je Ereignis (D3, D4). Zählwerte **inklusive der wegen Legal Hold zurückgestellten** in die Lauf-Zusammenfassung.
6. **Legal Hold** dreifach absichern: (i) der Koordinationspunkt — `create_hold_for_target` sperrt für `target_type="Event"` die Ereigniszeile in einer eigenen `transaction.atomic()`, bevor es die `LegalHold`-Zeile schreibt, und kennzeichnet einen Hold auf ein bereits redigiertes Ereignis als verspätet (D2); (ii) die Hold-Prüfung des Sweeps je Charge **nach** dem `select_for_update` (D2, zwingend — sie schließt das Fenster zwischen Löschung und Lauf); (iii) die Prüfung am Einstieg des manuellen Löschpfads (M-9, empfohlen — sie verhindert, dass der Fall überhaupt entsteht).
7. **Bestandsmigration:** Sie darf `0074` nicht kopieren. Erforderlich sind (i) `Migration.atomic = False` — Django-Migrationen sind sonst **eine** Transaktion, und die Trigger-DDL-Sperre stünde bis zum Ende der gesamten Datenmigration; (ii) **materialisierte PK-Chargen** (`values_list("pk")` in Blöcken; `.iterator(chunk_size=…)` steuert nur die Fetch-Größe und erzeugt weder Update-Chargen noch Commit-Grenzen); (iii) je Charge eine eigene explizite Transaktion mit eigenem Bypass-Block; (iv) Wiederherstellung des **exakten** `tgenabled` (das pauschale `ENABLE TRIGGER` aus `0074` würde `ENABLE ALWAYS` still auf Origin zurückstufen, §3); (v) Idempotenz und `reverse_code = noop`. Inhaltlich holt sie den D2-Bestand nach (Zeilen bereits soft-gelöschter Ereignisse). Der D3-Bestand gehört **nicht** in die Migration — unbestimmte Laufzeit im Deploy-Fenster, ihn erledigt der erste Cron-Lauf.
8. **Doku:** [`docs/admin-guide.md`](../admin-guide.md) (neue Einstellung, Wirkung auf die Rotationsdauer), [`docs/ops-runbook.md`](../ops-runbook.md) § Retention, DSGVO-Vorlagen in [`src/core/dsgvo_templates/`](../../src/core/dsgvo_templates/) — nach [ADR-021](021-retention-modell.md) zwingt jede Friständerung zum Abgleich mit Verarbeitungsverzeichnis und Löschkonzept.
9. **Guards:** genau ein Redaktions-Schreiber; ein soft-gelöschtes Ereignis hinterlässt nach einem Lauf **keine** unredigierte Historienzeile (der §5-Befund als Regressionsnetz); ein soft-gelöschtes Ereignis **unter aktivem Hold** behält seine Zeilen und erscheint als zurückgestellt in der Zusammenfassung; ein Hold, der zwischen Chargen-Sperre und Commit angelegt wird, gewinnt oder verliert **definiert** — der Guard prüft beide Richtungen aus D2 (Hold zuerst → Ereignis zurückgestellt; Redaktion zuerst → Hold entsteht, trägt aber das Verspätungsmerkmal); eine redigierte Zeile löst in der Key-Rotation kein UPDATE mehr aus; der Nachweis-Eintrag liegt in derselben Transaktion wie seine Charge; `tgenabled` ist nach Migration und Lauf unverändert `'A'` (Ergänzung zum Bestandstest in `test_rls.py`); die Sperr-Reihenfolge in `_APPEND_ONLY_TRIGGERS` ist `core_eventhistory` vor `core_auditlog` und wird als Ableitung gepinnt (D5.5) — sie ist sonst genau die Art von Reihenfolge, die ein späteres Refactoring stillschweigend dreht.

## Maintainer-Entscheide — entschieden 2026-08-28

Alle neun Empfehlungen wurden angenommen; die Umsetzung folgt #2065 (Entscheid-Kommentar am Issue).

| | Frage | Entscheidung |
|---|---|---|
| **M-1** | Zuschnitt | **D2 + D3** |
| **M-2** | Schutzrand | **N = 1** |
| **M-3** | `field_metadata` mitredigieren | **nein** — der Snapshot bleibt vollständig (revisionsfeste Feld-Labels, #824) |
| **M-4** | Nachweis | eigene Aktion `EVENT_HISTORY_REDACTED` je Charge, **prune-exempt** wie `AUDIT_PRUNE_CHECKPOINT` |
| **M-5** | Vorgabewert `eventhistory_detail_months` | **24** |
| **M-6** | Bypass-Verengung | **ja** — Trigger-Auswahl; der Sweep öffnet nur die beiden `core_eventhistory`-Trigger, und `_APPEND_ONLY_TRIGGERS` führt jetzt `core_eventhistory` vor `core_auditlog` |
| **M-7** | Chronologie (D8) | **ja** — `eventhistory_chronicle_months`, Default 24, `0` = aus, gemessen ab `redacted_at` |
| **M-8** | Synchronität | **nachgelagerter owner-seitiger Sweep** (keine `SECURITY DEFINER`-Funktion) |
| **M-9** | Legal Hold am Löschpfad | **in 0.89 mitgezogen** |

Die Fragestellungen, aus denen diese Entscheidungen hervorgingen, bleiben zur Nachvollziehbarkeit stehen:


- **M-1 (Zuschnitt):** D2 allein (DSGVO-Korrektur, keine neue Einstellung) oder D2 + D3? D2 ist ohne Gegenrede; D3 fügt einen weiteren Betreiber-Regler hinzu — [ADR-044](044-fall-loeschpfad.md) §2 hat einen zweiten Fristbegriff bewusst **abgelehnt**, dort allerdings, weil er dieselbe Frage doppelt gestellt hätte. Hier ist die Frage eine andere („wie lange bleibt der *Inhalt* alter Fassungen lesbar").
- **M-2 (Schutzrand):** N = 1 wie in D4, N = 3, oder kein Schutzrand?
- **M-3 (`field_metadata`):** mitredigieren, also auf die in `fields` genannten Slugs eindampfen? Der Snapshot deckt heute **alle** Felder des Dokumenttyps, die Slug-Liste nur die befüllten — der Gewinn ist also die Metadatenzeile der leeren Felder und schwankt mit dem Ausfüllgrad. Kostet die revisionsfeste Darstellung der übrigen Feld-Labels (#824).
- **M-4 (Nachweis):** eigene AuditLog-Aktion `EVENT_HISTORY_REDACTED` je Charge (D5.3) — und wenn ja, prune-exempt wie `AUDIT_PRUNE_CHECKPOINT` oder der regulären Frist unterworfen?
- **M-5 (Vorgabewert):** 24 Monate in Anlehnung an `auditlog_retention_months`, oder kürzer (12) — die Historie trägt Inhalte, das AuditLog nur Feldnamen, was für eine **kürzere** Frist spräche?
- **M-6 (Bypass-Verengung):** `bypass_replication_triggers` um eine Auswahl der zu öffnenden Trigger erweitern, damit ein Historien-Sweep nicht auch `auditlog_immutable` mitöffnet? Empfohlen mit **ja** — es ist zugleich die saubere Auflösung des Deadlock-Pfads aus §3 (D5.5). Berührt vier Bestandsaufrufer; die Mindestvariante ist die bloße Umkehr der Reihenfolge.
- **M-7 (Chronologie, siehe D8):** Wird der dort ausformulierte Vorschlag für Stufe 2 angenommen — `eventhistory_chronicle_months`, Default 24, `0` = aus, gemessen ab `redacted_at`, ja oder nein? Ein „nein" ist eine Entscheidung für unbefristete Aufbewahrung und muss in Verarbeitungsverzeichnis und Löschkonzept nachgezogen werden. **Nur ohne jede Antwort bleibt die Retention-Lücke offen**, während die Inhalts-Lücke geschlossen ist.
- **M-8 (Synchronität):** nachgelagerter owner-seitiger Sweep (Empfehlung, Verzögerung bis zu einem Cron-Intervall) oder eng gefasste `SECURITY DEFINER`-Funktion für die synchrone Redaktion im Löschpfad?
- **M-9 (Legal Hold am Löschpfad):** die Hold-Prüfung am manuellen Ereignis-Löschpfad in 0.89 mitziehen oder als eigenes Ticket führen? Sie ist heute nicht vorhanden. **Nicht** Teil dieses Entscheids sind die beiden anderen Stufen aus D2 — die Hold-Prüfung im Sweep und der gemeinsame Koordinationspunkt in `create_hold_for_target`: sie decken das Zeitfenster nach der Löschung ab, das eine Prüfung am Einstieg prinzipiell nicht erreicht, und sind deshalb gesetzt.

### Nachtrag zu M-8 — empirisch bestätigt (2026-09-01)

Die in M-8 verworfene Alternative — eine eng gefasste `SECURITY DEFINER`-Funktion mit `GRANT EXECUTE` an die App-Rolle — wurde im Rahmen von #2085 tatsächlich gebaut und im Cross-Review missbraucht. Zwei Wege, beide gegen echtes PostgreSQL reproduziert: Die Zulässigkeitsprüfung im Funktionsrumpf muss die Einrichtung aus `app.current_facility_id` lesen — eine Custom-GUC, die genau die App-Rolle, der `EXECUTE` gewährt ist, per `set_config()` selbst setzen kann; damit war die Historie **jeder** Einrichtung tilgbar, und `FORCE ROW LEVEL SECURITY` greift im Definer-Kontext wegen des `rolbypassrls` des Owners nicht. Und der privilegierte Rumpf umging den Legal-Hold-Schutz, den der Service-Pfad (`anonymize_client_now`) als Spoliation ausdrücklich verbietet.

Ausschlaggebend war dabei nicht die fälschbare GUC an sich — RLS stützt sich auf dieselbe, sondern dass die App-Rolle die **append-only**-Historie überhaupt hätte zerstören können; genau das verhindert die Runtime-/Owner-Trennung aus [ADR-033](033-runtime-owner-role-separation.md) sonst vollständig. **M-8 bleibt damit unverändert gültig, jetzt mit Beleg statt Abwägung**; die Formulierung „Bleibt als `SECURITY DEFINER`-Variante offen (M-8)" unter § Alternatives considered ist damit beantwortet und geschlossen. Der Art.-17-Sofortpfad nutzt seit #2085 denselben nachgelagerten owner-seitigen Sweep (Kandidatenmenge `_anonymized_client_rows`, Nachweiskategorie `eventhistory_anonymized_client`).

Gegen einen erneuten Anlauf ohne eigenen Entscheid steht ein Guard: `TestSecurityDefinerInventory` in [`src/tests/test_rls.py`](../../src/tests/test_rls.py) pinnt, dass es im Repo **keine** `SECURITY DEFINER`-Funktion gibt und dass `01-app-role.sh` der App-Rolle keine Ausführungsrechte auf Funktionen erteilt; sein Docstring führt beide Missbrauchswege.

### Nachtrag zu D5.5 — `core_case` in der kanonischen Sperr-Reihenfolge (2026-09-04)

D5.5 legt die Reihenfolge `core_event` → `core_eventhistory` → `core_auditlog` fest und benennt `core_client` als vorgelagerte erste Stufe (D2). Nicht erfasst war `core_case`: `create_hold_for_target` nahm für `target_type="Case"` **gar keine** Sperre, obwohl ein Fall-Hold über `exclude_clients_with_active_holds` dieselbe Personen-Anonymisierung blockiert wie ein Personen-Hold. Es gab damit keinen gemeinsamen Koordinationspunkt zwischen Fall-Hold-Anlage und Sofortpfad, Nacht-Batch, K-Anon-Bridge, Ereignis-Sweep und Approve — der Hold konnte neben einer laufenden Anonymisierung her committen (#2122).

Die kanonische Ordnung lautet damit vollständig:

> **`core_client` → `core_case` → `core_event` → `core_retentionproposal` → `core_eventhistory` → `core_auditlog`**

`core_case` steht **nach** der Person (jeder Fall hängt an genau einer Person, `Case.client` ist Pflichtfeld) und **vor** dem Ereignis (der Fall rahmt Ereignisse, nicht umgekehrt). Der Fall-Hold sperrt seither `core_client` → `core_case` → die Ereigniszeilen der Person (`_lock_case_and_check_history`) und trägt — wie Ereignis- und Personen-Hold — das Verspätungsmerkmal `history_already_redacted`. Gepinnt wird die Ordnung am ausgeführten SQL in `TestCaseHoldLockOrder` ([`src/tests/test_retention_hold_toctou_rest.py`](../../src/tests/test_retention_hold_toctou_rest.py)), nach dem Muster von [`test_restriction_lock_order.py`](../../src/tests/test_restriction_lock_order.py).

`core_activity` war hier zunächst bewusst **nicht** aufgenommen: Aktivitäts-Holds (`target_type="Activity"`) sind über keine Oberfläche und keinen In-App-Aufrufer anlegbar, und der Aktivitäten-Sweep (`enforce_activities`) löschte als Bulk-`DELETE` ohne Zeilensperre. Beides zusammen war ein eigener Entscheid und einem Folge-Ticket vorbehalten — dieses Folge-Ticket ist #2124, siehe den Nachtrag unten.

### Nachtrag zu D5.5 — `core_activity` als letztes Glied (2026-09-05)

Mit #2124 ist der offene Punkt entschieden. Die kanonische Ordnung lautet vollständig:

> **`core_client` → `core_case` → `core_event` → `core_retentionproposal` → `core_eventhistory` → `core_auditlog` → `core_activity`**

`core_activity` steht **zuletzt**. Begründung: Aktivitäten sind Protokollzeilen. Sie hängen nur generisch am Ziel (`target_type`/`target_id`, kein Fremdschlüssel, keine `client_id`/`case_id`-Spalte), und kein Pfad im Repo sperrt nach einer Aktivitätszeile noch eine andere Tabelle — die Position am Ende kann deshalb keinen ABBA-Zyklus erzeugen, egal welcher Pfad hinzukommt.

Beide Seiten nehmen seither denselben Anker: `create_hold_for_target` sperrt für `target_type="Activity"` die **Personenzeile des Ziels** (Client-Ziel → dessen pk; Ereignis-/Fall-Ziel → deren `client_id`; sonst keine) und danach die Aktivitätszeile (`_lock_activity_and_check`); der Sweep `enforce_activities` löscht nicht mehr per Bulk-`DELETE`, sondern materialisiert die pk-Liste, fährt je Aktivität eine eigene Transaktion, nimmt dieselben beiden Sperren und liest Aktivitäts- und Personen-Hold erst **danach** (`_lock_activity_and_recheck_hold`). Eine gehaltene Aktivität wird zurückgestellt und als `deferred` im Audit-Detail gemeldet, statt hart gelöscht zu werden — dieselbe Zähl-Semantik wie am Ereignis-Sweep (D5.3). Die Auflösung des Personenbezugs teilen sich beide Seiten über `resolve_activity_client_id`, damit sie nicht auseinanderlaufen.

Seit #2152 schützen auch Holds auf einem Ereignis- oder Fall-Ziel die zugehörige Aktivitätszeile. Bei Event-Zielen sperrt der Sweep deshalb in der kanonischen Ordnung `core_client` → `core_event` → `core_activity`; bei Case-Zielen reicht der gemeinsame Client-Anker zur Serialisierung mit der Fall-Hold-Anlage, bevor der Sweep den Fall-Hold unter der Sperre erneut liest.

Ein Verspätungsmerkmal (`history_already_redacted`) trägt der Aktivitäts-Hold weiterhin **nicht**: Aktivitäten haben keine Ereignis-Historie, über deren Vollständigkeit etwas auszusagen wäre.

Gepinnt wird die Ordnung am ausgeführten SQL in `TestActivityLockOrder` ([`src/tests/test_retention_hold_toctou_rest.py`](../../src/tests/test_retention_hold_toctou_rest.py)) — für beide Seiten (Hold-Anlage und Sweep), inklusive der Zusicherung, dass `core_activity` in der Hold-Anlage das letzte gesperrte Objekt ist.

## Consequences

- **+** Die Frist eines Ereignisses wirkt endlich auch auf seine Historie; die in §5 belegte Lücke — Inhalte überleben die eigene Löschfrist, bei personenlosen Ereignissen dauerhaft — schließt sich, für Bestand und Neuzugang.
- **+** Die Kosten der Schlüsselrotation werden proportional zum unredigierten Fenster statt zur Tabellengröße; das Wartungsfenster wird planbar, statt jährlich zu wachsen.
- **+** Kein neuer Ablageort, keine neue Wiederherstellungs-Prozedur, keine Verlängerung des Betriebshandbuchs für Selbsthoster.
- **+** Der Entwurf kommt ohne Trigger-Bypass im Request-Pfad aus und respektiert damit die Runtime-/Owner-Trennung aus [ADR-033](033-runtime-owner-role-separation.md) unverändert.
- **−** Das nächtliche Retention-Kommando wird zu einem **regelmäßigen** Aufrufer des Trigger-Bypasses. Die Unveränderlichkeit von `core_eventhistory` setzt planmäßig aus (nicht die von `core_auditlog` — der Sweep öffnet nach M-6 nur die beiden Historien-Trigger) statt nur bei seltenen Verwaltungsvorgängen; schreibende Zugriffe auf beide Tabellen warten währenddessen. Die Auflagen aus D5 sind kein Beiwerk, sondern die Bedingung, unter der diese Empfehlung gilt.
- **−** Der Sweep macht den **Deadlock-Pfad aus §3 zur Routine**: ohne die kanonische Sperr-Reihenfolge (D5.5) trifft ein Abbruch mit `deadlock detected` entweder die Charge — hinnehmbar, sie ist idempotent und wird wiederholt — oder eine Fachkraft mitten im Speichern eines Ereignisses. Der Befund besteht schon heute für `prune_auditlog` und `reencrypt_fields`; diese ADR erhöht seine Eintrittswahrscheinlichkeit und schuldet deshalb die Gegenmaßnahme.
- **−** Zwischen Löschung und Redaktion liegt ein Fenster von bis zu einem Cron-Intervall (D2/M-8) — in dieser Zeit kann ein Legal Hold eintreffen. Der Sweep prüft ihn deshalb selbst und teilt sich mit der Hold-Anlage die Sperre auf der Ereigniszeile; das macht `create_hold_for_target` transaktional und sperrend, wo es heute weder das eine noch das andere ist (D2).
- **−** Die Chronologie bleibt nach der Redaktion weitere `eventhistory_chronicle_months` stehen (D8/M-7, Vorgabe 24); wer die Frist auf `0` stellt, entscheidet sich für unbefristete Aufbewahrung und muss das in Verarbeitungsverzeichnis und Löschkonzept nachziehen.
- **−** Alte Fassungen sind nach Fristablauf inhaltlich unwiederbringlich; ein Berichtigungsstreit über einen fünf Jahre alten Wortlaut lässt sich dann nicht mehr aus der Anwendung heraus rekonstruieren, sondern nur noch aus einem Backup-Stand.
- **−** Zwei weitere Regler in den Einrichtungs-Einstellungen (M-1 hat für D2 + D3 entschieden, M-7 für Stufe 2), mit Pflicht zum Abgleich von Verarbeitungsverzeichnis und Löschkonzept.
- **−** Die Anzeige- und Auskunfts-Lücken aus D6 müssen vor dem ersten Sweep geschlossen sein; sie sind heute latent und werden durch diese Änderung akut.

### Nachtrag zu D5.5 — Zustandsvertrag bei Statuswechseln (2026-09-22)

Die Sperrreihenfolge beantwortet, **in welcher Ordnung** Zeilen genommen werden. Sie beantwortet nicht, **ob** an einer Zeile überhaupt noch geschrieben werden darf. Genau diese zweite Frage fehlte den Statuswechseln der Fallakte: `close_case`/`reopen_case` (#2288) sowie `achieve_goal`/`unachieve_goal`/`toggle_milestone` (#2295) speicherten die Instanz, die die View vor dem Klick gelesen hatte — vollständig. Fiel in das Fenster dazwischen eine Bearbeitung, ein Soft-Delete oder die Tilgung (`redact_case_content`/`redact_case_goals`, beide zugleich Kaskadenschritt der Personen-Anonymisierung), dann schrieb der Save die alten Freitexte **und** die alten Zustandsmerkmale (`is_deleted`, `deleted_at`, `anonymized_at`) zurück.

Der Vertrag lautet: **gelöscht oder anonymisiert ⇒ keine Statusmutation.** Er wird in [`services/case/state_contract.py`](../../src/core/services/case/state_contract.py) durchgesetzt, und zwar auf dem **frisch unter Sperre gelesenen** Stand, nicht auf der übergebenen Instanz — Person nach Art. 18 eingeschränkt, Person im Papierkorb/anonymisiert, Fall im Papierkorb/anonymisiert oder Zeile verschwunden bzw. Fall zwischenzeitlich an eine andere Person gehängt: jeweils `ValidationError`, kein Schreibvorgang, keine Audit-Zeile, kein Zeitstrom-Eintrag. Erst danach wird der Status geschrieben, spaltengenau über `update_fields` (Status plus zugehörige Zeitspalte). `update_fields` allein wäre nur die halbe Antwort: es hält die Spalten klein, lässt eine getilgte Akte aber weiter Statuswechsel annehmen.

Die kanonische Ordnung bekommt dafür zwei neue Glieder:

> **`core_client` → `core_case` → `core_event` → `core_outcomegoal` → `core_milestone` → `core_retentionproposal` → `core_eventhistory` → `core_auditlog` → `core_activity`**

Wirkungsziel und Meilenstein stehen **nach** dem Fall (beide hängen über `case`/`goal` an ihm) und **nach** dem Ereignis. Die Einordnung folgt dem einzigen Pfad, der beide Zweige zugleich anfasst: `anonymize_case` hält über `_lock_case_chain` die Ereigniszeilen des Falls und redigiert **danach** in derselben Transaktion Ziele und Meilensteine (`redact_case_goals`) — also `core_event` vor `core_outcomegoal`. Die Statuswechsel nehmen von diesem Abschnitt nur `core_outcomegoal`/`core_milestone` und fassen `core_event` nie an; ihre Folge ist damit eine Teilfolge der Gesamtordnung. **Zyklusfrei sind beide Seiten nicht deshalb, weil sie disjunkt wären, sondern wegen der gemeinsamen ersten Stufe:** `core_client` nehmen Tilgung wie Statuswechsel als erstes, und dort serialisieren sie sich, bevor eine von ihnen eine Zeile weiter unten hält. Die Personenzeile bleibt die erste Stufe und wird weiterhin über den gemeinsamen PK-geordneten Helfer `lock_clients` genommen (#2268), den das Art.-18-Gate mit `lock=True` ohnehin verwendet; eine zweite `select_for_update`-Form neben ihm wäre eine zweite Ordnung. Der Meilenstein-Pfad sperrt die Zielzeile mit, obwohl er sie nicht schreibt: eine übersprungene Stufe ist die nächste Reihenfolge, die niemand mit den übrigen vergleicht.

Gepinnt wird der Vertrag in [`src/tests/test_case_status_redaction_guard.py`](../../src/tests/test_case_status_redaction_guard.py) und [`src/tests/test_goal_status_redaction_guard.py`](../../src/tests/test_goal_status_redaction_guard.py) — letzteres mit einem Rendezvous beider Reihenfolgen (Redaktion vor bzw. nach dem Statuswechsel) auf zwei echten Verbindungen, beobachtend nach dem Muster von [`test_client_lock_order.py`](../../src/tests/test_client_lock_order.py).

### Nachtrag zu D5.5 — Fallzuordnung, Zielpflege und FK-Vorsperre (2026-09-23)

**Zustandsvertrag.** Derselbe Vertrag gilt seit #2329 für die übrigen Schreibvorgänge an der Fallakte: `assign_event_to_case`/`remove_event_from_case` sperren alle beteiligten Personen in einer PK-geordneten Abfrage, danach Ziel- **und** abgebenden Fall in PK-Ordnung, zuletzt die Ereigniszeile (`core_client` → `core_case` → `core_event`, eine Teilfolge der Gesamtordnung) und schreiben nur die Zuordnungsspalte samt `updated_at`; `create_goal`/`update_goal` nehmen `core_client` → `core_case` (→ `core_outcomegoal`). Ein Ereignis im Papierkorb gilt dabei wie eine getilgte Akte.

**FK-Vorsperre.** Eine neue Zeile nimmt über die aufgeschobene Fremdschlüssel-Prüfung beim COMMIT `FOR KEY SHARE` auf jedes referenzierte Ziel — hinter dem Ketten-/Kontingent-Lock, wenn niemand sie vorher genommen hat (#2259/#2260). Import- und Anhangspfad nehmen diese Ziele deshalb **vor** dem Ketten-Lock über denselben Helfer [`services/fk_locks.py`](../../src/core/services/fk_locks.py) (#2337): die Ziele werden aus dem Modell abgeleitet (ausdrücklich gesetzte Werte, für alle übrigen Fremdschlüssel der Modell-Default), in der Stärke `FOR KEY SHARE` und in der festen Reihenfolge Tabelle, dann PK. Im Anhangspfad nimmt `lock_batch_attachment_rows` sie im Anschluss an Owner-/Personenzeile und Bestandszeilen, noch vor dem ersten Ketten-Lock, und nur für Batches, die eine Zeile anlegen (Zugang oder Ersetzen). **Bewusste Ausnahme:** Anhänge an einem in derselben Transaktion **neu angelegten** Owner (`finalize_staged_files`) bekommen keine FK-Vorsperre. Die Owner-Zeile ist frisch und für keinen anderen Vorgang greifbar, die Personenzeile hat der Create-Service vor seiner Audit-Zeile gesperrt; die `FOR KEY SHARE`-Sperre auf die Feldvorlage (wie auf Einrichtung und anlegende Person) nimmt hier erst die FK-Prüfung beim COMMIT — sie fällt also hinter die Audit-Kette des Owner-Create. Ein später hinzukommender Fremdschlüssel ist damit in beiden Pfaden automatisch dabei; welche Werte der Anhangs-Insert ausdrücklich setzt, hält ein Inventar-Test gegen das Modell fest. Gepinnt in [`src/tests/test_fk_lock_targets_guard.py`](../../src/tests/test_fk_lock_targets_guard.py).

## Alternatives considered

- **Nichts tun.** Verworfen: der Befund aus §5 ist kein Wachstumsthema, sondern eine unterlaufene Frist — er wird ohne Eingriff nicht kleiner, sondern wächst mit jedem Fristablauf.
- **Option (a), Zeilen löschen.** Verworfen als *primäre* Strategie: der Mehrertrag ist real und nicht klein (~47 % einer UPDATE-, ~62 % einer CREATE-Zeile, einige zehn MB pro Jahr), aber nach §2 nicht die bindende Größe — und er kostet die Chronologie, die nach Ablauf der Audit-Frist nirgendwo sonst mehr steht und für delta-freie Speicherungen von vornherein nirgends stand. Als *zweite Stufe auf bereits redigierten Zeilen* bleibt die Mechanik ausdrücklich im Spiel (D8/M-7).
- **Option (c), Partitionierung mit Cold Storage.** Verworfen für jetzt (D7): die Partitionsgrenze ist gröber als der Löschanspruch (Fristen je Stratum bis 3650 Tage, Legal Hold), und ausgelagerte Zeilen entzögen sich Art. 15 und Art. 17 — Betroffenenrechte würden zur Handanleitung am externen Ablageort.
- **Synchrone Redaktion im Löschpfad.** Verworfen in der naheliegenden Form: der Bypass ist Owner-DDL, die App-Rolle scheitert mit `must be owner` und risse den Löschvorgang mit zurück (§4). Bleibt als `SECURITY DEFINER`-Variante offen (M-8).
- **Weniger schreiben statt später aufräumen** — nur geänderte Felder je Zeile speichern (echtes Delta). Verworfen als Ersatz: es senkt den Zufluss, lässt aber jede Bestandszeile unangetastet und wirkt gegen die §5-Lücke gar nicht. Bleibt als **ergänzender** Kandidat für eine spätere Ausbaustufe interessant, insbesondere zusammen mit M-3.
- **Historienzeile nur bei echtem Delta schreiben** (Symmetrie zum AuditLog). Verworfen: die Historie beantwortet „wer hat wann gespeichert", und ein Speichern ohne Änderung ist ein realer Bearbeitungsvorgang; die Entkopplung von der Audit-Bedingung ist in `update_event` ausdrücklich begründet.
- **Nur `data_before` redigieren, `data_after` behalten** (die wörtliche Ticket-Formulierung). Verworfen: `data_after` der Vorgängerzeile ist inhaltsgleich mit `data_before` der Folgezeile — die Werte blieben über die Kette vollständig rekonstruierbar, und die Ersparnis wäre etwa halbiert bei unverändertem DSGVO-Befund.

## References

- Schreiber: [`services/events/crud.py`](../../src/core/services/events/crud.py) (`create_event`, `update_event`, `soft_delete_event`), [`retention/enforcement.py`](../../src/core/retention/enforcement.py) (`_soft_delete_events`)
- Zeilenformat: [`models/event_history.py`](../../src/core/models/event_history.py), `_snapshot_field_metadata` in [`services/events/fields.py`](../../src/core/services/events/fields.py)
- Append-only und Rollen: Migrationen [`0012`](../../src/core/migrations/0012_eventhistory_append_only_trigger.py) / [`0107`](../../src/core/migrations/0107_append_only_triggers_enable_always.py), Bypass [`services/system/_db_admin.py`](../../src/core/services/system/_db_admin.py), [`deploy/postgres-init/01-app-role.sh`](../../deploy/postgres-init/01-app-role.sh), Guard `test_non_owner_runtime_role_cannot_disable_append_only_triggers` in [`src/tests/test_rls.py`](../../src/tests/test_rls.py), [`docs/ops-runbook.md`](../ops-runbook.md) § 9.5
- Bestehende Redaktionen: [`services/client/main.py`](../../src/core/services/client/main.py) (`_redact_event_history`, `anonymize_client_now`, `anonymize_eligible_soft_deleted_clients`), [`services/events/fields.py`](../../src/core/services/events/fields.py) (`build_redacted_delete_history`), Migration [`0074`](../../src/core/migrations/0074_redact_legacy_eventhistory_delete.py)
- Key-Rotation: [`services/file_vault/rotation.py`](../../src/core/services/file_vault/rotation.py) (`_reencrypt_event_history`), [`management/commands/reencrypt_fields.py`](../../src/core/management/commands/reencrypt_fields.py)
- Leser: [`services/events/context.py`](../../src/core/services/events/context.py), [`services/client/export.py`](../../src/core/services/client/export.py) (`_serialize_history_payload`, `_gather_event_history`), [`services/events/history_diff.py`](../../src/core/services/events/history_diff.py), [`admin/events.py`](../../src/core/admin/events.py) (`EventHistoryAdmin`)
- Vorbild-Politik: [`retention/audit_pruning.py`](../../src/core/retention/audit_pruning.py) (`prune_auditlog`, Checkpoint-Tombstone, § 26 BDSG als Fristbegründung)
