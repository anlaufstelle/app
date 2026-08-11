# Konfigurationspakete (Schema v1)

Ein **Konfigurationspaket** ist eine YAML-Datei, die Feldvorlagen, Dokumentationstypen, Feldgruppen, Feldzuordnungen und Feldbedingungen einer Einrichtung beschreibt. Pakete machen Formular-Konfiguration teilbar: Sie lassen sich versionieren, reviewen, zwischen Trägern austauschen und **idempotent** einspielen — ein zweiter Lauf gegen denselben Stand ändert nichts.

Der mitgelieferte Standardkatalog ist selbst ein Paket: [`src/core/packages/default.yaml`](../src/core/packages/default.yaml). Jeder `make seed`-Lauf spielt ihn über denselben Loader ein, den auch `manage.py load_package` benutzt.

- **Warum das Format so aussieht, wie es aussieht:** [ADR-037](adr/037-konfigurationspakete-dynamische-felder.md).
- **Warum `sort_order` zwei Bedeutungen hat:** [ADR-036](adr/036-globale-block-sequenz-feldordnung.md).
- **Welche Operatoren zu welchem Feldtyp passen:** [ADR-035](adr/035-evaluator-semantik-bedingte-feldlogik.md).

> **Der Slug ist die Identität.** Der Loader findet Bestandsobjekte ausschließlich über ihren Slug. Ein geänderter Slug legt in **jeder** Bestands-Einrichtung ein Duplikat an, statt das vorhandene Objekt zu aktualisieren. Slugs sind nach dem Anlegen in Modell **und** Datenbank-Trigger unveränderlich — es gibt kein „Umbenennen" auf Slug-Ebene.

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
    system_type: contact     # nur bei entity_type: event; immer mitführen
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
| `is_required` | Bool | `false` | Pflichtfeld im Formular |
| `is_encrypted` | Bool | `false` | **Unveränderlich**; bei `file` und `sensitivity: high` erzwungen |
| `sensitivity` | `""`, `normal`, `elevated`, `high` | `""` | `""` = erbt vom Dokumentationstyp; `high` erfordert `is_encrypted: true` |
| `options` | Liste | *(leer)* | Nur bei `select`/`multi_select`. **Weglassen leert die Liste** |
| `help_text` | String | `""` | |
| `default_value` | String | `""` | Wird bei jedem Lauf aus dem Paket geschrieben |

`options[]`-Einträge: `slug` (Pflicht, Slug-Format, eindeutig je Vorlage), `label` (Pflicht, nicht leer), `is_active` (Bool, Default `true`).

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
| `system_type` | `ban`, `contact`, `crisis`, `medical`, `needle_exchange`, `accompaniment`, `counseling`, `referral`, `note` oder `null` | `null` | Nur bei `entity_type: event`. **Unveränderlich**, Ausnahme `null → Wert` |
| `sensitivity` | `normal`, `elevated`, `high` | `normal` | steuert Rollen-Sichtbarkeit |
| `min_contact_stage` | `identified`, `qualified` oder `null` | `null` | Mindest-Kontaktstufe der Person — **wirkt nur bei `entity_type: event`** |
| `icon`, `color` | String | `""` | |
| `sort_order` | Int | `0` | Position des Typs in der Typ-Auswahl |
| `groups` | Liste | *(leer)* | additiv — siehe unten |
| `field_refs` | Liste | *(leer)* | additiv — siehe unten |
| `conditions` | Liste **oder Schlüssel weglassen** | *(nicht anfassen)* | Tri-State, siehe § 3 |

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
| `conditions`-Schlüssel fehlt | Bestehende Bedingungen bleiben **unangetastet** |
| `conditions: []` | Alle Bedingungen des Typs werden **gelöscht** |
| `conditions: [ … ]` | Wholesale-Replace; bei inhaltlicher Gleichheit passiert nichts (`unverändert`) |
| Gruppe/Feldzuordnung nicht (mehr) erwähnt | bleibt **stehen** — v1 ist additiv und entfernt nie |
| Feld außerhalb des Schemas | wird nie angefasst: `retention_days`, `description`, `is_active` (Typ); `statistics_category`, `is_active` (Vorlage) |

### Unveränderliche Werte

Weicht einer dieser Werte vom Bestand ab, ist das ein **Fehler** — der Loader wechselt sie nie still:

