"""Raw additional count populations and their artifact-local contracts.

Collectors read scoped sources once. Interval preparation is pure and never
calls a backend's build or export hooks. Person cohorts use actual ID unions.
"""

from dataclasses import dataclass, replace
from datetime import date

from core.reporting.base import DatasetResult, FieldSpec
from core.services.dashboard.shared_disclosure import CountKey, RawCount, TemporalPopulation, population_units


@dataclass(frozen=True)
class BoundDataset:
    """Raw/local dataset and stable identities for its public count fields."""

    raw: DatasetResult
    local: DatasetResult
    row_keys: tuple[CountKey, ...]
    count_key: str
    summary_keys: dict[str, CountKey]

    def observations(self):
        """Import local numeric and row-existence decisions before solving."""
        records = self.local.records
        dropped = bool(self.raw.records) and not records
        for index, (row, key) in enumerate(zip(self.raw.records, self.row_keys, strict=True)):
            published = records[index][self.count_key] if not dropped else None
            yield RawCount(
                key,
                row[self.count_key],
                hidden=published is None,
                lower=0 if dropped or "kennzahl" in row else 1,
            )
        for field, key in self.summary_keys.items():
            yield RawCount(key, self.raw.summary[field], hidden=self.local.summary[field] is None)

    def dropped(self):
        """A missing local table withholds its row identities in every alias."""
        return self.row_keys if self.raw.records and not self.local.records else ()

    def project(self, resolve, mask):
        """Project the final mask, honoring table removal and stricter locals."""
        keys = [resolve(key.dimension, key.population) for key in self.row_keys]
        if any(key is None or key in mask.dropped for key in keys):
            records = []
        else:
            records = [
                {**row, self.count_key: mask.published[key] if row[self.count_key] is not None else None}
                for row, key in zip(self.local.records, keys, strict=False)
            ]
        summary = dict(self.local.summary)
        for field, identity in self.summary_keys.items():
            key = resolve(identity.dimension, identity.population)
            summary[field] = mask.published[key] if key is not None and summary[field] is not None else None
        return replace(self.local, records=records, summary=summary)


def collect_additional_populations(facility):
    """Read admission dates and case dates/stock with independent identities."""
    from collections import defaultdict

    from django.db.models.functions import TruncDate

    from core.models import Case, Client

    members = defaultdict(set)
    members[("person_counts", "admissions")] = set()
    members[("person_counts", "case-affected-persons")] = set()
    for identity in ("created-cases", "closed-cases", "current-open-cases"):
        members[("case_counts", identity)] = set()
    clients = (
        Client.objects.for_facility(facility)
        .filter(is_deleted=False)
        .annotate(day=TruncDate("created_at"))
        .values_list("pk", "day")
    )
    for pk, day in clients.iterator(chunk_size=1000):
        members[("person_counts", "admissions")].add((str(pk), day))
    cases = (
        Case.objects.for_facility(facility)
        .filter(is_deleted=False)
        .annotate(created_day=TruncDate("created_at"), closed_day=TruncDate("closed_at"))
        .values("pk", "client_id", "status", "created_day", "closed_day")
    )
    for row in cases.iterator(chunk_size=1000):
        unit, person = str(row["pk"]), str(row["client_id"])
        members[("case_counts", "created-cases")].add((unit, row["created_day"]))
        members[("person_counts", "case-affected-persons")].add((person, row["created_day"]))
        if row["closed_day"] is not None:
            members[("case_counts", "closed-cases")].add((unit, row["closed_day"]))
            members[("person_counts", "case-affected-persons")].add((person, row["closed_day"]))
        if row["status"] == Case.Status.OPEN:
            members[("case_counts", "current-open-cases")].add((unit, date.min))
    return tuple(
        TemporalPopulation(dimension, identity, tuple(sorted(units)), point_in_time=identity == "current-open-cases")
        for (dimension, identity), units in sorted(members.items())
    )


def prepare_additional_interval(populations, start, end, threshold):
    """Apply existing raw contracts for monthly, person and case consumers."""
    from core.services.dashboard.external_report import (
        ORDER_INPUT,
        _Breakdown,
        _protect_client_dataset,
        _protect_metric_dataset,
        _protect_single_margin,
    )

    members = {(p.dimension, p.identity): population_units(p, start, end) for p in populations}

    def size(identity, dimension="event_counts"):
        return len(members.get((dimension, identity), ()))

    def key(identity, dimension="event_counts"):
        return CountKey(dimension, identity, start, end)

    monthly = sorted(
        (identity.removeprefix("monthly-category:").split(":", 1), len(units), identity)
        for (dimension, identity), units in members.items()
        if dimension == "event_counts" and identity.startswith("monthly-category:") and units
    )
    raw_monthly = DatasetResult(
        "rows",
        [{"monat": month, "kategorie": category, "count": value} for (month, category), value, _ in monthly],
        {"total": size("mapped-events")},
        [],
    )
    records, summary = _protect_single_margin(
        raw_monthly,
        threshold,
        breakdown=_Breakdown("records", "total", ORDER_INPUT),
        field_specs=[FieldSpec(field, field) for field in ("monat", "kategorie", "count")],
    )
    monthly_binding = BoundDataset(
        raw_monthly,
        replace(raw_monthly, records=records, summary=summary),
        tuple(key(identity) for _, _, identity in monthly),
        "count",
        {"total": key("mapped-events")},
    )
    ages = sorted(
        (identity.removeprefix("age:"), len(units))
        for (dimension, identity), units in members.items()
        if dimension == "person_counts" and identity.startswith("age:") and units
    )
    raw_client = DatasetResult(
        "aggregate",
        [{"alterscluster": age, "personen": value} for age, value in ages],
        {
            "aktive_personen": size("active-persons", "person_counts"),
            "neuaufnahmen": size("admissions", "person_counts"),
        },
        [],
    )
    client_binding = BoundDataset(
        raw_client,
        _protect_client_dataset(raw_client, threshold),
        tuple(key(f"age:{age}", "person_counts") for age, _ in ages),
        "personen",
        {"aktive_personen": key("active-persons", "person_counts"), "neuaufnahmen": key("admissions", "person_counts")},
    )
    case_keys = {
        "neue_faelle": key("created-cases", "case_counts"),
        "geschlossene_faelle": key("closed-cases", "case_counts"),
        "offene_faelle_aktuell": key("current-open-cases", "case_counts"),
        "betroffene_personen": key("case-affected-persons", "person_counts"),
    }
    values = {field: size(identity.population, identity.dimension) for field, identity in case_keys.items()}
    raw_case = DatasetResult(
        "aggregate", [{"kennzahl": field, "wert": value} for field, value in values.items()], values, []
    )
    case_binding = BoundDataset(
        raw_case,
        _protect_metric_dataset(raw_case, threshold, count_key="wert"),
        tuple(case_keys.values()),
        "wert",
        case_keys,
    )
    return {"monthly-trend": monthly_binding, "client-metrics": client_binding, "case-metrics": case_binding}
