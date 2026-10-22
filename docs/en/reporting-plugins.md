> This is the English translation of [reporting-plugins.md](../reporting-plugins.md).
> The German version is the authoritative source.

# Reporting Plugins (Report Backends)

A **report backend** encapsulates the domain logic of one reporting standard — "contacts by category", "events per documentation type", a regional funding-body form — behind a fixed interface. The panel preview, the CSV/JSON/XML export and the snapshot storage talk to that interface only and know nothing about the individual report.

The practical consequence: **a new report is a single file.** A module under [`src/core/reporting/backends/`](../../src/core/reporting/backends/) containing a `DatasetBackend` subclass and the `@register_backend` decorator automatically appears as a card on the "Dataset reports" tab of the statistics page and in all three export formats. No migration, no URL pattern, no view, no entry in the authorization matrix.

- **Why the system looks the way it does:** [ADR-038](../adr/038-reporting-plugin-backends.md).
- **Why small cell counts are suppressed:** [ADR-023](../adr/023-k-anonymization-statistik.md) and #1278.
- **Why the mapping uses `report_key` and never `category`:** [ADR-042](../adr/042-report-key-berichtszuordnung.md) (German, supersedes [ADR-038 § 4](../adr/038-reporting-plugin-backends.md)), [ADR-034](../adr/034-config-ui-category-system-type.md). `DocumentType.category` has been removed as of [ADR-040](../adr/040-documenttype-category-entfernt.md) (German) -- the rule is now structural.

