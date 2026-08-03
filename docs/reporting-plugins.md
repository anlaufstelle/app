# Reporting-Plugins (Berichts-Backends)

Ein **Berichts-Backend** kapselt die Fachlogik eines Berichtsstandards — „Kontakte nach Kategorie", „Ereignisse je Dokumentationstyp", ein regionales Kostenträger-Formular — hinter einer festen Schnittstelle. Panel-Vorschau, CSV-/JSON-/XML-Export und die Snapshot-Speicherung sprechen ausschließlich diese Schnittstelle an und wissen nichts über den einzelnen Bericht.

Praktische Folge: **ein neuer Bericht ist eine Datei.** Ein Modul unter [`src/core/reporting/backends/`](../src/core/reporting/backends/) mit einer `DatasetBackend`-Unterklasse und dem `@register_backend`-Decorator erscheint automatisch im Berichte-Dropdown der Statistik-Seite und in allen drei Exportformaten. Keine Migration, kein URL-Pattern, kein View, kein Eintrag in der AuthZ-Matrix.

- **Warum das System so aussieht, wie es aussieht:** [ADR-038](adr/038-reporting-plugin-backends.md).
- **Warum Kleinstfallzahlen unterdrückt werden:** [ADR-023](adr/023-k-anonymization-statistik.md) und #1278.
- **Warum das Mapping über `system_type` läuft und nie über `category`:** [ADR-034](adr/034-config-ui-category-system-type.md).

