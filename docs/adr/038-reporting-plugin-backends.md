# ADR-038: Reporting-Backends als Plugin-System

- **Status:** Accepted
- **Date:** 2026-08-03
- **Deciders:** Tobias Nix
- **Refs:** #1061 (M5, Planung), [anlaufstelle/app#38](https://github.com/anlaufstelle/app/issues/38) (M5-Feature), [anlaufstelle/app#40](https://github.com/anlaufstelle/app/issues/40) (Programm), #1278 (k-Anon-Suppression im Sachbericht), [ADR-023](023-k-anonymization-statistik.md) (K-Anonymisierung), [ADR-020](020-three-role-postgres-model.md) (Drei-Rollen-Postgres-Modell, RLS beim Backfill), [ADR-034](034-config-ui-category-system-type.md) (`category` vs. `system_type`), [ADR-037](037-konfigurationspakete-dynamische-felder.md) (M2, `DocumentType.slug`)

## Context

Berichte waren bis M5 **hart verdrahtet**. Der Sachbericht („Jugendamt-Bericht") lebte als `JUGENDAMT_CATEGORY_MAP` + `get_jugendamt_statistics()` in `src/core/services/system/export.py`, sein Aggregat in einem eigenen Modellfeld `StatisticsSnapshot.jugendamt_data`, sein Ausgabeweg in genau einer View mit genau einem Format (PDF). Daraus folgten vier Probleme:

1. **Ein zweiter Berichtsstandard war ein Code-Umbau, kein Modul.** Jeder neue Bericht hätte ein weiteres Snapshot-Feld, eine weitere View, ein weiteres URL-Pattern, weitere AuthZ-Matrix-Einträge und einen weiteren Export-Pfad gebraucht. Kostenträger, Kommunen und Fördermittelgeber verlangen aber je Region ein anderes Zahlenwerk — die Bauform skalierte nicht über den ersten Bericht hinaus.
2. **Kein Vertrag, an dem entlang sich testen ließe.** Es gab keine Stelle, die sagte, was ein Bericht *ist*: welche Felder er hat, was in einen Snapshot darf, was ein Export enthalten muss.
3. **Die Datenqualität war unsichtbar.** Ereignisse, deren `system_type` in keiner Kategorie vorkam, fielen still aus dem Bericht — die Zahl war „vollständig" plausibel und trotzdem zu niedrig. Kein Empfänger konnte das erkennen.
4. **Die k-Anonymisierung hing am Format, nicht am Bericht.** #1278 hatte die Kleinstfallzahl-Suppression für den PDF-Pfad nachgezogen. Ein *neuer* Ausgabeweg (CSV/JSON/XML) hätte diese Härtung ohne Gegenmaßnahme wieder umgangen — dieselben Zahlen, anderes Dateiformat, keine Suppression.

Zusätzliche Randbedingungen:

- **Keine neuen Abhängigkeiten** (Repo-Verbot): kein Plugin-Framework, kein XML-Paket, kein Schema-Validator.
- **Verhaltensparität ist Pflicht.** Der Sachbericht läuft in Bestandsinstanzen; sein Snapshot-Payload und sein PDF mussten die Umstellung byte-gleich überleben.
- **Zyklenfreiheit.** Berichts-Backends fragen zwangsläufig `core.models` ab. Jede Discovery-Bauform, die zur Import- oder App-Start-Zeit scannt, koppelt das Reporting-Package an die halbe App.
- **`DocumentType` trägt seit M2 einen `slug`** ([ADR-037](037-konfigurationspakete-dynamische-felder.md)) — ein zweiter, stabiler Kandidat für den Reporting-Lookup neben `system_type`. Welcher gilt, war offen.

## Decision

Wir führen ein **Plugin-System für Berichts-Datensätze** ein: `src/core/reporting/` mit einem abstrakten Backend-Vertrag, einer Registry mit Lazy-Auto-Discovery, einer formatunabhängigen Export-Pipeline und einer backend-unabhängigen Snapshot-Map. Ein neuer Bericht ist ab jetzt **eine Datei** in `src/core/reporting/backends/` — ohne View, URL, Migration oder AuthZ-Eintrag.

### 1. `DatasetBackend`-ABC + frozen Dataclasses

[`core.reporting.base`](../../src/core/reporting/base.py) definiert den kompletten Vertrag und importiert dafür **nichts** aus der App (kein Model, kein Service):

| Typ | Rolle |
|---|---|
| `DatasetBackend` (ABC) | `slug`, `name`, `snapshot_enabled`; abstrakt `get_field_specs()`, `build_dataset(facility, date_from, date_to)`; überschreibbar `is_available(facility)`, `validate_completeness(facility, date_from, date_to)`, `prepare_export(facility, result)` |
| `DatasetResult` (frozen) | `mode` (`"rows"`/`"aggregate"`), `records`, `summary`, `completeness` |
| `FieldSpec` (frozen) | `key` (stabil, `^[a-z0-9_]+$`) + `label` — eine Spalte der tabellarischen Projektion |
| `CompletenessIssue` (frozen) | `code`, `message`, `severity` (`info`/`warning`), `count` |

**`records`/`summary`-Contract.** `records` ist die **tabellarische Projektion** — eine Liste von Dicts, deren Schlüssel exakt den `FieldSpec.key`-Werten entsprechen; das ist die CSV-Quelle. `summary` ist das **vollständige Aggregat** — der Snapshot-/PDF-/JSON-Payload, der auch Verschachteltes tragen darf, das keine sinnvolle Tabellenzeile ergibt. `records` ist eine Projektion aus `summary`, kein Ersatz dafür. Der Contract macht CSV für **beide** Modi sinnvoll: ein Aggregat-Backend liefert eine Kategorie-Tabelle, ein `rows`-Backend seine Zeilen.

`CompletenessIssue.count` ist **internes Transformationsmaterial**: die Suppression muss die in `message` bezifferte Zahl maschinenlesbar mit der Schwelle vergleichen können. Die Serialisierer geben `count` bewusst **nicht** aus (JSON/XML führen nur `code`/`message`/`severity`) — sonst stünde die gerade unterdrückte Zahl wieder im Artefakt.

`validate_completeness` bekommt **Facility + Zeitraum, nicht das fertige `DatasetResult`**. Die wertvollen Checks betreffen genau das, was *nicht* im Datensatz landet (nicht gemappte Dokumentationstypen, Ereignisse ohne Personenbezug); ein fertiges Ergebnis kann sie definitionsgemäß nicht mehr sehen.

### 2. Registry: Modul-Dict + Decorator + Lazy-Discovery

[`core.reporting.registry`](../../src/core/reporting/registry.py) hält ein schlichtes Modul-Dict `slug -> Singleton-Instanz` (Backends sind zustandslos — Facility und Zeitraum kommen bei jedem Aufruf als Argumente). `@register_backend("slug")` setzt `cls.slug`, validiert `^[a-z0-9_]+$` und weist Doppelregistrierungen mit `ImproperlyConfigured` ab.

**Discovery läuft lazy beim ersten `get_*`-Aufruf**, nicht in `AppConfig.ready()` und nie zur Modul-Importzeit: `_discover()` importiert einmalig (Flag `_discovered`) alle Module unter `core.reporting.backends` per `pkgutil.iter_modules`. Zwei Gründe:

- **Zyklenfreiheit.** Backends importieren `core.models`. Würde `registry.py` das `backends`-Package beim eigenen Import ziehen, entstünde ein Zyklus, sobald ein Backend (mittelbar) auf `core.reporting` zurückverweist. Der Decorator-Import `from core.reporting.registry import register_backend` bleibt dagegen immer zyklusfrei.
- **Importgewicht.** Kein `manage.py`-Aufruf soll die Berichtslogik samt ORM-Abfragen mitladen.

Scheitert ein Backend-Modul beim Import, wird der Fehler als `ImproperlyConfigured` mit Modulnamen re-raised **und `_discovered` zurückgesetzt** — sonst gälte die Discovery als erledigt und jeder weitere Aufruf lieferte still eine unvollständige Registry (fehlender Bericht statt lauter Fehler). Ein `ImproperlyConfigured` *aus dem Modul selbst* (ungültiger Slug, Doppelregistrierung) wird unverändert durchgereicht, statt hinter Import-Vokabular zu verschwinden.

### 3. Snapshot-Generalisierung: `backend_data`-Map + Dual-Write-Übergangsphase

Migration [`0121`](../../src/core/migrations/0121_statisticssnapshot_backend_data.py) ergänzt `StatisticsSnapshot.backend_data` — eine Map `backend_slug -> Roh-Aggregat`. `create_or_update_snapshot` füllt sie aus der Registry (`build_dataset(...).summary` für jedes Backend mit `snapshot_enabled = True`), statt einen Bericht hart aufzurufen.

**Dual-Write als Übergangsphase:** `jugendamt_data` wird weiter synchron mitgeschrieben, Reads bevorzugen `backend_data["jugendamt"]` und fallen auf das Altfeld zurück. Damit ist ein Rollback in **beide** Richtungen ohne Datenverlust möglich. Das Abschalten von Dual-Write und Altfeld ist ein eigenes Folge-Issue (Vorbedingung: mindestens ein Release-Zyklus mit gefülltem `backend_data` in Produktion) — und macht den Rückweg auf Migration `0120` dann datenverlustbehaftet.

Backfill [`0122`](../../src/core/migrations/0122_backfill_backend_data.py) hebt Bestandssnapshots auf das neue Schema: rein additiv, idempotent, `reverse_code = noop` (die Quelle steht unverändert daneben). Er läuft ohne `SET ROLE`-Turnen auf der Admin-Rolle — `core_statisticssnapshot` trägt eine `FORCE ROW LEVEL SECURITY`-Policy, und ein `SET ROLE` auf die App-Rolle würde die Policy scharf schalten und den Lauf ohne gesetztes `app.current_facility_id` still auf **null Zeilen** treffen lassen ([ADR-020](020-three-role-postgres-model.md)).

**`snapshot_enabled` ist ein Datenschutz-Vertrag, kein Feature-Schalter.** Das `summary` eines so markierten Backends muss **aggregatrein** sein: keine Pseudonyme, keine Personenzeilen, keine Freitexte. Der Grund liegt außerhalb des Backends — der Payload wird **roh** (also ohne `prepare_export`) persistiert, und `backend_data` ist im PII-Residue-Sweep (`src/tests/_residue_expectations.py`) **pauschal** als non-PII klassifiziert; die Klassifikation prüft den Inhalt nicht je Backend nach. Ein Backend, das die Zusage nicht halten kann, bleibt `snapshot_enabled = False`, sonst wandert PII an der Klassifikation vorbei in den Snapshot. Der Vertrag steht am Attribut selbst in [`base.py`](../../src/core/reporting/base.py).

### 4. Mapping: `system_type` zuerst, `slug` als Zweitschlüssel, `category` nie

Das Reporting-Mapping läuft über **`DocumentType.system_type`** — die stabile, nach Erstellung unveränderliche Systemkennung. **`DocumentType.category` wird fürs Reporting nie verwendet**: es ist laut Feld-`help_text` eine reine UI-Gruppierung, frei editierbar, und eine Umgruppierung in der Config-UI dürfte niemals Berichtszahlen verschieben. Das löst die in [ADR-034](034-config-ui-category-system-type.md) beschriebene Überlappung der beiden Klassifikationsfelder für den Reporting-Kontext auf.

**`DocumentType.slug`** (seit M2, [ADR-037](037-konfigurationspakete-dynamische-felder.md)) ist der **dokumentierte Zweitschlüssel** und bleibt Folgearbeit: Er ist ebenfalls stabil und unveränderlich, deckt aber auch einrichtungseigene Typen ohne `system_type` ab — genau die, die heute als „ohne Zuordnung" im Vollständigkeitshinweis auftauchen. Der Integrationspunkt ist im Code an `CATEGORY_MAP` ([`backends/jugendamt.py`](../../src/core/reporting/backends/jugendamt.py)) markiert.

### 5. K-Anon-Invariante: der `prepare_export`-Hook

Repo-Invariante seit #1278 (Security R4/R14): **jedes extern zirkulierende Berichts-Artefakt unterdrückt Kleinstfallzahlen.** Ein Formatwechsel darf diese Härtung nicht aushebeln. Deshalb bekommt der Vertrag einen Transformations-Hook, und zwar mit einer scharfen Grenze:

> **Scope-Ergänzung während der Umsetzung.** Dieser Abschnitt gehörte **nicht** zu den ursprünglichen Entscheiden E1–E14 der M5-Planung. `prepare_export`, die Umformulierung der Vollständigkeits-Meldungen und die Schließung der Vier-Term-Identität sind aus den Reviews der Umsetzungsphase entstanden — die generische CSV/JSON/XML-Pipeline hätte die Härtung aus #1278 sonst am PDF-Pfad vorbei ausgehebelt. Die Entscheidung wird hier nachgetragen, weil sie den Vertrag in `base.py` dauerhaft erweitert.

| Pfad | Aufruf | Sicht |
|---|---|---|
| Export CSV/JSON/XML | `export_dataset()` ruft `prepare_export` vor **jedem** Renderer | suppressiert |
| Berichts-Panel (HTMX-Vorschau) | View ruft `prepare_export(facility, build_dataset(...))` | suppressiert — Parität zum Download |
| Snapshot | `create_or_update_snapshot` ruft `build_dataset` direkt | **roh** |
| Sachbericht-PDF | unverändert wie bisher (`suppress_jugendamt_stats` im PDF-Pfad) | wie bisher |

Der Default ist die Identität; wer Zahlen liefert, die das Haus verlassen, überschreibt ihn. `JugendamtBackend.prepare_export` legt dieselbe Maschinerie wie der PDF-Pfad auf Export und Panel und projiziert `records` aus den bereits suppressierten Zeilen neu — sonst hätte das CSV die Rohzahlen weiter gezeigt. `ExampleBackend.prepare_export` zeigt den Minimalfall inklusive der A6.1-Sekundär-Suppression (#1024/#1016): bleibt genau **eine** Zelle unterdrückt, fällt zusätzlich die nächstkleinere sichtbare Zelle, und die Randsumme `total` fällt, sobald überhaupt eine Zeile unterdrückt ist.

**Vollständigkeits-Meldungen sind Teil der Suppression.** Sie werden für den Export **immer** neu formuliert: ohne Bezugsgröße und Prozentanteil, und ganz **ohne jede Ziffer**, sobald die Zahl selbst *oder ihr Komplement* unter der Schwelle liegt. Der Hinweis auf die Lücke bleibt stehen (ein Bericht, der Lücken verschweigt, wäre irreführend vollständig), nur die Zahl verschwindet.

Der Sachbericht braucht darüber hinaus eine **ganzheitliche Prüfung der Vier-Term-Identität**. Konstruktionsbedingt gilt

```
total + unmapped_events  =  Σ by_age_cluster + events_without_client
```

— beide Seiten sind „alle sichtbaren Ereignisse im Zeitraum". Sind **drei** Terme bekannt und der vierte verborgen, ist der vierte exakt bestimmt und die Suppression liefe leer. `_close_identity_leak` degradiert dann einen weiteren Term, und zwar immer über die *Meldungen* (der Report-Körper gehört der geteilten PDF-Funktion): bevorzugt fällt die Zahl einer bezifferten Meldung; erst wenn keine mehr beziffert ist, wird eine **fehlende** Meldung durch einen Unterdrückungs-Hinweis ersetzt — denn eine fehlende Meldung ist deterministisch die Zahl 0 und damit selbst ein bekannter Term. Platzhalter- und Degradations-Text sind **wortgleich**; wäre der Wortlaut unterscheidbar, verriete er, ob die Lücke > 0 ist.

### 6. Export-Pipeline: ein Einstieg, drei Renderer

[`export_dataset(backend, facility, date_from, date_to, format="csv")`](../../src/core/reporting/export.py) ist der einzige öffentliche Einstiegspunkt; `SUPPORTED_FORMATS` ist die Single Source of Truth für Aufrufer, die ein unbekanntes Format vorab selbst als HTTP 400 beantworten wollen (ein zu breiter `except ValueError` um den ganzen Aufruf würde sonst auch Renderer-Fehler als 400 maskieren). Alle drei Formate laufen über `safe_download_response` (RFC 5987, `nosniff`, `Cache-Control: no-store`).

**CSV wird gestreamt**, JSON und XML nicht — sie sind kleine, geschlossene Aggregat-Payloads. Jede CSV-Zelle (auch Metadaten und Header) läuft durch `_sanitize_csv_cell`, den bestehenden Formula-Injection-Schutz aus `core.services.system.export`; dieselbe Zelle ist in jedem CSV gleich gefährlich, eine Zweitimplementierung wäre eine Drift-Quelle. XML wird ausschließlich **generiert**, nie geparst — die XXE-/Billion-Laughs-Fläche von `xml.etree.ElementTree` betrifft nur `parse()`/`fromstring()`, `defusedxml` wäre daher eine Abhängigkeit ohne Wirkung.

### 7. Generische Endpunkte statt Endpunkt je Bericht

Zwei URL-Patterns decken **alle** Backends ab, Auswahl über `?backend=<slug>`: `statistics/reports/panel/` (HTMX-Vorschau) und `statistics/reports/export/` (Download). Beide sind `LEAD_PLUS`-gated wie die übrige Export-Familie; der Export auditiert über die bestehende `AuditLog.Action.EXPORT` mit `detail={"backend", "format", "date_from", "date_to"}` — keine neue Action, keine Migration. Ein neues Backend braucht deshalb **keinen** URL-, View- oder AuthZ-Matrix-Eintrag.

### 8. Bewusste Grenzen

- **Der monatsübergreifende Merge bleibt sachberichtspezifisch** (`_merge_jugendamt_stats`). Ein generischer Merge über beliebige Aggregat-Strukturen ist YAGNI, solange es genau ein snapshot-fähiges Backend gibt — und er wäre ohne Merge-Semantik im Vertrag (summierbar? Maximum? Menge?) nicht definierbar.
- **JSON/XML ungestreamt.** Aggregat-Payloads sind klein. Ein `rows`-Backend mit Massendaten wäre die Grenze dieser Entscheidung; dann bräuchte es Streaming-Renderer.
- **`core/reporting` liegt außerhalb der mypy-strict-Zone und der mutmut-Pfade.** Der Code ist strict-kompatibel annotiert; die Aufnahme in die Gates ändert Build-Konfiguration und bleibt ein offener Maintainer-Entscheid.
- **Keine externen Plugin-Pakete.** Discovery scannt ausschließlich `core/reporting/backends/`; `entry_points` von Drittpaketen sind bewusst nicht vorgesehen — ein fremdes Paket mit Datenbankzugriff auf Sozialdaten wäre eine Vertrauensentscheidung eigener Güte. Denkbar als spätere Erweiterung, dann mit eigenem Sicherheitskonzept.
- **Der PDF-Pfad des Sachberichts bleibt unverändert.** M5 ergänzt CSV/JSON/XML generisch, ersetzt den etablierten PDF-Bericht nicht.

## Consequences

**Positiv:**

- **+ Ein neuer Bericht ist eine Datei.** Modul in `core/reporting/backends/` ablegen, `@register_backend` setzen, zwei Methoden implementieren — Panel-Dropdown, Vorschau und alle drei Exportformate stehen automatisch zur Verfügung. Keine Migration, keine URL, kein AuthZ-Eintrag.
- **+ Die k-Anon-Härtung hängt am Bericht, nicht am Dateiformat.** `prepare_export` läuft vor jedem Renderer und vor der Panel-Anzeige; ein künftiges viertes Format erbt die Suppression, ohne dass jemand daran denken muss.
- **+ Datenqualität ist sichtbar.** `validate_completeness` beziffert, was der Bericht **nicht** zeigt. Der Empfänger erkennt Lücken, statt eine plausible, zu niedrige Zahl für vollständig zu halten.
- **+ Snapshots sind backend-unabhängig.** Ein zweites Aggregat-Backend braucht kein Modellfeld mehr, nur `snapshot_enabled = True`.
- **+ Der Vertrag ist testbar.** Registry-Isolation, Parity-, Suppressions- und Panel-Tests hängen an einer definierten Schnittstelle statt an einer Funktion in einem Service-Modul.

**Negativ / Kosten:**

1. **`snapshot_enabled` ist eine Zusage, die niemand automatisch prüft.** Die PII-Klassifikation von `backend_data` ist pauschal. Wer den Vertrag bricht, schreibt PII an der Klassifikation vorbei in den Snapshot — der Sweep schlägt nicht an. Jedes neue snapshot-fähige Backend braucht deshalb eine bewusste Prüfung, ob sein `summary` wirklich aggregatrein ist.
2. **`prepare_export` ist nicht idempotent.** Der Hook erwartet das **rohe** `build_dataset`-Ergebnis (z. B. wandelt `JugendamtBackend` `by_category` von Tupeln in Dict-Zeilen). Ein zweiter Durchlauf über dasselbe Ergebnis ist kein sinnvoller Aufruf; Aufrufer müssen genau einmal transformieren.
3. **Zwei Sichten auf denselben Datenstand.** Panel/Export zeigen suppressierte Zahlen, Snapshot und Dashboard-Kennzahlen rohe. Das ist gewollt (intern vs. extern zirkulierend), aber es bedeutet, dass dieselbe Kennzahl an zwei Stellen der Oberfläche unterschiedlich aussehen kann.
4. **Übersuppression an den Rändern.** Ein leerer Zeitraum ist von einer Suppression nicht unterscheidbar (0 < k), und bei leeren Altersgruppen-Listen kann die Identitäts-Schließung mehr degradieren als nötig. Die Richtung ist sicher (lieber zu wenig als zu viel Information), der Aussagekraftverlust ist real.
5. **Ein defektes Backend lässt den Snapshot-Lauf der Einrichtung scheitern.** `create_or_update_snapshot` iteriert ohne Fehlerisolation über alle `snapshot_enabled`-Backends — fail-loud statt still unvollständiger Snapshots. Ein Berichts-Bug ist damit ein Betriebsvorfall, kein leises Datenloch.
6. **Dual-Write bis auf Weiteres.** `jugendamt_data` und `backend_data["jugendamt"]` laufen parallel und können bei manuellen Eingriffen auseinanderlaufen; `backend_data` ist die führende Quelle. Die Abschaltung ist Folgearbeit.
7. **Die Aggregations-Ebene wächst außerhalb von `services/`.** `core/reporting/` ist eine bewusste Ergänzung zur „Business-Logik in `services/`"-Konvention; wer Fachlogik sucht, muss künftig an zwei Stellen schauen.
8. **Panel/Export lesen live, das Sachbericht-PDF liest hybrid.** `export_dataset` und die Panel-Vorschau bauen den Datensatz immer über `build_dataset`; der bewusst unveränderte PDF-Pfad geht weiter über `get_jugendamt_statistics_hybrid` (Monats-Snapshots). Dieselbe Kennzahl kann deshalb je Artefakt abweichen: mehrmonatige `unique_clients` (Summe der Monatswerte vs. echter DISTINCT über den Gesamtzeitraum) und Zeiträume jenseits der Aufbewahrungsfristen (Snapshots überdauern die Event-Retention, die Live-Abfrage sieht soft-gelöschte Ereignisse nicht mehr). Die Divergenzklasse ist nicht neu — sie besteht seit jeher zwischen dem live gelesenen Externen Bericht und dem hybriden PDF; M5 fügt eine dritte Instanz desselben Musters hinzu. Eine Vereinheitlichung ist Folgearbeit über alle drei Artefakte (#1743) und bräuchte zusätzlich `completeness` im Snapshot — den generischen Monats-Merge schließt Entscheid 8 („Bewusste Grenzen") heute bewusst aus.
9. **Interne Bezeichner bleiben „jugendamt".** Slug, Modulname und Kategorien-Map heißen weiter so, obwohl das sichtbare Label bereits neutral ist. Die restlose Umbenennung ist eigene Arbeit (#1739) — ein Slug-Wechsel bricht Bestands-`backend_data`-Schlüssel und muss migriert werden.

## Alternatives considered

- **Discovery in `AppConfig.ready()`.** Verworfen: Backends importieren `core.models`; ein Scan zur App-Start-Zeit koppelt das Reporting-Package an die halbe App und erzeugt Importzyklen, sobald ein Backend mittelbar auf `core.reporting` zurückverweist. Zusätzlich Importgewicht bei jedem `manage.py`-Aufruf.
- **Ein Snapshot-Feld je Bericht** (wie bisher `jugendamt_data`). Verworfen: jeder neue Bericht wäre eine Migration, und die Menge der Berichte ist per Definition offen.
- **`validate_completeness(dataset)` mit fertigem Ergebnis** (Wortlaut des Ausgangs-Issues). Verworfen: die wertvollen Checks betreffen Daten, die im Datensatz gar nicht vorkommen — nicht gemappte Dokumentationstypen werden vor der Aggregation ausgefiltert.
- **Suppression im Renderer statt im Backend.** Verworfen: nur das Backend kennt die Struktur seines Aggregats und weiß, welche Zellen komplementär zusammenhängen. Ein generischer Renderer-Filter würde entweder zu wenig unterdrücken (Randsummen, Identitäten) oder unbrauchbar viel.
- **Suppression schon in `build_dataset`.** Verworfen: dann wären auch Snapshots suppressiert, und der interne Rohbestand — Grundlage jeder späteren Auswertung und des unveränderten PDF-Pfads — wäre unwiederbringlich beschnitten.
- **`prepare_export` auch für den PDF-Pfad.** Verworfen für M5: der PDF-Bericht suppressiert bereits über `suppress_jugendamt_stats` (#1278); ihn zusätzlich durch den Hook zu ziehen wäre eine Doppeltransformation an einem Bestandspfad ohne Gewinn.
- **`DocumentType.category` als Reporting-Schlüssel.** Verworfen: frei editierbare UI-Gruppierung ([ADR-034](034-config-ui-category-system-type.md)) — ein Umgruppieren in der Config-UI würde Berichtszahlen verschieben.
- **`DocumentType.slug` sofort als Primärschlüssel.** Verworfen für v1: `system_type` ist der Bestandsschlüssel, an dem die Verhaltensparität hängt. Der Slug kommt als *zusätzlicher* Schlüssel, nicht als Ersatz — sonst wäre die Migration des Sachberichts keine Parität, sondern ein Zahlen-Umbau.
- **Externe Plugin-Pakete über `entry_points`.** Verworfen: ein fremdes Paket mit Lesezugriff auf Sozialdaten ist eine Vertrauensentscheidung eigener Güte und braucht ein Sicherheitskonzept (Signatur, Freigabe, Sandbox), nicht nur einen Discovery-Mechanismus.
- **Generischer Monats-Merge im Vertrag.** Verworfen: ohne Merge-Semantik je Feld (summierbar, Maximum, Mengenvereinigung) nicht definierbar; bei genau einem snapshot-fähigen Backend YAGNI.
- **`defusedxml` für den XML-Renderer.** Verworfen: `defusedxml` härtet Parsing. Hier wird ausschließlich serialisiert — eine Abhängigkeit ohne Wirkung.

## References

- [`src/core/reporting/base.py`](../../src/core/reporting/base.py) — Vertrag (`DatasetBackend`, `DatasetResult`, `FieldSpec`, `CompletenessIssue`, `snapshot_enabled`-Zusage)
- [`src/core/reporting/registry.py`](../../src/core/reporting/registry.py) — Registry + Lazy-Discovery (`register_backend`, `get_backends`, `get_backend`, `get_available_backends`)
- [`src/core/reporting/export.py`](../../src/core/reporting/export.py) — Export-Pipeline (`export_dataset`, `SUPPORTED_FORMATS`, CSV/JSON/XML-Renderer)
- [`src/core/reporting/backends/jugendamt.py`](../../src/core/reporting/backends/jugendamt.py) — Sachbericht-Backend inkl. Vier-Term-Identitäts-Schließung
- [`src/core/reporting/backends/example.py`](../../src/core/reporting/backends/example.py) — Referenz-Plugin (Copy-Vorlage, A6.1-Sekundär-Suppression)
- [`src/core/views/statistics.py`](../../src/core/views/statistics.py) — generische Endpunkte (`DatasetReportPanelView`, `DatasetExportView`)
- [`src/core/services/dashboard/snapshot.py`](../../src/core/services/dashboard/snapshot.py) — `backend_data`-Dual-Write + hybride Leseschicht
- Migrationen [`0121`](../../src/core/migrations/0121_statisticssnapshot_backend_data.py) (Feld), [`0122`](../../src/core/migrations/0122_backfill_backend_data.py) (Backfill)
- [`docs/reporting-plugins.md`](../reporting-plugins.md) — Entwickler-Doku (API-Referenz, „Eigenes Backend in 5 Schritten", Test-Checkliste); EN: [`docs/en/reporting-plugins.md`](../en/reporting-plugins.md)
- [ADR-023](023-k-anonymization-statistik.md) — K-Anonymisierung für externe Statistik (Schwelle, Suppressions-Semantik)
- [ADR-020](020-three-role-postgres-model.md) — Drei-Rollen-Postgres-Modell (warum der Backfill auf der Admin-Rolle läuft)
- [ADR-034](034-config-ui-category-system-type.md) — `category` vs. `system_type` (UI-Seite derselben Überlappung)
- [ADR-037](037-konfigurationspakete-dynamische-felder.md) — `DocumentType.slug` als stabiler Zweitschlüssel-Kandidat
- Issue #1061 — M5-Planung (Entscheide E1–E14)
- Issue #1278 — k-Anon-Suppression im Sachbericht (Ursprung der Export-Invariante)