| Objekt | Unveränderlich | Ausnahme |
|---|---|---|
| Feldvorlage | `slug`, `field_type`, `is_encrypted` ||
| Dokumentationstyp | `slug`, `entity_type`, `system_type` | `system_type: null → Wert` (Bestandsdaten vor M2) |
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
python src/manage.py load_package --file <pfad/zur/paket.yaml> --facility <uuid> [--dry-run]
```

| Argument | Pflicht | Bedeutung |
|---|---|---|
| `--file` | ja | Pfad zur Paket-YAML |
| `--facility` | ja | UUID der Ziel-Einrichtung (`Facility.id`) |
| `--dry-run` | nein | Simuliert den Lauf und rollt am Ende zurück — **identischer Code-Pfad**, der Report zeigt exakt, was ein Echtlauf getan hätte |

Im Container **immer mit der Admin-Rolle** (siehe Kasten):

```bash
docker compose exec \
  -e POSTGRES_USER=$POSTGRES_ADMIN_USER \
  -e POSTGRES_PASSWORD=$POSTGRES_ADMIN_PASSWORD \
  web python manage.py load_package --file /tmp/paket.yaml --facility <uuid> --dry-run
```

> ⚠️ **Rollen-Voraussetzung.** Der Loader schreibt in fünf Tabellen mit Row-Level-Security (`core_documenttype`, `core_fieldtemplate`, `core_documenttypefield`, `core_fieldgroup`, `core_fieldcondition`). Die Laufzeit-Rolle des `web`-Containers ist `NOBYPASSRLS` und hat außerhalb eines Requests keinen Einrichtungs-Kontext — ohne den ENV-Override sähe der Lauf 0 Bestandszeilen und scheiterte am ersten Schreibzugriff. Der Command prüft das vorab und bricht mit einer klaren Meldung ab. Hintergrund: [Ops-Runbook § 9.5](ops-runbook.md), [ADR-033](adr/033-runtime-owner-role-separation.md). Bei einer lokalen Entwicklungs-Datenbank, deren Rolle ohnehin Superuser ist, ist der Override überflüssig.

**Exit-Verhalten:** `0` bei Erfolg. Ein Abbruch mit `CommandError` (Exit ≠ 0) bei unlesbarer Datei, YAML-Syntaxfehler (dazu zählen **doppelte Schlüssel** in einem Mapping — siehe unten), fehlender/unbekannter Facility-UUID, Schema-Verstoß oder Loader-Fehler. Bei Loader-Fehlern wird der (verworfene) Report **trotzdem** ausgegeben — er zeigt, was ohne die Fehler passiert wäre.

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
- **`system_type` immer mitführen**, wo einer existiert. Fehlender Schlüssel bedeutet `null`, und `Wert → null` ist ein Fehler, der den ganzen Lauf abbricht.
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

**Senkt** ein Lauf die **effektive** `sensitivity` (am Typ oder an der Vorlage), meldet der Report zusätzlich eine Warnung „Zugriffsschranke gesenkt“. Die Anwendung bleibt erlaubt — aber bereits erfasste Werte werden damit für zusätzliche Rollen lesbar, und ein `--dry-run` zeigt das vorab. Anhebungen sind still.

Maßgeblich ist die **effektive** Sensibilität `max(Typ, Feld)`, nicht der rohe Feldwert. Eine Feld-`sensitivity` von `""` heißt „erbt vom Typ" (nicht Rang 0): Wird eine Vorlage von `elevated` auf `""` gesetzt, während der referenzierende Typ selbst `elevated` (oder höher) ist, ändert sich effektiv **nichts** — dann **keine** Warnung. Sinkt die effektive Schranke dagegen für mindestens einen referenzierenden Typ (z. B. dieselbe Absenkung unter einem `normal`-Typ), wird gewarnt. Ist die Vorlage noch keinem Typ zugeordnet, greift ein konservativer Roh-Vergleich (`""` ⇒ Rang 0).

Was **nicht** angefasst wird: `is_active`, `description`, `retention_days` am Typ und `is_active`, `statistics_category` an der Vorlage. Eine Einrichtung kann einen Paket-Typ also deaktivieren, ohne dass der nächste Lauf ihn reaktiviert.

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
