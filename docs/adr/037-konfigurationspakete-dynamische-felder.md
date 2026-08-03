# ADR-037: Konfigurationspakete (YAML) und entitätsübergreifende dynamische Felder

- **Status:** Accepted
- **Date:** 2026-08-03
- **Deciders:** Tobias Nix
- **Refs:** #1058 (M2, Planung), [anlaufstelle/app#35](https://github.com/anlaufstelle/app/issues/35) (M2-Feature), [anlaufstelle/app#40](https://github.com/anlaufstelle/app/issues/40) (Programm), #1713 (globale Block-Sequenz), [ADR-024](024-slug-based-identifiers.md) (Slug-basierte Identifikatoren), [ADR-035](035-evaluator-semantik-bedingte-feldlogik.md) (Evaluator-Semantik), [ADR-036](036-globale-block-sequenz-feldordnung.md) (globale Block-Sequenz), [ADR-006](006-fernet-field-encryption.md) (Feldverschlüsselung)

## Context

Bis M2 war das dynamische Feldsystem an **eine** Entität gebunden: nur `Event` trug `document_type` + `data_json`, und der Dokumentationstyp-Katalog lag als 229-zeiliger Python-Dict-Katalog in `src/core/seed/doc_types.py`. Daraus folgten drei Probleme:

1. **Keine stabile Identität.** `DocumentType` hatte keinen `slug`; der Unique-Key war `(facility, name, category)`, und der Seed identifizierte über den **Namen**. Eine per UI umbenannte „Kontakt"-Zeile war für den Seed ein neuer Typ — jeder Re-Seed legte ein Duplikat an. `FieldTemplate` und `FieldGroup` hatten diesen Schritt bereits hinter sich ([ADR-024](024-slug-based-identifiers.md), Migrationen `0019`/`0110`).
2. **Keine Teilbarkeit.** Eine Einrichtung, die ihr Feldset mit einer anderen teilen wollte, hatte keinen Weg außer „Screenshots nachbauen". Der Katalog war Quellcode — Träger und Community konnten ihn nicht versionieren, reviewen oder importieren.
3. **Dynamische Felder nur an Ereignissen.** Die Praxis verlangt Zusatzfelder auch an **Person** und **Fall** (Aufnahmebogen, Fall-Stammblatt). Der Form-Builder (`DynamicEventDataForm`), die Verschlüsselung (`encrypt_event_data`) und die Validierung waren aber am Ereignis-Modul festgewachsen, obwohl sie faktisch nur `DocumentType` + ein `data_json`-Dict brauchen.

Zusätzliche Randbedingungen:

- **Bestandsdaten sind heilig.** Jede Einrichtung, die heute läuft (dev, demo, Pilotinstanzen), hat einen gewachsenen Katalog. Ein Loader, der gegen Bestand „vielleicht" idempotent ist, produziert Duplikate oder überschreibt gepflegte Konfiguration.
- **Fremder Input.** Ein Community-Paket ist unvertrauenswürdiger Input in einem sicherheitskritischen Kontext: Feldsensibilität und Verschlüsselungs-Flags steuern, wer welche Art.-9-nahen Daten zu sehen bekommt.
- **Keine neuen Dependencies.** `PyYAML` ist bereits im Baum (Fixtures); `jsonschema` wäre neu.
- **Zwei Ordnungssemantiken laufen parallel ein.** [ADR-036](036-globale-block-sequenz-feldordnung.md) legt zeitgleich die globale Block-Sequenz fest — das Paketformat muss sie serialisieren können, sonst wäre eine Format-Migration nötig, bevor das Format je ausgeliefert wurde.

## Decision

Wir führen ein **versioniertes YAML-Paketformat (Schema v1)** ein, spielen es über einen **idempotenten Loader** ein und machen das dynamische Feldsystem **entitätsübergreifend** (`event`/`client`/`case`). Der mitgelieferte Standardkatalog ist selbst ein Paket ([`src/core/packages/default.yaml`](../../src/core/packages/default.yaml)) und wird vom Seed über denselben Loader angewendet.

### 1. Schema v1 — versioniert, strikt, pfadgenau

[`core.packages.schema.parse_package(raw: dict) -> PackageV1`](../../src/core/packages/schema.py) nimmt ein bereits geparstes Mapping (das Modul parst selbst **kein** YAML) und liefert einen unveränderlichen Dataclass-Baum. Verbindlich:

