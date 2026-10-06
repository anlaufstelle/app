# ADR-036: Globale Block-Sequenz für Feldgruppen und ungruppierte Felder

- **Status:** Accepted
- **Date:** 2026-08-02
- **Deciders:** Tobias Nix
- **Refs:** #1713 (Produktentscheid), #1058 (M2-Vorarbeiten), #1056 (M0, abgelöste Zwei-Ebenen-Sortierung), #1709/#1710 (Slug-Stabilität), [ADR-024](024-slug-based-identifiers.md) (Slug-basierte Identifikatoren)

> **Nachtrag (2026-08-03, #1058, M2):** Die unter „Konsequente Anwendung auf allen Rendering-Flächen" genannte Implementierung `DynamicEventDataForm.grouped_fields()` liegt seit M2 auf der **Basisklasse** [`DynamicDataForm`](../../src/core/forms/dynamic.py) — `DynamicEventDataForm` erbt sie unverändert, ebenso die Person- und Fall-Ausprägungen. Die Block-Semantik und ihre Testfixierung bleiben identisch; es gibt sie jetzt einmal statt einmal je Entität. Das M2-Paketformat serialisiert genau diese Dual-Semantik von `sort_order` — siehe [ADR-037](037-konfigurationspakete-dynamische-felder.md).

## Context

M0 (#1056) führte eine **Zwei-Ebenen-Sortierung** ein: `FieldGroup.sort_order` ordnete die Gruppen eines Dokumentationstyps, `DocumentTypeField.sort_order` die Felder **innerhalb** ihrer Gruppe; ungruppierte Felder erschienen gesammelt in einem festen Abschnitt „Ohne Gruppe" **nach** allen Gruppen. Ein ungruppiertes Feld hatte damit keine Position relativ zu den Gruppen.

Der manuelle M0-Test des Maintainers ergab, dass genau das gebraucht wird: Formular-Layouts wie `Feld_x · Gruppe_X(…) · Feld_a · Gruppe_Y(…) · Feld_b`. Der beobachtete SortableJS-Neben-Effekt (Drop „in die Lücke" zwischen Gruppenkarten landete in der nächstgelegenen Gruppen-Feldliste) verstärkte den Eindruck, Zwischen-Positionierung sei vorgesehen, aber kaputt. Der Workaround — leere Zwischengruppen ohne Einklapp-Funktion — verschmutzt die Konfiguration.

Der Zeitdruck ist real: Das M2-Paketformat (#1058) referenziert `field_group.slug` und serialisiert die Feld-Ordnung eines Typs. Eine spätere Umstellung der Ordnungssemantik hätte das Paketformat migrieren müssen.

Erwogene Optionen (#1713):

1. **Globale Sequenz** — Position über Gruppen *und* ungruppierte Felder hinweg modellieren.
2. **Implizite anonyme Gruppen** — jeder „freie" Block wird intern eine Gruppe ohne Header. Kleinerer Modell-Eingriff, aber Merge-/Split-Sonderfälle beim Drag und auto-generierte anonyme Slugs im M2-Paketformat — für Slug-Stabilität kontraproduktiv.
3. **Status quo + Dead-Zone** — liefert das gewünschte Layout bewusst nicht.

## Decision

Wir modellieren **eine globale Block-Sequenz je Dokumentationstyp** (Option 1, Maintainer-Entscheid 2026-08-02). Ein **Block** ist entweder eine Feldgruppe oder ein einzelnes ungruppiertes Feld.

### Semantik (ohne Schema-Änderung)

- `FieldGroup.sort_order` = Position des Gruppen-Blocks in der globalen Sequenz.
- `DocumentTypeField.sort_order` bei `field_group IS NULL` = Position des Feld-Blocks in **derselben** globalen Sequenz.
- `DocumentTypeField.sort_order` bei gesetzter Gruppe = unverändert Position **innerhalb** der Gruppe (0..n-1).
- Deterministischer Tie-Break (nur für Alt-Daten/Races relevant, die Endpunkte renumerieren immer kompakt 0..n-1): Gruppe vor freiem Feld, dann Name.

Die Datenmigration `0115_global_block_sequence` überführt Bestandsdaten layout-erhaltend (Gruppen 0..k-1 in bisheriger Reihenfolge, ungruppierte Felder dahinter — das alte „Ohne Gruppe zuletzt"-Layout bleibt exakt bestehen).

### Konsequente Anwendung auf allen Rendering-Flächen

Die Block-Ordnung ist über die zentralen Service-Iteratoren (`iter_blocks_in_order`, `iter_fields_in_block_order`) implementiert und wird von Feld-Board (`board_context`), **Offline-Sync-Serialisierer** und **Feed-Vorschau** direkt verwendet. Das Ereignis-Formular (`DynamicEventDataForm.grouped_fields()`) wendet dieselbe Semantik auf BoundFields an (eigene, testfixierte Implementierung); aufeinanderfolgende freie Felder werden zu Runs zusammengeführt, die — sobald benannte Gruppen existieren — weiterhin unter der Überschrift „Ohne Gruppe" stehen. Ein flaches `order_by("sort_order")` über alle Felder eines Typs ist unter der neuen Semantik **falsch** (Innen-Indizes gruppierter Felder kollidieren mit globalen Positionen) — neue Konsumenten müssen die Iteratoren nutzen.

### Endpunkte und Board

- `config_reorder_groups` → **`config_reorder_blocks`**: nimmt die gemischte Top-Level-Reihenfolge als `blocks`-Array (`group:<uuid>`/`field:<uuid>`-Prefix-Codierung) mit strikter ID-Set-Validierung je Sorte entgegen.
- `config_move_field` ist zweiwegig: in eine Gruppe (Innen-Reihenfolge + Kompaktierung der globalen Sequenz) oder an eine Top-Level-Position (gemischtes `blocks`-Array).
- Gruppen-Löschung ersetzt den Gruppen-Block **an seiner Position** durch seine Felder in Innen-Reihenfolge — das sichtbare Layout bleibt erhalten (`SET_NULL` allein hätte Innen-Indizes als falsche Globalpositionen hinterlassen).
- Neue Gruppen und neue ungruppierte Felder werden ans **Sequenz-Ende** gesetzt (`_next_block_position`), nicht auf den Modell-Default 0.
- Board-UI: EIN Top-Level-Sortable (`data-sortable="blocks"`) mit Gruppen-Karten und freien Feld-Zeilen; der frühere „Lücken-Drop"-Neben-Effekt ist jetzt das Feature (Drop zwischen Gruppen = freier Block an dieser Position). SortableJS routet Vorfahr→Nachfahr-Drops nicht (empirisch verifiziert), daher erkennt der Blocks-onEnd den Beitritt eines freien Felds zu einer Gruppe per `elementFromPoint` an der Loslass-Position.

### Seed

Der Seed normalisiert am Ende jedes Laufs deterministisch: Gruppen kompakt 0..k-1 (Katalog-Reihenfolge), gruppierte Felder auf Innen-Index, ungruppierte Felder dahinter (#1710 stellt zugleich den Gruppen-Lookup auf stabile Katalog-Slugs um).

## Consequences

**Positiv:**

- Das gewünschte Interleaving-Layout ist ohne Workaround-Gruppen möglich; Board, Ereignis-Formular, Live-Vorschau, Offline-Editor und Feed-Vorschau zeigen dieselbe Reihenfolge.
- Das M2-Paketformat kann die endgültige Ordnungssemantik serialisieren — keine spätere Format-Migration.
- Keine Schema-Änderung, keine anonymen Slugs (Option 2 entfällt), Slug-Stabilität unberührt.

**Negativ / Kosten:**

- Die Doppel-Bedeutung von `DocumentTypeField.sort_order` (global für freie, innen für gruppierte Felder) ist implizit und nur über die Service-Iteratoren sicher konsumierbar — dokumentiert in Modell-Docstrings; flache `order_by("sort_order")`-Queries über gemischte Felder sind ein Bug-Muster.
- Reorder-Mutationen renumerieren mehr Zeilen als zuvor (kompakt 0..n-1 je Ereignis) — bei den realen Feld-Zahlen (≤ Dutzende) irrelevant.
- Die `elementFromPoint`-Erkennung im Board-JS ist ein bewusster Umweg um SortableJS-Routing-Grenzen; bei einem SortableJS-Major-Update erneut prüfen.

## Nachtrag 2026-10-06: Verworfene Alternative zur Blockordnung (Refs #2450)

Die Alternative getrennter Sortierräume für Gruppen und ungruppierte Felder wird zugunsten einer gemeinsamen Sequenz verworfen: sie könnte keine eindeutige Reihenfolge zwischen beiden Arten abbilden. Beleg: [FieldGroup.sort_order](../../src/core/models/field_group.py) und [DocumentTypeField.sort_order](../../src/core/models/document_type_field.py); der Entscheid und seine Sortierregeln bleiben maßgeblich.