> **The most important rule up front.** Everything a backend returns from `build_dataset()` is **raw**. Everything that leaves the building (download **and** panel preview) passes through `prepare_export()` first. If you produce numbers that circulate externally, you must implement that hook — see [§ 6](#6-k-anonymity--the-prepare_export-hook).


### Quantity sum report

The **Sum of number fields** backend (`quantity-sum`) evaluates events by documentation type over the selected period. It binds only to the `quantity_units` report key ("Quantity") and reads number fields, producing one period total without monthly breakdowns or snapshots. The table shows **contributing events** (with a usable quantity) and **quantity in period** for each type; it publishes no separate event counts or bound-event subtotal. Missing, invalid, or unreadable values do not contribute; a completeness notice reports that such values or sums were excluded.

The population includes only event types with an active, bound number field; types without that field or with the type report key `excluded` remain outside the counts and the “Appears in” information. Excluded types do not make the report available and do not generate missing-field or duplicate-binding notices. Availability requires at least one active, non-excluded event type with a matching number field; historical events of a subsequently deactivated type are still included while its field remains active.

The k-anonymity threshold *k* applies to contributing event counts. A sum is shown only with at least *k* contributing events. The shared decision `build_shared_document_type_counts()` in `core/services/dashboard/external_report.py` respects both the type suppression in **General key figures** and category suppression in the **Sample activity report** for the same period, including secondary and whole-table suppression. All types belonging to a hidden activity-report category remain hidden in the quantity report; the same applies to all types of a visible category if at least one member type is hidden in general key figures; both live and hybrid PDF sources are considered. A contributor lower bound therefore cannot unlock the hidden category using the mapped-event total; quantity units are not subtracted as event counts. If a row or a small missing-value gap is hidden, contributor and quantity totals are withheld. Preview and downloads use the same published values.

---

## 1. Architecture Overview

```
                        src/core/reporting/backends/*.py
                        ┌───────────────────────────────────┐
                        │  @register_backend("my_slug")     │
                        │  class MyBackend(DatasetBackend)  │
                        └───────────────┬───────────────────┘
                                        │ registration as an import-time side effect
                                        ▼
   get_backends() ──── lazy ────►  registry._REGISTRY   { slug → singleton instance }
   get_backend(slug)                    │
   get_available_backends(facility)     │
                                        │
        ┌───────────────────────────────┼────────────────────────────────┐
        ▼                               ▼                                ▼
   reports panel                 export_dataset()            create_or_update_snapshot()
   (HTMX preview)                (CSV · JSON · XML)          (monthly, per facility)
        │                               │                                │
        │ build_dataset()               │ build_dataset()                │ build_dataset()
        │ prepare_export()  ◄── k-anon ─┤ prepare_export()               │ (NO prepare_export)
        ▼                               ▼                                ▼
   suppressed preview            suppressed file            StatisticsSnapshot.backend_data
                                                                  (raw aggregate)
```

| Module | Contents |
|---|---|
| [`core/reporting/base.py`](../../src/core/reporting/base.py) | The contract: `DatasetBackend`, `DatasetResult`, `FieldSpec`, `CompletenessIssue`. Imports **nothing** from the app. |
| [`core/reporting/registry.py`](../../src/core/reporting/registry.py) | Registry plus lazy auto-discovery: `register_backend`, `get_backends`, `get_backend`, `get_available_backends`. |
| [`core/reporting/export.py`](../../src/core/reporting/export.py) | Format-independent export pipeline: `export_dataset`, `SUPPORTED_FORMATS`. |
| [`core/reporting/backends/`](../../src/core/reporting/backends/) | The backends themselves. Discovery target — modules placed here are loaded automatically. |
| [`core/views/statistics.py`](../../src/core/views/statistics.py) | Generic endpoints: `DatasetReportPanelView` (preview), `DatasetExportView` (download). |
| [`core/services/dashboard/snapshot.py`](../../src/core/services/dashboard/snapshot.py) | Writes `StatisticsSnapshot.backend_data` from every `snapshot_enabled` backend. |

---

## 2. API Reference

### 2.1 `DatasetBackend`

```python
class DatasetBackend(ABC):
    slug: str = ""  # set and validated by @register_backend
    name: str = ""  # display name on the report card (gettext_lazy-capable)
    description: str = ""  # one sentence "what this report counts" (gettext_lazy-capable)
    snapshot_enabled: bool = False  # True → summary is stored in StatisticsSnapshot.backend_data

    def is_available(self, facility) -> bool: ...
    @abstractmethod
    def get_field_specs(self) -> list[FieldSpec]: ...
    @abstractmethod
    def build_dataset(self, facility, date_from, date_to) -> DatasetResult: ...
    def validate_completeness(self, facility, date_from, date_to) -> list[CompletenessIssue]: ...
    def prepare_export(self, facility, result: DatasetResult) -> DatasetResult: ...
```

| Element | Required | Meaning |
|---|---|---|
| `slug` | — | Set by the decorator. Do **not** assign it yourself. Format `^[a-z0-9]+(?:[-_][a-z0-9]+)*$` (lowercase letters/digits, single `-`/`_` as separators, never at the edges). It appears in query parameters, file names and `backend_data` keys, which makes it effectively immutable — changing it afterwards requires a data migration (pattern: [`0123`](../../src/core/migrations/0123_rename_backend_data_key_sample_report_1.py)). |
| `name` | **yes** | Visible name on the report card and in the JSON payload. Declare it as `gettext_lazy`; the export pipeline casts to `str` at the payload boundary itself. |
| `snapshot_enabled` | no | Defaults to `False`. Set it to `True` only if `summary` is **purely aggregated** and `summarize_events` is implemented — see [§ 5](#5-snapshot-integration). |
| `is_available(facility)` | no | Defaults to `True`. Hook for facility-dependent reports. `False` — like any other falsy return value, for instance the `None` of a nullable `BooleanField` — hides the backend from the report list **and** makes panel and export respond with HTTP 404. The list filter and the access guard check the same truthiness. |
| `description` | no | One sentence "what this report counts", shown directly below the name on the card (Refs #1804). Declare it as `gettext_lazy`. Empty = the card omits the line — leaving only the name, which turns the choice back into guesswork. |
| `get_field_specs()` | **yes** | Column definition of the tabular projection. The order is the column order in the CSV. |
| `build_dataset(facility, date_from, date_to)` | **yes** | Builds the dataset for the **inclusive** period `[date_from, date_to]`. The result is **raw** (unsuppressed). |
| `validate_completeness(facility, date_from, date_to)` | no | Defaults to `[]`. Data-quality checks — see [§ 8](#8-completeness--what-the-report-does-not-show). |
| `prepare_export(facility, result)` | **in practice yes** | The default returns the result unchanged, but that is no longer sufficient: transformation applied before export **and** before the panel display. Without a declared threshold (`summary["k_anonymity_threshold"]`), both the download **and** the panel fail closed (#2069) — see [§ 6](#6-k-anonymity--the-prepare_export-hook). |

> **Backends are process-wide singletons — no instance state.**
>
> `@register_backend` creates exactly **one** instance per slug, and it lives as long as the process. All requests and **all facilities** share it. An intermediate result cached on `self` (a tempting optimisation, because panel and download build the same dataset) would still be there in the next request of a different facility — in a multi-tenant application that is a cross-facility data leak. Everything a call needs is passed in as an argument: `facility` and the period.

`validate_completeness` deliberately receives **facility plus period rather than the finished `DatasetResult`**: the meaningful checks concern exactly what does *not* end up in the dataset (filtered-out documentation types, events without a person reference). A finished result can no longer see them. `build_dataset()` usually calls the method itself and embeds the outcome in `DatasetResult.completeness`.

### 2.2 `DatasetResult`

```python
@dataclass(frozen=True)
class DatasetResult:
    mode: Literal["rows", "aggregate"]
    records: list[dict]  # tabular projection; keys == FieldSpec.key
    summary: dict  # complete aggregate; snapshot payload
    completeness: list[CompletenessIssue]
```

**The `records`/`summary` contract is the core of the interface:**

- **`records`** is the **tabular projection** — a list of dicts whose keys correspond exactly to the `key` values from `get_field_specs()`. One row per entry, one column per `FieldSpec`. This is the CSV source.
- **`summary`** is the **complete aggregate** — a free-form dict holding the full payload (nested counters, time series, marginal totals) as it is stored in `StatisticsSnapshot.backend_data` and used for JSON/XML.

`records` is a projection **out of** `summary`, not a replacement for it: `summary` may contain information that does not make a meaningful table row. Conversely, anything that exists only in `summary` never appears in the CSV.

`mode` describes the character of the dataset and controls the panel preview: `"aggregate"` shows **all** records (there are few, condensed rows), `"rows"` shows the first 10 plus the total count.

The dataclass is `frozen`. Changes go through `dataclasses.replace(result, records=…, summary=…)`.

### 2.3 `FieldSpec`

```python
@dataclass(frozen=True)
class FieldSpec:
    key: str  # stable column/record key, ^[a-z0-9_]+$
    label: str  # display name
```

`key` is the dict key inside `records` **and** the XML element name of a field. Keys that are not valid XML element names are emitted by the XML renderer as `<entry key="…">` instead of as an element of their own — the format stays well-formed but becomes more awkward to consume. Stick to `^[a-z0-9_]+$` **and do not start with a digit**, then the case never arises: the renderer validates against `^[A-Za-z_][A-Za-z0-9_]*$` ([`export.py`](../../src/core/reporting/export.py)), so a key such as `2026_total` would fall back to `entry` despite being a valid slug.

`label` is the CSV header cell, the table heading in the panel and the `label` inside `field_specs` of the JSON export. **Declare it as `gettext_lazy`** (#1744) — exactly like `name`. German plain text would sit in the middle of translated text in the English interface; both shipped backends have been converted accordingly. The renderers cast to `str` themselves (CSV via `_sanitize_csv_cell`, JSON explicitly at the payload boundary, XML via `_xml_safe`), so you never need to cast.

**Translatable strings in a backend — the three rules:**

1. **Labels and messages:** `gettext_lazy`. Build numbered messages with [`django.utils.text.format_lazy`](https://docs.djangoproject.com/en/stable/ref/utils/#django.utils.text.format_lazy) and **named** placeholders, not with f-strings and not with `.format()` on the proxy — `.format()` evaluates the proxy **eagerly** and pins the language before rendering.
2. **What is not translated: data.** Values that end up in `summary`/`records` and are persisted or merged across months (for `snapshot_enabled` backends) stay language-independent. In the activity report those are the category names in `CATEGORY_MAP`: a locale-dependent key would make the stored snapshot depend on the language of the writing process — two months, two category names, no merge. The same rule applies to machine-readable codes (`CompletenessIssue.code`, `data_source`).
3. **K-anonymity messages stay indistinguishable — in translation too.** The placeholder and the degraded variant of a `code` must remain word-identical (see [§ 6](#6-k-anonymity--the-prepare_export-hook)). That is structurally guaranteed because both paths use the same constant; a translation must not add a digit or a reference quantity to it.

### 2.4 `CompletenessIssue`

```python
@dataclass(frozen=True)
class CompletenessIssue:
    code: str  # machine-readable, e.g. "unmapped_system_types"
    message: str  # human-readable (German)
    severity: Literal["info", "warning"] = "warning"
    count: int | None = None  # raw value behind ``message``; never serialized
```

| Field | Usage |
|---|---|
| `code` | Stable identifier. Replacement texts in `prepare_export` are matched through it. |
| `message` | What the recipient reads. Panel, JSON and XML emit it; the CSV does not. |
| `severity` | `"warning"` renders in the panel as a yellow "Hinweis" badge, `"info"` as a grey "Info" badge. |
| `count` | **Internal only.** The raw number quoted inside `message` — k-anonymity suppression needs it in machine-readable form in order to compare it against the threshold. **JSON and XML deliberately do not serialize `count`** (they carry only `code`/`message`/`severity`); otherwise the number just suppressed would be back in the artifact. |

---

## 3. Your Own Backend in Five Steps

The reference plugin [`backends/example.py`](../../src/core/reporting/backends/example.py) is meant as a copy template — it shows everything a backend needs, in the smallest possible case. The excerpts below are taken from it.

### Step 1 — Create the module and register it

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

Put the file at `src/core/reporting/backends/<name>.py`. The decorator sets `slug`, validates its format and rejects duplicate slugs with `ImproperlyConfigured`.

This is the **complete** import header of `example.py` — the excerpts in steps 3 and 4 assume it and do not repeat it.

### Step 2 — Declare the columns

```python
    def get_field_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec("document_type", _("Dokumentationstyp")),
            FieldSpec("system_type", _("System-Typ")),
            FieldSpec("count", _("Anzahl")),
        ]
```

The order is the column order in the CSV and in the panel. The labels are `gettext_lazy` — see the three i18n rules in [§ 2.3](#23-fieldspec).

### Step 3 — Build the dataset

Imports as in step 1 (`Event`, `Count`):

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

Three points that are easy to miss:

- **Always filter by `facility`** (`for_facility`). Backends run in the request context with RLS, but also inside the snapshot command; do not rely on the database layer alone.
- **`is_deleted=False`**, otherwise soft-deleted events are counted.
- **Aggregate instead of iterating.** `.values(...).annotate(Count(...))` returns one row per group in **one** query; a Python loop over events becomes unusably slow for year-long periods.

#### If your report maps documentation types onto categories

The binding is **declared, not hand-coded** (#1824): one `TypBindung` per report, from which resolution, SQL filter, availability and the key vocabulary are derived. The rule (#1849) is: **one key per level** -- `DocumentType.report_key` on the type, `FieldTemplate.report_key` on the field ([ADR-042](../adr/042-report-key-berichtszuordnung.md), German). There is no slug secondary key (#1741); `category` is not a key and does not exist.

```python
from core.reporting.base import Anforderung, BindungsEintrag, FeldBindung, TypBindung

TYP_BINDUNG = TypBindung(
    (
        BindungsEintrag("contact", _("Kontakt"), ziel="Kontakte", slugs=("kontakt",)),
        BindungsEintrag("counseling", _("Beratungsgespräch"), ziel="Beratung", slugs=("beratungsgespraech",)),
    )
)
# Optional, if your report reads field values:
FELD_DAUER = FeldBindung((BindungsEintrag("dauer_minuten", _("Minuten"), slugs=("dauer",)),))


@register_backend("my-report")
class MyBackend(DatasetBackend):
    typ_bindung = TYP_BINDUNG
    feld_bindungen = (FELD_DAUER,)
    # Only set this if the report would be pointless without a matching
    # configuration — `is_available` is derived from it (empty = always offered).
    verfuegbarkeit = (Anforderung(TYP_BINDUNG, (FELD_DAUER,)),)
```

What you get from it — and must **never** write next to it:

| Derivation | used for |
|---|---|
| `TYP_BINDUNG.resolve(report_key)` / `.resolve_row(row, "document_type__")` | the resolution in `build_dataset` |
| `TYP_BINDUNG.values_fields("document_type__")` | the key columns of your `.values()` grouping |
| `TYP_BINDUNG.matching_q(prefix)` / `.unmapped_q(prefix)` | the SQL filter and its exact complement |
| `TYP_BINDUNG.ziele()` | your output order (value-independent, K5) |
| `verfuegbarkeit` | `is_available()` — no hand-built query chain any more |

**Consumer API outside the backend** (E2, #1848): `resolve()` returns the `ziel` — that is not enough for "which report key is this?" (config UI, inventory command), because several keys can fall onto the same `ziel` (`crisis`/`counseling` → "Beratung"). For that there is a dedicated path back to the key:

| Derivation | used for |
|---|---|
| `TYP_BINDUNG.resolve_key(report_key)` / `.resolve_key_row(row, prefix)` | the same match as `resolve()`, but as a **key** instead of a `ziel` — the basis of `describe_type_bindings()` in [`registry.py`](../../src/core/reporting/registry.py) and of the `inventory_report_bindings` command |
| `FELD_BINDUNG.key_for_template(field_template)` | field template → field report key, the same role at the field level (`describe_field_bindings()`); reads the declaration's first stage |
| `FELD_BINDUNG.leseschluessel(facility)` | the `data_json` **read path** per documentation type — binding goes through the key, reading through the slug of the template bound that way |

The `inventory_report_bindings` maintenance command uses the same information as “Appears in”: available reports remain listed for matching type keys, including deactivated or locally incomplete types whose events are included by the counting logic; independent groups require the respective group to be configured. The field template section places templates in `bound` when an available report reads their values through its actual read path, including incomplete or deactivated types; unassigned, inactive, or unread templates appear in `unbound`. The inventory describes mappings, not event counts or totals.

Consumers that want to answer "does this type/field template appear in a report?" build on these two — **not** on their own copy of `resolve()`.

- **`key` vs. `ziel` vs. `label` — three different things.** `key` is the report key (the value in `report_key`; for a behaviour report, the `system_type` value). `ziel` is what `resolve()` returns: **backend-specific**, and for `snapshot_enabled` reports a **persisted** key — German plain text, never `gettext_lazy` (otherwise the stored data depends on the language of the writing process, see [§ 2](#2-api-reference)). Without `ziel` a key resolves to itself. `label` is the **translated name of the key** and is canonical facility-wide: if two reports declare the same key with different labels, the registry raises `ImproperlyConfigured`.
- **One key, no second path** (#1849, decision E-E). Only `report_key` decides: set and declared ⇒ match, anything else ⇒ no match. The former slug secondary key was invisible (you had to work it out from the slug) and produced a disclosure leak in #1785 — a ban type with the slug `kontakt` pulled its events into the report. The repair rule that came with it ("a set first-stage key wins including its exclusions") is gone too: there is nothing left to lock out.
- **`bindet="system_type"` only for behaviour reports.** `ban-overview` reports on entry bans and stays bound to the system identifier for good — the deviation belongs in the declaration, not in a comment.
- **At most one field template per (type, field key)** (decision E-J). At type level multiple assignment is the whole point; at field level the backend reads **values per event**, so two templates with the same key would mean double-counted minutes. Validated at the form, the service and the package loader; if the duplicate binding arises anyway, the backend counts **neither** field and reports `duplicate_field_binding`.
- **`category` never.** It is pure UI grouping, freely editable — regrouping in the config UI must never move report figures ([ADR-034](../adr/034-config-ui-category-system-type.md)).
- **Keep the declaration conservative.** Only add what unambiguously belongs to a category. The key `excluded` is reserved ("explicitly belongs in no report") and must not be declared — the registry raises `ImproperlyConfigured` otherwise.
- **The unmapped filter must give the same answer — that is what `unmapped_q()` is for.** `validate_completeness` counts "no report category" in the database; the predicate is the exact negation of `matching_q()`. Do **not** write it by hand: two truths here mean the notice counts events as missing that are in fact in the report — and that is exactly the pattern the #1785 disclosure leak lived in. A guard (`src/tests/reporting/test_reporting_bindung.py`) forbids hand-built key predicates inside `backends/` and checks across the whole value universe that the Python and SQL views agree.
- **"Unmapped" is not "excluded".** The completeness notice reports only types that make **no** statement at all (`report_key` empty/`NULL`). A set key is a complete statement — including the sentinel `excluded` (#1819). A set but registry-wide **unknown** key gets its own code `unknown_report_keys` (decision E-H): "I did map it, nothing just happens" calls for a different reaction than "I said nothing".
- **`is_active` belongs in the `Anforderung`, never in the data filter.** "Does the facility offer this?" is the configuration question; "what happened back then?" is the data filter. A switch flipped today must not rewrite a past reporting period.
- **Two field templates = two `FeldBindung`s.** Across a multi-valued relation Django creates a separate JOIN per `filter()` call; only separate bindings check "both fields on the **same** type". A single binding with two slugs would be an `IN`, and therefore an OR.
- **No backfill for snapshots.** Extending the declaration takes effect from the next `build_dataset` onwards; stored `backend_data` stays as it was (see [§ 5](#5-snapshot-integration)).

### Step 4 — Guarantee k-anonymity

As soon as the report can be downloaded, `prepare_export` is **required reading**, not an optional extra. Details in [§ 6](#6-k-anonymity--the-prepare_export-hook); the minimal case from `example.py` (imports as in step 1, `dataclasses` included):

```python
# Declaration for the central residual guard: ``total`` is the sum of all
# row ``count``s, so ``records`` is the exact partition of that marginal
# total. ``ORDER_INPUT``: the query sorts by ``document_type__name`` — the
# row position is already value-independent (K5).
_EXPORT_BREAKDOWN = _Breakdown("records", "total", ORDER_INPUT)


def prepare_export(self, facility, result: DatasetResult) -> DatasetResult:
    records, summary = suppress_single_margin(
        facility, result, breakdown=self._EXPORT_BREAKDOWN, field_specs=self.get_field_specs()
    )
    return dataclasses.replace(result, records=records, summary=summary)
```

For backends with **exactly one** marginal total (`summary["total"]`) and **one** breakdown that partitions it exactly, `suppress_single_margin` encapsulates all k-anonymity steps: determine the threshold, check the marginal total against R14 (a marginal total < k is itself a small cell), suppress the rows via `_suppress_small`, run `apply_residual_guard` as the **final** step, and project back onto the declared `FieldSpec` keys. The function sets `summary["k_anonymity_threshold"]`; the caller sets `completeness` itself. Backends with several related metrics or with no marginal total at all build the suppression themselves, following `SampleReport1Backend.prepare_export` — there is deliberately no second base class for this.

What happens behind it matters for understanding:

- `_suppress_small` takes `margin` and `order` as **keyword-only parameters without a default** — a call without them raises `TypeError` (fail-closed): `margin` states whether a published marginal total exists for the feasibility audit to work against, `order` names the canonical output order of suppressed rows.
- `apply_residual_guard` is the **final** step and is mandatory for every `prepare_export` with a published marginal total (the architecture guard in `test_architecture_guards_services.py` enforces it): it runs the recipient inference and, if necessary, drops the marginal total or the whole breakdown. A home-grown rule "if a cell is dropped, drop `total` too" would be a second, blunter suppression logic beside the central one, exactly what the architecture guard forbids (#1770).
- Projecting back onto the declared `FieldSpec` keys is not cosmetic: `_suppress_small` attaches an internal `suppressed` flag to every row, which would otherwise end up unfiltered in CSV, JSON and XML.

### Step 5 — Write tests

See [§ 10](#10-test-checklist-for-new-backends). After that there is nothing left to do — no registration entry, no URL, no migration.

---

## 4. Discovery Rules

- **Placing the file is enough.** On the first call to `get_backends()`/`get_backend()`/`get_available_backends()`, [`registry._discover()`](../../src/core/reporting/registry.py) imports every module under `core.reporting.backends` exactly once (`pkgutil.iter_modules`). A module-level flag makes the run idempotent.
- **Registration happens at import time — exclusively via the decorator.** There is no configuration list a backend would additionally have to be added to.
- **No new URLs, views or authorization entries.** The generic endpoints `statistics/reports/panel/` and `statistics/reports/export/` select the backend via `?backend=<slug>` and therefore cover every backend. Both are already gated to `LEAD_PLUS`; the export is rate-limited and audited (`AuditLog.Action.EXPORT` with `detail={"backend", "format", …}`).
- **Discovery never runs at module import time** — neither when `registry.py` is imported nor in `AppConfig.ready()`. That keeps the import chain free of cycles even though backends import `core.models`. The reverse direction (`from core.reporting.registry import register_backend`) is always safe.
- **A broken module is a loud failure.** If the import fails, it is re-raised as `ImproperlyConfigured` including the module name, and the discovery flag is reset — a silently incomplete registry would be worse than a crash.
- **A slug is effectively immutable.** It appears in query parameters, file names and — for `snapshot_enabled` backends — in the keys of `StatisticsSnapshot.backend_data`. Changing it requires a data migration.

**Check visibility:**

```bash
python src/manage.py shell -c "from core.reporting.registry import get_backends; print([b.slug for b in get_backends()])"
```

### Inventory: the bundled example backends

Every module under [`src/core/reporting/backends/`](../../src/core/reporting/backends/) is a **template**, not a binding reporting standard. Each one has a different shape — when writing your own backend, first find the case closest to yours and copy **that** module. All seven from #1754 carry `snapshot_enabled = False` and tie their `is_available` to the seed/package configuration.

| Slug | Reference character |
|---|---|
| [`example`](../../src/core/reporting/backends/example.py) | The minimal case: one metric per row plus a margin total (`mode="rows"`). The entry point for everything else. |
| [`sample-report-1`](../../src/core/reporting/backends/sample_report_1.py) | The full case: several interrelated metrics, a completeness block, `snapshot_enabled = True`. |
| [`monthly-trend`](../../src/core/reporting/backends/monthly_trend.py) | **Time series + imported binding.** Shows that the activity report's `TypBindung` is imported and never rebuilt — and documents the refinement relationship to the activity report (K4a, see [§ 6](#6-k-anonymity--the-prepare_export-hook)). |
| [`client-metrics`](../../src/core/reporting/backends/client_metrics.py) | **Exact person partition + margin doctrine.** Pure person counts (DISTINCT over `Event.client`) and a breakdown that partitions its margin total exactly — the strictest class in the k-anonymity model. |
| [`case-metrics`](../../src/core/reporting/backends/case_metrics.py) | **Independent scalars without a margin total.** The opposite pole: no figure partitions another, each stands on its own at the k threshold. Shows how little machinery this common shape needs. |
| [`ban-overview`](../../src/core/reporting/backends/ban_overview.py) | **Highly sensitive special case.** Facility bans are deliberately not part of the activity report; this report is an internal analysis path by product decision. Rows are emitted data-independently — otherwise their very existence would be an assertion. |
| [`needle-exchange-balance`](../../src/core/reporting/backends/needle_exchange_balance.py) | **Field-value sums.** The first backend that reads `Event.data_json` instead of counting events. The base pattern for any analysis over field values (slug addressing, `safe_decrypt`, the sum rule). |
| [`counseling-hours`](../../src/core/reporting/backends/counseling_hours.py) | **Row → total cascade.** Field-value sums with a breakdown: one row per category, made of an event count and a minute total. When a row falls, that cascades into the grand total. |
| [`service-distribution`](../../src/core/reporting/backends/service_distribution.py) | **Multiple selection.** Two field groups side by side: a `select` (exact partition) and a `multi_select` (does **not** partition). The case where the margin-total question flips — see [§ 6](#6-k-anonymity--the-prepare_export-hook). |

---

## 5. Snapshot Integration

For every backend with `snapshot_enabled = True`, `create_or_update_snapshot(facility, year, month)` writes the **raw** `build_dataset(...).summary` into the map `StatisticsSnapshot.backend_data` (key = slug). `prepare_export` deliberately does **not** run there — the snapshot is the internal raw record.

The loop iterates over `get_backends()`, **not** over `get_available_backends(facility)`: a snapshot-capable backend is therefore persisted even when its `is_available(facility)` returns `False`. `is_available` controls only the visibility in the report list and the 404 response of panel and export — it is not a switch for data storage. Do not rely on it if you really want to disable a backend for individual facilities.

> **`snapshot_enabled` is a data-protection contract, not a feature switch.**
>
> The `summary` of a snapshot-capable backend must be **purely aggregated**: no pseudonyms, no per-person rows, no free text. The reason lies outside the backend — `backend_data` is classified **wholesale** as non-PII in the PII residue sweep (`src/tests/_residue_expectations.py`), and that classification does not inspect the contents per backend. A backend that cannot keep the promise stays at `snapshot_enabled = False`; otherwise personal information travels past the classification into the snapshot.
>
> `mode="rows"` backends are therefore usually **not** snapshot-capable: row-level data is intended for permanent storage neither in terms of size nor in terms of data protection.

**`backend_data` is the only source.** The sample activity report's aggregate lives in `backend_data`, like that of every other backend; there is no dedicated model field (#1740). Write and read paths only know `backend_data`.

**Merge boundary.** The hybrid read path assembles periods from monthly snapshots and therefore has to merge the monthly aggregates. That merge is **specific to the activity report** (`_merge_sample_report_1_stats`) and knows exactly its structure. For a second snapshot-capable backend there is **no** generic merge today: without per-field merge semantics (sum? maximum? set union?) it cannot be defined. Anyone who wants to make a second aggregate backend snapshot-capable must plan the merge as work of its own — until then it reads live via `build_dataset`.

**Mandatory hook `summarize_events` for deleted events.** The retention run (`enforce_retention`) deletes events and carries their counts into the monthly aggregate in the same transaction, so that the numbers of past months stay stable. Every backend with `snapshot_enabled = True` must therefore implement `summarize_events(self, events) -> dict`: it returns the `summary` over an **explicit** event set, in the same shape as `build_dataset(...).summary`. The backend additionally needs an entry in `_SUMMARY_MERGERS` in [`snapshot.py`](../../src/core/services/dashboard/snapshot.py) that merges monthly summaries. If the hook is missing (the base class raises `NotImplementedError`) or the summarising raises, **every carry-over fails**: the deletion of that event is rolled back, the error is logged, and the next run retries — until then the event is kept instead of vanishing uncounted. A snapshot-capable backend without this hook therefore blocks the retention deadlines of its facilities. A plugin that provides a snapshot-capable backend must also register a merger. If it is missing, the carry-over can fail (`KeyError` at the missing entry), even the first one into an empty monthly row; the deletion is then rolled back and the next run retries. A backend without a snapshot (`snapshot_enabled = False`) is not affected.

**A broken backend is skipped — and loudly marked** (#2070). The snapshot run isolates every backend call: if a backend raises, its slug lands together with the name of the exception class in the reserved key `backend_data["_errors"]` (`{slug: "OperationalError"}`), the error is logged with a full traceback (and thereby reported to error tracking), and the remaining backends plus the already computed dashboard aggregates are stored normally. The key only appears in the error case and cannot collide with any slug — `registry._SLUG_RE` forbids a leading underscore. No model field, no migration: `backend_data` is a free `JSONField`.

> **Why the job does not abort fail-loud (#2070).** If a single raising backend aborted the whole run, the goal — a silently incomplete snapshot would be a quiet data hole that only surfaces months later — would be met, but the remedy would be too coarse: the abort would hit not only the broken backend but the already computed aggregates of that facility, **all following months** (`ensure_snapshots_for_months`) and, in the cron command, **all following facilities** — including the last-run marker, which would then never be written at all. Because the cron runs monthly while event retention keeps running, that would be a data-loss window and not merely a delay. The data hole is therefore **not quiet**: a marker in the snapshot row plus a log/Sentry entry. For the other maintenance jobs (`enforce_retention`, `verify_audit_chain`, `cleanup_orphan_storage_files`) fail-loud is the house rule — this job is the reasoned exception, not a precedent.

**Query cap.** Every backend call of the snapshot run happens under a transaction-local `statement_timeout` — see [§ 9](#9-boundaries).

**Live read path vs. hybrid read path.** The panel preview and the generic export **always** call `build_dataset(facility, date_from, date_to)` — the live query against the events. The activity report **PDF**, by contrast, reads the same report as a hybrid via the monthly snapshots (`get_sample_report_1_statistics_hybrid`). Two artifacts of the same report over the same period can therefore differ systematically:

- **`unique_clients` across several months.** The hybrid path does not sum the monthly values (#1770, K4b) — people are not additive, and an inflated sum would bypass the k check (3 people × 6 months ⇒ "18"). Instead it computes a dedicated **live DISTINCT count over the whole period** for that one term and declares its diverging source in a footnote. For periods already cleaned up by retention, it therefore comes out lower than the other, snapshot-fed figures in the same PDF — that is deliberate and made visible by the footnote.
- **Periods beyond the retention deadlines.** Snapshots deliberately outlive event retention; the live query filters out soft-deleted events (`is_deleted=False`). For a period whose events have already been deleted, the PDF returns the snapshot figures whereas the panel and CSV/JSON/XML return (close to) zero, or suppression markers.

This is not a peculiarity of the plugin system: the same class of divergence has always existed between the live external report and the hybrid PDF — M5 adds a third instance of the same pattern.

**Decision: the divergence stays, the source is declared** (#1743, product decision of 2026-08-03). The read paths are **not** unified — both are intentional (the snapshot outlives the retention deadlines, the live query counts more accurately across the whole period). Instead **every artifact declares its own source**. The texts and the stable codes live centrally in [`core/reporting/data_source.py`](../../src/core/reporting/data_source.py):

| Artifact | Code | Declaration inside the artifact |
|---|---|---|
| Report panel (preview) | `live` | visible line "Datenquelle: Live-Abfrage zum Erstellungszeitpunkt — …" |
| CSV export | `live` | metadata line, segments `\| data_source: live \| Datenquelle: …` **before** the k-anonymity note (Refs #1765: stable code AND translated label, like JSON/XML) |
| JSON export | `live` | fields `data_source` (code) + `data_source_note` (translated short form) |
| XML export | `live` | root attributes `data_source` + `data_source_note` |
| External report (HTML + `?format=json`) | `live` | `metadata.data_source` / `metadata.data_source_note`, plus a header line and an explanatory sentence in the HTML |
| Activity report PDF | `snapshot_hybrid` | footer line "Datenquelle: Monats-Aggregate — …"; **exception** `unique_clients`: dedicated `live` term with a footnote on the value (K4b, #1770) |

`data_source` is deliberately a **stable, language-independent code** — it must never change with the interface language, otherwise a consumer would have to parse German prose. The human-readable variants (`data_source_note`, explanatory sentence) are translatable.

Unifying the paths later remains possible but is not planned; it would additionally require `completeness` in the snapshot, which is not stored there today — otherwise a hybrid-fed export would silently report "no gaps".

---

## 6. K-Anonymity — the `prepare_export` Hook

**Repository invariant** (#1278, security findings R4/R14): every externally circulating report artifact suppresses small cell counts. Switching the format must never bypass that hardening.

| Path | `prepare_export`? | View |
|---|---|---|
| Download CSV / JSON / XML | **yes** — `export_dataset()` calls it before *every* renderer and then **enforces** the declared threshold | suppressed |
| Reports panel (HTMX preview) | **yes** — the view calls `prepare_export(facility, build_dataset(...))` and **enforces** the threshold in the same way | suppressed (parity with the download) |
| Snapshot | no — `build_dataset` directly | raw |
| Activity report PDF | no — separate path, suppressed in its own right (#1278) | suppressed |

The "View" column says nothing about the **source**: panel and download read live via `build_dataset`, the activity report PDF reads a hybrid of monthly snapshots. Suppression applies on every path, yet the underlying figures can still differ — see [§ 5](#5-snapshot-integration).

### Do I need my own override?

**Yes — and the *declaration* is enforced, not merely agreed (#2069).** As soon as the backend is registered, download and panel become available automatically; both pass the dataset through `enforce_suppression_contract()` and reject it **fail-closed** if `summary["k_anonymity_threshold"]` is missing, is not an integer (`bool` does not count) or is smaller than 1. The default `return result` is therefore no longer defensible for **any** exporting backend — not even for one whose cells cannot be small counts by construction: such a backend simply declares the threshold without suppressing anything.

> **The check validates the declaration, not the effect.** It makes sure your backend *states* the group size below which it suppressed — not that it actually did so. A `prepare_export` that sets `stamp_threshold(summary, 5)` and leaves a cell with the value 1 in place passes with HTTP 200. This cannot be checked generically at the export boundary: `records` and `summary` are shaped differently per backend, and a blanket small-number scan would be false-positive or false-negative depending on the schema. **The effect itself remains a matter for your backend's own tests** (see the checklist in [§ 10](#10-test-checklist-for-new-backends)) and for the central suppression machinery — the runtime check closes exactly one gap: the silent absence of any suppression. It replaces no backend test whatsoever.

The reason for the runtime check: the AST guard `test_jedes_backend_mit_randsumme_ruft_den_residual_guard` cannot see a backend **without** an override at all — it produces no `prepare_export` node for the guard to inspect. In addition, `TestJedesBackendUeberschreibtPrepareExport` statically verifies that every class under `core/reporting/backends/` with a direct `DatasetBackend` base defines the method — in its **own class body**. By convention that directory holds only concrete backends with a local override: no abstract intermediate classes, and no suppression inherited from a mixin. For a security-relevant hook the suppression should be visible in the file that builds the report, not at the end of an inheritance chain.

The threshold *k* comes per facility from `Settings.k_anonymity_threshold` (default 5) and is read via `_get_threshold(facility)` from [`core.services.dashboard.external_report`](../../src/core/services/dashboard/external_report.py) — the same module that provides `_suppress_small` and `suppress_sample_report_1_stats` for the PDF path and the external report. `_suppress_small` additionally establishes a **value-independent output order** (#1767): suppressed rows move to the end of the list as a block in canonical order, so that the position does not reveal which cell is the smallest. Set `summary["k_anonymity_threshold"] = threshold` if your backend suppresses: the CSV renderer then appends a note to the metadata line, and the panel shows the suppression notice.

### Minimal example — `ExampleBackend`

One metric per row plus a marginal total. Exactly one back-calculation risk has to be solved: `total = Σ counts`. If `total` stayed raw after a row was suppressed, the suppressed cell would be exactly determinable as the difference from the visible rows. Therefore:

- `_suppress_small(rows, threshold, margin=…, order=…)` sets cells `< k` to `count=None`/`suppressed=True` and performs **secondary suppression** in the same step. It does not hinge on the *number* of cells (A6.1: "exactly one suppressed cell") but on the measured **feasible span** (`_audit_feasible_span`, #1770): as long as only a single value remains possible for a suppressed cell, the **smallest** visible cell is dropped as well — and the calculation is redone, up to the fixpoint. The goal is a span ≥ 2, i.e. no exact pin.
- `total` is dropped if it is **itself** below the threshold (R14). Beyond that, `apply_residual_guard` decides: it checks the published partition against the inference model and drops first the marginal total, if necessary the whole breakdown. The old blanket rule "one row suppressed ⇒ drop `total`" is gone; it suppressed more than necessary and still missed the cases the span audit finds.

### Full example — `SampleReport1Backend`

Several interrelated metrics plus completeness notices. Cell suppression alone is not enough here:

1. **Report body:** the same machinery as in the PDF (`suppress_sample_report_1_stats`) — category and age-group cells `< k` are dropped including secondary suppression, `unique_clients` and the marginal total `total < k` become `None`. `records` is re-projected from the **already suppressed** rows; without that step the CSV would have kept showing the raw numbers.
2. **The k threshold checks person-derived cells against DISTINCT person counts** (#1770, K0-b): event counts alone do not suffice, since an age cell "Under 18: 7" could come from a single person. `build_dataset` therefore emits an additional `persons` value per age-group row (the period-scoped DISTINCT person count of the bucket), and `_suppress_small` suppresses as soon as the **stricter** of the two numbers falls below the threshold. `persons` is purely a suppression input and is stripped from every row before output — otherwise the person count itself would be an uncontrolled small cell inside the artefact. If it is missing (a snapshot without this value that could not be reconstructed) it counts as *unknown* and the cell is dropped.
3. **Person-derived breakdowns are additionally dropped as a whole** as soon as `unique_clients < k` (#1768). This guard remains in place alongside (2): the cell criterion only nulls values, whereas `_drop_person_derived_breakdowns` removes `by_age_cluster` (and, in the statistics report, `by_contact_stage`) from the artefact entirely — key gone, because a nulled row would still assert "this age group occurs". Event-derived aggregates remain untouched.
4. **Completeness notices are data-independent** (#1769): **both** notices always appear in the export and **never carry a number**. The problem was not how large a stated number is but that it was stated at all — a quantified notice implicitly publishes "my complement is ≥ k", i.e. a lower bound, and the decision about it ran over a disjunction on raw data and was therefore informative in itself. The block is therefore built from fixed codes rather than derived from `result.completeness`. The notice about the gap always remains — a report that conceals gaps would be misleadingly complete; conversely the notice also stands when there is no gap at all (if the two cases were distinguishable, the mere shape of the block would reveal the raw data again).

**Remember:** what makes a backend dangerous are **relationships between metrics** — by construction, for instance, `total + unmapped_events = Σ by_age_cluster + events_without_client` (both sides are "all visible events in the period"). If three terms are known, the fourth is exactly determined and the suppression would run empty. Since (3) and (4), both notice terms are hidden by default and Σ age groups disappears entirely for small person counts — never fewer than two unknowns remain, without any data-dependent post-correction. A `rows` backend with *one* metric per row has no such identity; the pattern from `example.py` is sufficient there. Anyone emitting several interrelated metrics should examine the relationships between them **as a whole**, not pairwise.

### Sums over field values — count the contributing events, not the sum

A backend that adds up `Event.data_json` (`needle-exchange-balance`, `counseling-hours`) publishes a **sum**, not a case count. The k threshold therefore applies to a different quantity: **a sum is suppressed when the number of contributing events is `< k`** — not when the sum itself is small. The reason is the downward inference: if "Total issued: 340" stands next to two contributing events, the individual value is all but determined, and an individual value is one person's datum. A large sum from few events is thus more dangerous than a small sum from many.

Three points follow, and they belong together:

- **The event count itself also passes through the threshold** (R14) — otherwise it would be exactly the small cell the sum was meant to protect.
- **With breakdowns this cascades** (`counseling-hours`): when a category row falls, its contribution also drops out of every grand total it feeds — otherwise the suppressed row would be recoverable as a difference.
- **Events without a usable numeric value are in no sum.** That belongs in the artefact as a data-independent (unquantified) completeness notice; otherwise the recipient reads a partial sum as a grand total.

### Multiple selection: `margin=None` is not an all-clear

A `multi_select` field **does not partition** — one event can carry several options, so only `Σ option counts ≥ margin total` holds. Reading that as "nothing can be inferred here" was the mistake in the first version of `service-distribution`. The correct treatment is a two-step:

1. **`margin=None` passed to `_suppress_small`** — because a declared margin total that does not partition exactly would be *worse* than none: the feasibility audit and the fixed-point guard compute `residual = margin total − Σ visible cells`, and that residual is simply wrong under multiple selection. `margin=None` deliberately switches both off.
2. **And precisely for that reason the group needs its own, named rule:** the inequality still holds and is itself a channel. It yields a **lower** bound `x ≥ margin total − Σ visible cells` for every suppressed cell, while primary suppression yields the **upper** bound `x ≤ k − 1`. When the two coincide, the cell is exactly determined (with *k* = 5, a visible cell of 6 and a published margin total of 10, the suppressed cell is necessarily 4). **The margin total therefore falls as soon as *one* cell of the group is suppressed** — without it there is no lower bound left, and `1 ≤ x ≤ k − 1` remains.

**Remember:** "does not partition" does not mean "harmless", it means "the central machinery is not responsible". `margin=None` on its own does **not** protect; it merely switches off the protection that would have computed the wrong thing here.

### K4a — the guarantee holds per artefact, not across artefacts

**Rule for new backends:** A dimension already published by another backend may be output only with the same suppression decision, including secondary suppression and whole-table removal. Reuse the shared function for that dimension; an independent threshold check is insufficient. Each registered backend declares its related counting dimensions through `disclosure_dimensions`; the disclosure overview derives relationships for identical and overlapping periods from these declarations. The event family comprises `general-key-figures`, `quantity-sum`, `example`, `sample-report-1`, `monthly-trend`, `counseling-hours`, `needle-exchange-balance`, `ban-overview` and `service-distribution`, including category refinements and subsets. `client-metrics` shares the person dimension with `general-key-figures`; `case-metrics` counts cases and is outside the event family. These warning relationships equate neither populations nor units. The quantity report respects type and activity-report category suppression together; numerical suppression has not been unified for the other relationships below.

Everything in this section reasons **within one report**. Two reports for the same facility and the same period can unlock each other without either of them breaking a rule:

- **Refinement.** `monthly-trend` decomposes exactly the category figures of the activity report by month. A category cell suppressed there is summable from the visible monthly rows — and vice versa. This holds even when *neither* report suppressed a single cell: the finer resolution is itself the gain.
- **Difference.** `service-distribution` counts contact events **with** a selected value, `counseling-hours` counts **all** contact events. The difference — contacts without a service entry — appears in neither report and passes through no suppression rule.
- **Reproduction.** The category rows of `counseling-hours` (`contact`/`counseling`/`crisis`) reproduce the categories of the activity report — same maps, same base set, same count measure, only refined by the minutes column. This also lifts the **secondary** suppression there: a category that falls in the activity report only so that the primarily suppressed neighbouring cell cannot be back-calculated shows its event count here (which passed its own k-check). No small case count becomes visible, but a correct suppression rationale loses its effect — secondary suppression is table-local by construction.
- **Reporting date.** `ban-overview` (`aktiv_aktuell`) and `case-metrics` (`offene_faelle_aktuell`) each carry one figure tied to the **creation day**, not to the reporting period. Two releases of the same period at different points in time differ in exactly this figure; the difference is the net change between the two days and passes through no suppression rule. The metrics stay (maintainer decision 2026-08-05) — both artefacts carry a `CompletenessIssue` naming the snapshot for it.

For these other relationships, the **existing definitional boundary** remains: there is no cell an artefact-local rule could attach to, and a cross-artefact guard would have to know which reports went to the same recipient — knowledge that does not exist in the generation path. Relationships between these event reports are now covered by the warning family. As a flanking measure, the **release overview** (`ReportReleaseLogView`, [`core/services/dashboard/release_log.py`](../../src/core/services/dashboard/release_log.py)) makes overlapping releases visible so the facility can decide.

> **Obligation for imitators** (from [`monthly_trend.py`](../../src/core/reporting/backends/monthly_trend.py)): anyone who breaks an existing population down more finely is building a refinement. Three questions — **the same maps? the same base set (filters, period semantics)? the same counted quantity?** Three times "yes" and it is one, and the relationship belongs **in the module docstring**. It is visible nowhere else, neither in the code nor in the artefact, and release practice can only take into account what somebody wrote down.

### Boundary behaviour you need to know

- **`prepare_export` is not idempotent.** The hook expects the **raw** `build_dataset` result (`SampleReport1Backend`, for instance, converts `by_category` from tuples into dict rows). Calling it twice is not a meaningful call; transform exactly once.
- **An empty period looks like suppression.** `0 < k`, so an honest zero is suppressed as well. That is the behaviour of the shared suppression function and the safe direction, but it can confuse recipients.
- **Over-suppression is priced in.** The completeness notices stand there unquantified even when there is no gap at all, and the age distribution disappears completely for just a few persons — both are intended (the alternative would be a data-dependent branch, i.e. exactly the channel that was closed). The direction is safe, the loss of informative value is real.
- **Suppression never belongs in `build_dataset`.** Otherwise snapshots would be trimmed too — and with them the basis of every later analysis and of the unchanged PDF path.
- **The error path shows nothing, not "something".** The chain `build_dataset` → `prepare_export` → `enforce_suppression_contract` is **atomic**: either it runs completely and delivers the suppressed view, or there is an error card or an error response **without a single data row** (#2070). `SuppressionContractError` is explicitly **not** a special case here — it is handled like any other backend error. An intermediate state (rendering the raw `build_dataset` result because `prepare_export` failed) would be exactly the regression that [ADR-038 § 5](../adr/038-reporting-plugin-backends.md) rules out.

---

## 7. Export Formats

[`export_dataset(backend, facility, date_from, date_to, format="csv")`](../../src/core/reporting/export.py) is the only public entry point. It builds the dataset, calls `prepare_export` and hands off to exactly one renderer. An unknown format raises `ValueError`.

`SUPPORTED_FORMATS` (`frozenset({"csv", "json", "xml"})`) is the single source of truth for callers that want to check beforehand themselves — which is exactly what `DatasetExportView` does, instead of blindly catching the internal `ValueError` (an overly broad `except ValueError` would also mask renderer errors as HTTP 400).

All three formats go through `safe_download_response` (RFC 5987 file name, `X-Content-Type-Options: nosniff`, `Cache-Control: no-store`). The file name always follows `f"{backend.slug}_{date_from}_{date_to}.{format}"`.

| Format | Content type | Streaming | Contents |
|---|---|---|---|
| `csv` | `text/csv; charset=utf-8` | **yes** (generator) | metadata line, header from `FieldSpec.label`, then `records` |
| `json` | `application/json` | no | complete payload including `summary` and `completeness` |
| `xml` | `application/xml; charset=utf-8` | no | the same payload as an element tree |

### CSV

```
# Report: <slug> | <facility> | <date_from>–<date_to> | <generated_at> | data_source: live | Data source: Live query at the time of creation | population: all_events | Population: all events in the period | k-anonymity: cells < k suppressed
Category,Count
Kontakte,42
```

- The metadata line **always** carries the k-anonymity note: `summary["k_anonymity_threshold"]` is a precondition for exporting at all (#2069), and `export.py` appends the note unconditionally. Without it the recipient would see empty cells with no explanation — unlike JSON, XML and the panel, the CSV carries no completeness notices.
- Refs #1765: the `data_source: live` segment is the stable, language-independent code (parity with JSON/XML) — a parser should match on it, never on the translated label `Datenquelle: …`. Under the EN locale that reads `data_source: live | Data source: Live query at the time of creation`.
- Refs #1770 (K3-A3): the same pattern for the **population** — `population: <code>` is the stable code from `DatasetBackend.population`, `Grundgesamtheit: …` the translated label. A report that does not say *what* it counts is not only leaky but misleading: several tables over different base sets add up to different numbers.
- **Every** cell, including metadata and header, passes through `_sanitize_csv_cell`, the existing formula-injection guard from `core.services.system.export` (OWASP CSV injection, #719). Deliberate reuse instead of a second implementation.
- Completeness notices deliberately do **not** appear in the CSV: it stays machine-readable. Use JSON, XML or the panel if you need them.
- Cells are read via `record.get(spec.key)` — a missing key is an empty cell, not an error.

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

Serialized with `DjangoJSONEncoder`, `ensure_ascii=False`, `indent=2`. `completeness` carries **only** `code`/`message`/`severity` — `count` stays internal. `population`/`population_note` come from `DatasetBackend.population` (default `all_events`); a backend that counts more narrowly overrides the attribute and the declaration follows everywhere.

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

Generated with the standard library (`xml.etree.ElementTree`), **generating only**: foreign XML is never parsed, so the XXE / billion-laughs attack surface of `ElementTree` does not apply here, and `defusedxml` (which hardens parsing only) would be a dependency without effect.

`summary` is mapped recursively: dict → one child element per key, list → `<item>` children, scalar → text. Keys that are not valid XML element names (typically month keys such as `"2026-01"`) are rendered as `<entry key="2026-01">`.

---

## 8. Completeness — What the Report Does *Not* Show

`validate_completeness` quantifies gaps that the dataset itself cannot make visible. Without those notices, a number that is too low would look plausibly "complete".

Examples from the activity report:

| `code` | `severity` | Reports |
|---|---|---|
| `unmapped_system_types` | `warning` | Events whose documentation type carries **no** report mapping at all (`report_key` empty/`NULL`). They are missing from the report entirely. The code name predates `report_key` and **stays** — it is a machine interface (JSON/XML, imported by `monthly-trend`); renaming it would break every consumer parsing on it. |
| `unknown_report_keys` | `warning` | Events whose documentation type carries a `report_key` that **no** backend (any longer) knows — foreign package, removed backend, typo (#1849, decision E-H). |
| `duplicate_field_binding` | `warning` | At least one documentation type carries **several** field templates with the same field report key. The backend then counts neither of them (fail-safe, decision E-J). |
| `events_without_client` | `info` | Events without a person reference — they count towards the categories but are missing from age groups and the number of persons. |

**Severity semantics:** `warning` = data missing from the report (numbers are too low). `info` = data included but unevenly distributed (partial analyses differ). The panel renders `warning` as a yellow "Hinweis" badge and `info` in grey.

**Conventions:**

- Report gaps **> 0** only. A missing notice means "this gap does not exist" — the suppression logic relies on that.
- A quantified notice carries its number **additionally** in `count`; otherwise `prepare_export` cannot transform it and has to suppress it entirely when in doubt.
- Pull all metrics of a check in **one** aggregation if possible. `build_dataset` calls `validate_completeness` along with it, and the hybrid snapshot path calls `build_dataset` once per monthly segment — individual `count()` calls multiply there.
- Provide a **low-digit** wording for the export version (without reference quantity, without percentage) as well as a **digit-free** replacement text for the suppression case. If both are missing, the number is dropped from the export as a precaution.
- Message texts are **translatable** (`gettext_lazy`, or `format_lazy` with named placeholders for numbered variants) — the rules are in [§ 2.3](#23-fieldspec).

---

## 9. Boundaries

- **No generic monthly merge** for snapshot-capable aggregate backends (see [§ 5](#5-snapshot-integration)).
- **Error isolation per path, not per backend set** (#2070). A broken backend only takes itself out of service. What that means per path:

  | Path | Behaviour with a raising backend |
  |---|---|
  | Statistics overview (KPIs, charts) | loads unchanged; a backend whose `is_available` raises counts as **not available** and disappears from the card list (fail-closed) |
  | Report panel (tab **and** HTMX partial) | HTTP **200** with an error card instead of the preview — 200, because HTMX only swaps 2xx; a 5xx response would leave the old preview in place |
  | Download CSV / JSON / XML | HTTP **502**, terse text, **no** `Content-Disposition`, not a single byte of the dataset — and **no** `EXPORT` audit entry: a failed export must not appear as a release in the append-only audit |
  | Activity report PDF (`SampleReport1ExportView`) | HTTP **502** like the generic download — this path does not go through `export_dataset` but through the hybrid read path `generate_sample_report_1_pdf` → `get_sample_report_1_statistics_hybrid`, which calls `build_dataset` itself in the live fallback |
  | Snapshot run | backend skipped, marker in `backend_data["_errors"]`, the rest is stored (see [§ 5](#5-snapshot-integration)) |

  Every caught case is logged via `logger.exception` including the traceback and reaches error tracking (GlitchTip/Sentry) that way. The error card names the affected report but no exception details.

  **For the export catch to actually take effect, everything backend-owned is evaluated eagerly.** The CSV renderer returns a generator whose body only runs while streaming — that is, after status 200 and `Content-Disposition` have been sent. An error there would produce a **truncated file** instead of a clear error (no k-anonymity leak: suppression has run by then, but a broken promise). Evaluated before the response is built are therefore: `get_field_specs()`, the `FieldSpec` values `label`/`key`, `backend.slug`/`backend.population` in the metadata line — and `DatasetResult.records`, which `export_dataset` resolves right behind the suppression bracket and checks against the mapping contract (`_materialize_records`). **The contract is: every record is a `Mapping`** — the renderers project via `record.get(key)`. A lazy iterable is not an error here: it is consumed in full (`list(...)`) and never streams through to the renderer; if it raises while being consumed, the download ends with 502 instead of half a file. Only a non-mapping is rejected, with a `TypeError` carrying slug, position and type — never the record contents. The declared type stays `records: list[dict]`, and that remains the expectation: an *infinite* iterable would hang at this point. Only the chunking of finished, repo-owned strings by the `csv.writer` stays lazy — that is the actual streaming benefit; `build_dataset` builds the records in full anyway.
- **Query cap per backend call.** Panel, export and snapshot run wrap the backend call in `core.reporting.db_guard.reporting_statement_timeout` — a transaction-local `SET LOCAL statement_timeout` (`set_config(..., is_local=true)`, a no-op outside PostgreSQL). The value comes from the setting `REPORTING_BACKEND_STATEMENT_TIMEOUT_MS` (default `10000` ms, overridable via the env var of the same name, `0` disables it). Deliberately **transaction**-local and not session-wide: with `CONN_MAX_AGE > 0` Django hands connections on between requests, so a session-wide `SET` would stay on the reused connection. The cancelled statement reaches the callers as `django.db.utils.OperationalError` and is isolated like any other backend error — no special case. The cap bounds the runtime of **one statement**, not the total duration of a backend call made up of many small queries.
- **JSON and XML are not streamed.** Aggregate payloads are small; a `rows` backend with mass data (tens of thousands of records) would build both renderers entirely in memory. That is the documented limit of the current pipeline — such a backend would need streaming renderers analogous to the CSV path, or it has to restrict its period.
- **No external plugin packages.** Discovery scans `core/reporting/backends/` only; `entry_points` from third-party packages are not provided for. A foreign package with read access to social-services data is a trust decision of its own kind (signing, approval, sandboxing) — conceivable as a later extension, and then with a security concept of its own.
- **No generic PDF pipeline.** The pipeline formats are CSV, JSON and XML; the activity report PDF remains a separate path of its own (its own view, the hybrid read path, its own suppression via `suppress_sample_report_1_stats`). It does share its **error isolation** with the generic download (#2070) — see the table above.
- **`core/reporting` lies outside the mypy strict zone and outside the mutmut paths.** The code is annotated strict-compatibly; adding it to those gates changes build configuration and is an open maintainer decision.
- **`report_key` affects dataset reports only.** The statistics dashboard is not a `DatasetBackend`: key figures, trend charts and the distributions by documentation type and age group are built in [`core/services/dashboard/statistics.py`](../../src/core/services/dashboard/statistics.py). Its `get_statistics()` function uses `event_queryset_for_period()`, which restricts data only by facility, period, `is_deleted` and the optional UI `document_type` filter. For fully covered past calendar months -- and only when no `document_type` filter is set -- the overview reads the same aggregates from `StatisticsSnapshot.data` instead of calculating them live (`get_statistics_hybrid()`, and `get_statistics_trend()` for the chart API, both in [`core/services/dashboard/snapshot.py`](../../src/core/services/dashboard/snapshot.py); see [§ 5](#5-snapshot-integration)). **None of these paths reads `report_key`.** Within one snapshot row, the boundary runs exactly between `data`, which contains the key-independent dashboard aggregates, and `backend_data`, which contains the raw `summary` values of backends that bind through `report_key`. This is a decision, not an oversight: the overview is the population and the reports are projections of it. That is also why the dashboard names each table's population (`ALL_EVENTS` for documentation types, `EVENTS_WITH_PERSON` for age groups; K3-A3 from #1770). Adding a `report_key` filter to one of these paths would make the dashboard and dataset reports operate over different sets -- related to the divergence that the documentation-type filter from #1804 prevents within the dashboard aggregates.
- **The mapping is hard-coded.** The key → report row mapping (`CATEGORY_MAP`) lives in the backend module; there is **no** configuration field for it. Only **which key** a type or field carries is configurable (#1849) — not what a key maps to ([ADR-042](../adr/042-report-key-berichtszuordnung.md), German). That is a decision, not a transitional state ([ADR-038](../adr/038-reporting-plugin-backends.md), update of 2026-08-04): a config UI would make report figures movable by a click. Changing a mapping is therefore a code change with review — and `DocumentType.category` is not used for reporting and does not exist ([ADR-040](../adr/040-documenttype-category-entfernt.md), German).
- **The `system_type` value list is frozen** (RFC #1748, decision of 2026-08-03) — and reduced to **two** values as of #1850: `ban` and `crisis`, the two that carry application behaviour (ban logic, crisis highlight, `ban-overview`). **No new values** — an additional report category is no reason to extend a core data-model enum; that is what `report_key` is for. A freeze guard pins the list (`src/tests/test_system_type.py`).

  The full track from concept #1847 is thereby complete: declarative binding (#1824), visibility (#1848), the `report_key` introduction (#1849, [ADR-042](../adr/042-report-key-berichtszuordnung.md)) and the reduction (#1850). The report mapping no longer runs through `system_type`/`slug` at all. A package that still carries one of the seven former values remains loadable: the value is dropped with a warning in the load report.


---

## 10. Test Checklist for New Backends

Test files live in the family sub-package `src/tests/reporting/` and follow the pattern `src/tests/reporting/test_reporting_<slug>.py`; the family `conftest.py` there isolates the registry per test automatically (`_isolated_registry`, autouse) — new files no longer need their own isolation fixture. The existing suites (`test_reporting_base.py`, `test_reporting_registry.py`, `test_reporting_export.py`, `test_reporting_sample_report_1.py`, `test_reporting_example.py`, `test_reporting_views.py`) serve as templates.

**Registry isolation (mandatory — otherwise you get order-dependent failures).** Ad-hoc test backends must not leak into subsequent tests on the same worker. Patch the module directly, no proxy:

```python
@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    registry._discover()  # real discovery ONCE beforehand
    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))
    monkeypatch.setattr(registry, "_discovered", True)
```

The real discovery has to run **before** the patch: `@register_backend` is an import-time side effect and fires per module only on the *first* import. If discovery ran against a test dict for the first time, the real backends would land there only, would be gone after teardown — and since the modules are then in `sys.modules`, the decorator would never run again: the real registry would stay empty for the rest of the process.

If you want the expected values to be independent of how many backends are really registered, isolate with an **empty** dict (`{}`) instead of a copy.

**Checklist:**

| Check | Why |
|---|---|
| `[b.slug for b in get_backends()]` contains the new slug | Discovery works without an extra entry |
| `build_dataset` against fixture data: `records`, `summary`, `mode` | Domain correctness |
| Every `record` key corresponds to a `FieldSpec.key` | Otherwise CSV columns are silently missing |
| Empty period → empty `records`, no crash | The most common edge case |
| Foreign facility → no rows | Facility filter in the queryset |
| Soft-deleted events are not counted | `is_deleted` filter |
| **Suppression:** cell `< k` → `None`; marginal total dropped per the backend's rule | K-anonymity invariant |
| **Fail-closed:** a dataset without `summary["k_anonymity_threshold"]` raises `SuppressionContractError` in both export **and** panel | Enforced k-anonymity invariant (#2069) |
| **Secondary suppression:** exactly one suppressed cell → a second one is dropped | A6.1 back-calculation |
| **No internal flag in the export** (`suppressed`, `count`) | Confirms the projection onto `FieldSpec` keys |
| `export_dataset` in all three formats: 200, content type, file name | Format dispatch |
| CSV injection: a `=SUM(...)` cell is prefixed | Formula-injection guard |
| XML well-formed (`ET.fromstring` on your own output) | Invalid element names surface |
| Panel view: 200, backend name, preview rows; `?backend=nope` → 404 | Generic endpoints |
| **The panel shows suppressed values** (not the raw ones) | Panel/export parity — the actual leak candidate |
| With `snapshot_enabled = True`: `backend_data[slug]` populated **and purely aggregated** | Snapshot contract |

Before committing: `make verify-fast` (lint, checks, type check, unit tests). For UI-relevant changes, additionally the E2E run described in the [E2E runbook](../e2e-runbook.md).

---

## 11. Related Documents

- [ADR-038](../adr/038-reporting-plugin-backends.md) — design decisions, alternatives, consequences
- [ADR-042](../adr/042-report-key-berichtszuordnung.md) — `report_key` as a separate report-assignment axis; supersedes the **key choice** in [ADR-038 § 4](../adr/038-reporting-plugin-backends.md), whose core principle ("report figures cannot be moved by a click") remains in force. Its *Scope boundaries* section also explains why an unknown key is reported at the type level **only**, with deliberately no field-level equivalent of `unknown_report_keys`.
- [ADR-023](../adr/023-k-anonymization-statistik.md) — k-anonymization for external statistics
- [ADR-034](../adr/034-config-ui-category-system-type.md) — `category` vs. `system_type`
- [ADR-040](../adr/040-documenttype-category-entfernt.md) — `category` removed
- [ADR-037](../adr/037-konfigurationspakete-dynamische-felder.md) — configuration packages, `DocumentType.slug`
- [admin-guide.md § 2.5](admin-guide.md#25-configure-documentation-types) — the same assignment from the configuration UI perspective: report keys on documentation types and field templates, their states, one-time nature and repair path (add later yes, change no). Keep it in sync when changing the code rules.
- [user-guide.md § 7](user-guide.md#7-statistics-and-export) — using the reports section from an end-user perspective
- [packages.md](../packages.md) — configuration packages (source of the documentation types that reports evaluate)

<!-- translation-source: docs/reporting-plugins.md -->
<!-- translation-version: v0.99.8 -->
<!-- translation-date: 2026-10-11 -->
<!-- source-hash: 49c47860c576f70d02442da588c16e665f98bebf -->