- **`schema_version` ist Pflicht** und muss `1` sein. Ein Paket ohne oder mit abweichender Version wird abgewiesen — künftige Formatgenerationen sind damit unterscheidbar, ohne zu raten.
- **Unbekannte Schlüssel auf jeder Ebene sind ein Fehler.** Ein Tippfehler (`sort_oder`) soll auffallen, statt still zu verpuffen und ein Layout zu verändern.
- **Enum-Werte werden gegen die echten Modell-Choices geprüft** (`FieldTemplate.FieldType`, `DocumentType.Category`/`Sensitivity`/`SystemType`/`EntityType`, `FieldCondition.Operator`, `core.constants.CONTACT_STAGE_CHOICES`) — keine im Schema gespiegelten Literale, die auseinanderlaufen könnten. Das Slug-Format kommt aus `_SLUG_RE` in `core.models.document_type`.
- **Alle Verstöße eines Laufs werden gesammelt**, jeder mit **pfadgenauem** Bezeichner: `document_types[2].field_refs[0].template_slug`. `PackageValidationError.errors` ist eine Liste aus `(Pfad, Meldung)`; `str(exc)` ist dieselbe Liste zeilenweise und damit direkt CLI-tauglich.
- **Pflicht sind nur Identität und Werte ohne sinnvollen Modell-Default:** `slug` + `name` (bzw. `template_slug`, `target`/`depends_on`/`operator`, `slug`/`label` bei Optionen). Alles andere fällt auf den Modell-Default zurück. Ein Minimal-Eintrag ist `{slug, name}`.
- **Handgerollt statt `jsonschema`.** Die Hälfte der Regeln sind Cross-Referenzen zwischen Abschnitten (Slug-Verweise) und Fach-Invarianten (Datei-Felder nur an Ereignis-Typen, `system_type` nur bei `entity_type: event`, HIGH-Sensibilität erzwingt Verschlüsselung), die ein JSON-Schema nicht ausdrücken könnte. Eine neue Dependency hätte den kleineren Teil abgedeckt.

### 2. Slug-Identität — `DocumentType.slug` ist der Lookup-Key und unveränderlich

Migration [`0117_documenttype_slug_entity_type`](../../src/core/migrations/0117_documenttype_slug_entity_type.py) ergänzt `slug` (+ `entity_type`) in **einer** atomaren Datei: Spalte anlegen → deterministischer Backfill (Umlaut-Map + `slugify`, Truncation vor dem Counter-Suffix, Counter **pro Facility**) → fail-loud-Assert (kein Leerslug, keine Duplikate) → `UniqueConstraint(facility, slug)` → Slug-Immutabilitäts-Trigger.

Die Unveränderlichkeit ist **zweifach** verankert:

- **Python:** `DocumentType.save()` weist eine Slug-Änderung an einer bestehenden Zeile mit `ValidationError` ab (gleiches Muster wie `system_type` und `entity_type`).
- **Datenbank:** ein `BEFORE UPDATE`-Trigger mit `ENABLE ALWAYS` (Muster [`0116_slug_immutable_triggers`](../../src/core/migrations/0116_slug_immutable_triggers.py) für `FieldGroup`/`FieldTemplate`). Er schließt die ORM-Bypässe, die an `save()` vorbeigehen — `QuerySet.update()`, `bulk_update()`, Raw SQL, Admin-Bulk-Actions, künftige Migrationen — und gilt auch für die Replica-Rolle. `IS DISTINCT FROM` statt `!=`, damit ein UPDATE, das den Slug auf seinen bisherigen Wert setzt, durchgelassen wird.

Bewusst **nicht** in `bypass_replication_triggers` aufgenommen: die Slug-Immutabilität soll auch für Wartungs-/Importpfade gelten, die die Append-Only-Trigger umgehen (`prune_auditlog`, `anonymize`, `backfill_audit_chain`, `reencrypt_fields`) — neue Slugs entstehen dort ohnehin nur per INSERT.

**Konsequenz für die Paketpflege:** der Slug ist der einzige Weg, einen Bestandstyp wiederzufinden. Ein geänderter Slug im Paket legt in **jeder** Bestands-Einrichtung einen Duplikat-Typ an, statt den vorhandenen zu aktualisieren. Slugs im Standardkatalog müssen deshalb exakt dem `0117`-Backfill entsprechen.

### 3. `entity_type` — ein Typ gehört zu genau einer Entität

