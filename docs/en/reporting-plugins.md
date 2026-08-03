> This is the English translation of [reporting-plugins.md](../reporting-plugins.md).
> The German version is the authoritative source.

# Reporting Plugins (Report Backends)

A **report backend** encapsulates the domain logic of one reporting standard — "contacts by category", "events per documentation type", a regional funding-body form — behind a fixed interface. The panel preview, the CSV/JSON/XML export and the snapshot storage talk to that interface only and know nothing about the individual report.

The practical consequence: **a new report is a single file.** A module under [`src/core/reporting/backends/`](../../src/core/reporting/backends/) containing a `DatasetBackend` subclass and the `@register_backend` decorator automatically appears in the reports dropdown of the statistics page and in all three export formats. No migration, no URL pattern, no view, no entry in the authorization matrix.

- **Why the system looks the way it does:** [ADR-038](../adr/038-reporting-plugin-backends.md).
- **Why small cell counts are suppressed:** [ADR-023](../adr/023-k-anonymization-statistik.md) and #1278.
- **Why the mapping uses `system_type` and never `category`:** [ADR-034](../adr/034-config-ui-category-system-type.md).

> **The most important rule up front.** Everything a backend returns from `build_dataset()` is **raw**. Everything that leaves the building (download **and** panel preview) passes through `prepare_export()` first. If you produce numbers that circulate externally, you must implement that hook — see [§ 6](#6-k-anonymity--the-prepare_export-hook).

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
    slug: str = ""                  # set and validated by @register_backend
    name: str = ""                  # display name in the dropdown (gettext_lazy-capable)
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
| `slug` || Set by the decorator. Do **not** assign it yourself. Format `^[a-z0-9]+(?:[-_][a-z0-9]+)*$` (lowercase letters/digits, single `-`/`_` as separators, never at the edges). It appears in query parameters, file names and `backend_data` keys, which makes it effectively immutable — changing it afterwards requires a data migration (pattern: [`0123`](../../src/core/migrations/0123_rename_backend_data_key_sample_report_1.py)). |
| `name` | **yes** | Visible name in the reports dropdown and in the JSON payload. Declare it as `gettext_lazy`; the export pipeline casts to `str` at the payload boundary itself. |
| `snapshot_enabled` | no | Defaults to `False`. Set it to `True` only if `summary` is **purely aggregated** — see [§ 5](#5-snapshot-integration). |
| `is_available(facility)` | no | Defaults to `True`. Hook for facility-dependent reports. `False` — like any other falsy return value, for instance the `None` of a nullable `BooleanField` — hides the backend from the dropdown **and** makes panel and export respond with HTTP 404. The dropdown filter and the access guard check the same truthiness. |
| `get_field_specs()` | **yes** | Column definition of the tabular projection. The order is the column order in the CSV. |
| `build_dataset(facility, date_from, date_to)` | **yes** | Builds the dataset for the **inclusive** period `[date_from, date_to]`. The result is **raw** (unsuppressed). |
| `validate_completeness(facility, date_from, date_to)` | no | Defaults to `[]`. Data-quality checks — see [§ 8](#8-completeness--what-the-report-does-not-show). |
| `prepare_export(facility, result)` | no | Defaults to returning the result unchanged. Transformation applied before export **and** before the panel display — see [§ 6](#6-k-anonymity--the-prepare_export-hook). |

> **Backends are process-wide singletons — no instance state.**
>
> `@register_backend` creates exactly **one** instance per slug, and it lives as long as the process. All requests and **all facilities** share it. An intermediate result cached on `self` (a tempting optimisation, because panel and download build the same dataset) would still be there in the next request of a different facility — in a multi-tenant application that is a cross-facility data leak. Everything a call needs is passed in as an argument: `facility` and the period.

`validate_completeness` deliberately receives **facility plus period rather than the finished `DatasetResult`**: the meaningful checks concern exactly what does *not* end up in the dataset (filtered-out documentation types, events without a person reference). A finished result can no longer see them. `build_dataset()` usually calls the method itself and embeds the outcome in `DatasetResult.completeness`.

### 2.2 `DatasetResult`

```python
@dataclass(frozen=True)
class DatasetResult:
    mode: Literal["rows", "aggregate"]
    records: list[dict]                   # tabular projection; keys == FieldSpec.key
    summary: dict                         # complete aggregate; snapshot payload
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
    key: str    # stable column/record key, ^[a-z0-9_]+$
    label: str  # display name
```

`key` is the dict key inside `records` **and** the XML element name of a field. Keys that are not valid XML element names are emitted by the XML renderer as `<entry key="…">` instead of as an element of their own — the format stays well-formed but becomes more awkward to consume. Stick to `^[a-z0-9_]+$` **and do not start with a digit**, then the case never arises: the renderer validates against `^[A-Za-z_][A-Za-z0-9_]*$` ([`export.py`](../../src/core/reporting/export.py)), so a key such as `2026_total` would fall back to `entry` despite being a valid slug.

`label` is the CSV header cell and the table heading in the panel. **German plain text or `gettext_lazy`** are both allowed — unlike for `name`, the contract demands nothing here. Both shipped backends currently use plain text, so the table headings ("Kategorie", "Anzahl", …) stay German in the English interface as well. Full i18n of the backend strings — labels **and** completeness messages — is follow-up work (#1744).

### 2.4 `CompletenessIssue`

```python
@dataclass(frozen=True)
class CompletenessIssue:
    code: str                                        # machine-readable, e.g. "unmapped_system_types"
    message: str                                     # human-readable (German)
    severity: Literal["info", "warning"] = "warning"
    count: int | None = None                         # raw value behind ``message``; never serialized
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

from core.models import DocumentType, Event
from core.reporting.base import DatasetBackend, DatasetResult, FieldSpec
from core.reporting.registry import register_backend
from core.services.dashboard.external_report import _get_threshold, _suppress_small


@register_backend("example")
class ExampleBackend(DatasetBackend):
    """Ereignisse je Dokumenttyp im Zeitraum — kleinstmoegliches Referenz-Backend."""

    name = _("Beispiel: Ereignisse je Dokumenttyp")
    snapshot_enabled = False
```

Put the file at `src/core/reporting/backends/<name>.py`. The decorator sets `slug`, validates its format and rejects duplicate slugs with `ImproperlyConfigured`.

This is the **complete** import header of `example.py` — the excerpts in steps 3 and 4 assume it and do not repeat it.

### Step 2 — Declare the columns

```python
    def get_field_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec("document_type", "Dokumenttyp"),
            FieldSpec("category", "Kategorie"),
            FieldSpec("system_type", "System-Typ"),
            FieldSpec("count", "Anzahl"),
        ]
```

The order is the column order in the CSV and in the panel.

### Step 3 — Build the dataset

Imports as in step 1 (`Event`, `DocumentType`, `Count`):

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

Three points that are easy to miss:

- **Always filter by `facility`.** Backends run in the request context with RLS, but also inside the snapshot command; do not rely on the database layer alone.
- **`is_deleted=False`**, otherwise soft-deleted events are counted.
- **Aggregate instead of iterating.** `.values(...).annotate(Count(...))` returns one row per group in **one** query; a Python loop over events becomes unusably slow for year-long periods.

### Step 4 — Guarantee k-anonymity

As soon as the report can be downloaded, `prepare_export` is **required reading**, not an optional extra. Details in [§ 6](#6-k-anonymity--the-prepare_export-hook); the minimal case from `example.py` (imports as in step 1 — `dataclasses`, `_get_threshold`, `_suppress_small`):

```python
    def prepare_export(self, facility, result: DatasetResult) -> DatasetResult:
        threshold = _get_threshold(facility)
        suppressed_rows = _suppress_small(result.records, threshold, count_key="count")
        field_keys = [spec.key for spec in self.get_field_specs()]
        records = [{key: row[key] for key in field_keys} for row in suppressed_rows]

        total = result.summary["total"]
        any_row_suppressed = any(row["suppressed"] for row in suppressed_rows)
        drop_total = total < threshold or any_row_suppressed

        summary = dict(result.summary)
        summary["total"] = None if drop_total else total
        summary["k_anonymity_threshold"] = threshold
        return dataclasses.replace(result, records=records, summary=summary)
```

Projecting back onto the declared `FieldSpec` keys is not cosmetic: `_suppress_small` attaches an internal `suppressed` flag to every row, which would otherwise end up unfiltered in CSV, JSON and XML.

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

---

## 5. Snapshot Integration

For every backend with `snapshot_enabled = True`, `create_or_update_snapshot(facility, year, month)` writes the **raw** `build_dataset(...).summary` into the map `StatisticsSnapshot.backend_data` (key = slug). `prepare_export` deliberately does **not** run there — the snapshot is the internal raw record.

The loop iterates over `get_backends()`, **not** over `get_available_backends(facility)`: a snapshot-capable backend is therefore persisted even when its `is_available(facility)` returns `False`. `is_available` controls only the visibility in the dropdown and the 404 response of panel and export — it is not a switch for data storage. Do not rely on it if you really want to disable a backend for individual facilities.

> **`snapshot_enabled` is a data-protection contract, not a feature switch.**
>
> The `summary` of a snapshot-capable backend must be **purely aggregated**: no pseudonyms, no per-person rows, no free text. The reason lies outside the backend — `backend_data` is classified **wholesale** as non-PII in the PII residue sweep (`src/tests/_residue_expectations.py`), and that classification does not inspect the contents per backend. A backend that cannot keep the promise stays at `snapshot_enabled = False`; otherwise personal information travels past the classification into the snapshot.
>
> `mode="rows"` backends are therefore usually **not** snapshot-capable: row-level data is intended for permanent storage neither in terms of size nor in terms of data protection.

**`backend_data` is the only source.** Until M5 the sample activity report's aggregate lived in a dedicated model field that was written in parallel during a transition period (dual write). That phase is over: migration [`0122`](../../src/core/migrations/0122_backfill_backend_data.py) moved existing snapshots over additively, [`0123`](../../src/core/migrations/0123_rename_backend_data_key_sample_report_1.py) renamed the key to today's slug and [`0124`](../../src/core/migrations/0124_drop_jugendamt_residue.py) dropped the dedicated field (#1740). Write and read paths only know `backend_data` — which was always the case for **new** backends anyway.

**Merge boundary.** The hybrid read path assembles periods from monthly snapshots and therefore has to merge the monthly aggregates. That merge is **specific to the activity report** (`_merge_sample_report_1_stats`) and knows exactly its structure. For a second snapshot-capable backend there is **no** generic merge today: without per-field merge semantics (sum? maximum? set union?) it cannot be defined. Anyone who wants to make a second aggregate backend snapshot-capable must plan the merge as work of its own — until then it reads live via `build_dataset`.

**Fail loud.** The snapshot run iterates over the backends without error isolation: if a backend raises, the **entire** snapshot run for that facility fails. This is intentional — a silently incomplete snapshot would be a quiet data hole that only surfaces months later.

**Live read path vs. hybrid read path.** The panel preview and the generic export **always** call `build_dataset(facility, date_from, date_to)` — the live query against the events. The activity report **PDF**, by contrast, reads the same report as a hybrid via the monthly snapshots (`get_sample_report_1_statistics_hybrid`). Two artifacts of the same report over the same period can therefore differ systematically:

- **`unique_clients` across several months.** The hybrid path sums the monthly values (a documented approximation: anyone appearing in two months is counted twice); the live query performs a true DISTINCT count over the whole period.
- **Periods beyond the retention deadlines.** Snapshots deliberately outlive event retention; the live query filters out soft-deleted events (`is_deleted=False`). For a period whose events have already been deleted, the PDF returns the snapshot figures whereas the panel and CSV/JSON/XML return (close to) zero, or suppression markers.

This is not a peculiarity of the plugin system: the same class of divergence has always existed between the live external report and the hybrid PDF — M5 adds a third instance of the same pattern.

**Decision: the divergence stays, the source is declared** (#1743, product decision of 2026-08-03). The read paths are **not** unified — both are intentional (the snapshot outlives the retention deadlines, the live query counts more accurately across the whole period). Instead **every artifact declares its own source**. The texts and the stable codes live centrally in [`core/reporting/data_source.py`](../../src/core/reporting/data_source.py):

| Artifact | Code | Declaration inside the artifact |
|---|---|---|
| Report panel (preview) | `live` | visible line "Datenquelle: Live-Abfrage zum Erstellungszeitpunkt — …" |
| CSV export | `live` | metadata line, segment `\| Datenquelle: …` **before** the k-anonymity note |
| JSON export | `live` | fields `data_source` (code) + `data_source_note` (translated short form) |
| XML export | `live` | root attributes `data_source` + `data_source_note` |
| External report (HTML + `?format=json`) | `live` | `metadata.data_source` / `metadata.data_source_note`, plus a header line and an explanatory sentence in the HTML |
| Activity report PDF | `snapshot_hybrid` | footer line "Datenquelle: Monats-Aggregate — …" |

`data_source` is deliberately a **stable, language-independent code** — it must never change with the interface language, otherwise a consumer would have to parse German prose. The human-readable variants (`data_source_note`, explanatory sentence) are translatable.

Unifying the paths later remains possible but is not planned; it would additionally require `completeness` in the snapshot, which is not stored there today — otherwise a hybrid-fed export would silently report "no gaps".

---

## 6. K-Anonymity — the `prepare_export` Hook

**Repository invariant** (#1278, security findings R4/R14): every externally circulating report artifact suppresses small cell counts. Switching the format must never bypass that hardening.

| Path | `prepare_export`? | View |
|---|---|---|
| Download CSV / JSON / XML | **yes** — `export_dataset()` calls it before *every* renderer | suppressed |
| Reports panel (HTMX preview) | **yes** — the view calls `prepare_export(facility, build_dataset(...))` | suppressed (parity with the download) |
| Snapshot | no — `build_dataset` directly | raw |
| Activity report PDF | no — separate path, suppressed in its own right since #1278 | suppressed |

The "View" column says nothing about the **source**: panel and download read live via `build_dataset`, the activity report PDF reads a hybrid of monthly snapshots. Suppression applies on every path, yet the underlying figures can still differ — see [§ 5](#5-snapshot-integration).

### Do I need my own override?

**Yes, as soon as your backend produces numbers that leave the building** — and they do as soon as the backend is registered, because download and panel then become available automatically. The default (`return result`) is only defensible for backends whose cells cannot be small counts by construction.

The threshold *k* comes per facility from `Settings.k_anonymity_threshold` (default 5) and is read via `_get_threshold(facility)` from [`core.services.dashboard.external_report`](../../src/core/services/dashboard/external_report.py) — the same module that provides `_suppress_small` and `suppress_sample_report_1_stats` for the PDF path and the external report. Set `summary["k_anonymity_threshold"] = threshold` if your backend suppresses: the CSV renderer then appends a note to the metadata line, and the panel shows the suppression notice.

### Minimal example — `ExampleBackend`

One metric per row plus a marginal total. Exactly one back-calculation risk has to be solved: `total = Σ counts`. If `total` stayed raw after a row was suppressed, the suppressed cell would be exactly determinable as the difference from the visible rows. Therefore:

- `_suppress_small(rows, threshold, count_key="count")` sets cells `< k` to `count=None`/`suppressed=True` and performs **secondary suppression per A6.1** (#1024/#1016) in the same step: if exactly **one** cell remains suppressed afterwards, the next-smallest visible cell is suppressed as well, so that at least two unknowns remain.
- `total` is dropped if it is **itself** below the threshold (R14) **or** if any row is suppressed at all. The latter is the conservative choice: secondary suppression only protects the case of *exactly one* suppressed cell; if several stay suppressed, `total` would remain a lever for back-calculation.

### Full example — `SampleReport1Backend`

Several interrelated metrics plus completeness notices. Cell suppression alone is not enough here:

1. **Report body:** the same machinery as in the PDF (`suppress_sample_report_1_stats`) — category and age-group cells `< k` are dropped including secondary suppression, `unique_clients` and the marginal total `total < k` become `None`. `records` is re-projected from the **already suppressed** rows; without that step the CSV would have kept showing the raw numbers.
2. **Completeness notices** are **always** reworded for the export: without the reference quantity and without a percentage (the reference quantity would be the lever for complementary back-calculation). The number is dropped entirely if it is itself `< k`, if its **complement** is `< k`, or if there is no vetted low-digit wording for that `code` (fail-safe for future checks). The notice about the gap always remains — a report that conceals gaps would be misleadingly complete.
3. **Four-term identity.** By construction,

 ```
   total + unmapped_events  =  Σ by_age_cluster + events_without_client
   ```

 — both sides are "all visible events in the period". If **three** terms are known and the fourth is hidden, the fourth is exactly determined and the suppression would run empty. `_close_identity_leak` then degrades one further term, and always via the *notices*: preferably the number of a quantified notice is dropped; only when none is quantified any more is a **missing** notice replaced by a suppression hint — because `validate_completeness` only reports gaps > 0, so a missing notice deterministically means zero and is itself a known term. The placeholder text and the degradation text are **word-for-word identical**; if the wording were distinguishable, it would reveal whether the gap is > 0.

**Remember:** a `rows` backend with *one* metric per row has no such identity — the pattern from `example.py` is sufficient there. Anyone emitting several interrelated metrics or quantified notices should follow `SampleReport1Backend.prepare_export` and examine the relationships between them **as a whole**, not pairwise.

### Boundary behaviour you need to know

- **`prepare_export` is not idempotent.** The hook expects the **raw** `build_dataset` result (`SampleReport1Backend`, for instance, converts `by_category` from tuples into dict rows). Calling it twice is not a meaningful call; transform exactly once.
- **An empty period looks like suppression.** `0 < k`, so an honest zero is suppressed as well. That is the behaviour of the shared suppression function and the safe direction, but it can confuse recipients.
- **Over-suppression at the edges.** With empty age-group lists or a very small *k*, closing the identity can degrade more than strictly necessary. The direction is safe, the loss of informative value is real.
- **Suppression never belongs in `build_dataset`.** Otherwise snapshots would be trimmed too — and with them the basis of every later analysis and of the unchanged PDF path.

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
# Bericht: <slug> | <facility> | <date_from>–<date_to> | <generated_at> [| k-Anonymität: Zellen < k unterdrückt]
Kategorie,Anzahl
Kontakte,42
```

- The metadata line carries the k-anonymity note only if `summary["k_anonymity_threshold"]` is set. Without it the recipient would see empty cells with no explanation — unlike JSON, XML and the panel, the CSV carries no completeness notices.
- **Every** cell, including metadata and header, passes through `_sanitize_csv_cell`, the existing formula-injection guard from `core.services.system.export` (OWASP CSV injection, #719). Deliberate reuse instead of a second implementation.
- Completeness notices deliberately do **not** appear in the CSV: it stays machine-readable. Use JSON, XML or the panel if you need them.
- Cells are read via `record.get(spec.key)` — a missing key is an empty cell, not an error.

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

Serialized with `DjangoJSONEncoder`, `ensure_ascii=False`, `indent=2`. `completeness` carries **only** `code`/`message`/`severity` — `count` stays internal.

### XML

```xml
<dataset backend="example" date_from="2026-01-01" date_to="2026-01-31" generated_at="…" mode="rows">
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
| `unmapped_system_types` | `warning` | Events whose documentation type maps to no report category (including `system_type` NULL/empty) — they are missing from the report entirely. |
| `events_without_client` | `info` | Events without a person reference — they count towards the categories but are missing from age groups and the number of persons. |

**Severity semantics:** `warning` = data missing from the report (numbers are too low). `info` = data included but unevenly distributed (partial analyses differ). The panel renders `warning` as a yellow "Hinweis" badge and `info` in grey.

**Conventions:**

- Report gaps **> 0** only. A missing notice means "this gap does not exist" — the suppression logic relies on that.
- A quantified notice carries its number **additionally** in `count`; otherwise `prepare_export` cannot transform it and has to suppress it entirely when in doubt.
- Pull all metrics of a check in **one** aggregation if possible. `build_dataset` calls `validate_completeness` along with it, and the hybrid snapshot path calls `build_dataset` once per monthly segment — individual `count()` calls multiply there.
- Provide a **low-digit** wording for the export version (without reference quantity, without percentage) as well as a **digit-free** replacement text for the suppression case. If both are missing, the number is dropped from the export as a precaution.

---

## 9. Boundaries

- **No generic monthly merge** for snapshot-capable aggregate backends (see [§ 5](#5-snapshot-integration)).
- **JSON and XML are not streamed.** Aggregate payloads are small; a `rows` backend with mass data (tens of thousands of records) would build both renderers entirely in memory. That is the documented limit of the current pipeline — such a backend would need streaming renderers analogous to the CSV path, or it has to restrict its period.
- **No external plugin packages.** Discovery scans `core/reporting/backends/` only; `entry_points` from third-party packages are not provided for. A foreign package with read access to social-services data is a trust decision of its own kind (signing, approval, sandboxing) — conceivable as a later extension, and then with a security concept of its own.
- **No generic PDF pipeline.** The pipeline formats are CSV, JSON and XML; the activity report PDF remains a separate, unchanged path.
- **`core/reporting` lies outside the mypy strict zone and outside the mutmut paths.** The code is annotated strict-compatibly; adding it to those gates changes build configuration and is an open maintainer decision.
- **Mapping v1 uses `system_type`.** `DocumentType.slug` is the documented secondary key and follow-up work ([ADR-038](../adr/038-reporting-plugin-backends.md)); `DocumentType.category` is never used for reporting.

---

## 10. Test Checklist for New Backends

Test files follow the pattern `src/tests/test_reporting_<slug>.py`. The existing suites (`test_reporting_base.py`, `test_reporting_registry.py`, `test_reporting_export.py`, `test_reporting_sample_report_1.py`, `test_reporting_example.py`, `test_reporting_views.py`) serve as templates.

**Registry isolation (mandatory — otherwise you get order-dependent failures).** Ad-hoc test backends must not leak into subsequent tests on the same worker. Patch the module directly, no proxy:

```python
@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    registry._discover()                                              # real discovery ONCE beforehand
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
- [ADR-023](../adr/023-k-anonymization-statistik.md) — k-anonymization for external statistics
- [ADR-034](../adr/034-config-ui-category-system-type.md) — `category` vs. `system_type`
- [ADR-037](../adr/037-konfigurationspakete-dynamische-felder.md) — configuration packages, `DocumentType.slug`
- [user-guide.md § 7](user-guide.md#7-statistics-and-export) — using the reports section from an end-user perspective
- [packages.md](../packages.md) — configuration packages (source of the documentation types that reports evaluate)

<!-- translation-source: docs/reporting-plugins.md -->
<!-- translation-version: v0.23.0 -->
<!-- translation-date: 2026-08-03 -->
<!-- source-hash: 2c9f5acfc4c61ddb8f065fda0653b38b42e40553 -->
