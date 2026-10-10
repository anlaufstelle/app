# Reporting-Plugins (Berichts-Backends)

Ein **Berichts-Backend** kapselt die Fachlogik eines Berichtsstandards — „Kontakte nach Kategorie", „Ereignisse je Dokumentationstyp", ein regionales Kostenträger-Formular — hinter einer festen Schnittstelle. Panel-Vorschau, CSV-/JSON-/XML-Export und die Snapshot-Speicherung sprechen ausschließlich diese Schnittstelle an und wissen nichts über den einzelnen Bericht.

Praktische Folge: **ein neuer Bericht ist eine Datei.** Ein Modul unter [`src/core/reporting/backends/`](../src/core/reporting/backends/) mit einer `DatasetBackend`-Unterklasse und dem `@register_backend`-Decorator erscheint automatisch als Karte auf dem Reiter „Datensatz-Berichte“ der Statistik-Seite und in allen drei Exportformaten. Keine Migration, kein URL-Pattern, kein View, kein Eintrag in der AuthZ-Matrix.

- **Warum das System so aussieht, wie es aussieht:** [ADR-038](adr/038-reporting-plugin-backends.md).
- **Warum Kleinstfallzahlen unterdrückt werden:** [ADR-023](adr/023-k-anonymization-statistik.md) und #1278.
- **Warum das Mapping über `report_key` läuft und nie über `category`:** [ADR-042](adr/042-report-key-berichtszuordnung.md) (superseded [ADR-038 § 4](adr/038-reporting-plugin-backends.md)), [ADR-034](adr/034-config-ui-category-system-type.md). `DocumentType.category` gibt es nicht ([ADR-040](adr/040-documenttype-category-entfernt.md)), und `system_type` trägt nur `ban` und `crisis` (#1850) — die Regel ist damit strukturell besiegelt.

> **Die wichtigste Regel vorweg.** Alles, was ein Backend über `build_dataset()` liefert, ist **roh**. Alles, was das Haus verlässt (Download **und** Panel-Vorschau), läuft vorher durch `prepare_export()`. Wer Zahlen produziert, die extern zirkulieren, muss diesen Hook implementieren — siehe [§ 6](#6-k-anonymität--der-prepare_export-hook).

### Zahlenfeld-Summenbericht

Das Backend **„Summe von Zahlenfeldern“** (`quantity-sum`) wertet Ereignisse je Dokumentationstyp über den gewählten Zeitraum aus. Es bindet ausschließlich an den Berichtsschlüssel `quantity_units` („Stückzahl“), liest Zahlenfelder und bildet eine Zeitraum-Summe ohne Monatsaufteilung oder Monats-Snapshots. Die Tabelle zeigt je Typ **Ereignisse**, **beitragende Ereignisse** (mit verwertbarer Stückzahl) und **Stückzahl im Zeitraum**. Fehlende, ungültige oder nicht lesbare Werte tragen nicht zur Summe bei; ein Lückenhinweis meldet, dass solche Stückzahlen oder Summen unberücksichtigt bleiben.

Die K-Anonymitäts-Schwelle *k* gilt für Ereignis- und Beitragszahlen je Typ. Eine Summe wird nur zusammen mit mindestens *k* beitragenden Ereignissen gezeigt. Auch Gesamtsummen und Randsummen werden unterdrückt, sobald sie eine verborgene Zeile oder eine kleine Fehlmenge rückrechenbar machen könnten. Vorschau, CSV und PDF verwenden dieselben freigegebenen Werte.

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
    slug: str = ""  # von @register_backend gesetzt/validiert
    name: str = ""  # Anzeigename auf der Berichtskarte (gettext_lazy-fähig)
    description: str = ""  # ein Satz „was dieser Bericht zählt“ (gettext_lazy-fähig)
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
| `slug` | — | Wird vom Decorator gesetzt. **Nicht** selbst zuweisen. Format `^[a-z0-9]+(?:[-_][a-z0-9]+)*$` (Kleinbuchstaben/Ziffern, einzelne `-`/`_` als Trennzeichen, nicht am Rand). Er steckt in Query-Parametern, Dateinamen und `backend_data`-Schlüsseln und ist damit faktisch unveränderlich — ein nachträglicher Wechsel braucht eine Datenmigration (Muster: [`0123`](../src/core/migrations/0123_rename_backend_data_key_sample_report_1.py)). |
| `name` | **ja** | Sichtbarer Name auf der Berichtskarte und im JSON-Payload. Als `gettext_lazy` deklarieren; die Export-Pipeline castet an der Payload-Grenze selbst nach `str`. |
| `snapshot_enabled` | nein | Default `False`. `True` nur bei **aggregatreinem** `summary` und mit implementiertem `summarize_events` — siehe [§ 5](#5-snapshot-integration). |
| `is_available(facility)` | nein | Default `True`. Hook für einrichtungsabhängige Berichte. `False` — wie jeder andere falsy Rückgabewert, etwa das `None` eines nullable `BooleanField` — blendet das Backend aus der Berichtsliste aus **und** lässt Panel/Export mit HTTP 404 antworten. Listen-Filter und Zugriffs-Guard prüfen dieselbe Truthiness. |
| `description` | nein | Ein Satz „was dieser Bericht zählt“, direkt unter dem Namen auf der Karte (Refs #1804). Als `gettext_lazy` deklarieren. Leer = die Karte lässt die Zeile weg — dann bleibt nur der Name, und die Auswahl wird wieder zum Ratespiel. |
| `get_field_specs()` | **ja** | Spaltendefinition der tabellarischen Projektion. Reihenfolge = Spaltenreihenfolge im CSV. |
| `build_dataset(facility, date_from, date_to)` | **ja** | Baut den Datensatz für den **inklusiven** Zeitraum `[date_from, date_to]`. Ergebnis ist **roh** (unsuppressiert). |
| `validate_completeness(facility, date_from, date_to)` | nein | Default `[]`. Datenqualitäts-Checks — siehe [§ 8](#8-completeness--was-der-bericht-nicht-zeigt). |
| `prepare_export(facility, result)` | **faktisch ja** | Der Default ist die Identität, reicht aber nicht mehr aus: Transformation vor Export **und** Panel-Anzeige. Ohne ausgewiesene Schwelle (`summary["k_anonymity_threshold"]`) scheitern Download **und** Panel fail-closed (#2069) — siehe [§ 6](#6-k-anonymität--der-prepare_export-hook). |

> **Backends sind prozessweite Singletons — kein Instanz-Zustand.**
>
> `@register_backend` legt genau **eine** Instanz je Slug an, und die lebt so lange wie der Prozess. Alle Requests und **alle Einrichtungen** teilen sie sich. Ein auf `self` gecachtes Zwischenergebnis (naheliegend, weil Panel und Download denselben Datensatz bauen) läge im nächsten Request einer anderen Einrichtung noch dort — in einer mandantenfähigen Anwendung ein einrichtungsübergreifender Datenleak. Alles, was ein Aufruf braucht, kommt als Argument herein: `facility` und Zeitraum.

`validate_completeness` bekommt bewusst **Facility + Zeitraum statt des fertigen `DatasetResult`**: Die aussagekräftigen Checks betreffen genau das, was *nicht* im Datensatz landet (ausgefilterte Dokumentationstypen, Ereignisse ohne Personenbezug). Ein fertiges Ergebnis kann sie nicht mehr sehen. `build_dataset()` ruft die Methode üblicherweise selbst und bettet das Ergebnis in `DatasetResult.completeness` ein.

### 2.2 `DatasetResult`

```python
@dataclass(frozen=True)
class DatasetResult:
    mode: Literal["rows", "aggregate"]
    records: list[dict]  # tabellarische Projektion; Keys == FieldSpec.key
    summary: dict  # vollständiges Aggregat; Snapshot-Payload
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
    key: str  # stabiler Spalten-/Record-Schlüssel, ^[a-z0-9_]+$
    label: str  # Anzeigename
```

`key` ist der Dict-Schlüssel in `records` **und** der XML-Elementname eines Feldes. Schlüssel, die kein gültiger XML-Elementname sind, werden vom XML-Renderer als `<entry key="…">` ausgegeben statt als eigenes Element — das Format bleibt wohlgeformt, wird aber unhandlicher. Halte dich an `^[a-z0-9_]+$` **und beginne nicht mit einer Ziffer**, dann tritt der Fall nicht ein: Der Renderer prüft gegen `^[A-Za-z_][A-Za-z0-9_]*$` ([`export.py`](../src/core/reporting/export.py)), ein Schlüssel wie `2026_total` fiele also trotz gültigem Slug-Format in den `entry`-Fallback.

`label` ist die CSV-Kopfzeile, die Tabellenüberschrift im Panel und das `label` in `field_specs` des JSON-Exports. **Deklariere es als `gettext_lazy`** (#1744) — genau wie `name`. Deutscher Klartext stünde in der englischen Oberfläche mitten in übersetztem Text; beide ausgelieferten Backends sind entsprechend umgestellt. Die Renderer casten selbst nach `str` (CSV über `_sanitize_csv_cell`, JSON explizit an der Payload-Grenze, XML über `_xml_safe`), du musst also nirgends selbst casten.

**Übersetzbare Strings im Backend — die drei Regeln:**

1. **Labels und Meldungen:** `gettext_lazy`. Bezifferte Meldungen baust du mit [`django.utils.text.format_lazy`](https://docs.djangoproject.com/en/stable/ref/utils/#django.utils.text.format_lazy) und **benannten** Platzhaltern, nicht mit f-Strings und nicht mit `.format()` auf dem Proxy — `.format()` wertet den Proxy **eager** aus und schreibt die Sprache fest, bevor gerendert wird.
2. **Was nicht übersetzt wird: Daten.** Werte, die in `summary`/`records` landen und (bei `snapshot_enabled`) persistiert oder monatsübergreifend gemerged werden, bleiben sprachunabhängig. Im Sachbericht sind das die Kategoriennamen aus `CATEGORY_MAP`: Ein locale-abhängiger Schlüssel machte den gespeicherten Snapshot-Bestand von der Sprache des schreibenden Prozesses abhängig — zwei Monate, zwei Kategoriennamen, kein Merge. Dieselbe Regel gilt für maschinenlesbare Codes (`CompletenessIssue.code`, `data_source`).
3. **K-Anon-Meldungen bleiben ununterscheidbar — auch übersetzt.** Platzhalter- und Degradations-Fassung eines `code` müssen wortgleich bleiben (siehe [§ 6](#6-k-anonymität--der-prepare_export-hook)). Das ist strukturell gesichert, weil beide Pfade dieselbe Konstante benutzen; eine Übersetzung darf ihr aber keine Ziffer und keine Bezugsgröße hinzufügen.

### 2.4 `CompletenessIssue`

```python
@dataclass(frozen=True)
class CompletenessIssue:
    code: str  # maschinenlesbar, z. B. "unmapped_system_types"
    message: str  # menschenlesbar (deutsch)
    severity: Literal["info", "warning"] = "warning"
    count: int | None = None  # Rohwert hinter ``message``; nie serialisiert
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
import dataclasses

from django.db.models import Count
from django.utils.translation import gettext_lazy as _

from core.models import Event
from core.reporting.base import DatasetBackend, DatasetResult, FieldSpec
from core.reporting.registry import register_backend
from core.services.dashboard.external_report import ORDER_INPUT, _Breakdown, suppress_single_margin


@register_backend("example")
class ExampleBackend(DatasetBackend):
    """Ereignisse je Dokumentationstyp im Zeitraum — kleinstmoegliches Referenz-Backend."""

    name = _("Beispiel: Ereignisse je Dokumentationstyp")
    description = _("Zählt die Ereignisse des Zeitraums je Dokumentationstyp.")
    snapshot_enabled = False
```

Datei nach `src/core/reporting/backends/<name>.py`. Der Decorator setzt `slug`, prüft das Format und weist doppelte Slugs mit `ImproperlyConfigured` ab.

Das ist der **vollständige** Import-Kopf von `example.py` — die Auszüge in Schritt 3 und 4 setzen ihn voraus und wiederholen ihn nicht.

### Schritt 2 — Spalten deklarieren

```python
    def get_field_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec("document_type", _("Dokumentationstyp")),
            FieldSpec("system_type", _("System-Typ")),
            FieldSpec("count", _("Anzahl")),
        ]
```

Die Reihenfolge ist die Spaltenreihenfolge im CSV und im Panel. Die Labels sind `gettext_lazy` — siehe die drei i18n-Regeln in [§ 2.3](#23-fieldspec).

### Schritt 3 — Datensatz bauen

Imports wie in Schritt 1 (`Event`, `Count`):

```python
def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
    rows = (
        Event.objects.for_facility(facility)
        .filter(
            is_deleted=False,
            occurred_at__date__gte=date_from,
            occurred_at__date__lte=date_to,
        )
        .values("document_type_id", "document_type__name", "document_type__system_type")
        .annotate(count=Count("id"))
        .order_by("document_type__name")
    )

    records = [
        {
            "document_type": row["document_type__name"],
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

- **Immer nach `facility` filtern** (`for_facility`). Backends laufen im Request-Kontext mit RLS, aber auch im Snapshot-Command; verlasse dich nicht auf die Datenbankschicht allein.
- **`is_deleted=False`**, sonst zählen soft-gelöschte Ereignisse mit.
- **Aggregieren statt iterieren.** `.values(...).annotate(Count(...))` liefert eine Zeile je Gruppe in **einer** Query; eine Python-Schleife über Ereignisse wird bei Jahreszeiträumen unbrauchbar langsam.

#### Wenn dein Bericht Dokumentationstypen auf Kategorien abbildet

Die Bindung wird **deklariert, nicht ausprogrammiert** (#1824): eine `TypBindung` je Bericht, aus der Auflösung, SQL-Filter, Verfügbarkeit und Schlüssel-Vokabular abgeleitet werden. Es gilt (#1849): **ein Schlüssel je Ebene** — `DocumentType.report_key` am Typ, `FieldTemplate.report_key` am Feld ([ADR-042](adr/042-report-key-berichtszuordnung.md)). Einen Slug als Zweitschlüssel gibt es nicht (#1741); `category` ist kein Schlüssel und existiert nicht.

```python
from core.reporting.base import Anforderung, BindungsEintrag, FeldBindung, TypBindung

TYP_BINDUNG = TypBindung(
    (
        BindungsEintrag("contact", _("Kontakt"), ziel="Kontakte"),
        BindungsEintrag("counseling", _("Beratungsgespräch"), ziel="Beratung"),
    )
)
# Optional, wenn dein Bericht Feldwerte liest. ``slugs`` ist hier die
# Herkunftsangabe (welcher Paket-Slug diesen Schlüssel per Backfill bekam) und
# der Rückweg für "erscheint in …" — gebunden wird über ``bindet``.
FELD_DAUER = FeldBindung((BindungsEintrag("dauer_minuten", _("Minuten"), slugs=("dauer",)),), bindet="report_key")


@register_backend("mein-bericht")
class MeinBackend(DatasetBackend):
    typ_bindung = TYP_BINDUNG
    feld_bindungen = (FELD_DAUER,)
    # Nur setzen, wenn der Bericht ohne passende Konfiguration sinnlos wäre —
    # `is_available` wird daraus abgeleitet (leer = immer wählbar).
    verfuegbarkeit = (Anforderung(TYP_BINDUNG, (FELD_DAUER,)),)
```

Was du daraus bekommst — und **nie** danebenschreibst:

| Ableitung | wofür |
|---|---|
| `TYP_BINDUNG.resolve(report_key)` / `.resolve_row(row, "document_type__")` | die Auflösung in `build_dataset` |
| `TYP_BINDUNG.values_fields("document_type__")` | die Schlüsselfelder deiner `.values()`-Gruppierung |
| `TYP_BINDUNG.matching_q(prefix)` / `.unmapped_q(prefix)` | der SQL-Filter und sein exaktes Komplement |
| `TYP_BINDUNG.ziele()` | deine Ausgabeordnung (wertunabhängig, K5) |
| `verfuegbarkeit` | `is_available()` — kein handgebauter Query-Chain mehr |

**Konsumenten-API außerhalb des Backends** (E2, #1848): `resolve()` liefert das `ziel` — für „welcher Berichtsschlüssel ist das?" (Konfigurations-UI, Inventar-Command) reicht das nicht, weil mehrere Schlüssel auf dasselbe `ziel` fallen können (`crisis`/`counseling` → „Beratung"). Dafür gibt es den eigenen Rückweg auf den Schlüssel:

| Ableitung | wofür |
|---|---|
| `TYP_BINDUNG.resolve_key(report_key)` / `.resolve_key_row(row, prefix)` | derselbe Treffer wie `resolve()`, aber als **Schlüssel** statt `ziel` — Grundlage von `describe_type_bindings()` in [`registry.py`](../src/core/reporting/registry.py) und des `inventory_report_bindings`-Commands |
| `FELD_BINDUNG.key_for_template(feldvorlage)` | Feldvorlage → Feld-Berichtsschlüssel, dieselbe Rolle auf Feld-Ebene (`describe_field_bindings()`); liest die Erststufe der Deklaration |
| `FELD_BINDUNG.leseschluessel(facility)` | der `data_json`-**Lesepfad** je Dokumentationstyp — gebunden wird über den Schlüssel, gelesen unter dem Slug der so gebundenen Vorlage |

Das Wartungskommando `inventory_report_bindings` verwendet dieselbe Auskunft wie „Erscheint in“: Verfügbare Berichte bleiben bei passenden Typ-Schlüsseln auch für deaktivierte oder lokal unvollständige Typen gelistet, sofern ihre Ereignisse mitgezählt werden. Die Feldvorlagen-Sektion führt nicht zugeordnete, inaktive oder nur an unvollständigen Alternativen beteiligte Vorlagen unter `unbound`; eine andere vollständige Alternative allein macht die Vorlage nicht beitragend. Das Inventar beschreibt Zuordnungen, nicht Ereignisanzahlen oder Summen.

Konsumenten, die „erscheint dieser Typ/diese Feldvorlage in einem Bericht?" beantworten wollen, bauen auf diesen beiden — **nicht** auf einer eigenen Kopie von `resolve()`.

- **`key` vs. `ziel` vs. `label` — drei verschiedene Dinge.** `key` ist der Berichtsschlüssel (der Wert in `report_key`; bei einem Verhaltens-Bericht der `system_type`-Wert). `ziel` ist, was `resolve()` liefert: **backend-eigen** und bei `snapshot_enabled`-Berichten ein **persistierter** Schlüssel — deutscher Klartext, niemals `gettext_lazy` (sonst hängt der gespeicherte Bestand an der Sprache des schreibenden Prozesses, siehe [§ 2](#2-api-referenz)). Ohne `ziel` löst der Schlüssel auf sich selbst auf. `label` ist der **übersetzte Name des Schlüssels** und einrichtungsweit kanonisch: deklarieren zwei Berichte denselben Schlüssel mit verschiedenen Labels, wirft die Registry `ImproperlyConfigured`.
- **Ein Schlüssel, kein zweiter Weg** (#1849, Entscheid E-E). Es entscheidet ausschließlich `report_key`: gesetzt und deklariert ⇒ Treffer, alles andere ⇒ kein Treffer. Der frühere Slug-Zweitschlüssel war unsichtbar (man musste ihn aus dem Slug erschließen) und hat in #1785 ein Disclosure-Leck erzeugt — ein Hausverbots-Typ mit dem Slug `kontakt` zog seine Ereignisse in den Bericht. Mit ihm ist auch die Reparaturregel „ein gesetzter Erstschlüssel gewinnt inklusive seiner Ausschlüsse" entfallen: es gibt nichts mehr zu sperren.
- **`bindet="system_type"` nur für Verhaltens-Berichte.** `ban-overview` berichtet über Hausverbote und bleibt dauerhaft an der Systemkennung — die Abweichung gehört in die Deklaration, nicht in einen Kommentar.
- **`category` nie.** Reine UI-Gruppierung, frei editierbar — eine Umgruppierung in der Config-UI dürfte niemals Berichtszahlen verschieben ([ADR-034](adr/034-config-ui-category-system-type.md)); das Feld existiert nicht ([ADR-040](adr/040-documenttype-category-entfernt.md)).
- **Die Deklaration konservativ halten.** Nimm nur auf, was eindeutig in eine Kategorie gehört. Der Schlüssel `excluded` ist reserviert („gehört ausdrücklich in keinen Bericht") und darf nicht deklariert werden — die Registry wirft sonst `ImproperlyConfigured`.
- **Je (Typ, Feld-Schlüssel) höchstens eine Feldvorlage** (Entscheid E-J). Auf Typ-Ebene ist Mehrfachbelegung der Zweck der Übung; auf Feld-Ebene liest das Backend **Werte je Ereignis**, zwei Vorlagen mit demselben Schlüssel hießen doppelte Minuten. Validiert wird an Formular, Service und Paket-Loader; entsteht die Doppelbindung trotzdem, zählt das Backend **keines** der Felder und meldet `duplicate_field_binding` (`doppelbindungs_issues()` in [`base.py`](../src/core/reporting/base.py)).
- **Der Unmapped-Filter muss dieselbe Antwort geben — deshalb `unmapped_q()`.** `validate_completeness` zählt „ohne Berichtszuordnung" auf der Datenbank; das Prädikat ist die exakte Negation von `matching_q()`. Schreib es **nicht** von Hand: zwei Wahrheiten hier heißen, dass der Hinweis Ereignisse als fehlend zählt, die im Bericht stehen — und genau in diesem Muster steckte das Disclosure-Leck aus #1785. Ein Guard (`src/tests/reporting/test_reporting_bindung.py`) verbietet handgebaute Schlüssel-Prädikate in `backends/` und prüft über das ganze Wertuniversum, dass Python- und SQL-Sicht übereinstimmen.
- **„Nicht zugeordnet" ist nicht „ausgeschlossen".** Der Vollständigkeitshinweis meldet nur Typen, die **gar keine** Aussage treffen (`report_key` leer/`NULL`). Ein gesetzter Schlüssel ist eine vollständige Aussage — auch der Sentinel `excluded` (#1819). Ein gesetzter, aber registry-weit **unbekannter** Schlüssel bekommt den eigenen Code `unknown_report_keys` (Entscheid E-H): „ich habe zugeordnet, es passiert nur nichts" verlangt eine andere Reaktion als „ich habe nichts gesagt".
- **`is_active` gehört in die `Anforderung`, nie in den Datenfilter.** „Bietet die Einrichtung das an?" ist die Konfigurationsfrage; „was ist damals passiert?" ist der Datenfilter. Ein heute umgelegter Schalter darf keinen vergangenen Zeitraum umschreiben.
- **Zwei Feldvorlagen = zwei `FeldBindung`en.** Über eine Mehrfach-Relation erzeugt Django je `filter()`-Aufruf einen eigenen JOIN; nur getrennte Bindungen prüfen „beide Felder am **demselben** Typ". Eine Bindung mit zwei Slugs wäre ein `IN` und damit ein ODER.
- **Kein Backfill für Snapshots.** Eine Erweiterung der Deklaration wirkt ab dem nächsten `build_dataset`; gespeicherte `backend_data` bleiben auf ihrem Stand (siehe [§ 5](#5-snapshot-integration)).

### Schritt 4 — K-Anonymität sicherstellen

Sobald der Bericht heruntergeladen werden kann, ist `prepare_export` **Pflichtlektüre**, nicht Kür. Details in [§ 6](#6-k-anonymität--der-prepare_export-hook); der Minimalfall aus `example.py` (Imports wie in Schritt 1, `dataclasses` eingeschlossen):

```python
# Deklaration für den zentralen Residual-Guard: ``total`` ist die Summe
# aller Zeilen-``count``s, ``records`` also die exakte Partition dieser
# Randsumme. ``ORDER_INPUT``: die Query sortiert nach ``document_type__name``
# — die Zeilenposition ist bereits wertunabhängig (K5).
_EXPORT_BREAKDOWN = _Breakdown("records", "total", ORDER_INPUT)


def prepare_export(self, facility, result: DatasetResult) -> DatasetResult:
    records, summary = suppress_single_margin(
        facility, result, breakdown=self._EXPORT_BREAKDOWN, field_specs=self.get_field_specs()
    )
    return dataclasses.replace(result, records=records, summary=summary)
```

`suppress_single_margin` kapselt für Backends mit **genau einer** Randsumme (`summary["total"]`) und **einer** Aufschlüsselung, die sie exakt partitioniert, alle Schritte der K-Anonymität: Schwelle ermitteln, die Randsumme gegen R14 prüfen (eine Randsumme < k ist selbst eine Kleinstfallzahl), die Zeilen über `_suppress_small` unterdrücken, `apply_residual_guard` als **letzten** Schritt ausführen und auf die deklarierten `FieldSpec`-Schlüssel zurückprojizieren. Die Funktion setzt `summary["k_anonymity_threshold"]`; `completeness` setzt der Aufrufer selbst. Backends mit mehreren zusammenhängenden Kennzahlen oder ganz ohne Randsumme bauen die Suppression selbst, nach dem Muster von `SampleReport1Backend.prepare_export` — eine zweite Basisklasse dafür gibt es bewusst nicht.

Was dahinter passiert, ist für das Verständnis wichtig:

- `_suppress_small` bekommt `margin` und `order` als **Keyword-only-Parameter ohne Default** — ein Aufruf ohne sie ist ein `TypeError` (fail-closed): `margin` sagt, ob es eine publizierte Randsumme gibt, an der das Machbarkeits-Audit rechnen kann, `order` benennt die kanonische Ausgabeordnung unterdrückter Zeilen.
- `apply_residual_guard` ist der **letzte** Schritt und für jedes `prepare_export` mit publizierter Randsumme Pflicht (der Architektur-Guard `test_architecture_guards_services.py` erzwingt es): er führt die Empfänger-Inferenz aus und lässt notfalls die Randsumme oder die ganze Aufschlüsselung fallen. Eine eigene Regel „fällt eine Zelle, fällt auch `total`" wäre eine zweite, pauschalere Suppressionslogik neben der zentralen, also genau das, was der Architektur-Guard verbietet (#1770).
- Die Rückprojektion auf die deklarierten `FieldSpec`-Schlüssel ist kein Schönheitsschritt: `_suppress_small` hängt jeder Zeile ein internes `suppressed`-Flag an, das sonst ungefiltert in CSV, JSON und XML landen würde.

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

### Bestand: die mitgelieferten Beispiel-Backends

Alle Module unter [`src/core/reporting/backends/`](../src/core/reporting/backends/) sind **Vorlagen**, keine verbindlichen Berichtsstandards. Jedes hat einen anderen Zuschnitt — wer ein eigenes Backend schreibt, sucht sich zuerst den Fall, der dem eigenen am nächsten kommt, und kopiert **dieses** Modul. Alle sieben aus #1754 tragen `snapshot_enabled = False` und binden ihre `is_available` an die Seed-/Paket-Konfiguration.

| Slug | Referenz-Charakter |
|---|---|
| [`example`](../src/core/reporting/backends/example.py) | Der Minimalfall: eine Kennzahl je Zeile plus Randsumme (`mode="rows"`). Einstiegspunkt für alles Weitere. |
| [`sample-report-1`](../src/core/reporting/backends/sample_report_1.py) | Der Vollfall: mehrere zusammenhängende Kennzahlen, Vollständigkeitsblock, `snapshot_enabled = True`. |
| [`monthly-trend`](../src/core/reporting/backends/monthly_trend.py) | **Zeitreihe + importierte Bindung.** Zeigt, dass die `TypBindung` des Sachberichts importiert und nie nachgebaut wird — und dokumentiert die Verfeinerungs-Beziehung zum Sachbericht (K4a, siehe [§ 6](#6-k-anonymität--der-prepare_export-hook)). |
| [`client-metrics`](../src/core/reporting/backends/client_metrics.py) | **Exakte Personen-Partition + Randsummen-Doktrin.** Reine Personenzählungen (DISTINCT über `Event.client`), eine Aufschlüsselung, die ihre Randsumme exakt partitioniert — die schärfste Klasse im K-Anon-Modell. |
| [`case-metrics`](../src/core/reporting/backends/case_metrics.py) | **Unabhängige Skalare ohne Randsumme.** Der Gegenpol: keine Zahl partitioniert eine andere, jede steht für sich an der k-Schwelle. Zeigt, wie wenig Maschinerie dieser häufige Zuschnitt braucht. |
| [`ban-overview`](../src/core/reporting/backends/ban_overview.py) | **Hochsensibler Sonderfall.** Hausverbote sind bewusst kein Teil des Sachberichts; dieser Bericht ist ein interner Auswertungspfad per Produktentscheid. Zeilen werden datenunabhängig emittiert — schon ihre Existenz wäre sonst eine Aussage. |
| [`needle-exchange-balance`](../src/core/reporting/backends/needle_exchange_balance.py) | **Feldwert-Summen.** Das erste Backend, das `Event.data_json` liest statt Ereignisse zu zählen. Grundmuster für jede Auswertung über Feldwerten (Slug-Adressierung, `safe_decrypt`, Summen-Regel). |
| [`counseling-hours`](../src/core/reporting/backends/counseling_hours.py) | **Kaskade Zeile → Gesamt.** Feldwert-Summen mit Aufschlüsselung: je Kategorie eine Zeile aus Ereigniszahl und Minutensumme. Fällt eine Zeile, kaskadiert das in die Gesamtsumme. |
| [`service-distribution`](../src/core/reporting/backends/service_distribution.py) | **Mehrfachnennung.** Zwei Feldgruppen nebeneinander: ein `select` (exakte Partition) und ein `multi_select` (partitioniert **nicht**). Der Fall, an dem die Randsummen-Frage kippt — siehe [§ 6](#6-k-anonymität--der-prepare_export-hook). |

---

## 5. Snapshot-Integration

`create_or_update_snapshot(facility, year, month)` schreibt für jedes Backend mit `snapshot_enabled = True` das **rohe** `build_dataset(...).summary` in die Map `StatisticsSnapshot.backend_data` (Schlüssel = Slug). `prepare_export` läuft dabei bewusst **nicht** — der Snapshot ist der interne Rohbestand.

Die Schleife läuft über `get_backends()`, **nicht** über `get_available_backends(facility)`: Ein snapshot-fähiges Backend wird also auch dann persistiert, wenn sein `is_available(facility)` `False` liefert. `is_available` steuert ausschließlich die Sichtbarkeit in der Berichtsliste und die 404-Antwort von Panel und Export — es ist kein Schalter für die Datenhaltung. Wer ein Backend für einzelne Einrichtungen wirklich stilllegen will, darf sich darauf nicht verlassen.

> **`snapshot_enabled` ist ein Datenschutz-Vertrag, kein Feature-Schalter.**
>
> Das `summary` eines snapshot-fähigen Backends muss **aggregatrein** sein: keine Pseudonyme, keine Personenzeilen, keine Freitexte. Der Grund liegt außerhalb des Backends — `backend_data` ist im PII-Residue-Sweep (`src/tests/_residue_expectations.py`) **pauschal** als non-PII klassifiziert, und diese Klassifikation prüft den Inhalt nicht je Backend nach. Ein Backend, das die Zusage nicht halten kann, bleibt `snapshot_enabled = False`; sonst wandert personenbezogene Information an der Klassifikation vorbei in den Snapshot.
>
> `mode="rows"`-Backends sind daher im Regelfall **nicht** snapshot-fähig: Zeilendaten sind weder größen- noch datenschutzseitig für eine dauerhafte Ablage gedacht.

**`backend_data` ist die einzige Quelle.** Das Aggregat des Beispiel-Sachberichts liegt wie das jedes anderen Backends in `backend_data`; ein eigenes Modellfeld gibt es nicht (#1740). Schreib- und Lesepfad kennen nur `backend_data`.

**Merge-Grenze.** Der hybride Lesepfad setzt Zeiträume aus Monats-Snapshots zusammen und muss die Monatsaggregate dafür verschmelzen. Dieser Merge ist **sachberichtspezifisch** (`_merge_sample_report_1_stats`) und kennt genau dessen Struktur. Für ein zweites snapshot-fähiges Backend gibt es heute **keinen** generischen Merge: Ohne Merge-Semantik je Feld (summieren? Maximum? Mengenvereinigung?) ist er nicht definierbar. Wer ein zweites Aggregat-Backend snapshot-fähig machen will, plant den Merge als eigene Arbeit ein — bis dahin liest es über `build_dataset` live.

**Pflicht-Hook `summarize_events` für gelöschte Ereignisse.** Der Aufbewahrungslauf (`enforce_retention`) löscht Ereignisse und überträgt ihre Zählwerte in derselben Transaktion in das Monatsaggregat, damit die Zahlen vergangener Monate stabil bleiben. Jedes Backend mit `snapshot_enabled = True` muss dafür `summarize_events(self, events) -> dict` implementieren: Es liefert das `summary` über eine **explizite** Ereignismenge, in derselben Form wie `build_dataset(...).summary`. Zusätzlich braucht das Backend einen Eintrag in `_SUMMARY_MERGERS` in [`snapshot.py`](../src/core/services/dashboard/snapshot.py), der Monatssummen zusammenführt. Fehlt der Hook (die Basisklasse wirft `NotImplementedError`) oder wirft die Zusammenfassung, **scheitert jeder Übertrag**: Die Löschung dieses Ereignisses wird zurückgerollt, der Fehler wird protokolliert, und der nächste Lauf versucht es erneut — das Ereignis bleibt bis dahin bestehen, statt ungezählt zu verschwinden. Ein snapshot-fähiges Backend ohne diesen Hook blockiert damit die Löschfristen seiner Einrichtungen. Ein Plugin, das ein snapshot-fähiges Backend liefert, muss auch einen Merger registrieren. Fehlt er, kann der Übertrag scheitern (`KeyError` am fehlenden Eintrag), auch der erste in eine leere Monatszeile; dann wird die Löschung zurückgerollt, und der nächste Lauf versucht es erneut. Ein Backend ohne Snapshot (`snapshot_enabled = False`) ist nicht betroffen.

**Ein defektes Backend wird übersprungen — und laut markiert** (#2070). Der Snapshot-Lauf isoliert jeden Backend-Aufruf: Wirft ein Backend, wandert sein Slug mit dem Namen der Exception-Klasse in den reservierten Schlüssel `backend_data["_errors"]` (`{slug: "OperationalError"}`), der Fehler wird mit vollem Traceback geloggt (und damit ans Fehler-Tracking gemeldet), und die übrigen Backends samt der bereits berechneten Dashboard-Aggregate werden normal gespeichert. Der Schlüssel entsteht **nur** im Fehlerfall und kann mit keinem Slug kollidieren — `registry._SLUG_RE` verbietet den führenden Unterstrich. Kein Modellfeld, keine Migration: `backend_data` ist ein freies `JSONField`.

> **Warum der Job nicht fail-loud abbricht (#2070).** Risse ein einziges werfendes Backend den gesamten Lauf ab, wäre das Ziel, ein still unvollständiger Snapshot sei ein leises Datenloch, das erst Monate später auffällt, zwar erreicht, das Mittel aber zu grob: Der Abbruch träfe nicht nur das defekte Backend, sondern die bereits berechneten Aggregate dieser Einrichtung, **alle Folgemonate** (`ensure_snapshots_for_months`) und im Cron-Command **alle folgenden Einrichtungen** — inklusive des Last-Run-Markers, der dann gar nicht mehr geschrieben würde. Weil der Cron monatlich läuft, die Event-Retention aber weiter, wäre das ein Datenverlust-Fenster und nicht bloß eine Verzögerung. Das Datenloch ist deshalb **nicht leise**: Marker in der Snapshot-Zeile plus Log-/Sentry-Eintrag. Für die übrigen Wartungsjobs (`enforce_retention`, `verify_audit_chain`, `cleanup_orphan_storage_files`) bleibt Fail-loud die Hausregel — dieser Job ist die begründete Ausnahme, kein Präzedenzfall.

**Query-Deckel.** Jeder Backend-Aufruf des Snapshot-Laufs läuft unter einem transaktionslokalen `statement_timeout` — siehe [§ 9](#9-grenzen).

**Live-Lesepfad vs. hybrider Lesepfad.** Panel-Vorschau und generischer Export rufen **immer** `build_dataset(facility, date_from, date_to)` — also die Live-Abfrage gegen die Ereignisse. Das Sachbericht-**PDF** liest denselben Bericht dagegen hybrid über die Monats-Snapshots (`get_sample_report_1_statistics_hybrid`). Zwei Artefakte desselben Berichts über denselben Zeitraum können deshalb systematisch abweichen:

- **`unique_clients` über mehrere Monate.** Der hybride Pfad summiert die Monatswerte **nicht** (#1770, K4b) — Personen sind nicht additiv, und eine aufgeblähte Summe liefe an der k-Prüfung vorbei (3 Personen × 6 Monate ⇒ „18"). Stattdessen berechnet er für diesen einen Term einen eigenen **Live-DISTINCT über den Gesamtzeitraum** und weist dessen abweichende Quelle als Fußnote aus. Für retention-bereinigte ältere Zeiträume fällt er damit niedriger aus als die übrigen, snapshot-gespeisten Zahlen desselben PDF — das ist bewusst so und durch die Fußnote kenntlich.
- **Zeiträume jenseits der Aufbewahrungsfristen.** Snapshots überdauern die Event-Retention bewusst; die Live-Abfrage filtert soft-gelöschte Ereignisse (`is_deleted=False`) heraus. Für einen Zeitraum, dessen Ereignisse bereits gelöscht sind, liefert das PDF die Snapshot-Zahlen, Panel und CSV/JSON/XML dagegen (nahe) null bzw. Unterdrückungs-Marker.

Das ist keine Eigenheit des Plugin-Systems: dieselbe Divergenzklasse besteht seit jeher zwischen dem live gelesenen Externen Bericht und dem hybrid gelesenen PDF — M5 fügt eine dritte Instanz desselben Musters hinzu.

**Entscheid: Divergenz bleibt, Quelle wird ausgewiesen** (#1743, Produktentscheid 2026-08-03). Die Lesepfade werden **nicht** vereinheitlicht — beide sind fachlich gewollt (der Snapshot überdauert die Aufbewahrungsfristen, die Live-Abfrage zählt über den Gesamtzeitraum genauer). Stattdessen **weist jedes Artefakt seine Quelle selbst aus**. Die Texte und die stabilen Codes dafür stehen zentral in [`core/reporting/data_source.py`](../src/core/reporting/data_source.py):

| Artefakt | Code | Ausweis im Artefakt |
|---|---|---|
| Berichts-Panel (Vorschau) | `live` | sichtbare Zeile „Datenquelle: Live-Abfrage zum Erstellungszeitpunkt — …" |
| CSV-Export | `live` | Metadatenzeile, Segmente `\| data_source: live \| Datenquelle: …` **vor** dem k-Anon-Hinweis (Refs #1765: stabiler Code UND übersetztes Label, wie JSON/XML) |
| JSON-Export | `live` | Felder `data_source` (Code) + `data_source_note` (übersetzte Kurzform) |
| XML-Export | `live` | Wurzel-Attribute `data_source` + `data_source_note` |
| Externer Bericht (HTML + `?format=json`) | `live` | `metadata.data_source` / `metadata.data_source_note`, im HTML zusätzlich als Kopfzeile + Erklärsatz |
| Sachbericht-PDF | `snapshot_hybrid` | Fußzeile „Datenquelle: Monats-Aggregate — …"; **Ausnahme** `unique_clients`: eigener `live`-Term mit Fußnote am Wert (K4b, #1770) |

`data_source` ist bewusst ein **stabiler, sprachunabhängiger Code** — er darf sich nie mit der Oberflächensprache ändern, sonst müsste ein Konsument deutschen Fließtext parsen. Die menschenlesbaren Fassungen (`data_source_note`, Erklärsatz) sind übersetzbar.

Eine spätere Vereinheitlichung bleibt möglich, ist aber nicht geplant; sie bräuchte zusätzlich `completeness` im Snapshot, das dort heute nicht gespeichert wird — ein hybrid gespeister Export meldete sonst still „keine Lücken".

---

## 6. K-Anonymität — der `prepare_export`-Hook

**Repo-Invariante** (#1278, Security R4/R14): Jedes extern zirkulierende Berichts-Artefakt unterdrückt Kleinstfallzahlen. Ein Formatwechsel darf diese Härtung nie aushebeln.

| Pfad | `prepare_export`? | Sicht |
|---|---|---|
| Download CSV / JSON / XML | **ja** — `export_dataset()` ruft ihn vor *jedem* Renderer und **erzwingt** danach die ausgewiesene Schwelle | suppressiert |
| Berichts-Panel (HTMX-Vorschau) | **ja** — die View ruft `prepare_export(facility, build_dataset(...))` und **erzwingt** die Schwelle genauso | suppressiert (Parität zum Download) |
| Snapshot | nein — `build_dataset` direkt | roh |
| Sachbericht-PDF | nein — eigener Pfad, suppressiert selbst (#1278) | suppressiert |

Die Spalte „Sicht" sagt nichts über die **Quelle**: Panel und Download lesen live über `build_dataset`, das Sachbericht-PDF hybrid über Monats-Snapshots. Die Suppression greift auf allen Pfaden, die Zahlenbasis kann trotzdem abweichen — siehe [§ 5](#5-snapshot-integration).

### Brauche ich einen eigenen Override?

**Ja — und die *Deklaration* ist erzwungen, nicht nur verabredet (#2069).** Sobald das Backend registriert ist, stehen Download und Panel automatisch zur Verfügung; beide reichen den Datensatz durch `enforce_suppression_contract()` und weisen ihn **fail-closed** ab, wenn `summary["k_anonymity_threshold"]` fehlt, keine ganze Zahl (`bool` zählt nicht) oder kleiner als 1 ist. Der Default `return result` ist damit für **kein** exportierendes Backend mehr vertretbar — auch nicht für eines, dessen Zellen konstruktionsbedingt keine Kleinstfallzahlen sein können: es weist die Schwelle dann eben aus, ohne etwas zu unterdrücken.

> **Der Check validiert die Erklärung, nicht die Wirkung.** Er stellt sicher, dass dein Backend *sagt*, ab welcher Gruppengröße es unterdrückt hat — nicht, dass es das auch getan hat. Ein `prepare_export`, das `stamp_threshold(summary, 5)` setzt und eine Zelle mit dem Wert 1 stehen lässt, kommt mit HTTP 200 durch. Das lässt sich an der Exportgrenze nicht generisch prüfen: `records` und `summary` sind je Backend anders geschnitten, ein pauschaler Kleinzahl-Scan wäre je nach Schema falsch-positiv oder falsch-negativ. **Die Wirkung selbst gehört unverändert in die Tests deines Backends** (siehe die Checkliste in [§ 10](#10-test-checkliste-für-neue-backends)) und in die zentrale Suppressionsmaschinerie — der Runtime-Check schließt genau eine Lücke: das stille Fehlen jeder Suppression. Er ersetzt keinen einzigen Backend-Test.

Der Grund für die Laufzeitprüfung: der AST-Guard `test_jedes_backend_mit_randsumme_ruft_den_residual_guard` kann ein Backend **ohne** Override prinzipiell nicht sehen — es erzeugt keinen `prepare_export`-Knoten, an dem der Guard ansetzen könnte. Ergänzend prüft `TestJedesBackendUeberschreibtPrepareExport` statisch, dass jede Klasse unter `core/reporting/backends/` mit direkter `DatasetBackend`-Basis die Methode definiert — und zwar im **eigenen Klassenkörper**. Das Verzeichnis enthält per Konvention nur konkrete Backends mit lokalem Override: keine abstrakten Zwischenklassen, keine Suppression, die aus einem Mixin geerbt wird. Bei einem sicherheitsrelevanten Hook soll die Suppression in der Datei sichtbar sein, die den Bericht baut, statt am Ende einer Vererbungskette.

Die Schwelle *k* kommt je Einrichtung aus `Settings.k_anonymity_threshold` (Default 5) und wird über `_get_threshold(facility)` aus [`core.services.dashboard.external_report`](../src/core/services/dashboard/external_report.py) gelesen — demselben Modul, das auch `_suppress_small` und `suppress_sample_report_1_stats` für den PDF- und den externen Berichtspfad bereitstellt. `_suppress_small` stellt dabei zusätzlich eine **wertunabhängige Ausgabereihenfolge** her (#1767): unterdrückte Zeilen wandern als Block in kanonischer Ordnung ans Listenende, damit die Position nicht verrät, welche Zelle die kleinste ist. Setze `summary["k_anonymity_threshold"] = threshold`, wenn dein Backend suppressiert: Der CSV-Renderer hängt daraufhin einen Hinweis an die Metadatenzeile, und das Panel blendet den Suppressions-Hinweis ein.

### Minimalbeispiel — `ExampleBackend`

Eine Kennzahl je Zeile plus eine Randsumme. Zu lösen ist genau eine Rückrechnungs-Gefahr: `total = Σ counts`. Bliebe `total` nach einer Zeilen-Suppression roh stehen, wäre die unterdrückte Zelle exakt als Differenz zu den sichtbaren Zeilen bestimmbar. Deshalb:

- `_suppress_small(rows, threshold, margin=…, order=…)` setzt Zellen `< k` auf `count=None`/`suppressed=True` und führt die **Sekundär-Suppression** gleich mit aus. Sie hängt nicht an der Zellen*zahl* (A6.1: „genau eine unterdrückte Zelle"), sondern an der gemessenen **Machbarkeitsspanne** (`_audit_feasible_span`, #1770): Solange für eine unterdrückte Zelle nur noch ein einziger Wert möglich ist, fällt zusätzlich die **kleinste** sichtbare Zelle — und danach wird neu gerechnet, bis zum Fixpunkt. Ziel ist eine Spanne ≥ 2, also kein exakter Pin.
- `total` fällt, wenn es **selbst** unter der Schwelle liegt (R14). Darüber hinaus entscheidet `apply_residual_guard`: Er prüft die publizierte Partition gegen das Inferenzmodell und lässt erst die Randsumme, notfalls die ganze Aufschlüsselung fallen. Die alte pauschale Regel „eine Zeile unterdrückt ⇒ `total` weg" ist ersatzlos entfallen; sie unterdrückte mehr als nötig und lag trotzdem neben den Fällen, die der Spannen-Audit findet.

### Vollbeispiel — `SampleReport1Backend`

Mehrere zusammenhängende Kennzahlen plus Vollständigkeits-Meldungen. Hier reicht Zellen-Suppression nicht:

1. **Report-Körper:** dieselbe Maschinerie wie im PDF (`suppress_sample_report_1_stats`) — Kategorien- und Altersgruppen-Zellen `< k` fallen inklusive Sekundär-Suppression, `unique_clients` und die Randsumme `total < k` werden `None`. `records` wird aus den **bereits suppressierten** Zeilen neu projiziert; ohne diesen Schritt hätte das CSV die Rohzahlen weiter gezeigt.
2. **Die k-Schwelle prüft personenabgeleitete Zellen gegen DISTINCT-Personenzahlen** (#1770, K0-b): Ereigniszahlen allein genügen nicht, eine Alterszelle „Unter 18: 7" könnte von einer einzigen Person stammen. `build_dataset` liefert deshalb je Altersgruppen-Zeile zusätzlich `persons` (die zeitraum-skopierte DISTINCT-Personenzahl des Buckets), und `_suppress_small` unterdrückt, sobald die **strengere** der beiden Zahlen unter der Schwelle liegt. `persons` ist reine Suppressions-Eingabe und wird vor der Ausgabe aus jeder Zeile entfernt — sonst wäre die Personenzahl selbst eine unkontrollierte Kleinstfallzahl im Artefakt. Fehlt sie (ein Snapshot ohne diese Angabe, die sich nicht rekonstruieren ließ), gilt sie als *unbekannt* und die Zelle fällt.
3. **Personenabgeleitete Aufschlüsselungen fallen zusätzlich als Ganzes**, sobald `unique_clients < k` (#1768). Dieser Guard bleibt neben (2) bestehen: das Zellkriterium nullt nur Werte, `_drop_person_derived_breakdowns` entfernt `by_age_cluster` (und im Statistikbericht `by_contact_stage`) vollständig aus dem Artefakt — Key weg, denn eine genullte Zeile wäre selbst noch die Aussage „diese Altersgruppe kommt vor". Ereignisabgeleitete Aggregate bleiben unberührt.
4. **Vollständigkeits-Meldungen sind datenunabhängig** (#1769): **beide** Meldungen erscheinen im Export **immer** und **nie mit einer Zahl**. Nicht die Größe einer genannten Zahl war das Problem, sondern dass sie überhaupt genannt wurde — eine bezifferte Meldung publiziert implizit „mein Komplement ≥ k", also eine untere Schranke, und die Entscheidung darüber lief über eine Disjunktion auf Rohdaten und war damit selbst informativ. Der Block wird deshalb aus festen Codes gebaut, nicht aus `result.completeness` abgeleitet. Der Hinweis auf die Lücke bleibt in jedem Fall stehen — ein Bericht, der Lücken verschweigt, wäre irreführend vollständig; umgekehrt steht der Hinweis auch dann, wenn es gar keine Lücke gibt (wären beide Fälle unterscheidbar, verriete die bloße Gestalt des Blocks wieder die Rohdaten).

**Merke:** Der Punkt, an dem ein Backend gefährlich wird, sind **Beziehungen zwischen Kennzahlen** — konstruktionsbedingt gilt hier etwa `total + unmapped_events = Σ by_age_cluster + events_without_client` (beide Seiten sind „alle sichtbaren Ereignisse im Zeitraum"). Sind drei Terme bekannt, ist der vierte exakt bestimmt und die Suppression liefe leer. Seit (3) und (4) sind die beiden Meldungs-Terme grundsätzlich verborgen und Σ Altersgruppen fällt bei kleiner Personenzahl ganz weg — es bleiben nie weniger als zwei Unbekannte, ganz ohne datenabhängige Nachbesserung. Ein `rows`-Backend mit *einer* Kennzahl je Zeile hat keine solche Identität; dort genügt das Muster aus `example.py`. Wer mehrere zusammenhängende Kennzahlen ausgibt, prüft die Beziehungen zwischen ihnen **als Ganzes**, nicht paarweise.

### Summen über Feldwerte — die beitragenden Ereignisse zählen, nicht die Summe

Ein Backend, das `Event.data_json` addiert (`needle-exchange-balance`, `counseling-hours`), publiziert keine Fallzahl, sondern eine **Summe**. Die k-Schwelle greift dort deshalb an einer anderen Größe: **eine Summe fällt, wenn die Zahl der beitragenden Ereignisse `< k` ist** — nicht, wenn die Summe selbst klein ist. Der Grund ist die Rückrechnung nach unten: Steht „Ausgabe gesamt: 340" neben zwei beitragenden Ereignissen, ist der Einzelwert nahezu bestimmt, und ein Einzelwert ist ein Datum einer einzelnen Person. Eine große Summe aus wenigen Ereignissen ist damit gefährlicher als eine kleine aus vielen.

Daraus folgen drei Punkte, die zusammengehören:

- **Die Ereigniszahl selbst läuft ebenfalls durch die Schwelle** (R14) — sonst wäre sie die Kleinstfallzahl, die die Summe gerade schützen sollte.
- **Bei Aufschlüsselungen kaskadiert das** (`counseling-hours`): Fällt die Zeile einer Kategorie, fällt ihr Beitrag auch aus jeder Gesamtsumme, in die sie eingeht — sonst wäre die unterdrückte Zeile als Differenz bestimmbar.
- **Ereignisse ohne verwertbaren Zahlenwert sind in keiner Summe enthalten.** Das gehört als datenunabhängige (unbezifferte) Vollständigkeits-Meldung ins Artefakt, sonst liest der Empfänger eine Teilsumme als Gesamtsumme.

### Mehrfachnennung: `margin=None` ist keine Entwarnung

Ein `multi_select`-Feld **partitioniert nicht** — ein Ereignis kann mehrere Optionen tragen, es gilt nur `Σ Options-Counts ≥ Randsumme`. Der Reflex, daraus „hier ist nichts zurückzurechnen" zu lesen, war der Fehler in der ersten Fassung von `service-distribution`. Richtig ist ein Doppelschritt:

1. **`margin=None` an `_suppress_small`** — weil eine deklarierte Randsumme, die nicht exakt partitioniert, *schlimmer* wäre als keine: Machbarkeits-Audit und Fixpunkt-Guard rechnen mit `Residuum = Randsumme − Σ sichtbare Zellen`, und dieses Residuum ist bei Mehrfachnennung schlicht falsch. `margin=None` schaltet beide bewusst ab.
2. **Und genau deshalb braucht die Gruppe eine eigene, benannte Regel:** Die Ungleichung besteht ja weiter und ist selbst ein Kanal. Sie liefert für jede unterdrückte Zelle die **untere** Schranke `x ≥ Randsumme − Σ sichtbare Zellen`, die Primär-Suppression die **obere** `x ≤ k − 1`. Fallen beide zusammen, ist die Zelle exakt bestimmt (bei *k* = 5, sichtbarer Zelle 6 und publizierter Randsumme 10 ist die unterdrückte Zelle zwingend 4). **Deshalb fällt die Randsumme, sobald *eine* Zelle der Gruppe unterdrückt ist** — ohne sie gibt es die untere Schranke nicht mehr, und es bleibt `1 ≤ x ≤ k − 1`.

**Merke:** „partitioniert nicht" heißt nicht „ungefährlich", sondern „die zentrale Maschinerie ist nicht zuständig". `margin=None` allein schützt **nicht**; es schaltet nur den Schutz ab, der hier falsch gerechnet hätte.

### K4a — die Zusicherung gilt je Artefakt, nicht über Artefakte hinweg

Alles in diesem Abschnitt rechnet **innerhalb eines Berichts**. Zwei Berichte derselben Einrichtung und desselben Zeitraums können sich gegenseitig aufschließen, ohne dass in einem von beiden eine Regel verletzt wäre:

- **Verfeinerung.** `monthly-trend` zerlegt exakt die Kategoriezahlen des Sachberichts nach Monat. Eine dort unterdrückte Kategoriezelle ist aus den sichtbaren Monatszeilen summierbar — und umgekehrt. Das gilt auch, wenn in *keinem* der beiden Berichte eine Zelle unterdrückt wurde: die feinere Auflösung ist selbst der Zugewinn.
- **Differenz.** `service-distribution` zählt Kontakt-Ereignisse **mit** Auswahlwert, `counseling-hours` **alle** Kontakt-Ereignisse. Die Differenz — Kontakte ohne Leistungs-Eintrag — steht in keinem der beiden Berichte und läuft durch keine Suppressionsregel.
- **Reproduktion.** Die Kategorien-Zeilen von `counseling-hours` (`contact`/`counseling`/`crisis`) reproduzieren die Kategorien des Sachberichts — dieselben Maps, dieselbe Basismenge, dieselbe Zählgröße, nur um die Minutenspalte verfeinert. Das hebt dort auch die **Sekundär**-Suppression auf: eine Kategorie, die im Sachbericht nur fällt, damit die primär unterdrückte Nachbarzelle nicht rückrechenbar wird, zeigt hier ihre Ereigniszahl (die ihre eigene k-Prüfung bestanden hat). Sichtbar wird keine Kleinstfallzahl, wohl aber verliert eine korrekte Suppressionsbegründung ihre Wirkung — Sekundär-Suppression ist per Konstruktion tabellenlokal.
- **Stichtag.** `ban-overview` (`aktiv_aktuell`) und `case-metrics` (`offene_faelle_aktuell`) tragen je eine Zahl, die am **Erstellungstag** hängt, nicht am Berichtszeitraum. Zwei Herausgaben desselben Zeitraums zu verschiedenen Zeitpunkten unterscheiden sich genau in ihr; die Differenz ist die Netto-Veränderung zwischen beiden Tagen und läuft durch keine Suppressionsregel. Die Kennzahlen bleiben (Maintainer-Entscheid 2026-08-05) — beide Artefakte tragen dafür einen `CompletenessIssue`, der die Momentaufnahme benennt.

Das ist eine **akzeptierte Definitionsgrenze, kein Versäumnis**: Es gibt keine Zelle, an der eine artefaktlokale Regel ansetzen könnte, und ein artefaktübergreifender Guard müsste wissen, welche Berichte an denselben Empfänger gegangen sind — Wissen, das im Erzeugungspfad nicht existiert. Flankierend macht die **Freigabe-Übersicht** (`ReportReleaseLogView`, [`core/services/dashboard/release_log.py`](../src/core/services/dashboard/release_log.py)) überlappende Herausgaben sichtbar, damit die Einrichtung entscheiden kann.

> **Auflage für Nachahmer** (aus [`monthly_trend.py`](../src/core/reporting/backends/monthly_trend.py)): Wer eine bestehende Grundgesamtheit feiner aufschlüsselt, baut eine Verfeinerung. Drei Fragen — **dieselben Maps? dieselbe Basismenge (Filter, Zeitraumsemantik)? dieselbe Zählgröße?** Dreimal „ja" ⇒ es ist eine, und die Beziehung gehört **in den Modul-Docstring**. Sie ist sonst nirgends sichtbar, weder im Code noch im Artefakt, und die Freigabepraxis kann nur berücksichtigen, was jemand aufgeschrieben hat.

### Grenzverhalten, das man kennen muss

- **`prepare_export` ist nicht idempotent.** Der Hook erwartet das **rohe** `build_dataset`-Ergebnis (`SampleReport1Backend` wandelt `by_category` z. B. von Tupeln in Dict-Zeilen). Zweimal aufrufen ist kein sinnvoller Aufruf; genau einmal transformieren.
- **Ein leerer Zeitraum sieht aus wie eine Suppression.** `0 < k`, also wird auch die ehrliche Null unterdrückt. Das ist Verhalten der geteilten Suppressions-Funktion und die sichere Richtung, aber es kann Empfänger verwirren.
- **Übersuppression ist eingepreist.** Die Vollständigkeits-Meldungen stehen auch dann unbeziffert da, wenn es gar keine Lücke gibt, und die Altersverteilung fällt schon bei wenigen Personen komplett — beides ist gewollt (die Alternative wäre eine datenabhängige Verzweigung, also genau der geschlossene Kanal). Die Richtung ist sicher, der Aussagekraftverlust real.
- **Suppression gehört nie in `build_dataset`.** Sonst wären auch Snapshots beschnitten — und damit die Grundlage jeder späteren Auswertung und des unveränderten PDF-Pfads.
- **Der Fehlerpfad zeigt nichts, nicht „etwas".** Die Kette `build_dataset` → `prepare_export` → `enforce_suppression_contract` ist **atomar**: Entweder läuft sie komplett und liefert die suppressierte Sicht, oder es gibt eine Fehlerkarte bzw. eine Fehlerantwort **ohne jede Datenzeile** (#2070). `SuppressionContractError` ist dabei ausdrücklich **kein** Sonderfall — sie wird wie jeder andere Backend-Fehler behandelt. Ein Zwischenzustand (das rohe `build_dataset`-Ergebnis rendern, weil `prepare_export` scheiterte) wäre genau die Regression, die [ADR-038 § 5](adr/038-reporting-plugin-backends.md) ausschließt.

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
# Bericht: <slug> | <facility> | <date_from>–<date_to> | <generated_at> | data_source: live | Datenquelle: Live-Abfrage zum Erstellungszeitpunkt | population: all_events | Grundgesamtheit: alle Ereignisse im Zeitraum | k-Anonymität: Zellen < k unterdrückt
Kategorie,Anzahl
Kontakte,42
```

- Die Metadatenzeile trägt den k-Anon-Hinweis **immer**: `summary["k_anonymity_threshold"]` ist Voraussetzung für den Export überhaupt (#2069), `export.py` setzt den Zusatz ohne Bedingung. Ohne den Zusatz sähe der Empfänger leere Zellen ohne Erklärung — das CSV trägt (anders als JSON/XML/Panel) keine Completeness-Hinweise.
- Refs #1765: das Segment `data_source: live` ist der stabile, sprachunabhängige Code (Parität zu JSON/XML) — ein Parser matcht darauf, nie auf das übersetzte Label `Datenquelle: …`. Unter EN-Locale steht dort `data_source: live | Data source: Live query at the time of creation`.
- Refs #1770 (K3-A3): dasselbe Muster für die **Grundgesamtheit** — `population: <code>` ist der stabile Code aus `DatasetBackend.population`, `Grundgesamtheit: …` das übersetzte Label. Ein Bericht, der nicht sagt, *worüber* er zählt, ist nicht nur undicht, sondern irreführend: mehrere Tabellen über verschiedenen Basismengen summieren sich zu verschiedenen Zahlen.
- **Jede** Zelle, auch Metadaten und Header, läuft durch `_sanitize_csv_cell` — den bestehenden Formula-Injection-Schutz aus `core.services.system.export` (OWASP CSV-Injection, #719). Bewusste Wiederverwendung statt Zweitimplementierung.
- Completeness-Meldungen erscheinen **nicht** im CSV: Es bleibt maschinenlesbar. Wer sie braucht, nimmt JSON, XML oder das Panel.
- Zellen werden über `record.get(spec.key)` gezogen — ein fehlender Schlüssel ist eine leere Zelle, kein Fehler.

### JSON

```json
{
  "backend": "example",
  "name": "Beispiel: Ereignisse je Dokumentationstyp",
  "date_from": "2026-01-01",
  "date_to": "2026-01-31",
  "generated_at": "2026-02-01T09:00:00+01:00",
  "mode": "rows",
  "data_source": "live",
  "data_source_note": "Live-Abfrage zum Erstellungszeitpunkt",
  "population": "all_events",
  "population_note": "alle Ereignisse im Zeitraum",
  "field_specs": [{"key": "document_type", "label": "Dokumentationstyp"}],
  "summary": {"total": 42, "k_anonymity_threshold": 5},
  "records": [{"document_type": "Kontakt", "system_type": "", "count": 42}],
  "completeness": [{"code": "…", "message": "…", "severity": "warning"}]
}
```

Serialisiert mit `DjangoJSONEncoder`, `ensure_ascii=False`, `indent=2`. `completeness` führt **nur** `code`/`message`/`severity` — `count` bleibt intern. `population`/`population_note` kommen aus `DatasetBackend.population` (Default `all_events`); ein Backend, das enger zählt, überschreibt das Attribut und der Ausweis zieht überall mit.

### XML

```xml
<dataset backend="example" date_from="2026-01-01" date_to="2026-01-31" generated_at="…" mode="rows"
         data_source="live" data_source_note="Live-Abfrage zum Erstellungszeitpunkt"
         population="all_events" population_note="alle Ereignisse im Zeitraum">
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
| `unmapped_system_types` | `warning` | Ereignisse, deren Dokumentationstyp **gar keine** Berichtszuordnung trägt (`report_key` leer/`NULL`). Sie fehlen im Bericht vollständig. Der Code-Name stammt aus der Zeit vor `report_key` und **bleibt** — er ist eine Maschinenschnittstelle (JSON/XML, von `monthly-trend` importiert), ein Rename wäre ein Bruch für jeden Empfänger, der darauf parst. |
| `unknown_report_keys` | `warning` | Ereignisse, deren Dokumentationstyp einen `report_key` trägt, den **kein** Backend (mehr) kennt — Fremdpaket, entferntes Backend, Tippfehler (#1849, Entscheid E-H). |
| `duplicate_field_binding` | `warning` | Mindestens ein Dokumentationstyp trägt **mehrere** Feldvorlagen mit demselben Feld-Berichtsschlüssel. Das Backend zählt dann keines der Felder (fail-safe, Entscheid E-J). |
| `events_without_client` | `info` | Ereignisse ohne Personenbezug — sie zählen in den Kategorien mit, fehlen aber in Altersgruppen und Personenzahl. |

**Semantik der Severity:** `warning` = im Bericht fehlende Daten (Zahlen sind zu niedrig). `info` = enthaltene, aber ungleich verteilte Daten (Teilauswertungen weichen ab). Das Panel rendert `warning` als gelbes „Hinweis"-Badge, `info` grau.

**Konventionen:**

- Nur Lücken **> 0** melden. Eine fehlende Meldung heißt „diese Lücke existiert nicht" — die Suppressions-Logik verlässt sich darauf.
- Eine bezifferte Meldung trägt ihre Zahl **zusätzlich** in `count`, sonst kann `prepare_export` sie nicht transformieren und muss sie im Zweifel komplett unterdrücken.
- Alle Kennzahlen einer Prüfung möglichst in **einer** Aggregation ziehen. `build_dataset` ruft `validate_completeness` mit, und der hybride Snapshot-Pfad ruft `build_dataset` je Monatssegment einmal auf — Einzel-`count()`s vervielfachen sich dort.
- Für die Export-Fassung eine **ziffernarme** Formulierung vorsehen (ohne Bezugsgröße, ohne Prozentanteil) sowie einen **ziffernfreien** Ersatztext für den Suppressionsfall. Fehlt beides, fällt die Zahl im Export vorsichtshalber ganz weg.
- Meldungstexte sind **übersetzbar** (`gettext_lazy` bzw. `format_lazy` mit benannten Platzhaltern) — die Regeln stehen in [§ 2.3](#23-fieldspec).

---

## 9. Grenzen

- **Kein generischer Monats-Merge** für snapshot-fähige Aggregat-Backends (siehe [§ 5](#5-snapshot-integration)).
- **Fehlerisolation je Pfad, nicht je Backend-Menge** (#2070). Ein defektes Backend legt nur sich selbst still. Was das je Pfad heißt:

  | Pfad | Verhalten bei einem werfenden Backend |
  |---|---|
  | Statistik-Übersicht (KPI, Diagramme) | lädt unverändert; ein Backend, dessen `is_available` wirft, gilt als **nicht verfügbar** und verschwindet aus der Kartenliste (fail-closed) |
  | Berichts-Panel (Reiter **und** HTMX-Partial) | HTTP **200** mit Fehlerkarte statt Vorschau — 200, weil HTMX nur 2xx einswappt; eine 5xx-Antwort ließe die alte Vorschau stehen |
  | Download CSV / JSON / XML | HTTP **502**, knapper Text, **kein** `Content-Disposition`, kein Byte des Datensatzes — und **kein** `EXPORT`-Audit-Eintrag: ein gescheiterter Export darf im append-only Audit nicht als Herausgabe stehen |
  | Sachbericht-PDF (`SampleReport1ExportView`) | HTTP **502** wie der generische Download — dieser Pfad geht nicht über `export_dataset`, sondern über den hybriden Lesepfad `generate_sample_report_1_pdf` → `get_sample_report_1_statistics_hybrid`, der im Live-Fallback `build_dataset` selbst ruft |
  | Snapshot-Lauf | Backend übersprungen, Marker in `backend_data["_errors"]`, Rest gespeichert (siehe [§ 5](#5-snapshot-integration)) |

  Jeder Fangfall wird mit `logger.exception` samt Traceback protokolliert und erreicht darüber das Fehler-Tracking (GlitchTip/Sentry). Die Fehlerkarte nennt den betroffenen Bericht, aber keine Exception-Details.

  **Damit der Export-Fang wirklich greift, wird alles Backend-Eigene eager ausgewertet.** Der CSV-Renderer gibt einen Generator zurück; dessen Körper läuft erst beim Streamen — also nachdem Status 200 und `Content-Disposition` gesendet sind. Ein Fehler dort ergäbe eine **abgeschnittene Datei** statt eines klaren Fehlers (kein k-Anon-Leck: die Suppression ist zu dem Zeitpunkt gelaufen, aber ein Bruch der Zusage). Vor dem Response-Bau liegen deshalb: `get_field_specs()`, die `FieldSpec`-Werte `label`/`key`, `backend.slug`/`backend.population` in der Metadatenzeile — und `DatasetResult.records`, die `export_dataset` direkt hinter der Suppressions-Klammer auflöst und gegen den Mapping-Vertrag prüft (`_materialize_records`). **Der Vertrag lautet: jeder Record ist ein `Mapping`** — die Renderer projizieren über `record.get(key)`. Ein lazy Iterable ist dabei kein Fehler: es wird hier vollständig konsumiert (`list(...)`) und streamt nicht bis zum Renderer durch; wirft es beim Konsum, endet der Download mit 502 statt mit einer halben Datei. Abgewiesen wird nur ein Nicht-Mapping, mit `TypeError` samt Slug, Position und Typ — nie mit dem Record-Inhalt. Deklariert bleibt `records: list[dict]`, und das bleibt auch die Erwartung: ein *unendliches* Iterable hinge an dieser Stelle. Lazy bleibt nur das Chunking fertiger, repo-eigener Strings durch den `csv.writer` — das ist der eigentliche Streaming-Gewinn; `build_dataset` baut die Records ohnehin vollständig.
- **Query-Deckel je Backend-Aufruf.** Panel, Export und Snapshot-Lauf umschließen den Backend-Aufruf mit `core.reporting.db_guard.reporting_statement_timeout` — einem transaktionslokalen `SET LOCAL statement_timeout` (`set_config(..., is_local=true)`, no-op außerhalb PostgreSQL). Der Wert steht im Setting `REPORTING_BACKEND_STATEMENT_TIMEOUT_MS` (Default `10000` ms, per gleichnamiger Env-Var überschreibbar, `0` schaltet ab). Bewusst **transaktions**lokal und nicht session-weit: bei `CONN_MAX_AGE > 0` reicht Django Verbindungen zwischen Requests weiter, ein session-weiter `SET` bliebe auf der wiederverwendeten Verbindung stehen. Der abgebrochene Lauf erreicht die Aufrufer als `django.db.utils.OperationalError` und wird wie jeder andere Backend-Fehler isoliert — kein Sonderfall. Der Deckel begrenzt die Laufzeit **einer Anweisung**, nicht die Gesamtdauer eines Backend-Aufrufs mit vielen kleinen Queries.
- **JSON und XML werden nicht gestreamt.** Aggregat-Payloads sind klein; ein `rows`-Backend mit Massendaten (zehntausende Records) würde beide Renderer vollständig im Speicher aufbauen. Das ist die dokumentierte Grenze der aktuellen Pipeline — ein solches Backend bräuchte Streaming-Renderer analog dem CSV-Pfad, oder es beschränkt seinen Zeitraum.
- **Keine externen Plugin-Pakete.** Discovery scannt ausschließlich `core/reporting/backends/`; `entry_points` von Drittpaketen sind nicht vorgesehen. Ein fremdes Paket mit Lesezugriff auf Sozialdaten wäre eine Vertrauensentscheidung eigener Güte (Signatur, Freigabe, Sandbox) — als spätere Erweiterung denkbar, dann mit eigenem Sicherheitskonzept.
- **Keine generische PDF-Pipeline.** Die Formate der Pipeline sind CSV, JSON und XML; der Sachbericht-PDF bleibt ein eigener, separater Pfad (eigene View, hybrider Lesepfad, eigene Suppression über `suppress_sample_report_1_stats`). Seine **Fehlerisolation** teilt er mit dem generischen Download (#2070) — siehe die Tabelle weiter oben.
- **`core/reporting` liegt außerhalb der mypy-strict-Zone und der mutmut-Pfade.** Der Code ist strict-kompatibel annotiert; die Aufnahme in die Gates ändert Build-Konfiguration und ist ein offener Maintainer-Entscheid.
- **`report_key` wirkt nur auf Datensatz-Berichte.** Das Statistik-Dashboard ist kein `DatasetBackend`: Kennzahlen, Trend-Diagramme und die Verteilungen nach Dokumentationstyp/Altersgruppe entstehen in [`core/services/dashboard/statistics.py`](../src/core/services/dashboard/statistics.py) — `get_statistics()` baut sie über `event_queryset_for_period()`, das ausschließlich nach Einrichtung, Zeitraum, `is_deleted` und optional dem UI-Filter `document_type` einschränkt. Für vollständig abgedeckte, vergangene Kalendermonate — und nur ohne gesetzten `document_type`-Filter — liest die Übersicht dieselben Aggregate statt live aus `StatisticsSnapshot.data` (`get_statistics_hybrid()`, für die Chart-API `get_statistics_trend()`, beide in [`core/services/dashboard/snapshot.py`](../src/core/services/dashboard/snapshot.py); siehe [§ 5](#5-snapshot-integration)). **Keiner dieser Pfade liest `report_key`.** In einer Snapshot-Zeile verläuft die Grenze exakt dazwischen: `data` trägt die schlüsselfreien Dashboard-Aggregate, `backend_data` die rohen `summary`s der Backends, die über `report_key` binden. Das ist ein Entscheid, kein Versehen — die Übersicht ist die Grundgesamtheit, die Berichte sind die Projektion darauf; deshalb benennt das Dashboard seine Grundgesamtheit je Tabelle selbst (`ALL_EVENTS` für die Dokumentationstypen, `EVENTS_WITH_PERSON` für die Altersgruppen, K3-A3 aus #1770). Wer in einem dieser Pfade einen `report_key`-Filter ergänzt, lässt Dashboard und Datensatz-Berichte über verschiedene Mengen laufen — verwandt mit der Divergenz, die der Dokumentationstyp-Filter aus #1804 innerhalb der Dashboard-Aggregate vermeidet.
- **Das Mapping ist hart kodiert.** Die Zuordnung Schlüssel → Berichtszeile (`CATEGORY_MAP`) steht im Backend-Modul; es gibt **kein** Konfigurationsfeld dafür. Konfigurierbar ist (#1849) nur, **welchen Schlüssel** ein Typ oder Feld trägt — nicht, worauf ein Schlüssel abbildet ([ADR-042](adr/042-report-key-berichtszuordnung.md)). Das ist ein Entscheid, kein Übergangszustand ([ADR-038](adr/038-reporting-plugin-backends.md), Update 2026-08-04): eine Config-UI machte Berichtszahlen per Klick verschiebbar. Eine Mapping-Änderung ist damit ein Code-Change mit Review — und `DocumentType.category` wurde fürs Reporting nie verwendet und existiert nicht ([ADR-040](adr/040-documenttype-category-entfernt.md)).
- **Die `system_type`-Werteliste ist eingefroren** (RFC #1748, Entscheid 2026-08-03) — und auf **zwei** Werte beschränkt (#1850): `ban` und `crisis`, die beiden mit App-Wirkung (Bann-Logik, Krisen-Highlight, `ban-overview`). **Keine neuen Werte** — eine zusätzliche Berichtskategorie ist kein Grund, ein Kern-Datenmodell-Enum zu erweitern; dafür gibt es `report_key`. Ein Freeze-Guard pinnt die Liste (`src/tests/test_system_type.py`).

  Der Vollstrang aus Konzept #1847 ist damit abgeschlossen: deklarative Bindung (#1824), Sichtbarkeit (#1848), die `report_key`-Einführung (#1849, [ADR-042](adr/042-report-key-berichtszuordnung.md)) und die Reduktion (#1850). Die Berichtszuordnung läuft **weder über `system_type` noch über den Slug** — der Zweitschlüssel aus #1741 ist ersatzlos entfallen. Ein Paket, das einen der sieben Altwerte noch mitführt, lädt weiterhin: der Wert entfällt mit einer Warnung im Load-Report.


---

## 10. Test-Checkliste für neue Backends

Testdateien liegen im Familien-Unterpaket `src/tests/reporting/` und folgen dem Muster `src/tests/reporting/test_reporting_<slug>.py`; die Familien-`conftest.py` dort isoliert die Registry je Test automatisch (`_isolated_registry`, autouse) — eine eigene Isolations-Fixture ist in neuen Dateien nicht mehr nötig. Die bestehenden Suiten (`test_reporting_base.py`, `test_reporting_registry.py`, `test_reporting_export.py`, `test_reporting_sample_report_1.py`, `test_reporting_example.py`, `test_reporting_views.py`) sind Vorlagen.

**Registry-Isolation (Pflicht, sonst ordnungsabhängige Fehlschläge).** Ad-hoc registrierte Test-Backends dürfen nicht in Folgetests desselben Workers leaken. Direkt am Modul patchen, kein Proxy:

```python
@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    registry._discover()  # echte Discovery EINMAL vorher
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
| **Fail-closed:** ein Datensatz ohne `summary["k_anonymity_threshold"]` löst in Export **und** Panel `SuppressionContractError` aus | Erzwungene K-Anon-Invariante (#2069) |
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
- [ADR-042](adr/042-report-key-berichtszuordnung.md) — `report_key` als eigene Achse der Berichtszuordnung; superseded die **Schlüsselwahl** aus [ADR-038 § 4](adr/038-reporting-plugin-backends.md), deren Kern („Berichtszahlen nicht per Klick verschiebbar") in Kraft bleibt. Unter *Abgrenzung* steht auch, warum ein unbekannter Schlüssel **nur** auf Typ-Ebene gemeldet wird und es bewusst kein Feld-Pendant zu `unknown_report_keys` gibt.
- [ADR-023](adr/023-k-anonymization-statistik.md) — K-Anonymisierung für externe Statistik
- [ADR-034](adr/034-config-ui-category-system-type.md) — `category` vs. `system_type`
- [ADR-040](adr/040-documenttype-category-entfernt.md) — `category` entfernt
- [ADR-037](adr/037-konfigurationspakete-dynamische-felder.md) — Konfigurationspakete, `DocumentType.slug`
- [admin-guide.md § 2.5](admin-guide.md#25-dokumentationstypen-konfigurieren) — dieselbe Zuordnung aus Sicht der Konfigurations-UI: Berichtsschlüssel an Dokumentationstyp und Feldvorlage, die Zustände, die Einmaligkeit und der Reparaturweg (Nachtragen ja, Ändern nein). Wer die Regeln im Code ändert, zieht dort nach.
- [user-guide.md § 7](user-guide.md#7-statistik-und-export) — Bedienung der Berichte-Sektion aus Anwendersicht
- [packages.md](packages.md) — Konfigurationspakete (Quelle der Dokumentationstypen, die Berichte auswerten)
