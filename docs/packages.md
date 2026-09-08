# Konfigurationspakete (Schema v1)

Ein **Konfigurationspaket** ist eine YAML-Datei, die Feldvorlagen, Dokumentationstypen, Feldgruppen, Feldzuordnungen und Feldbedingungen einer Einrichtung beschreibt. Pakete machen Formular-Konfiguration teilbar: Sie lassen sich versionieren, reviewen, zwischen Trägern austauschen und **idempotent** einspielen — ein zweiter Lauf gegen denselben Stand ändert nichts.

Der mitgelieferte Standardkatalog ist selbst ein Paket: [`src/core/packages/default.yaml`](../src/core/packages/default.yaml). Jeder `make seed`-Lauf spielt ihn über denselben Loader ein, den auch `manage.py load_package` benutzt.

- **Warum das Format so aussieht, wie es aussieht:** [ADR-037](adr/037-konfigurationspakete-dynamische-felder.md).
- **Warum `sort_order` zwei Bedeutungen hat:** [ADR-036](adr/036-globale-block-sequenz-feldordnung.md).
- **Welche Operatoren zu welchem Feldtyp passen:** [ADR-035](adr/035-evaluator-semantik-bedingte-feldlogik.md).

> **Der Slug ist die Identität.** Der Loader findet Bestandsobjekte ausschließlich über ihren Slug. Ein geänderter Slug legt in **jeder** Bestands-Einrichtung ein Duplikat an, statt das vorhandene Objekt zu aktualisieren. Slugs sind nach dem Anlegen in Modell **und** Datenbank-Trigger unveränderlich — es gibt kein „Umbenennen" auf Slug-Ebene.

---

## Mitgelieferter Katalog

Mitgelieferte Pakete liegen flach unter `core/packages/<name>.yaml`. Der technische Name wird bei `setup_facility --package <name>` und `load_package --package <name>` verwendet.