`DocumentType.entity_type ∈ {event, client, case}`, Default `event` (spiegelt den Bestand exakt). Nach dem Anlegen unveränderlich (`save()`-Guard) — ein Typwechsel würde alle `data_json`-Bestände seiner Entität ins Leere zeigen lassen. `system_type` ist nur bei `entity_type: event` zulässig (Bann-Logik, Krisen-Highlight, Jugendamt-Export sind Ereignis-Semantik).

`Client` und `Case` bekommen `document_type` (`FK`, `null=True`, `on_delete=PROTECT`) und `data_json` (JSONB, Default `{}`) — Migrationen [`0118`](../../src/core/migrations/0118_client_dynamic_data.py)/[`0119`](../../src/core/migrations/0119_case_dynamic_data.py), reine Spalten-Erweiterungen ohne `RunPython`. Kein RLS-Update nötig: `core_client`/`core_case` stehen bereits in `DIRECT_TABLES` von `0047_postgres_rls_setup`, und RLS wirkt auf Zeilen, nicht auf Spalten.

**Die Entitätsgrenze wird an jeder Auswahlfläche durchgesetzt, nicht nur einmal.** Ein Ereignis-Typ darf nie an einer Person landen und umgekehrt:

- **Auswahl-Querysets** filtern hart: [`forms/events.py`](../../src/core/forms/events.py) und [`views/events.py`](../../src/core/views/events.py) (Create/Update/Partial/Serienerfassung), [`views/attachments.py`](../../src/core/views/attachments.py), [`views/statistics.py`](../../src/core/views/statistics.py), [`views/zeitstrom.py`](../../src/core/views/zeitstrom.py) auf `entity_type=event`; [`forms/clients.py`](../../src/core/forms/clients.py)/[`views/clients.py`](../../src/core/views/clients.py) auf `entity_type=client`.
- **Service-Guards als Defense-in-Depth:** `_check_client_document_type` ([`services/client/main.py`](../../src/core/services/client/main.py)), `_check_case_document_type` ([`services/case/cases.py`](../../src/core/services/case/cases.py)) und `create_event` ([`services/events/crud.py`](../../src/core/services/events/crud.py)) prüfen Facility-Scope **und** Entitätstyp und werfen eine deutsche `ValidationError`. Sie decken Aufrufer ohne Formular ab — Offline-Replay, Management-Commands, künftige Importe.
- **Paketformat:** `entity_type` steht im Schema und wird vom Loader als unveränderlich behandelt (siehe 4.).

### 4. Immutables im Loader — Abweichung ist ein Fehler, nie ein stiller Wechsel

[`core.packages.loader.load_package(facility, package, *, dry_run=False) -> PackageReport`](../../src/core/packages/loader.py) ist ein **handgerollter Upsert**, kein `update_or_create`: letzteres schreibt mit `save(update_fields=…)` und würde die Sonderlogik in `FieldTemplate.save()` (FILE/HIGH erzwingen `is_encrypted`) aushebeln; außerdem brauchen wir vor dem Schreiben einen Diff (created/updated/unchanged) und ein `full_clean()`, das Unique-Konflikte in lesbare `ValidationError` statt in `IntegrityError` übersetzt.

Unveränderlich nach dem Anlegen:

| Objekt | Unveränderlich | Ausnahme |
|---|---|---|
| `FieldTemplate` | `field_type`, `is_encrypted` ||
| `DocumentType` | `entity_type`, `system_type` | `system_type: None → Wert` (Bestand vor M2 hatte keinen) |
| alle | `slug` | per Definition — der Slug **ist** der Lookup-Key |

`system_type: Wert → None` und `Wert → anderer Wert` sind Fehler; das spiegelt `DocumentType.save()`. Praktische Folge: **ein Paket gegen einen Bestandstyp mit Systemtyp muss den Key mitführen** — Weglassen bedeutet `None` und damit „entfernen".

**`is_encrypted` wird nach oben normalisiert, bevor die Immutabilitäts-Prüfung greift.** `FieldTemplate.save()` erzwingt `is_encrypted=True` für `field_type: file` und für `sensitivity: high`. Ohne dieselbe Normalisierung im Loader wäre ein Paket mit `field_type: file` ohne explizites `is_encrypted: true` beim **zweiten** Lauf ein Immutabilitäts-Konflikt gegen die eigene erste Anwendung. Bewusst **ohne** Warnung — sonst rauschte jeder `unchanged`-Lauf. Ein *explizites* `is_encrypted: false` an einem FILE-Feld ist dagegen ein Widerspruch im Paket selbst und wird schon im Schema pfadgenau abgewiesen.

