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
- **Eigene `Event.search_text`-Spalte** (Refs #827) statt `data_json__icontains` — gepflegt im `pre_save`-Signal aus `compute_event_search_text()`. Sie enthält nur Felder mit `is_encrypted=False` und `sensitivity=NORMAL`. Damit wandert der Sensitivity-Filter vom Read-Pfad in den Write-Pfad — die Suche kann nichts treffen, was der Nutzer nicht sehen darf.
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

- [`src/core/services/dashboard/search.py`](../../src/core/services/dashboard/search.py)
- [`src/core/services/events/fields.py`](../../src/core/services/events/fields.py) (`compute_event_search_text`)
- [`src/core/migrations/0081_add_event_search_text.py`](../../src/core/migrations/0081_add_event_search_text.py) (TrigramExtension + GIN-Index)
- Refs #580 (Trigram-Similarity), #827 (Suchindex-Spalte)

## Nachtrag (2026-10-06): Der Suchindex filtert nicht allein, das Lese-Gate bleibt nötig (Refs #2499)

Die Decision sagt, `search_text` enthalte nur Felder mit `sensitivity=NORMAL`, der Sensitivity-Filter sei damit „vom Read-Pfad in den Write-Pfad“ gewandert. Die Consequences leiten daraus ab, die Eigenschaft sei allein über `search_text` statisch prüfbar. Beides beschreibt den Schutz stärker, als der Schreibpfad allein ihn trägt.

**Ist-Stand:**

- **Schreibpfad** ([`compute_event_search_text`](../../src/core/services/events/fields.py), aufgerufen in `services/events/crud.py` und im `pre_save`-Signal [`signals/event_search.py`](../../src/core/signals/event_search.py)): Indexiert werden unverschlüsselte Felder mit ausdrücklich `normal` **oder mit geerbter Sensitivität** (`sensitivity == ""`). Ausgeschlossen sind verschlüsselte Felder, Felder mit eigener Stufe `elevated`/`high`, jeder unbekannte Wert und Datei-Marker. Ein Feld ohne eigene Stufe in einem Dokumentationstyp mit erhöhter oder hoher Sensitivität landet also im Klartext-Index.
- **Lesepfad** ([`event_search_queryset`](../../src/core/services/dashboard/search.py)): Die Suche filtert zusätzlich auf Ereignis-Ebene über den Typ (`document_type__sensitivity__in=allowed_sensitivities_for_user(user)`). Erst dieses Typ-Gate verhindert, dass eine Rolle über `search_text` Inhalte eines Typs findet, den sie nicht sehen darf.

Den Schutz tragen also **beide** Schichten: Der Schreibpfad hält Felder mit eigener erhöhter Stufe und verschlüsselte Felder aus dem Index, das Lese-Gate den Rest. Das Lese-Gate ist nicht verzichtbar. Wer `event_search_queryset` oder eine neue Suchfläche auf `search_text` baut, muss das Typ-Gate mitnehmen. Fortschreibung des Suchraums: [ADR-043](043-suchraum-volltextsuche.md#nachtrag-2026-10-06-fall-löschpfad-und-such-sensitivität-refs-2499).