| Name | Domäne | Typen | Besonderheiten | Doku |
|---|---|---:|---|---|
| `default` | Niedrigschwellige Hilfe | 9 | Drei Standardschichten und zwei Schnellvorlagen am Typ „Kontakt“ | [Standardkatalog und Schema v1](#1-aufbau) |
| `beratungsstelle` | Offene Sprechstunde / Sozialberatung | 8 | Anonyme Kurzberatung, Beratungsgespräch ab Pseudonym, deaktivierbares Hausverbot | [Beratungsstelle-Paket](packages-beratungsstelle.md) |
| `awareness` | Awareness-Teams bei Veranstaltungen | 7 | Anonyme Kurzkontakte, sensibles Vorfallsprofil, kein Datei-Feld | [Awareness-Paket](packages-awareness.md) |
| `frauenhaus` | Schutzwohnung / Gewalt-Fachberatung | 8 | Pseudonyme Dokumentation, hochsensible Gefährdungslage, Schutzmaßnahmen ohne Hausverbotslogik | [Frauenhaus-Paket](packages-frauenhaus.md) |
| `jugendarbeit` | Mobile und offene Jugendarbeit | 8 | Anonyme Strichlisten, Gruppenangebote und U18-orientierte Fallarbeit | [Jugendarbeit-Paket](packages-jugendarbeit.md) |

Domänen-Pakete: siehe die Zeilen, die die jeweiligen Paket-Issues ergänzen ([#2145 Beratungsstelle](https://github.com/anlaufstelle/app/issues/2145), [#2137 Awareness](https://github.com/anlaufstelle/app/issues/2137), [#2138 Frauenhaus](https://github.com/anlaufstelle/app/issues/2138), [#2139 Jugendarbeit](https://github.com/anlaufstelle/app/issues/2139)).

---

## 1. Aufbau

Ein Paket hat drei Top-Level-Schlüssel:

| Schlüssel | Pflicht | Bedeutung |
|---|---|---|
| `schema_version` | **ja** | Muss `1` sein. Andere Werte werden abgewiesen. |
| `field_templates` | nein | Feldvorlagen der Einrichtung (facility-weit, wiederverwendbar über mehrere Typen). |
| `document_types` | nein | Dokumentationstypen mit ihren Gruppen, Feldzuordnungen und Bedingungen. |

Unbekannte Schlüssel sind auf **jeder** Ebene ein Fehler — ein Tippfehler wie `sort_oder` soll auffallen, statt still zu verpuffen.

### Vollständiges Beispiel

Gekürzter Ausschnitt aus [`default.yaml`](../src/core/packages/default.yaml); er zeigt jeden Formularbestandteil mindestens einmal.

```yaml
schema_version: 1

field_templates:
  # Eine Vorlage, die mehrere Dokumentationstypen teilen — genau einmal
  # deklariert, mehrfach per field_refs referenziert.
  - slug: dauer
    name: Dauer
    field_type: number
    min_value: 0          # optional; nur bei field_type: number
    max_value: 480         # optional; darf nicht unter min_value liegen
    is_encrypted: false
    sensitivity: ""
    help_text: Dauer in Minuten

  - slug: alterscluster
    name: Altersgruppe
    field_type: select
    is_encrypted: false
    sensitivity: ""
    help_text: Geschätztes Alter
    # PFLICHT bei select/multi_select: Weglassen LEERT die Optionsliste.
    options:
      - {slug: u18, label: "U18", is_active: true}
      - {slug: "18-26", label: "18-26", is_active: true}
      - {slug: "27-plus", label: "27+", is_active: true}
      # Nicht löschen, sondern deaktivieren: bereits erfasste Werte behalten
      # ihr Label, neue Einträge können die Option nicht mehr wählen.
      - {slug: unbekannt, label: Unbekannt, is_active: false}

  - slug: notiz-krise
    name: Notiz (Krise)
    field_type: textarea
    is_encrypted: true
    sensitivity: high        # high erzwingt is_encrypted: true

  - slug: strassenkontakt
    name: Straßenkontakt
    field_type: boolean
    is_encrypted: false
    sensitivity: ""

  - slug: anhang
    name: Anhang
    field_type: file         # an event, client und case zulässig (seit M24)
    is_required: false
    is_encrypted: true       # bei file modellseitig erzwungen
    sensitivity: ""

document_types:
  - slug: kontakt            # Lookup-Key — muss exakt stimmen
    name: Kontakt
    entity_type: event       # event | client | case — unveränderlich
    tally_enabled: true      # explizites Opt-in für die Strichliste
    # system_type: crisis    # nur ban | crisis, nur bei entity_type: event;
    #                        # wo einer existiert, immer mitführen
    report_key: contact      # Berichtszuordnung; leer→gesetzt ok, gesetzt→anders nicht
    sensitivity: normal
    icon: users
    color: indigo
    sort_order: 0            # Position des Typs in der Typ-Liste
    groups:
      # sort_order = Position des Gruppen-Blocks in der globalen Block-Sequenz
      - {slug: basisdaten, name: Basisdaten, sort_order: 0, is_collapsible: false}
      - {slug: leistungen-notizen, name: "Leistungen & Notizen", sort_order: 1, is_collapsible: true}
    field_refs:
      # MIT group_slug: sort_order = Innen-Index in der Gruppe (0..n-1)
      - {template_slug: dauer, group_slug: basisdaten, sort_order: 0}
      - {template_slug: alterscluster, group_slug: basisdaten, sort_order: 1}
      - {template_slug: notiz-krise, group_slug: leistungen-notizen, sort_order: 0}
      - {template_slug: anhang, group_slug: leistungen-notizen, sort_order: 1}
      # OHNE group_slug: sort_order = eigene Position in der globalen
      # Block-Sequenz — dieses freie Feld steht NACH beiden Gruppen (0, 1).
      - {template_slug: strassenkontakt, sort_order: 2}
    conditions:
      # Zeige „notiz-krise", wenn „dauer" größer als 10 ist.
      - {target: notiz-krise, depends_on: dauer, operator: greater_than, value: 10, group_index: 0}

  # Ein Typ ohne groups: alle Felder sind freie Blöcke und tragen ihre
  # Position in der globalen Block-Sequenz direkt.
  - slug: aufnahmebogen
    name: Aufnahmebogen
    entity_type: client      # Zusatzfelder an der Person
    sensitivity: normal
    sort_order: 0
    field_refs:
      - {template_slug: alterscluster, sort_order: 0}
      # Datei-Felder an Person und Fall (seit M24, ADR-039). Bewusst
      # auskommentiert: der Standardkatalog liefert KEIN file-Feld an einer
      # Personen-/Fall-Vorlage aus — ob eine Einrichtung Dokumente an der Person
      # ablegt, ist eine Trägerentscheidung, kein Auslieferungs-Default.
      # Zum Aktivieren die Zeile einkommentieren; die Vorlage `anhang` ist oben
      # bereits deklariert und wird hier nur ein zweites Mal referenziert.
      # - {template_slug: anhang, sort_order: 1}
```

---

## 2. Feld-Referenz

Pflicht sind nur **Identität** und Werte ohne sinnvollen Modell-Default. Alles andere darf fehlen und fällt dann auf den Default zurück — ein Minimal-Eintrag ist `{slug, name}`.

### `field_templates[]`

| Schlüssel | Typ | Default | Anmerkung |
|---|---|---|---|
| `slug` | Slug (`a-z`, `0-9`, `-`) | **Pflicht** | Lookup-Key, facility-weit eindeutig, unveränderlich. **Reservierte Slugs** (feste Formularfeld-Namen) sind ein Schema-Fehler — s. Hinweis unter der Tabelle |
| `name` | nicht-leerer String | **Pflicht** | Muss im Paket eindeutig sein (UI-Unterscheidbarkeit) |
| `field_type` | `text`, `textarea`, `number`, `date`, `time`, `boolean`, `select`, `multi_select`, `file` | `text` | **Unveränderlich** nach dem Anlegen |
| `min_value`, `max_value` | ganze Zahl, `null` oder Schlüssel weglassen | *(nicht anfassen)* | Nur bei `field_type: number`; `min_value` darf nicht größer als `max_value` sein. **Tri-State** (#1934), siehe Hinweis unter der Tabelle |
| `is_required` | Bool | `false` | Pflichtfeld im Formular |
| `is_encrypted` | Bool | `false` | **Unveränderlich**; bei `file` und `sensitivity: high` erzwungen |
| `sensitivity` | `""`, `normal`, `elevated`, `high` | `""` | `""` = erbt vom Dokumentationstyp; `high` erfordert `is_encrypted: true` |
| `options` | Liste | *(leer)* | Nur bei `select`/`multi_select`. **Weglassen leert die Liste** |
| `help_text` | String | `""` | |
| `default_value` | String | `""` | Wird bei jedem Lauf aus dem Paket geschrieben |
| `report_key` | Schlüssel (`a-z`, `0-9`, `_`) oder `null` | `null` | Berichtszuordnung der Feldvorlage (#1849). **Nicht** gegen eine Werteliste geprüft — s. Hinweis unter der Typ-Tabelle. `null`/weglassen = keine Angabe (Bestand bleibt unangetastet) |

`options[]`-Einträge: `slug` (Pflicht, Slug-Format, eindeutig je Vorlage), `label` (Pflicht, nicht leer), `is_active` (Bool, Default `true`).

> **Zahlenbereichsgrenzen `min_value`/`max_value` als Tri-State (#1934):** Fehlt der Schlüssel, bleibt eine bestehende Grenze **unangetastet** — dasselbe Muster wie `tally_enabled`. Ein explizites `null` löscht die Grenze bewusst, ein Zahlenwert setzt sie. Eine Neuanlage ohne Schlüssel nutzt den Modell-Default `null` (keine Grenze).
>
> **Vorher/Nachher — ein echter Semantikwechsel, kein Bugfix an undokumentiertem Verhalten:** Bis zu dieser Änderung führte die Rücksync-Tabelle unten `min_value`/`max_value` ausdrücklich als „wird zurückgesetzt": Ein erneuter Lauf zog eine weggelassene Grenze **absichtlich** auf `NULL` zurück, auch eine über die Konfigurationsoberfläche gepflegte. Das war zugesichertes, dokumentiertes Verhalten. Die neue Regel gilt trotzdem **sofort** für jedes bestehende v1-Paket, ohne `schema_version`-Sprung, weil der Effekt nachweislich nur in eine Richtung wirkt: Die neue Semantik löscht in **keinem** Fall mehr, als die alte gelöscht hätte — Weglassen löscht jetzt nie, wo es vorher immer löschte; ein explizites `null` löscht in beiden Fassungen unverändert. Wer die alte „bei jedem Lauf zurücksetzen"-Wirkung an einem Feld ausdrücklich weiter will, trägt `min_value: null`/`max_value: null` ein. `default.yaml` selbst zeigt den Praxis-Impact: `dauer`, `ausgabe` und `rueckgabe` führen mindestens eine der beiden Grenzen nicht mit — jeder Reseed einer Bestandsinstallation hätte dort eine per Konfigurationsoberfläche gesetzte Grenze bisher stillschweigend gelöscht.
>
> **Betreiber-Sichtbarkeit ohne Dauerwarnung:** Weglassen ist für die meisten Zahlenfelder der Normalfall (die meisten Vorlagen verwalten gar keine Grenze) — eine Warnung bei jedem fehlenden Schlüssel würde jeden gewöhnlichen Lauf zuspammen. Der Report listet stattdessen einen eigenen, bei jedem Lauf frisch berechneten Abschnitt „**Unangetastete Bestandsgrenzen**", aber **nur**, wenn dadurch tatsächlich ein vorhandener Wert erhalten bleibt. Er unterscheidet damit „unverändert, weil der Schlüssel fehlt (Bestand bleibt: `min_value=5`)" von „unverändert, weil Paket- und Bestandswert gleich sind" (Letzteres bleibt ein normaler `unverändert`-Eintrag ohne eigene Zeile) — ohne dafür eine Warnung zu erzeugen. Der Abschnitt erscheint gleichermaßen im Echtlauf und im `--dry-run`.
>
> **Bereichsverschärfung ohne stillen Datenumbau (#1914):** `min_value` und `max_value` gelten für künftige Schreibvorgänge; eine Migration oder ein Paketlauf verändert keine bereits gespeicherten `data_json`-Werte. Vor dem Setzen oder Verschärfen einer Grenze daher mit der Admin-DB-Rolle `python src/manage.py inventory_number_ranges --facility <UUID-oder-Name>` ausführen. Das Inventar nennt nur Entitätsart, Dokumentationstyp, Feld-Slug und Anzahl — nie gespeicherte Werte — und schreibt nichts.

> **Datei-Felder an allen drei Entitäten (seit Milestone 24, #1738, [ADR-039](adr/039-owner-generische-dateianhaenge.md)):** `field_type: file` war bis dahin **nur an `entity_type: event`** zulässig; ein `field_ref` auf ein Datei-Feld unter einem Personen- oder Fall-Typ war ein Schema-Fehler. Diese Restriktion ist **ersatzlos entfallen** — Anhänge hängen jetzt an Ereignis, Person oder Fall. Ein Beispiel steht (auskommentiert) im Paket oben unter `aufnahmebogen`.
>
> **Kein `schema_version`-Bump:** die Lockerung ist **abwärtskompatibel** — jedes bisher gültige Paket bleibt unverändert gültig, es wird lediglich etwas erlaubt, was vorher abgewiesen wurde. Pakete bleiben bei `schema_version: 1`. Umgekehrt gilt das nicht: ein Paket mit `file` an einem Personen-/Fall-Typ lässt sich auf einem Stand vor Milestone 24 **nicht** einspielen.

> **Reservierte Slugs (#1726):** Ein `slug`, der einen festen Formularfeld-Namen trifft, wird als **Schema-Fehler** abgelehnt — das dynamische Feld würde sonst nackt unter seinem Slug im selben `<form>` das gleichnamige feste Feld überschreiben (stiller Datenverlust). Betroffen sind u. a. `pseudonym`, `notes`, `client`, `title`, `description`, `case`, `document_type`, `lead_user`, `contact_stage`, `age_cluster`, `occurred_at`; die vollständige, an allen Schichten (Paket-Schema, Config-Formular, Modell) geteilte Liste steht als `_RESERVED_SLUGS` in `core/models/document_type.py`. Dieselbe Regel gilt für das nachträgliche **Zuordnen** einer bestehenden Feldvorlage mit reserviertem Slug über die Config-UI.

### `document_types[]`

| Schlüssel | Typ | Default | Anmerkung |
|---|---|---|---|
| `slug` | Slug | **Pflicht** | Lookup-Key, facility-weit eindeutig, unveränderlich |
| `name` | nicht-leerer String | **Pflicht** | muss im Paket eindeutig sein |
| `entity_type` | `event`, `client`, `case` | `event` | **Unveränderlich** nach dem Anlegen |
| `tally_enabled` | Bool oder Schlüssel weglassen | *(nicht anfassen)* | Explizites Opt-in für die Strichliste. `true`/`false` wird geschrieben; fehlt der Schlüssel, bleibt ein bestehender Konfigurationswert erhalten, bei einem neuen Typ gilt der Modell-Default `false` |
| `system_type` | `ban`, `crisis` oder `null` | `null` | Nur bei `entity_type: event`. **Unveränderlich**, Ausnahme `null → Wert`. Seit #1850 nur noch die beiden Werte mit App-Wirkung |
| `sensitivity` | `normal`, `elevated`, `high` | `normal` | steuert Rollen-Sichtbarkeit |
| `min_contact_stage` | `identified`, `qualified` oder `null` | `null` | Mindest-Kontaktstufe der Person — **wirkt nur bei `entity_type: event`** |
| `report_key` | Schlüssel (`a-z`, `0-9`, `_`) oder `null` | `null` | Berichtszuordnung des Typs (#1849). **Unveränderlich**, Ausnahme `null → Wert`; `null`/weglassen = keine Angabe |
| `icon`, `color` | String | `""` | |
| `sort_order` | Int | `0` | Position des Typs in der Typ-Auswahl |
| `groups` | Liste | *(leer)* | additiv — siehe unten |
| `field_refs` | Liste | *(leer)* | additiv — siehe unten |
| `conditions` | Liste **oder Schlüssel weglassen** | *(nicht anfassen)* | Tri-State, siehe § 3 |

> **Berichtsschlüssel `report_key` (#1849):** Der Wert wird **nicht** gegen eine Werteliste geprüft. Der Werteraum kommt aus den Deklarationen der Reporting-Backends (`docs/reporting-plugins.md`) und kann in einem Fremdpaket ein anderer sein; ein Parserfehler machte dort das **ganze** Paket unladbar, obwohl nur eine Zeile fremd ist. Geprüft wird nur das **Format** (`a-z`, `0-9`, `_`) — das fängt den Tippfehler, ohne die Konvention zu erzwingen. Ein Schlüssel, den kein Backend kennt, erscheint stattdessen im Vollständigkeitshinweis des Berichts (`unknown_report_keys`).
>
> **Ehemals gültige Werte werden normalisiert:** `report_key: note` und `report_key: ban` werden zu `excluded` (dem Sentinel „gehört ausdrücklich in keinen Bericht") — mit einer **Warnung** im Report, damit die Übersetzung nicht still passiert. Die sieben zählenden Werte (`contact`, `crisis`, `medical`, `needle_exchange`, `accompaniment`, `counseling`, `referral`) heißen als Berichtsschlüssel genauso und laufen unverändert durch.
>
> **Weglassen heißt „keine Angabe", nicht „entfernen":** anders als bei `system_type` ist ein fehlender `report_key` gegen einen gesetzten Bestandswert **kein** Fehler — jedes vor dieser Änderung veröffentlichte Paket bleibt gegen eine aktualisierte Instanz ladbar. Ein gesetzter Schlüssel lässt sich über das Paketformat nicht entfernen; das verbietet die Änderbarkeits-Regel ohnehin.
>
> **Zwei Ebenen, zwei Fragen.** `report_key` gibt es an beiden Objektarten, und sie beantworten Verschiedenes: Der Schlüssel am **Dokumentationstyp** entscheidet, **welche Ereignisse** ein Bericht zählt (er wirkt nur an Typen mit `entity_type: event`); der Schlüssel an der **Feldvorlage** entscheidet, **woher** ein Bericht seine Werte liest (Minuten, Stückzahlen, Auswahlwerte). Ein Bericht über Feldwerten braucht deshalb beides — einen Typ mit passendem Schlüssel **und** eine Feldvorlage mit dem Feld-Schlüssel, die per `field_refs` an genau diesem Typ hängt und dort aktiv ist; manche Berichte fordern mehrere davon am selben Typ (der Beispiel-Spritzentausch je eine für Ausgabe und Rückgabe).
>
> **Fehlt die Feldvorlage, entstehen keine Nullwerte — der Bericht verschwindet.** Trägt kein passender aktiver Typ sie, gilt der Bericht für diese Einrichtung als nicht verfügbar und wird gar nicht erst angeboten (ein Direktaufruf endet mit „nicht verfügbar"). Nur wenn sie an *einem* der gebundenen Typen fehlt, zählen dessen Ereignisse weiter mit, tragen aber nichts zur Summe bei — der Bericht weist das als Vollständigkeitshinweis aus.
>
> **`excluded` ist nicht dasselbe wie Weglassen.** Beides hält den Typ aus jedem Bericht heraus; nur `excluded` ist zugleich eine vollständige Aussage. Ein Typ **ohne** Schlüssel erscheint im Vollständigkeitshinweis der Berichte, die alle Ereignisse gegenrechnen („… Dokumentationstypen ohne Berichtszuordnung im Zeitraum") — ein Typ mit `excluded` nicht (#1819). Für Typen, die bewusst nie in einem Bericht auftauchen sollen, ist `excluded` deshalb der richtige Wert; `default.yaml` führt ihn an `notiz` und `hausverbot`. Aber mit Bedacht setzen: `excluded` zählt als **gesetzt** und ist danach unveränderlich (Tabellenzeile oben) — beim Empfänger eines Pakets bleibt dann nur Deaktivieren und Neuanlegen. Ausführlich: [admin-guide.md § 2.5](admin-guide.md#25-dokumentationstypen-konfigurieren).

> **Strichlisten-Opt-in `tally_enabled` (#1637, #1906):** Der Schlüssel akzeptiert nur die YAML-Wahrheitswerte `true` und `false`. Bei einem bestehenden Typ ist sein Fehlen absichtlich nicht gleichbedeutend mit `false`: Das Paket lässt ein in der Konfigurationsoberfläche gepflegtes Opt-in unverändert. Ein explizites `true` oder `false` synchronisiert den Wert dagegen zurück. Die Freigabe allein reicht nicht für die Anzeige: Die Strichliste nimmt nur aktive Ereignis-Typen ohne Mindest-Kontaktstufe; sie kann Pflichtwerte nur dann inline erfassen, wenn **alle** Pflichtfelder Zahlen sind.

> **Entfallene `system_type`-Werte (#1850):** Die sieben reinen Berichtswerte (`contact`, `medical`, `needle_exchange`, `accompaniment`, `counseling`, `referral`, `note`) sind aus dem Enum entfernt. Ein Paket, das einen davon noch mitführt, **bleibt ladbar** — der Wert entfällt ersatzlos, mit einer **Warnung** im Report (dieselbe Linie wie bei `category`: kein Fehler, aber auch kein stiller Wegfall). Ein *unbekannter* Wert bleibt dagegen ein Fehler: Anders als beim `report_key` ist der Werteraum hier eine feste, eingefrorene Liste (RFC #1748) und kein Fremdvokabular. Die Berichtszuordnung, die die sieben Werte trugen, steht im `report_key`; beim Update hat sie eine Datenmigration übernommen. Empfehlung: beim nächsten Pflege-Durchgang aus dem Paket entfernen.

> **Ignorierter Schlüssel `category` (#1749, [ADR-040](adr/040-documenttype-category-entfernt.md)):** `DocumentType.category` gibt es nicht mehr. Ein Paket darf den Schlüssel weiterhin enthalten — es bleibt gültig und ladbar, es gibt **keine** Schemaversion 2, aber der Wert wird nicht angewendet. Jeder betroffene Typ erscheint mit einer **Warnung** im Report („Diese Schlüssel gibt es im Datenmodell nicht mehr …“), damit die Zeile nicht still verschwindet. Empfehlung: beim nächsten Pflege-Durchgang aus dem Paket entfernen.

### `groups[]` · `field_refs[]` · `conditions[]`

| Abschnitt | Schlüssel | Anmerkung |
|---|---|---|
| `groups[]` | `slug` (Pflicht), `name` (Pflicht), `sort_order` (Int, `0`), `is_collapsible` (Bool, `false`) | Slug eindeutig **je Dokumentationstyp** |
| `field_refs[]` | `template_slug` (Pflicht), `group_slug` (Slug oder weglassen), `sort_order` (Int, `0`) | `template_slug` muss unter `field_templates` stehen, `group_slug` unter `groups` **desselben Typs**; jede Vorlage höchstens einmal je Typ |
| `conditions[]` | `target` (Pflicht), `depends_on` (Pflicht), `operator` (Pflicht), `value` (JSON-typisiert, Default `null`), `group_index` (Int ≥ 0, `0`) | `target`/`depends_on` sind **Feldvorlagen-Slugs** und müssen in den `field_refs` desselben Typs stehen; `target != depends_on` |

Operatoren: `equals`, `not_equals`, `contains`, `greater_than`, `less_than`, `is_empty`, `is_not_empty`. **Welche Kombination aus Operator und Quell-Feldtyp gültig ist, prüft nicht das Schema, sondern das Modell beim Laden** ([ADR-035](adr/035-evaluator-semantik-bedingte-feldlogik.md) enthält die Matrix). Solche Verstöße erscheinen als Loader-Fehler mit dem Bezeichner `<typ-slug>/<ziel><-<quelle>`, nicht als Schema-Pfadfehler. Innerhalb eines `group_index` gilt UND, über verschiedene `group_index` hinweg ODER.

### `sort_order` — eine Zahl, zwei Bedeutungen

Seit [ADR-036](adr/036-globale-block-sequenz-feldordnung.md) teilen sich Feldgruppen und ungruppierte Felder **eine** Sequenz je Dokumentationstyp. Ein **Block** ist entweder eine Gruppe oder ein einzelnes freies Feld:

| Ort | Bedeutung von `sort_order` |
|---|---|
| `groups[].sort_order` | Position des **Gruppen-Blocks** in der globalen Block-Sequenz |
| `field_refs[].sort_order` **ohne** `group_slug` | Position des **freien Feld-Blocks** in **derselben** globalen Sequenz |
| `field_refs[].sort_order` **mit** `group_slug` | Innen-Index (`0..n-1`) **innerhalb** der Gruppe |
| `document_types[].sort_order` | Position des Typs in der Typ-Auswahl (unabhängig davon) |

So entstehen Layouts wie `Feld_x · Gruppe_A(…) · Feld_a · Gruppe_B(…)`. Im Beispiel oben tragen `basisdaten` = 0, `leistungen-notizen` = 1 und `strassenkontakt` = 2 die drei Blockpositionen; `dauer` = 0 / `alterscluster` = 1 sind Innen-Indizes von `basisdaten`. Der Loader schreibt die Werte **1:1** — das Paket ist deklarativ, es wird nichts umnummeriert.

> **Blockpositionen müssen eindeutig sein (Schema-Fehler).** Weil der Loader die Werte 1:1 übernimmt, ist jede doppelt vergebene Position ein **Schema-Fehler**, nicht bloß eine Warnung (sonst wäre die Reihenfolge undefiniert und von der DB-Zeilenordnung abhängig). Konkret:
> - In der **globalen Block-Sequenz** eines Typs muss jede Position eindeutig sein — über Gruppen (`groups[].sort_order`) **und** ungruppierte Felder (`field_refs[].sort_order` **ohne** `group_slug`) hinweg. Zwei Gruppen auf `0`, oder eine Gruppe auf `0` und ein freies Feld auf `0`, sind ein Fehler.
> - **Innerhalb einer Gruppe** muss der Innen-Index (`field_refs[].sort_order` **mit** `group_slug`) je Gruppe eindeutig sein.
> - Beide Ebenen sind **unabhängig**: ein Innen-Index `0` darf gleich einer globalen Blockposition `0` sein, und derselbe Innen-Index in zwei verschiedenen Gruppen ist erlaubt.
>
> Achtung Default: `sort_order` fehlt ⇒ `0`. Mehrere freie Felder **ohne** `sort_order` landen damit alle auf Position `0` und kollidieren — freie Felder brauchen explizite, verschiedene Positionen.

---

## 3. Loader-Semantik

Der Loader ist ein Upsert über den Slug mit Diff: nur bei echter Abweichung wird geschrieben.

### Report-Buckets

| Bucket | Bedeutung |
|---|---|
| `angelegt` (`created`) | Objekt existierte nicht und wurde neu angelegt |
| `aktualisiert` (`updated`) | Objekt existierte, mindestens ein Wert wich ab und wurde überschrieben |
| `unverändert` (`unchanged`) | Objekt existierte und stimmte in allen vom Paket abgedeckten Werten überein |
| `Warnungen` | Nicht-blockierende Hinweise: verschwindende Options-Slugs, ersetzte/entfernte Bedingungen |
| `Fehler` | Mindestens ein Objekt konnte nicht angewendet werden ⇒ **der gesamte Lauf rollt zurück** |

**Idempotenz-Probe:** Ein zweiter Lauf direkt nach einem erfolgreichen ersten muss in **jedem** Abschnitt `angelegt (0)` und `aktualisiert (0)` melden. Tut er das nicht, ist entweder das Paket oder der Loader fehlerhaft.

### Was der Loader anfasst — und was nicht

| Angabe im Paket | Wirkung |
|---|---|
| Wert steht drin | wird geschrieben (auch wenn er per UI geändert wurde — **Rücksync**) |
| Einzelwert-Schlüssel fehlt (z. B. `icon`, `help_text`, `options`, `default_value`) | Der **Modell-Default** wird geschrieben; das ist **nicht** „unangetastet lassen" |
| `tally_enabled` fehlt | Ausnahme: Am bestehenden Typ bleibt ein in der Konfigurationsoberfläche gepflegter Wert **unangetastet**; beim Anlegen gilt der Modell-Default `false` |
| `min_value`/`max_value` fehlen (#1934) | Ausnahme, je Grenze einzeln: An der bestehenden Vorlage bleibt eine gepflegte Grenze **unangetastet**; `null` löscht sie bewusst; beim Anlegen gilt der Modell-Default `null` (keine Grenze) |
| `conditions`-Schlüssel fehlt | Bestehende Bedingungen bleiben **unangetastet** |
| `conditions: []` | Alle Bedingungen des Typs werden **gelöscht** |
| `conditions: [ … ]` | Wholesale-Replace; bei inhaltlicher Gleichheit passiert nichts (`unverändert`) |
| Gruppe/Feldzuordnung nicht (mehr) erwähnt | bleibt **stehen** — v1 ist additiv und entfernt nie |
| Feld außerhalb des Schemas | wird nie angefasst: `retention_days`, `description`, `is_active` (Typ); `is_active` (Vorlage) |

### Unveränderliche Werte

Weicht einer dieser Werte vom Bestand ab, ist das ein **Fehler** — der Loader wechselt sie nie still:

| Objekt | Unveränderlich | Ausnahme |
|---|---|---|
| Feldvorlage | `slug`, `field_type`, `is_encrypted`, `report_key` | `report_key: null → Wert`; Schlüssel im Paket weglassen = keine Angabe |
| Dokumentationstyp | `slug`, `entity_type`, `system_type`, `report_key` | `system_type: null → Wert` (Bestandsdaten vor M2); `report_key: null → Wert`, Weglassen = keine Angabe |
| Feldgruppe | `slug` ||

`is_encrypted` wird **vor** dieser Prüfung nach oben normalisiert: `field_type: file` und `sensitivity: high` erzwingen `true` (wie im Modell). Ein Paket mit `field_type: file` ohne `is_encrypted: true` bleibt dadurch idempotent. Ein *explizites* `is_encrypted: false` an einem `file`-Feld ist dagegen ein Schema-Fehler.

### Alles-oder-nichts

Der ganze Lauf liegt in **einer** Transaktion. Fehler werden über den kompletten Lauf **gesammelt** (jeder mit Bezeichner) und am Ende gebündelt gemeldet; danach rollt alles zurück — auch die bis dahin erfolgreichen Objekte. Es gibt keinen halb angewendeten Zustand.

Bezeichner in Report und Fehlermeldungen:

| Objektart | Bezeichner |
|---|---|
| Feldvorlage, Dokumentationstyp | `<slug>` |
| Feldgruppe, Feldzuordnung | `<typ-slug>/<slug>` |
| Bedingung | `<typ-slug>/<ziel-slug><-<quell-slug>` |

Schema-Fehler (vor dem Laden) tragen stattdessen den **Pfad in der YAML**, z. B. `document_types[2].field_refs[0].template_slug`.

---

## 4. `manage.py load_package`

```
python src/manage.py load_package (--file <pfad/zur/paket.yaml> | --package <name> | --default) --facility <uuid> [--dry-run]
```

| Argument | Pflicht | Bedeutung |
|---|---|---|
| `--file` | ja¹ | Pfad zur Paket-YAML |
| `--package` | ja¹ | Mitgeliefertes Paket aus dem Katalog nach technischem Namen |
| `--default` | ja¹ | Statt `--file`: der mitgelieferte Standardkatalog (`core/packages/default.yaml`) |
| `--facility` | ja | UUID der Ziel-Einrichtung (`Facility.id`) |
| `--dry-run` | nein | Simuliert den Lauf und rollt am Ende zurück — **identischer Code-Pfad**, der Report zeigt exakt, was ein Echtlauf getan hätte |

¹ Genau eines von `--file` / `--package` / `--default`. Mehrere Quellen zusammen oder gar keine brechen mit einer Meldung ab. `--default` ist ein weiterhin unterstützter Alias für `--package default`.

> **`--default` — Standardkatalog nachziehen (#1883).** `manage.py setup_facility` stattet eine **neue** Einrichtung automatisch mit dem Standardkatalog aus — eine Einrichtung, die schon Dokumentationstypen oder Feldvorlagen trägt, lässt es dagegen unangetastet (Ersteinrichtung, kein Katalog-Update). Für eine **bestehende** Einrichtung — im Django-Admin unter **Core → Einrichtungen** entstanden, oder mit `--no-default-package` bewusst leer gestartet — ist `--default` der Weg dorthin: fachlich identisch zu `--file <container-pfad>/default.yaml`, nur ohne dass der Betreiber diesen Pfad kennen muss. Auf Bestandszeilen gilt dabei die übliche Rücksync-Semantik dieses Commands (siehe Tabelle unten) — deshalb erst mit `--dry-run`. Ohne mindestens einen Typ mit `entity_type: event` bleibt die Typ-Auswahl beim Dokumentieren leer.

> ⚠️ **Domänen-Pakete sind Ersatz, nicht Zusatz.** Ein mitgeliefertes Paket außer `default` wird nur auf eine frische, katalogfreie Einrichtung geladen. Trägt die Einrichtung Typ- oder Feld-Slugs, die im gewählten Paket nicht vorkommen, bricht der Command auch mit `--dry-run` ab und nennt die fremden Slugs. Das schützt insbesondere wiederverwendete Slugs und deren vollständig zurückgeschriebene Optionslisten. Für eine neue Einrichtung deshalb `setup_facility --package <name>` verwenden; der Doppellauf **desselben** Pakets bleibt idempotent erlaubt. Nur `--default` / `--package default` behält für Bestandsinstallationen die dokumentierte Rücksync-Semantik.

Im Container immer ueber den dedizierten **Maintenance-Service** (siehe Kasten):

```bash
docker compose run --rm \
  -v "$PWD/paket.yaml:/tmp/paket.yaml:ro" maintenance \
  load_package --file /tmp/paket.yaml --facility <uuid> --dry-run

# Standardkatalog statt eigener Datei:
docker compose run --rm maintenance \
  load_package --default --facility <uuid> --dry-run

# Mitgeliefertes Domänen-Paket — nur gegen eine frische Einrichtung:
docker compose run --rm maintenance \
  load_package --package awareness --facility <uuid> --dry-run
```

> ⚠️ **Rollen-Voraussetzung.** Der Loader schreibt in fünf Tabellen mit Row-Level-Security (`core_documenttype`, `core_fieldtemplate`, `core_documenttypefield`, `core_fieldgroup`, `core_fieldcondition`). Die Laufzeit-Rolle des `web`-Containers ist `NOBYPASSRLS` und hat außerhalb eines Requests keinen Einrichtungs-Kontext — deshalb sieht `web` die Admin-Zugangsdaten gar nicht. Der opt-in Service `maintenance` laedt sie und verbindet als BYPASSRLS-Rolle; der Command prüft das vorab und bricht mit einer klaren Meldung ab. Hintergrund: [Ops-Runbook § 9.5](ops-runbook.md), [ADR-033](adr/033-runtime-owner-role-separation.md). Bei einer lokalen Entwicklungs-Datenbank, deren Rolle ohnehin Superuser ist, ist der Maintenance-Pfad überflüssig.

**Exit-Verhalten:** `0` bei Erfolg. Ein Abbruch mit `CommandError` (Exit ≠ 0) bei fehlender oder doppelter Paketquelle, unlesbarer Datei, YAML-Syntaxfehler (dazu zählen **doppelte Schlüssel** in einem Mapping — siehe unten), fehlender/unbekannter Facility-UUID, Schema-Verstoß oder Loader-Fehler. Bei Loader-Fehlern wird der (verworfene) Report **trotzdem** ausgegeben — er zeigt, was ohne die Fehler passiert wäre.

> **Doppelte YAML-Schlüssel sind ein Fehler.** Trägt ein Mapping denselben Schlüssel zweimal (z. B. versehentlich zwei `name:` oder zwei `sort_order:` in einem Eintrag), bricht der Import mit Key-Name und Zeile ab — statt still den letzten Wert zu übernehmen (Last-Write-Wins). Das gilt für Pakete **und** den Standardkatalog (`default.yaml`), die beide über denselben strikten Loader gelesen werden.

> **`--dry-run` kann fehlschlagen.** Ein Dry-Run mit Fehlern bricht genauso ab wie ein Echtlauf (Exit ≠ 0). Das ist der Sinn der Sache: erst prüfen, dann anwenden.

**Empfohlener Ablauf gegen eine Bestands-Einrichtung:**

1. `--dry-run` laufen lassen und den Report lesen — insbesondere den `aktualisiert`-Bucket und alle Warnungen.
2. Prüfen, ob die gemeldeten Aktualisierungen gewollt sind (siehe „Rücksync" unten).
3. Ohne `--dry-run` anwenden.
4. Denselben Befehl **erneut** ohne `--dry-run` laufen lassen: alles muss `unverändert` melden.

Beispiel-Ausgabe (gekürzt):

```
Feldvorlagen:
  angelegt (0): –
  aktualisiert (1): alterscluster
  unverändert (24): dauer, notiz, leistungen, …
Dokumentationstypen:
  angelegt (0): –
  aktualisiert (0): –
  unverändert (9): kontakt, krisengespraech, …

Warnungen (1):
  alterscluster: Diese Options-Slugs fehlen im Paket und verschwinden aus der Feldvorlage: unbekannt. …

Import abgeschlossen.
```

---

## 5. Pakete pflegen — die Regeln, die weh tun

Diese Punkte sind die häufigsten Fehlerquellen. Sie gelten für den Standardkatalog genauso wie für Community-Pakete.

**Identität**

- **Slugs nie ändern.** Ein geänderter Slug ist ein neues Objekt — in jeder Bestands-Einrichtung entsteht ein Duplikat. Wer einen anderen Slug will, legt einen neuen Typ an und deaktiviert den alten.
- **`system_type` immer mitführen**, wo einer existiert (`ban`/`crisis`). Fehlender Schlüssel bedeutet `null`, und `Wert → null` ist ein Fehler, der den ganzen Lauf abbricht. Für die sieben entfallenen Werte gilt das **nicht** mehr: sie sind ersatzlos aus dem Paket zu nehmen (bis dahin entfallen sie beim Laden mit einer Warnung).
- **`report_key` mitführen**, wo eine Berichtszuordnung gewollt ist. Weglassen ist gefahrlos (der Bestand bleibt unangetastet), aber dann entscheidet allein, was schon in der Einrichtung steht — ein frisch angelegter Typ erscheint in **keinem** Bericht. Ein *abweichender* Wert gegen einen gesetzten Bestandswert bricht dagegen den ganzen Lauf ab.
- **`entity_type` immer mitführen**, wenn der Typ kein Ereignis-Typ ist. Fehlt der Schlüssel, gilt `event` — bei einem Bestandstyp mit `client`/`case` ist das ein Immutabilitäts-Fehler.
- **`field_type` (und bei Bedarf `is_encrypted`) immer mitführen.** Fehlt `field_type`, gilt `text`; gegen eine bestehende Vorlage mit einem anderen Typ ist das ein Immutabilitäts-Fehler, der den **gesamten** Lauf abbricht. Kein stiller Footgun — der Fehler ist laut und pfadgenau, aber ein völlig vermeidbarer Abbruch, der beim Weiterreichen eines Pakets erst beim Empfänger auftritt.

**Optionen**

- **Optionen nie löschen, sondern `is_active: false` setzen.** Bereits erfasste Datensätze tragen den Options-Slug in ihrem `data_json` weiter; verschwindet die Option, verliert der Wert sein Label. Der Loader warnt in diesem Fall, blockiert aber nicht.
- **Bei `select`/`multi_select` immer die vollständige Optionsliste mitführen.** `options` weglassen leert die Liste — es gibt hier **kein** „nicht anfassen".

**Bedingungen**

- `conditions` **weglassen**, wenn das Paket sich nicht um Bedingungen kümmert. Ein leeres `conditions: []` löscht alle per UI gepflegten Regeln des Typs.

**Gruppen**

- **Nie die letzte Gruppe eines Typs aus dem Paket entfernen.** Achtung, zwei Pfade: `load_package` entfernt **nie** eine Gruppe (v1 ist additiv, siehe Semantik-Tabelle in § 3). Nur der **Seed** räumt verwaiste Gruppen des Standardkatalogs auf (`_delete_orphan_field_groups`) — und auch dort nur, wenn das Paket für den Typ mindestens eine Gruppe deklariert. Sauberer Weg in beiden Fällen: erst die Felder aus der Gruppe nehmen, die leere Gruppe im Paket stehen lassen.

**Rücksync — was ein Lauf überschreibt**

Alle vom Schema abgedeckten Werte werden bei **jedem** Lauf auf die Paketwerte gezogen, auch wenn sie zwischenzeitlich per Konfigurations-UI geändert wurden. Das betrifft insbesondere:

| Was | Folge eines Laufs |
|---|---|
| `name` des Typs | wird zurückbenannt — bei einem gleichnamigen betreiber-eigenen Typ bricht der Lauf am Unique-Constraint `(facility, name)` ab, bis das manuell aufgelöst ist |
| `icon`, `color`, `sensitivity`, `min_contact_stage`, `sort_order` | zurückgesetzt |
| `sort_order` der Feldzuordnungen | zurückgesetzt — auch bei Typen **ohne** Gruppen; eine per Drag-and-Drop geänderte Reihenfolge geht verloren |
| `default_value`, `help_text`, `options_json` der Vorlage | zurückgesetzt |
| `sensitivity` der Vorlage | zurückgesetzt — eine **Absenkung** wird zusätzlich als Warnung gemeldet |

**Ausnahme `min_value`/`max_value` der Vorlage (#1934):** anders als die übrigen Vorlagenwerte oben sind die beiden Zahlengrenzen **kein** bedingungsloser Rücksync mehr, sondern Tri-State wie `tally_enabled`: Fehlt der Schlüssel, bleibt eine per Konfigurationsoberfläche gepflegte Grenze stehen. Ein Lauf setzt sie nur, wenn das Paket einen Wert **oder** ein bewusstes `null` mitführt.

**Senkt** ein Lauf die **effektive** `sensitivity` (am Typ oder an der Vorlage), meldet der Report zusätzlich eine Warnung „Zugriffsschranke gesenkt“. Die Anwendung bleibt erlaubt — aber bereits erfasste Werte werden damit für zusätzliche Rollen lesbar, und ein `--dry-run` zeigt das vorab. Anhebungen sind still.

Maßgeblich ist die **effektive** Sensibilität `max(Typ, Feld)`, nicht der rohe Feldwert. Eine Feld-`sensitivity` von `""` heißt „erbt vom Typ" (nicht Rang 0): Wird eine Vorlage von `elevated` auf `""` gesetzt, während der referenzierende Typ selbst `elevated` (oder höher) ist, ändert sich effektiv **nichts** — dann **keine** Warnung. Sinkt die effektive Schranke dagegen für mindestens einen referenzierenden Typ (z. B. dieselbe Absenkung unter einem `normal`-Typ), wird gewarnt. Ist die Vorlage noch keinem Typ zugeordnet, greift ein konservativer Roh-Vergleich (`""` ⇒ Rang 0).

Was **nicht** angefasst wird: `is_active`, `description`, `retention_days` am Typ und `is_active` an der Vorlage. Eine Einrichtung kann einen Paket-Typ also deaktivieren, ohne dass der nächste Lauf ihn reaktiviert.

**Konflikte, die den ganzen Lauf abbrechen**

1. Eine einrichtungseigene Feldvorlage auf einem Paket-Slug mit abweichendem `field_type` oder `is_encrypted`. Auflösung: eigene Vorlage auf einen anderen Slug bringen oder den Paket-Slug ändern.
2. Ein Namenskonflikt beim Rückbenennen (siehe Tabelle oben).

In beiden Fällen wird **nichts** angewendet, auch nicht für die übrigen Typen — der Report zeigt trotzdem, was ohne den Konflikt passiert wäre.

**Weitere Grenzen von v1**

- Der Loader schreibt **kein AuditLog**. Nachvollziehbarkeit entsteht über die Versionsverwaltung des Pakets, nicht über das Audit-Log der App.
- Ein Paket wirkt immer auf **genau eine** Einrichtung. Für mehrere Einrichtungen den Befehl je Facility-UUID wiederholen.

---

## Siehe auch

- [ADR-037 — Konfigurationspakete und entitätsübergreifende dynamische Felder](adr/037-konfigurationspakete-dynamische-felder.md)
- [ADR-039 — Owner-generische Dateianhänge](adr/039-owner-generische-dateianhaenge.md) — löst die Sperre „Datei-Felder nur an Ereignis-Typen" ab
- [ADR-036 — Globale Block-Sequenz für Feldgruppen und ungruppierte Felder](adr/036-globale-block-sequenz-feldordnung.md)
- [ADR-035 — Evaluator-Semantik der bedingten Feldlogik](adr/035-evaluator-semantik-bedingte-feldlogik.md)
- [ADR-024 — Slug-basierte, stabile Identifikatoren](adr/024-slug-based-identifiers.md)
- [`src/core/packages/default.yaml`](../src/core/packages/default.yaml) — Standardkatalog als vollständiges Beispiel
- [Admin-Guide](admin-guide.md) — Betrieb, Schlüsselrotation, Backup