Operator × Feldtyp und `value` × Operator prüft **nicht** das Schema, sondern `FieldCondition.clean()` beim Laden — die Matrix lebt in `core.services.conditions` ([ADR-035](035-evaluator-semantik-bedingte-feldlogik.md)), und eine zweite Kopie im Schema würde auseinanderlaufen. Solche Verstöße erscheinen deshalb als Loader-`error` mit dem Bezeichner `"<typ>/<ziel><-<quelle>"`, nicht als Schema-Pfadfehler.

### 5. Alles-oder-nichts, Savepoint je Objekt, Dry-Run auf demselben Pfad

- **Ein äußeres `transaction.atomic`** über den gesamten Lauf. Sobald `report.errors` nicht leer ist, wird `PackageLoadError(report)` **innerhalb** des Blocks geworfen ⇒ Gesamtrollback. Ein Raise danach käme zu spät.
- **Ein Savepoint je Objekt** (`_save()`). Ohne ihn würde ein durchgerutschter `IntegrityError` die Postgres-Transaktion als *aborted* markieren, und jede weitere Abfrage des Laufs — auch die harmlosen Lookups — liefe auf einen `InternalError`, statt sammelbare Fehler zu liefern. Ziel ist, **alle** Fehler eines Laufs zu melden, nicht nur den ersten.
- **Kindobjekte werden übersprungen, wenn ihr Elter scheitert** (Typ-Fehler ⇒ keine Groups/Refs/Conditions dieses Typs; Template-Fehler ⇒ „Zuordnung übersprungen" am `field_ref`). Andere Dokumentationstypen laufen weiter, damit ein Lauf ein vollständiges Fehlerbild liefert.
- **Dry-Run nimmt exakt denselben Code-Pfad** und rollt am Ende über einen internen Sentinel zurück. Der Report zeigt damit belastbar, was ein Echtlauf getan hätte. **Ein Dry-Run mit Fehlern raist ebenfalls** — `--dry-run` ist kein „kann nicht fehlschlagen".
- **Kein Audit-Logging, kein RLS-Kontext.** Der Loader ist ein Betriebswerkzeug ohne Request-Kontext und läuft wie Seed und Management-Commands auf der Superuser-Verbindung (Muster-Konsistenz; die Audit-Allowlist bleibt unberührt).

### 6. Tri-State bei `conditions` — und bewusst **kein** Tri-State bei `options`

`conditions` ist **deklarativ, wenn vorhanden**:

| Angabe im Paket | Wirkung |
|---|---|
| Schlüssel fehlt | Bestehende Bedingungen bleiben **unangetastet** (per UI gepflegte Regeln überleben) |
| `conditions: []` | Alle Bedingungen des Typs werden **gelöscht** |
| `conditions: [ … ]` | Wholesale-Replace: Soll- und Ist-Menge werden normalisiert verglichen; bei Gleichheit passiert **nichts** (`unchanged`), sonst Delete + Recreate |

Der Gleichheits-Kurzschluss ist Pflicht: ein blindes Delete+Recreate meldete jeden Lauf als `created` und bräche den Idempotenz-Kernbeweis. `FieldCondition` hat keinen Natural Key, ein zeilenweiser Upsert wäre nicht definierbar; die PKs werden nirgends referenziert, der Austausch ist also beobachtbar identisch. Ersetzte/entfernte Bedingungen erzeugen **Warnungen** (der Report hat keinen `deleted`-Bucket) — das Wegräumen UI-gepflegter Regeln soll sichtbar sein.

**`options` kennt dieses Tri-State bewusst nicht — das ist die schärfste Footgun des Formats.** Der Loader schreibt `options_json` bei jedem Lauf aus dem Paket. Wer den `options`-Schlüssel bei einem `select`/`multi_select`-Feld **weglässt**, leert die Auswahlliste; es gibt nur eine Warnung, keinen Fehler. Dasselbe gilt für **`default_value`**: der Paketwert wird bei jedem Lauf geschrieben, ein per Config-UI gepflegter Vorbelegungswert überlebt einen Re-Lauf also nicht. Die Regel lautet daher: **bei `select`/`multi_select` immer die vollständige Optionsliste mitführen, und Optionen nie löschen, sondern `is_active: false` setzen** — bereits erfasste Ereignisse tragen den Options-Slug in `data_json` weiter und verlören sonst ihr Label. Der Loader warnt, wenn Options-Slugs aus der Datenbank im Paket fehlen (reiner Slug-Mengenvergleich; ein JSONB-Scan über alle `Event.data_json` je Option wäre teuer und würde den Loader an die Ereignis-Entität koppeln).

Ein Tri-State für `options`/`default_value` analog `conditions` bleibt eine mögliche v2-Erweiterung; für v1 wiegt die Einfachheit („was im Paket steht, steht in der DB") schwerer als der Sonderfall.

### 7. `groups` und `field_refs` sind additiv — v1 entfernt nie

Was das Paket nicht mehr erwähnt, bleibt stehen. Gründe: ein Community-Paket soll eine bestehende Konfiguration **ergänzen** können, ohne Kollateralschäden; und ein Entfernen wäre nicht von „Autor hat es vergessen" unterscheidbar. Konkrete Folgen:

- Fremde Felder, die eine Einrichtung einem Paket-Typ zusätzlich zugeordnet hat, überleben — auch ihre manuelle Gruppierung.
- Der Seed braucht deshalb einen eigenen, engen Cleanup-Pass für **verwaiste Feldgruppen** (`_delete_orphan_field_groups` in [`core/seed/doc_types.py`](../../src/core/seed/doc_types.py)), der **vor** `load_package` **in derselben Transaktion** läuft — sonst bliebe eine gelöschte Gruppe gelöscht, wenn der Loader danach zurückrollt. Opt-in ist „das Paket deklariert für diesen Typ mindestens eine Gruppe"; ein Paket kann damit nicht „lösche ALLE Gruppen dieses Typs" ausdrücken. **Pflegeregel: nie die letzte Gruppe eines Typs aus dem Katalog entfernen** (erst die Felder aus der Gruppe nehmen, leere Gruppe stehen lassen), sonst bleibt die verwaiste Zeile in Bestands-Einrichtungen liegen.
- Felder außerhalb von Schema v1 werden weder gesetzt noch zurückgesetzt: `retention_days`, `description`, `is_active` (Typ) sowie `statistics_category`, `is_active` (Vorlage). Eine Einrichtung kann einen Paket-Typ also deaktivieren, ohne dass der nächste Lauf ihn reaktiviert.

`sort_order` wird **1:1** geschrieben. Das Format ist deklarativ, und der Wert trägt die Dual-Semantik aus [ADR-036](036-globale-block-sequenz-feldordnung.md): an einer `group` und an einem `field_ref` **ohne** `group_slug` ist es die Position des Blocks in der globalen Block-Sequenz des Typs, an einem `field_ref` **mit** `group_slug` der Innen-Index in der Gruppe. Damit serialisiert das Format die endgültige Ordnungssemantik — eine spätere Format-Migration entfällt.

### 8. Typwechsel-Policy (E11): Rebuild statt Merge

`merge_dynamic_data` in [`core/services/dynamic_data.py`](../../src/core/services/dynamic_data.py) ist **der einzige** Punkt, an dem `data_json` für alle drei Entitäten entsteht:

1. Das Ergebnis wird **komplett** aus dem validierten Formular des **aktuellen** `document_type` aufgebaut. Alte Werte sind nicht die Basis.
2. Für Slugs, die per `remove_restricted_fields` sensibilitätsbedingt aus dem Formular entfernt wurden, wird der Alt-Wert nachgezogen — sonst löschte ein Speichern-Vorgang Daten, die die bearbeitende Person nie sehen (und daher nie absichtlich leeren) konnte.
3. Der Nachzug greift **nur innerhalb desselben Typs**: das Ergebnis läuft abschließend durch `validate_data_json`, dessen Whitelist die Slugs des **aktuellen** Typs sind. Wechselt der Typ, fallen die Alt-Slugs weg — **inklusive** der restricted Alt-Werte.

Das ist gewollt: ein Wert des alten Typs hat im Feldset des neuen Typs keine Bedeutung und wäre unsichtbarer Ballast im JSONB — bei Art.-9-nahen Daten sogar ein Speicherbegrenzungs-Problem. Die UI weist beim Typwechsel darauf hin. Derselbe Rebuild greift auch, wenn nur der Typ gewechselt wird (Service-Ebene, `update_client`/`update_case`), damit kein Pfad an der Policy vorbeiführt.

Flankierend: der `DynamicDataEncryptionMixin`-Cache der verschlüsselten Slugs ist an die `document_type_id` gebunden. Ohne das verschlüsselte eine Instanz, die zwischen zwei `save()`-Aufrufen den Typ wechselt, weiter nach der Feldliste des **alten** Typs — ein im neuen Typ als `is_encrypted` markiertes Feld landete im Klartext in der JSONB-Spalte.

### 9. Datei-Felder bleiben Ereignis-Sache — dreischichtig abgesichert

Nur der Ereignis-Pfad hat eine Attachment-/File-Vault-Pipeline ([ADR-014](014-encrypted-file-vault.md)). Ein `file`-Feld an einem Person-/Fall-Typ wäre eine Sackgasse: der Upload landete nirgends. Deshalb drei Schichten:

| Schicht | Ort | Verhalten |
|---|---|---|
| 1 — Paket | `core/packages/schema.py` (`_cross_check_document_type`) | Ein `field_ref` auf ein FILE-Template an `entity_type ∈ {client, case}` ist ein **Schema-Fehler** mit Pfad |
| 2 — Konfiguration | `core/services/config.py` (`add_field_to_doctype`) | Die Config-UI weist die Zuordnung eines FILE-Templates an einen Person-/Fall-Typ mit einer deutschen Fehlermeldung ab |
| 3 — Formular | `core/forms/dynamic.py` (`DynamicDataForm.exclude_file_fields`) | Client-/Case-Formulare **überspringen** FILE-Felder beim Aufbau und loggen eine Warnung — eine per Umweg entstandene Fehlkonfiguration darf keinen 500er auslösen |

Schicht 3 ist die letzte Verteidigungslinie und bewusst **kein** Fehler: der Datensatz muss bearbeitbar bleiben, auch wenn die Konfiguration kaputt ist.

### 10. Schlüsselrotation deckt die neuen `data_json`-Träger mit ab

`manage.py reencrypt_fields` rotiert seit M2 auch `Client.data_json` und `Case.data_json` (samt Heal von Klartext-PII nach `encrypt_dynamic_data`), sonst entstünde für die neuen Modellgruppen eine Rotationslücke: ein ausrangierter Fernet-Schlüssel wäre nicht wirklich entfernbar.

**Client und Case werden inklusive soft-deleted Zeilen rotiert** (Entscheid E17). `FacilityScopedManager` filtert `is_deleted` nicht, `Client.objects`/`Case.objects` decken den vollen Bestand also ohne Zusatzfilter ab. Bewusst anders als bei **Events**, die weiterhin mit `is_deleted=False` iteriert werden — das ist eine **bekannte, hier nicht angefasste Bestandslücke**: soft-deleted Ereignisse bleiben unter dem alten Schlüssel liegen, bis sie regulär hart gelöscht werden. Wer einen Schlüssel wirklich ausmustern will, muss das wissen. Nachzuziehen ist der Event-Pfad separat (getrackt an #1058, Folge-Issue offen) — als Teil von M2 wäre es eine unbegründete Verhaltensänderung an einem Bestandspfad gewesen.

### 11. CLI-Fassade

[`manage.py load_package --file <pfad> --facility <uuid> [--dry-run]`](../../src/core/management/commands/load_package.py) ist die einzige Stelle, die `yaml` importiert; Schema und Loader bleiben dict-basiert und damit unabhängig vom Serialisierungsformat. Fehler aus beiden Stufen werden als `CommandError` (Exit ≠ 0) gemeldet, der Report wird auch im Fehlerfall ausgegeben — er zeigt, was ohne die Fehler passiert wäre. Format-Referenz und Bedienung: [`docs/packages.md`](../packages.md).

## Consequences

**Positiv:**

- **+ Konfiguration ist ein reviewbares Artefakt.** Ein Feldset liegt als versionierte YAML vor, lässt sich diffen, in Git halten, zwischen Trägern teilen und vor dem Einspielen per `--dry-run` prüfen.
- **+ Der Seed hat keinen Sonderweg mehr.** Der Standardkatalog ist ein normales Paket, und der Seed ruft denselben Loader. Der Import-Pfad ist damit bei jedem `make seed` und in jedem E2E-Lauf mitgetestet — nicht nur, wenn jemand ihn gezielt benutzt.
- **+ Idempotenz ist beweisbar, nicht behauptet.** Der Report kennt `created`/`updated`/`unchanged` je Objektart; ein zweiter Lauf, der irgendwo `updated` meldet, ist ein Bug und fällt im Test auf (statt als stilles Property-Flapping durchzurutschen).
- **+ Umbenennen ist keine Duplikat-Falle mehr.** Der Lookup läuft über den Slug; ein per UI umbenannter Katalog-Typ wird aktualisiert statt dupliziert.
- **+ Dynamische Felder sind entitätsübergreifend**, ohne die Sicherheitsmechanik zu duplizieren: Verschlüsselung, Sensibilitätsfilter, Merge-Policy und Block-Ordnung leben je einmal in entity-neutralen Modulen und werden von Ereignis, Person und Fall geteilt.

**Negativ / Kosten — insbesondere für Bestands-Einrichtungen:**

Der Loader ist **update-sicher**, und genau das ist eine sichtbare Verhaltensänderung. Diese Punkte gehören in jede Betriebsdoku und in die Vorbereitung eines Deploys:

1. **Paket-Properties werden zurückgesetzt.** `name`, `icon`, `color`, `category`, `sensitivity`, `min_contact_stage`, `sort_order` (Typ) und `name`, `help_text`, `options_json`, `default_value`, `sensitivity`, `is_required` (Vorlage) werden bei **jedem** Lauf auf die Paketwerte gezogen. Bisher griffen sie nur beim Anlegen.
2. **`sort_order`-Rücksync gilt jetzt auch für gruppenlose Typen.** Eine per Config-UI umsortierte Feldliste eines Typs ohne Gruppen wird beim nächsten Seed auf die Katalogreihenfolge zurückgesetzt.
3. **Rückbenennen kann in einen Hard-Lock laufen.** Benennt ein Betreiber „Kontakt" in „Erstkontakt" um und legt danach einen *neuen* Typ „Kontakt" an (Slug `kontakt-2`), verletzt der Rückbenennen-Sync `UniqueConstraint(facility, name, category)` — ab da bricht **jeder** weitere Lauf komplett ab. Auflösung: den betreiber-eigenen Typ umbenennen oder den Namen im Paket anpassen.
4. **Konflikte sind fail-loud und kaskadieren.** Eine einrichtungseigene Feldvorlage auf einem Paket-Slug mit abweichendem `field_type`/`is_encrypted` bricht den **gesamten** Lauf ab — es wird nichts angewendet, auch nicht für die übrigen Typen. Das ist gewollt (kein halb angewendetes Paket), aber der Fehler muss gelesen und behoben werden, statt ihn zu ignorieren.
5. **Optionen und Vorbelegungswerte sind Paket-Hoheit** (siehe Abschnitt 6) — Weglassen leert.
6. Der Loader schreibt **kein AuditLog**. Wer eine Konfigurationsänderung nachvollziehen will, braucht das Paket-Diff aus der Versionsverwaltung, nicht das Audit-Log der App. Bewusste Konsistenz mit Seed/Commands; falls Paket-Importe je auditpflichtig werden, ist das eine eigene Entscheidung.
7. **Zwei Pfade schreiben dieselbe Konfiguration** — Config-UI und Paket-Loader. Wer beides mischt, muss die Rücksync-Regeln oben kennen; „per UI feinjustieren, dann Paket erneut einspielen" verwirft die Feinjustierung an allen von v1 abgedeckten Feldern.
8. **`options`/`default_value` ohne Tri-State** ist eine bewusste v1-Vereinfachung, die eine spätere Formaterweiterung wahrscheinlich macht (dann als `schema_version: 2`, nicht als stille Semantikänderung).
9. **Der Rotationslauf kennt keinen Row-Lock für Client/Case** (anders als der TOTP-Rewrap). Bei paralleler Bearbeitung während einer Rotation ist ein Lost-Update theoretisch möglich — konsistent mit den bestehenden Event-/Attachment-Pfaden, aber kein Freibrief für Rotation im laufenden Betrieb.

## Alternatives considered

- **`jsonschema` + JSON-Schema-Datei statt handgerollter Validierung.** Verworfen: neue Dependency für den kleineren Teil der Regeln; Cross-Referenzen zwischen Abschnitten und Fach-Invarianten (FILE × `entity_type`, `system_type` × `entity_type`, HIGH ⇒ verschlüsselt) wären ohnehin handgeschrieben, und die pfadgenauen deutschen Meldungen wären schlechter.
- **Django-Fixtures (`loaddata`) als Paketformat.** Verworfen: PK-basiert, nicht slug-basiert; kein Diff, keine Idempotenz, kein Dry-Run, keine Fach-Validierung — und ein Fixture aus einer fremden Einrichtung würde fremde UUIDs einschleppen.
- **`update_or_create` als Loader-Kern.** Verworfen: schreibt mit `update_fields` und würde die erzwungenen Felder aus `FieldTemplate.save()` ausschließen (E12); außerdem kein Diff für den Report und kein `full_clean()`.
- **Fehler sofort werfen statt sammeln.** Verworfen: ein Paket mit fünf Tippfehlern erzwänge fünf Läufe. Das Sammeln ist der Grund für die Savepoint-Bauform.
- **Loader ohne Alles-oder-nichts (Teilerfolge committen).** Verworfen: ein halb angewendetes Paket ist der schlechteste Zustand — die Konfiguration ist weder alt noch neu, und der nächste Lauf startet von einem undefinierten Punkt.
- **Slug änderbar mit Rename-Mapping im Paket.** Verworfen: der Slug ist der stabile Identifikator ([ADR-024](024-slug-based-identifiers.md)); ein Mapping-Mechanismus verlagert die Instabilität nur in eine zweite Tabelle, die selbst driften kann. Wer einen anderen Slug will, legt einen neuen Typ an.
- **`entity_type` nachträglich änderbar.** Verworfen: alle `data_json`-Bestände der bisherigen Entität würden bedeutungslos, und die FK-Verweise (`Event`/`Client`/`Case`) sind `PROTECT` — ein Wechsel wäre ein Datenmigrations-Projekt, kein Formularfeld.
- **Datei-Felder auch an Person/Fall.** Verworfen für v1: die Attachment-Pipeline (Virus-Scan, Chunk-Format, Vault, Reachability-Garbage-Collection) ist am Ereignis verankert; sie zu generalisieren ist ein eigenes Vorhaben und nicht Voraussetzung für Zusatzfelder.
- **Clear-on-Hide statt Rebuild beim Typwechsel.** Nicht dasselbe Problem: [ADR-035](035-evaluator-semantik-bedingte-feldlogik.md) §9 hält Werte *versteckter* Felder bewusst, der Typwechsel dagegen verwirft Slugs, die im neuen Feldset gar nicht existieren. Die beiden Regeln stehen nebeneinander, nicht im Widerspruch.

## References

- [`src/core/packages/schema.py`](../../src/core/packages/schema.py) — Schema v1 (`parse_package`, `PackageV1`, `PackageValidationError`)
- [`src/core/packages/loader.py`](../../src/core/packages/loader.py) — idempotenter Loader (`load_package`, `PackageReport`, `PackageLoadError`)
- [`src/core/packages/default.yaml`](../../src/core/packages/default.yaml) — Standardkatalog als Paket (Quelle des Seeds)
- [`src/core/management/commands/load_package.py`](../../src/core/management/commands/load_package.py) — CLI-Fassade
- [`src/core/seed/doc_types.py`](../../src/core/seed/doc_types.py) — Seed über den Loader (`load_default_package_data`, `seed_document_types`)
- [`src/core/forms/dynamic.py`](../../src/core/forms/dynamic.py) — entity-neutraler Form-Builder (`DynamicDataForm`, `exclude_file_fields`, `grouped_fields`)
- [`src/core/services/dynamic_data.py`](../../src/core/services/dynamic_data.py) — entity-neutrale Feld-Helfer (`merge_dynamic_data`, `validate_data_json`, `remove_restricted_fields`)
- [`src/core/management/commands/reencrypt_fields.py`](../../src/core/management/commands/reencrypt_fields.py) — Schlüsselrotation inkl. `Client`/`Case`
- Migrationen [`0116`](../../src/core/migrations/0116_slug_immutable_triggers.py), [`0117`](../../src/core/migrations/0117_documenttype_slug_entity_type.py), [`0118`](../../src/core/migrations/0118_client_dynamic_data.py), [`0119`](../../src/core/migrations/0119_case_dynamic_data.py)
- [`docs/packages.md`](../packages.md) — Format-Referenz, Loader-Semantik-Tabelle, `load_package`-Bedienung
- [ADR-024](024-slug-based-identifiers.md) — Slug-basierte, stabile Identifikatoren
- [ADR-035](035-evaluator-semantik-bedingte-feldlogik.md) — Evaluator-Semantik der bedingten Feldlogik (Operator-Matrix, die der Loader beim Laden erzwingt)
- [ADR-036](036-globale-block-sequenz-feldordnung.md) — globale Block-Sequenz (Dual-Semantik der `sort_order`-Felder im Paketformat)
- [ADR-014](014-encrypted-file-vault.md) — Encrypted File Vault (warum Datei-Felder ereignisgebunden bleiben)
- [ADR-006](006-fernet-field-encryption.md) — Fernet/MultiFernet-Feldverschlüsselung (Grundlage der Rotation)
- Issue #1058 — M2-Planung (Entscheide E3–E21)
</content>
</invoke>
