# ADR-016: Volltextsuche bleibt in PostgreSQL

- **Status:** Accepted
- **Date:** 2026-05-01
- **Deciders:** Tobias Nix

## Context

Die App soll eine schnelle Such-UX über Pseudonyme, Fall-Titel und Ereignis-Inhalte bieten — auch in einer Facility mit einigen tausend Ereignissen. Anforderungen:

- **Tippfehlertoleranz** für Pseudonyme — eine "Schmidt"-Suche soll auch "Schmitt" finden.
- **Sensitivity-respektierend** — die Suche darf keine Inhalte als Treffer zeigen, die der Nutzer im Detail-View nicht sehen würde.
- **Kein zusätzliches Backend** — eine On-Prem-Installation soll nicht zusätzlich Elasticsearch oder Meilisearch warten müssen.
- **Verschlüsselte Felder bleiben nicht durchsuchbar** — der Suchindex darf keinen Klartext aus Felder mit `is_encrypted=True` enthalten.

## Decision

- **PostgreSQL `pg_trgm`** als einziges Suchbackend. Trigram-Similarity auf `Client.pseudonym` liefert die Tippfehlertoleranz; ein zusätzlicher GIN-`gin_trgm_ops`-Index macht die `icontains`-Substring-Suche schnell.
- **Eigene `Event.search_text`-Spalte** ([]()) statt `data_json__icontains` — gepflegt im `pre_save`-Signal aus `compute_event_search_text()`. Sie enthält nur Felder mit `is_encrypted=False` und `sensitivity=NORMAL`. Damit wandert der Sensitivity-Filter vom Read-Pfad in den Write-Pfad — die Suche kann nichts treffen, was der Nutzer nicht sehen darf.
- **Trigram-Threshold pro Facility** in `Settings.search_trigram_threshold` (Default 0.3) — kleine Facilities mit ungewöhnlichen Pseudonymen können den Schwellwert anheben.
- **Kein WebSocket-Live-Search**: HTMX-Debounce-Endpoints reichen für die UI-Anforderungen aus.

## Consequences

- **+** Eine Datenbank, ein Backup-Pfad, ein Patchzyklus. Operations sparen sich Elasticsearch/Meilisearch komplett.
- **+** Sensitivity-Sicherheitseigenschaft ist statisch geprüfbar: was nicht in `search_text` landet, taucht in keinem Suchergebnis auf.
- **+** GIN-Index auf `search_text` macht `icontains` O(log n); auf 100k Events laut `pg_trgm`-Doku ~50–200× schneller als der Seq-Scan über JSONB.
- **−** Sprachspezifisches Stemming (Lemma-Reduktion deutscher Substantive) ist aktuell nicht aktiviert — `tsvector` mit deutschem Wörterbuch wäre ein nächster Schritt; aktuell überfordert das die Maintainability ohne klaren UX-Gewinn.
- **−** Bei sehr großen Facilities (>1 Mio. Events) kann der GIN-Index hinter externen Suchindizes zurückbleiben. Die Maßnahme dafür wäre, ELT in Meilisearch/OpenSearch zu spiegeln — nicht für Default-Setup.

## Alternatives considered

- **Externes Suchbackend (Elasticsearch / Meilisearch / Typesense):** Stärkere Suche, aber zusätzlicher Operations-Aufwand. Bleibt eine Option für künftige ADR, wenn ein Träger an die PG-Grenzen kommt.
- **`tsvector` + GiN-Index mit deutschem Wörterbuch:** Stärker für Stemming, aber Mehraufwand bei Mehrsprachigkeit (DE/EN-UI) und beim Index-Refresh nach Wörterbuch-Updates.
- **Suche direkt auf `data_json__icontains`:** War der Vorgängerstand. Skaliert nicht, kann verschlüsselte Marker als Treffer scannen — verworfen mit C-60.
- **Pseudonym-Hashing für unscharfe Suche:** Verwirft die Trigram-Eigenschaft; Tippfehler werden nicht mehr erkannt. Verworfen.

## References

- [`src/core/services/search.py`](././src/core/services/search.py)
- [`src/core/services/events/fields.py`](././src/core/services/events/fields.py) (`compute_event_search_text`)
- [`src/core/migrations/0081_add_event_search_text.py`](././src/core/migrations/0081_add_event_search_text.py) (TrigramExtension + GIN-Index)
- (Trigram-Similarity) (Suchindex-Spalte)