> **Die wichtigste Regel vorweg.** Alles, was ein Backend über `build_dataset()` liefert, ist **roh**. Alles, was das Haus verlässt (Download **und** Panel-Vorschau), läuft vorher durch `prepare_export()`. Wer Zahlen produziert, die extern zirkulieren, muss diesen Hook implementieren — siehe [§ 6](#6-k-anonymität--der-prepare_export-hook).

---

## 1. Architekturüberblick

```
                        src/core/reporting/backends/*.py
                        ┌───────────────────────────────────┐
                        │  @register_backend("mein_slug")   │
                        │  class MeinBackend(DatasetBackend)│
                        └───────────────┬───────────────────┘
                                        │ Registrierung als Import-Seiteneffekt
                                        ▼
   get_backends() ──── lazy ────►  registry._REGISTRY   { slug → Singleton-Instanz }
   get_backend(slug)                    │
   get_available_backends(facility)     │
                                        │
        ┌───────────────────────────────┼────────────────────────────────┐
        ▼                               ▼                                ▼
  Berichts-Panel                 export_dataset()            create_or_update_snapshot()
  (HTMX-Vorschau)                (CSV · JSON · XML)          (Monats-Snapshot je Facility)
        │                               │                                │
        │ build_dataset()               │ build_dataset()                │ build_dataset()
        │ prepare_export()  ◄── K-Anon ─┤ prepare_export()               │ (KEIN prepare_export)
        ▼                               ▼                                ▼
  suppressierte Vorschau        suppressierte Datei         StatisticsSnapshot.backend_data
                                                                  (Roh-Aggregat)
```

| Modul | Inhalt |
|---|---|
| [`core/reporting/base.py`](../src/core/reporting/base.py) | Der Vertrag: `DatasetBackend`, `DatasetResult`, `FieldSpec`, `CompletenessIssue`. Importiert **nichts** aus der App. |
| [`core/reporting/registry.py`](../src/core/reporting/registry.py) | Registry + Lazy-Auto-Discovery: `register_backend`, `get_backends`, `get_backend`, `get_available_backends`. |
| [`core/reporting/export.py`](../src/core/reporting/export.py) | Formatunabhängige Export-Pipeline: `export_dataset`, `SUPPORTED_FORMATS`. |
| [`core/reporting/backends/`](../src/core/reporting/backends/) | Die Backends selbst. Discovery-Ziel — hier abgelegte Module werden automatisch geladen. |
| [`core/views/statistics.py`](../src/core/views/statistics.py) | Generische Endpunkte: `DatasetReportPanelView` (Vorschau), `DatasetExportView` (Download). |
| [`core/services/dashboard/snapshot.py`](../src/core/services/dashboard/snapshot.py) | Schreibt `StatisticsSnapshot.backend_data` aus allen `snapshot_enabled`-Backends. |

---

## 2. API-Referenz

### 2.1 `DatasetBackend`

```python
class DatasetBackend(ABC):
    slug: str = ""                  # von @register_backend gesetzt/validiert
    name: str = ""                  # Anzeigename im Dropdown (gettext_lazy-fähig)
    snapshot_enabled: bool = False  # True → summary landet in StatisticsSnapshot.backend_data

    def is_available(self, facility) -> bool: ...
    @abstractmethod
    def get_field_specs(self) -> list[FieldSpec]: ...
    @abstractmethod
    def build_dataset(self, facility, date_from, date_to) -> DatasetResult: ...
    def validate_completeness(self, facility, date_from, date_to) -> list[CompletenessIssue]: ...
    def prepare_export(self, facility, result: DatasetResult) -> DatasetResult: ...
```

| Element | Pflicht | Bedeutung |
|---|---|---|
| `slug` || Wird vom Decorator gesetzt. **Nicht** selbst zuweisen. Format `^[a-z0-9_]+$`. Er steckt in Query-Parametern, Dateinamen und `backend_data`-Schlüsseln und ist damit faktisch unveränderlich. |
| `name` | **ja** | Sichtbarer Name im Berichte-Dropdown und im JSON-Payload. Als `gettext_lazy` deklarieren; die Export-Pipeline castet an der Payload-Grenze selbst nach `str`. |
| `snapshot_enabled` | nein | Default `False`. `True` nur bei **aggregatreinem** `summary` — siehe [§ 5](#5-snapshot-integration). |
| `is_available(facility)` | nein | Default `True`. Hook für einrichtungsabhängige Berichte. `False` blendet das Backend aus dem Dropdown aus **und** lässt Panel/Export mit HTTP 404 antworten. |
| `get_field_specs()` | **ja** | Spaltendefinition der tabellarischen Projektion. Reihenfolge = Spaltenreihenfolge im CSV. |
| `build_dataset(facility, date_from, date_to)` | **ja** | Baut den Datensatz für den **inklusiven** Zeitraum `[date_from, date_to]`. Ergebnis ist **roh** (unsuppressiert). |
| `validate_completeness(facility, date_from, date_to)` | nein | Default `[]`. Datenqualitäts-Checks — siehe [§ 8](#8-completeness--was-der-bericht-nicht-zeigt). |
| `prepare_export(facility, result)` | nein | Default: unverändert. Transformation vor Export **und** Panel-Anzeige — siehe [§ 6](#6-k-anonymität--der-prepare_export-hook). |

`validate_completeness` bekommt bewusst **Facility + Zeitraum statt des fertigen `DatasetResult`**: Die aussagekräftigen Checks betreffen genau das, was *nicht* im Datensatz landet (ausgefilterte Dokumentationstypen, Ereignisse ohne Personenbezug). Ein fertiges Ergebnis kann sie nicht mehr sehen. `build_dataset()` ruft die Methode üblicherweise selbst und bettet das Ergebnis in `DatasetResult.completeness` ein.

### 2.2 `DatasetResult`

```python
@dataclass(frozen=True)
class DatasetResult:
    mode: Literal["rows", "aggregate"]
    records: list[dict]                   # tabellarische Projektion; Keys == FieldSpec.key
    summary: dict                         # vollständiges Aggregat; Snapshot-Payload
    completeness: list[CompletenessIssue]
```

**Der `records`/`summary`-Contract ist der Kern des Vertrags:**

- **`records`** ist die **tabellarische Projektion** — eine Liste von Dicts, deren Schlüssel exakt den `key`-Werten aus `get_field_specs()` entsprechen. Eine Zeile pro Eintrag, eine Spalte pro `FieldSpec`. Das ist die CSV-Quelle.
- **`summary`** ist das **vollständige Aggregat** — ein freies Dict mit dem kompletten Payload (verschachtelte Zähler, Zeitreihen, Randsummen), wie es in `StatisticsSnapshot.backend_data` abgelegt und für JSON/XML verwendet wird.

`records` ist eine Projektion **aus** `summary`, kein Ersatz dafür: `summary` darf Informationen enthalten, die keine sinnvolle Tabellenzeile ergeben. Umgekehrt gilt: Was nur in `summary` steht, erscheint nie im CSV.

`mode` beschreibt den Charakter des Datensatzes und steuert die Panel-Vorschau: `"aggregate"` zeigt **alle** Records (es sind wenige, verdichtete Zeilen), `"rows"` zeigt die ersten 10 plus die Gesamtzahl.

Das Dataclass ist `frozen`. Änderungen laufen über `dataclasses.replace(result, records=…, summary=…)`.

### 2.3 `FieldSpec`

```python
@dataclass(frozen=True)
class FieldSpec:
    key: str    # stabiler Spalten-/Record-Schlüssel, ^[a-z0-9_]+$
    label: str  # Anzeigename
```

`key` ist der Dict-Schlüssel in `records` **und** der XML-Elementname eines Feldes. Schlüssel, die kein gültiger XML-Elementname sind, werden vom XML-Renderer als `<entry key="…">` ausgegeben statt als eigenes Element — das Format bleibt wohlgeformt, wird aber unhandlicher. Halte dich an `^[a-z0-9_]+$`, dann tritt der Fall nicht ein.

`label` ist die CSV-Kopfzeile und die Tabellenüberschrift im Panel.

### 2.4 `CompletenessIssue`

```python
@dataclass(frozen=True)
class CompletenessIssue:
    code: str                                        # maschinenlesbar, z. B. "unmapped_system_types"
    message: str                                     # menschenlesbar (deutsch)
    severity: Literal["info", "warning"] = "warning"
    count: int | None = None                         # Rohwert hinter ``message``; nie serialisiert
```

| Feld | Verwendung |
|---|---|
| `code` | Stabiler Bezeichner. Über ihn läuft die Zuordnung von Ersatztexten in `prepare_export`. |
| `message` | Was der Empfänger liest. Panel, JSON und XML geben ihn aus, das CSV nicht. |
| `severity` | `"warning"` rendert im Panel als gelbes „Hinweis"-Badge, `"info"` als graues „Info"-Badge. |
| `count` | **Nur intern.** Die Rohzahl, die in `message` beziffert ist — die K-Anon-Suppression braucht sie maschinenlesbar, um sie mit der Schwelle zu vergleichen. **JSON und XML serialisieren `count` bewusst nicht** (sie führen nur `code`/`message`/`severity`), sonst stünde die gerade unterdrückte Zahl wieder im Artefakt. |

---

## 3. Eigenes Backend in fünf Schritten

Das Referenz-Plugin [`backends/example.py`](../src/core/reporting/backends/example.py) ist als Copy-Vorlage gedacht — es zeigt am minimalen Fall alles, was ein Backend braucht. Die folgenden Auszüge stammen daraus.

### Schritt 1 — Modul anlegen und registrieren

```python
from core.reporting.base import DatasetBackend, DatasetResult, FieldSpec
from core.reporting.registry import register_backend


@register_backend("example")
class ExampleBackend(DatasetBackend):
    """Ereignisse je Dokumenttyp im Zeitraum — kleinstmoegliches Referenz-Backend."""

    name = _("Beispiel: Ereignisse je Dokumenttyp")
    snapshot_enabled = False
```

Datei nach `src/core/reporting/backends/<name>.py`. Der Decorator setzt `slug`, prüft das Format und weist doppelte Slugs mit `ImproperlyConfigured` ab.

### Schritt 2 — Spalten deklarieren

```python
    def get_field_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec("document_type", "Dokumenttyp"),
            FieldSpec("category", "Kategorie"),
            FieldSpec("system_type", "System-Typ"),
            FieldSpec("count", "Anzahl"),
        ]
```

Die Reihenfolge ist die Spaltenreihenfolge im CSV und im Panel.

### Schritt 3 — Datensatz bauen

```python
    def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
        rows = (
            Event.objects.filter(
                facility=facility,
                is_deleted=False,
                occurred_at__date__gte=date_from,
                occurred_at__date__lte=date_to,
            )
            .values("document_type_id", "document_type__name",
                    "document_type__category", "document_type__system_type")
            .annotate(count=Count("id"))
            .order_by("document_type__name")
        )

        records = [
            {
                "document_type": row["document_type__name"],
                "category": str(DocumentType.Category(row["document_type__category"]).label),
                "system_type": row["document_type__system_type"] or "",
                "count": row["count"],
            }
            for row in rows
        ]

        return DatasetResult(
            mode="rows",
            records=records,
            summary={"total": sum(row["count"] for row in records)},
            completeness=[],
        )
```

Drei Punkte, die leicht untergehen:

- **Immer nach `facility` filtern.** Backends laufen im Request-Kontext mit RLS, aber auch im Snapshot-Command; verlasse dich nicht auf die Datenbankschicht allein.
- **`is_deleted=False`**, sonst zählen soft-gelöschte Ereignisse mit.
- **Aggregieren statt iterieren.** `.values(...).annotate(Count(...))` liefert eine Zeile je Gruppe in **einer** Query; eine Python-Schleife über Ereignisse wird bei Jahreszeiträumen unbrauchbar langsam.

### Schritt 4 — K-Anonymität sicherstellen

Sobald der Bericht heruntergeladen werden kann, ist `prepare_export` **Pflichtlektüre**, nicht Kür. Details in [§ 6](#6-k-anonymität--der-prepare_export-hook); der Minimalfall aus `example.py`:

```python
    def prepare_export(self, facility, result: DatasetResult) -> DatasetResult:
        threshold = _get_threshold(facility)
        suppressed_rows = _suppress_small(result.records, threshold, count_key="count")
        field_keys = [spec.key for spec in self.get_field_specs()]
        records = [{key: row[key] for key in field_keys} for row in suppressed_rows]

        total = result.summary["total"]
        any_row_suppressed = any(row["suppressed"] for row in suppressed_rows)
        total_faellt = total < threshold or any_row_suppressed

        summary = dict(result.summary)
        summary["total"] = None if total_faellt else total
        summary["k_anonymity_threshold"] = threshold
        return dataclasses.replace(result, records=records, summary=summary)
```

Die Rückprojektion auf die deklarierten `FieldSpec`-Schlüssel ist kein Schönheitsschritt: `_suppress_small` hängt jeder Zeile ein internes `suppressed`-Flag an, das sonst ungefiltert in CSV, JSON und XML landen würde.

### Schritt 5 — Tests schreiben

Siehe [§ 10](#10-test-checkliste-für-neue-backends). Danach ist nichts mehr zu tun — kein Registrierungs-Eintrag, keine URL, keine Migration.

---

## 4. Discovery-Regeln

- **Ablegen genügt.** [`registry._discover()`](../src/core/reporting/registry.py) importiert beim ersten `get_backends()`/`get_backend()`/`get_available_backends()`-Aufruf einmalig alle Module unter `core.reporting.backends` (`pkgutil.iter_modules`). Ein Modul-Flag macht den Lauf idempotent.
- **Registrierung passiert zur Importzeit — ausschließlich über den Decorator.** Es gibt keine Konfigurationsliste, in die ein Backend zusätzlich eingetragen werden müsste.
- **Keine neuen URLs, Views oder AuthZ-Einträge.** Die generischen Endpunkte `statistics/reports/panel/` und `statistics/reports/export/` wählen das Backend über `?backend=<slug>` und decken damit jedes Backend ab. Beide sind bereits `LEAD_PLUS`-gated, der Export ratelimited und auditiert (`AuditLog.Action.EXPORT` mit `detail={"backend", "format", …}`).
- **Discovery läuft nie zur Modul-Importzeit** — weder beim Import von `registry.py` noch in `AppConfig.ready()`. Das hält die Importkette zyklusfrei, obwohl Backends `core.models` importieren. Der umgekehrte Weg (`from core.reporting.registry import register_backend`) ist immer sicher.
- **Ein defektes Modul ist ein lauter Fehler.** Scheitert der Import, wird er als `ImproperlyConfigured` mit Modulnamen re-raised und das Discovery-Flag zurückgesetzt — eine still unvollständige Registry wäre schlimmer als ein Absturz.
- **Ein Slug ist praktisch unveränderlich.** Er steckt in Query-Parametern, Dateinamen und — bei `snapshot_enabled` — in den Schlüsseln von `StatisticsSnapshot.backend_data`. Ein Wechsel braucht eine Datenmigration.

**Sichtbarkeit prüfen:**

```bash
python src/manage.py shell -c "from core.reporting.registry import get_backends; print([b.slug for b in get_backends()])"
```

---

## 5. Snapshot-Integration

`create_or_update_snapshot(facility, year, month)` schreibt für jedes Backend mit `snapshot_enabled = True` das **rohe** `build_dataset(...).summary` in die Map `StatisticsSnapshot.backend_data` (Schlüssel = Slug). `prepare_export` läuft dabei bewusst **nicht** — der Snapshot ist der interne Rohbestand.

> **`snapshot_enabled` ist ein Datenschutz-Vertrag, kein Feature-Schalter.**
>
> Das `summary` eines snapshot-fähigen Backends muss **aggregatrein** sein: keine Pseudonyme, keine Personenzeilen, keine Freitexte. Der Grund liegt außerhalb des Backends — `backend_data` ist im PII-Residue-Sweep (`src/tests/_residue_expectations.py`) **pauschal** als non-PII klassifiziert, und diese Klassifikation prüft den Inhalt nicht je Backend nach. Ein Backend, das die Zusage nicht halten kann, bleibt `snapshot_enabled = False`; sonst wandert personenbezogene Information an der Klassifikation vorbei in den Snapshot.
>
> `mode="rows"`-Backends sind daher im Regelfall **nicht** snapshot-fähig: Zeilendaten sind weder größen- noch datenschutzseitig für eine dauerhafte Ablage gedacht.

**Übergangsphase (Dual-Write).** Das Altfeld `StatisticsSnapshot.jugendamt_data` wird weiterhin synchron mitgeschrieben; Lesepfade bevorzugen `backend_data["jugendamt"]` und fallen nur für Snapshots vor dem Backfill auf das Altfeld zurück. Migration [`0122`](../src/core/migrations/0122_backfill_backend_data.py) hat Bestandssnapshots additiv nachgezogen. Das Abschalten von Dual-Write und Altfeld ist Folgearbeit (#1061) — **neue** Backends betrifft das nicht, sie schreiben ausschließlich in `backend_data`.

**Merge-Grenze.** Der hybride Lesepfad setzt Zeiträume aus Monats-Snapshots zusammen und muss die Monatsaggregate dafür verschmelzen. Dieser Merge ist **sachberichtspezifisch** (`_merge_jugendamt_stats`) und kennt genau dessen Struktur. Für ein zweites snapshot-fähiges Backend gibt es heute **keinen** generischen Merge: Ohne Merge-Semantik je Feld (summieren? Maximum? Mengenvereinigung?) ist er nicht definierbar. Wer ein zweites Aggregat-Backend snapshot-fähig machen will, plant den Merge als eigene Arbeit ein — bis dahin liest es über `build_dataset` live.

**Fail-loud.** Der Snapshot-Lauf iteriert ohne Fehlerisolation über die Backends: Wirft ein Backend, scheitert der **gesamte** Snapshot-Lauf dieser Einrichtung. Das ist Absicht — ein still unvollständiger Snapshot wäre ein leises Datenloch, das erst Monate später auffällt.

---

## 6. K-Anonymität — der `prepare_export`-Hook

**Repo-Invariante** (#1278, Security R4/R14): Jedes extern zirkulierende Berichts-Artefakt unterdrückt Kleinstfallzahlen. Ein Formatwechsel darf diese Härtung nie aushebeln.

| Pfad | `prepare_export`? | Sicht |
|---|---|---|
| Download CSV / JSON / XML | **ja** — `export_dataset()` ruft ihn vor *jedem* Renderer | suppressiert |
| Berichts-Panel (HTMX-Vorschau) | **ja** — die View ruft `prepare_export(facility, build_dataset(...))` | suppressiert (Parität zum Download) |
| Snapshot | nein — `build_dataset` direkt | roh |
| Sachbericht-PDF | nein — eigener Pfad, suppressiert seit #1278 selbst | suppressiert |

### Brauche ich einen eigenen Override?

**Ja, sobald dein Backend Zahlen liefert, die das Haus verlassen** — und das tun sie, sobald das Backend registriert ist, denn Download und Panel stehen dann automatisch zur Verfügung. Der Default (`return result`) ist nur für Backends vertretbar, deren Zellen konstruktionsbedingt keine Kleinstfallzahlen sein können.

Die Schwelle *k* kommt je Einrichtung aus `Settings.k_anonymity_threshold` (Default 5) und wird über `_get_threshold(facility)` aus [`core.services.dashboard.external_report`](../src/core/services/dashboard/external_report.py) gelesen — demselben Modul, das auch `_suppress_small` und `suppress_jugendamt_stats` für den PDF- und den externen Berichtspfad bereitstellt. Setze `summary["k_anonymity_threshold"] = threshold`, wenn dein Backend suppressiert: Der CSV-Renderer hängt daraufhin einen Hinweis an die Metadatenzeile, und das Panel blendet den Suppressions-Hinweis ein.

### Minimalbeispiel — `ExampleBackend`

Eine Kennzahl je Zeile plus eine Randsumme. Zu lösen ist genau eine Rückrechnungs-Gefahr: `total = Σ counts`. Bliebe `total` nach einer Zeilen-Suppression roh stehen, wäre die unterdrückte Zelle exakt als Differenz zu den sichtbaren Zeilen bestimmbar. Deshalb:

- `_suppress_small(rows, threshold, count_key="count")` setzt Zellen `< k` auf `count=None`/`suppressed=True` und führt die **Sekundär-Suppression nach A6.1** (#1024/#1016) gleich mit aus: Bleibt danach genau **eine** Zelle unterdrückt, fällt zusätzlich die nächstkleinere sichtbare Zelle, damit mindestens zwei Unbekannte übrig bleiben.
- `total` fällt, wenn es **selbst** unter der Schwelle liegt (R14) **oder** wenn überhaupt eine Zeile unterdrückt ist. Letzteres ist die konservative Wahl: Die Sekundär-Suppression schützt nur den Fall *genau einer* unterdrückten Zelle; bleiben mehrere unterdrückt, wäre `total` weiterhin ein Rückrechnungs-Hebel.

### Vollbeispiel — `JugendamtBackend`

Mehrere zusammenhängende Kennzahlen plus Vollständigkeits-Meldungen. Hier reicht Zellen-Suppression nicht:

1. **Report-Körper:** dieselbe Maschinerie wie im PDF (`suppress_jugendamt_stats`) — Kategorien- und Altersgruppen-Zellen `< k` fallen inklusive Sekundär-Suppression, `unique_clients` und die Randsumme `total < k` werden `None`. `records` wird aus den **bereits suppressierten** Zeilen neu projiziert; ohne diesen Schritt hätte das CSV die Rohzahlen weiter gezeigt.
2. **Vollständigkeits-Meldungen** werden für den Export **immer** neu formuliert: ohne Bezugsgröße und ohne Prozentanteil (die Bezugsgröße wäre der Hebel für die komplementäre Rückrechnung). Die Zahl fällt ganz, wenn sie selbst `< k` ist, wenn ihr **Komplement** `< k` ist, oder wenn es für den `code` keine geprüft ziffernarme Formulierung gibt (Fail-safe für künftige Checks). Der Hinweis auf die Lücke bleibt in jedem Fall stehen — ein Bericht, der Lücken verschweigt, wäre irreführend vollständig.
3. **Vier-Term-Identität.** Konstruktionsbedingt gilt

 ```
   total + unmapped_events  =  Σ by_age_cluster + events_without_client
   ```

 — beide Seiten sind „alle sichtbaren Ereignisse im Zeitraum". Sind **drei** Terme bekannt und der vierte verborgen, ist der vierte exakt bestimmt und die Suppression liefe leer. `_schliesse_identitaets_leak` degradiert dann einen weiteren Term, und zwar immer über die *Meldungen*: bevorzugt fällt die Zahl einer bezifferten Meldung; erst wenn keine mehr beziffert ist, wird eine **fehlende** Meldung durch einen Unterdrückungs-Hinweis ersetzt — denn `validate_completeness` meldet nur Lücken > 0, eine fehlende Meldung ist also deterministisch die Zahl 0 und damit selbst ein bekannter Term. Platzhalter- und Degradations-Text sind **wortgleich**; wäre der Wortlaut unterscheidbar, verriete er, ob die Lücke > 0 ist.

**Merke:** Ein `rows`-Backend mit *einer* Kennzahl je Zeile hat keine solche Identität — dort genügt das Muster aus `example.py`. Wer mehrere zusammenhängende Kennzahlen oder bezifferte Meldungen ausgibt, orientiert sich an `JugendamtBackend.prepare_export` und prüft die Beziehungen zwischen ihnen **als Ganzes**, nicht paarweise.

### Grenzverhalten, das man kennen muss

- **`prepare_export` ist nicht idempotent.** Der Hook erwartet das **rohe** `build_dataset`-Ergebnis (`JugendamtBackend` wandelt `by_category` z. B. von Tupeln in Dict-Zeilen). Zweimal aufrufen ist kein sinnvoller Aufruf; genau einmal transformieren.
- **Ein leerer Zeitraum sieht aus wie eine Suppression.** `0 < k`, also wird auch die ehrliche Null unterdrückt. Das ist Verhalten der geteilten Suppressions-Funktion und die sichere Richtung, aber es kann Empfänger verwirren.
- **Übersuppression an den Rändern.** Bei leeren Altersgruppen-Listen oder sehr kleinem *k* kann die Identitäts-Schließung mehr degradieren als nötig. Die Richtung ist sicher, der Aussagekraftverlust real.
- **Suppression gehört nie in `build_dataset`.** Sonst wären auch Snapshots beschnitten — und damit die Grundlage jeder späteren Auswertung und des unveränderten PDF-Pfads.

---

## 7. Export-Formate

[`export_dataset(backend, facility, date_from, date_to, format="csv")`](../src/core/reporting/export.py) ist der einzige öffentliche Einstiegspunkt. Er baut den Datensatz, ruft `prepare_export` und übergibt an genau einen Renderer. Ein unbekanntes Format wirft `ValueError`.

`SUPPORTED_FORMATS` (`frozenset({"csv", "json", "xml"})`) ist die Single Source of Truth für Aufrufer, die vorab selbst prüfen wollen — genau das tut `DatasetExportView`, statt den internen `ValueError` blind abzufangen (ein zu breiter `except ValueError` würde auch Renderer-Fehler als HTTP 400 maskieren).

Alle drei Formate laufen über `safe_download_response` (RFC-5987-Dateiname, `X-Content-Type-Options: nosniff`, `Cache-Control: no-store`). Der Dateiname folgt immer `f"{backend.slug}_{date_from}_{date_to}.{format}"`.

| Format | Content-Type | Streaming | Inhalt |
|---|---|---|---|
| `csv` | `text/csv; charset=utf-8` | **ja** (Generator) | Metadatenzeile, Header aus `FieldSpec.label`, dann `records` |
| `json` | `application/json` | nein | vollständiger Payload inkl. `summary` und `completeness` |
| `xml` | `application/xml; charset=utf-8` | nein | derselbe Payload als Elementbaum |

### CSV

```
# Bericht: <slug> | <facility> | <date_from>–<date_to> | <generated_at> [| k-Anonymität: Zellen < k unterdrückt]
Kategorie,Anzahl
Kontakte,42
```

- Die Metadatenzeile trägt den k-Anon-Hinweis nur, wenn `summary["k_anonymity_threshold"]` gesetzt ist. Ohne den Zusatz sähe der Empfänger leere Zellen ohne Erklärung — das CSV trägt (anders als JSON/XML/Panel) keine Completeness-Hinweise.
- **Jede** Zelle, auch Metadaten und Header, läuft durch `_sanitize_csv_cell` — den bestehenden Formula-Injection-Schutz aus `core.services.system.export` (OWASP CSV-Injection, #719). Bewusste Wiederverwendung statt Zweitimplementierung.
- Completeness-Meldungen erscheinen **nicht** im CSV: Es bleibt maschinenlesbar. Wer sie braucht, nimmt JSON, XML oder das Panel.
- Zellen werden über `record.get(spec.key)` gezogen — ein fehlender Schlüssel ist eine leere Zelle, kein Fehler.

### JSON

```json
{
  "backend": "example",
  "name": "Beispiel: Ereignisse je Dokumenttyp",
  "date_from": "2026-01-01",
  "date_to": "2026-01-31",
  "generated_at": "2026-02-01T09:00:00+01:00",
  "mode": "rows",
  "field_specs": [{"key": "document_type", "label": "Dokumenttyp"}],
  "summary": {"total": 42, "k_anonymity_threshold": 5},
  "records": [{"document_type": "Kontakt", "category": "Kontakt", "system_type": "contact", "count": 42}],
  "completeness": [{"code": "…", "message": "…", "severity": "warning"}]
}
```

Serialisiert mit `DjangoJSONEncoder`, `ensure_ascii=False`, `indent=2`. `completeness` führt **nur** `code`/`message`/`severity` — `count` bleibt intern.

### XML

```xml
<dataset backend="example" date_from="2026-01-01" date_to="2026-01-31" generated_at="…" mode="rows">
  <completeness><issue code="…" severity="warning">…</issue></completeness>
  <summary><total>42</total></summary>
  <records><record><document_type>Kontakt</document_type><count>42</count></record></records>
</dataset>
```

Erzeugt mit der stdlib (`xml.etree.ElementTree`), **nur generierend**: Es wird nie Fremd-XML geparst, daher greift die XXE-/Billion-Laughs-Angriffsfläche von `ElementTree` hier nicht und `defusedxml` (das ausschließlich Parsing härtet) wäre eine wirkungslose Abhängigkeit.

`summary` wird rekursiv abgebildet: Dict → ein Kindelement je Schlüssel, Liste → `<item>`-Kinder, Skalar → Text. Schlüssel, die keine gültigen XML-Elementnamen sind (typisch: Monatsschlüssel wie `"2026-01"`), werden als `<entry key="2026-01">` gerendert.

---

## 8. Completeness — was der Bericht *nicht* zeigt

`validate_completeness` beziffert Lücken, die der Datensatz selbst nicht sichtbar machen kann. Ohne diese Meldungen wäre eine zu niedrige Zahl plausibel „vollständig".

Beispiele aus dem Sachbericht:

| `code` | `severity` | Meldet |
|---|---|---|
| `unmapped_system_types` | `warning` | Ereignisse, deren Dokumentationstyp keiner Berichtskategorie zugeordnet ist (inkl. `system_type` NULL/leer) — sie fehlen im Bericht vollständig. |
| `events_without_client` | `info` | Ereignisse ohne Personenbezug — sie zählen in den Kategorien mit, fehlen aber in Altersgruppen und Personenzahl. |

**Semantik der Severity:** `warning` = im Bericht fehlende Daten (Zahlen sind zu niedrig). `info` = enthaltene, aber ungleich verteilte Daten (Teilauswertungen weichen ab). Das Panel rendert `warning` als gelbes „Hinweis"-Badge, `info` grau.

**Konventionen:**

- Nur Lücken **> 0** melden. Eine fehlende Meldung heißt „diese Lücke existiert nicht" — die Suppressions-Logik verlässt sich darauf.
- Eine bezifferte Meldung trägt ihre Zahl **zusätzlich** in `count`, sonst kann `prepare_export` sie nicht transformieren und muss sie im Zweifel komplett unterdrücken.
- Alle Kennzahlen einer Prüfung möglichst in **einer** Aggregation ziehen. `build_dataset` ruft `validate_completeness` mit, und der hybride Snapshot-Pfad ruft `build_dataset` je Monatssegment einmal auf — Einzel-`count()`s vervielfachen sich dort.
- Für die Export-Fassung eine **ziffernarme** Formulierung vorsehen (ohne Bezugsgröße, ohne Prozentanteil) sowie einen **ziffernfreien** Ersatztext für den Suppressionsfall. Fehlt beides, fällt die Zahl im Export vorsichtshalber ganz weg.

---

## 9. Grenzen

- **Kein generischer Monats-Merge** für snapshot-fähige Aggregat-Backends (siehe [§ 5](#5-snapshot-integration)).
- **JSON und XML werden nicht gestreamt.** Aggregat-Payloads sind klein; ein `rows`-Backend mit Massendaten (zehntausende Records) würde beide Renderer vollständig im Speicher aufbauen. Das ist die dokumentierte Grenze der aktuellen Pipeline — ein solches Backend bräuchte Streaming-Renderer analog dem CSV-Pfad, oder es beschränkt seinen Zeitraum.
- **Keine externen Plugin-Pakete.** Discovery scannt ausschließlich `core/reporting/backends/`; `entry_points` von Drittpaketen sind nicht vorgesehen. Ein fremdes Paket mit Lesezugriff auf Sozialdaten wäre eine Vertrauensentscheidung eigener Güte (Signatur, Freigabe, Sandbox) — als spätere Erweiterung denkbar, dann mit eigenem Sicherheitskonzept.
- **Keine generische PDF-Pipeline.** Die Formate der Pipeline sind CSV, JSON und XML; der Sachbericht-PDF bleibt ein eigener, unveränderter Pfad.
- **`core/reporting` liegt außerhalb der mypy-strict-Zone und der mutmut-Pfade.** Der Code ist strict-kompatibel annotiert; die Aufnahme in die Gates ändert Build-Konfiguration und ist ein offener Maintainer-Entscheid.
- **Mapping v1 über `system_type`.** `DocumentType.slug` ist der dokumentierte Zweitschlüssel und Folgearbeit ([ADR-038](adr/038-reporting-plugin-backends.md)); `DocumentType.category` wird fürs Reporting nie verwendet.

---

## 10. Test-Checkliste für neue Backends

Testdateien folgen dem Muster `src/tests/test_reporting_<slug>.py`. Die bestehenden Suiten (`test_reporting_base.py`, `test_reporting_registry.py`, `test_reporting_export.py`, `test_reporting_jugendamt.py`, `test_reporting_example.py`, `test_reporting_views.py`) sind Vorlagen.

**Registry-Isolation (Pflicht, sonst ordnungsabhängige Fehlschläge).** Ad-hoc registrierte Test-Backends dürfen nicht in Folgetests desselben Workers leaken. Direkt am Modul patchen, kein Proxy:

```python
@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    registry._discover()                                              # echte Discovery EINMAL vorher
    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))
    monkeypatch.setattr(registry, "_discovered", True)
```

Die echte Discovery muss **vor** dem Patchen laufen: `@register_backend` ist ein Import-Seiteneffekt und feuert je Modul nur beim *ersten* Import. Liefe die Discovery erstmals gegen ein Test-Dict, landeten die echten Backends nur dort, wären nach dem Teardown weg — und da die Module dann in `sys.modules` stecken, liefe der Decorator nie wieder: Die reale Registry bliebe für den Rest des Prozesses leer.

Wer die Erwartungswerte unabhängig von der Menge real registrierter Backends halten will, isoliert mit einem **leeren** Dict (`{}`) statt einer Kopie.

**Checkliste:**

| Prüfung | Warum |
|---|---|
| `[b.slug for b in get_backends()]` enthält den neuen Slug | Discovery greift ohne Zusatzeintrag |
| `build_dataset` gegen Fixture-Daten: `records`, `summary`, `mode` | Fachliche Korrektheit |
| Jeder `record`-Schlüssel entspricht einem `FieldSpec.key` | Sonst fehlen CSV-Spalten still |
| Leerer Zeitraum → leere `records`, kein Absturz | Häufigster Randfall |
| Fremde Einrichtung → keine Zeilen | Facility-Filter im Queryset |
| Soft-gelöschte Ereignisse zählen nicht mit | `is_deleted`-Filter |
| **Suppression:** Zelle `< k` → `None`; Randsumme fällt gemäß Backend-Regel | K-Anon-Invariante |
| **Sekundär-Suppression:** genau eine unterdrückte Zelle → zweite fällt | A6.1-Rückrechnung |
| **Kein internes Flag im Export** (`suppressed`, `count`) | Projektion auf `FieldSpec`-Keys greift |
| `export_dataset` in allen drei Formaten: 200, Content-Type, Dateiname | Format-Dispatch |
| CSV-Injection: `=SUM(...)`-Zelle wird präfixiert | Formula-Injection-Schutz |
| XML wohlgeformt (`ET.fromstring` auf eigenem Output) | Ungültige Elementnamen fallen auf |
| Panel-View: 200, Backend-Name, Vorschauzeilen; `?backend=nope` → 404 | Generische Endpunkte |
| **Panel zeigt suppressierte Werte** (nicht die rohen) | Panel/Export-Parität — der eigentliche Leak-Kandidat |
| Bei `snapshot_enabled = True`: `backend_data[slug]` gefüllt **und aggregatrein** | Snapshot-Vertrag |

Vor dem Commit: `make verify-fast` (Lint, Checks, Typecheck, Unit-Tests). Bei UI-relevanten Änderungen zusätzlich der E2E-Lauf gemäß [E2E-Runbook](e2e-runbook.md).

---

## 11. Verwandte Dokumente

- [ADR-038](adr/038-reporting-plugin-backends.md) — Entwurfsentscheidungen, Alternativen, Konsequenzen
- [ADR-023](adr/023-k-anonymization-statistik.md) — K-Anonymisierung für externe Statistik
- [ADR-034](adr/034-config-ui-category-system-type.md) — `category` vs. `system_type`
- [ADR-037](adr/037-konfigurationspakete-dynamische-felder.md) — Konfigurationspakete, `DocumentType.slug`
- [user-guide.md § 7](user-guide.md#7-statistik-und-export) — Bedienung der Berichte-Sektion aus Anwendersicht
- [packages.md](packages.md) — Konfigurationspakete (Quelle der Dokumentationstypen, die Berichte auswerten)
