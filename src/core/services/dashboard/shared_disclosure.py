"""Shared count masks over explicit population and calendar identities.

Raw collectors must supply the complete canonical population family for one
facility, before any renderer projects it. This module neither calls backends
nor caches requests. A mask is a decision for the supplied population graph;
it is not a publication history or a guarantee over unrelated releases.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta

from core.services.dashboard.disclosure import (
    MIN_FEASIBLE_SPAN,
    Cell,
    Partition,
    findings,
    shared_alternatives,
    shared_bounds,
)


@dataclass(frozen=True, order=True)
class CountKey:
    """Stable counting unit, population identity and inclusive date interval."""

    dimension: str
    population: str
    date_from: date
    date_to: date

    def __post_init__(self) -> None:
        if not self.dimension or not self.population or self.date_from > self.date_to:
            raise ValueError("Invalid count identity or interval")

    def token(self) -> str:
        return json.dumps((self.dimension, self.population, self.date_from.isoformat(), self.date_to.isoformat()))


@dataclass(frozen=True)
class RawCount:
    """One raw count, including any stricter artifact-local suppression.

    ``lower`` describes public row existence, not the actual secret value.
    ``hidden`` imports primary or secondary suppression from a raw
    collector's local contract. ``person_sensitive`` also protects exact pins
    above k that would disclose a small contributing-person population.
    ``drop`` imports the stronger requirement to withhold row existence.
    ``suppression_rank`` only selects which public equation is removed first;
    it never changes sensitivity or the required feasible span.
    """

    key: CountKey
    value: int
    hidden: bool = False
    lower: int = 0
    upper: int | None = None
    person_sensitive: bool = False
    drop: bool = False
    suppression_rank: int = 0

    def __post_init__(self) -> None:
        if (
            isinstance(self.value, bool)
            or not isinstance(self.value, int)
            or self.lower < 0
            or self.value < self.lower
            or (self.upper is not None and self.value > self.upper)
        ):
            raise ValueError("Invalid raw count or public bounds")


@dataclass(frozen=True)
class CountPartition:
    """An exact, disjoint partition; never sum overlapping distinct persons."""

    name: str
    margin: CountKey
    cells: tuple[CountKey, ...]


@dataclass(frozen=True)
class SharedMask:
    """Complete publication decision, including rows that must disappear.

    A key in ``dropped`` requires withholding its containing table, including
    row existence. Replacing only the number by None is insufficient there.
    ``published`` includes internal canonical atoms; it must be projected by
    consumers, never serialized as a report payload.
    """

    facility_id: int
    threshold: int
    published: dict[CountKey, int | None]
    dropped: frozenset[CountKey]


def _require_facility(facility) -> int:
    facility_id = getattr(facility, "pk", None)
    if facility_id is None:
        raise ValueError("A saved facility is required for a shared count mask")
    return facility_id


def _component(seed: set[CountKey], partitions: Sequence[CountPartition]) -> set[CountKey]:
    connected = set(seed)
    while True:
        before = len(connected)
        for partition in partitions:
            members = {partition.margin, *partition.cells}
            if connected & members:
                connected.update(members)
        if len(connected) == before:
            return connected


def protect_counts(facility, counts: Sequence[RawCount], partitions: Sequence[CountPartition]) -> SharedMask:
    """Compute one monotone primary/secondary/margin/table mask.

    Identity aliases are merged before solving. Each iteration removes one
    public equation by suppressing a visible cell. Constraints are rebuilt
    from the remaining published view, so reconstructed margins cannot evade
    the audit. Invalid raw equations are rejected rather than interpreted as
    evidence that a disclosure attack is infeasible.
    """
    from core.services.dashboard.external_report import _get_threshold

    _require_facility(facility)
    threshold = _get_threshold(facility)
    raw: dict[CountKey, RawCount] = {}
    for cell in counts:
        previous = raw.get(cell.key)
        if previous is not None:
            if previous.value != cell.value:
                raise ValueError("Conflicting raw counts for the same population")
            upper_values = [value for value in (previous.upper, cell.upper) if value is not None]
            cell = RawCount(
                cell.key,
                cell.value,
                hidden=cell.hidden or previous.hidden,
                lower=max(cell.lower, previous.lower),
                upper=min(upper_values) if upper_values else None,
                person_sensitive=cell.person_sensitive or previous.person_sensitive,
                drop=cell.drop or previous.drop,
                suppression_rank=min(cell.suppression_rank, previous.suppression_rank),
            )
        raw[cell.key] = cell
    for partition in partitions:
        if (
            partition.margin not in raw
            or any(key not in raw for key in partition.cells)
            or len(set(partition.cells)) != len(partition.cells)
            or partition.margin in partition.cells
            or any(key.dimension != partition.margin.dimension for key in partition.cells)
            or raw[partition.margin].value != sum(raw[key].value for key in partition.cells)
        ):
            raise ValueError(f"Invalid raw partition: {partition.name}")

    dropped = _component({key for key, cell in raw.items() if cell.drop}, partitions)
    hidden = {key for key, cell in raw.items() if cell.hidden or cell.value < threshold} | dropped
    tokens = {key: key.token() for key in raw}
    # Every pass either hides a visible cell or drops an entire component.
    for _ in range(len(raw) + 1):
        cells = {
            tokens[key]: Cell(
                tokens[key],
                None if key in hidden else cell.value,
                lo=cell.lower,
                hi=min(cell.upper, threshold - 1)
                if cell.value < threshold and cell.upper is not None
                else threshold - 1
                if cell.value < threshold
                else cell.upper,
            )
            for key, cell in raw.items()
        }
        active_partitions = [
            partition for partition in partitions if not ({partition.margin, *partition.cells} & dropped)
        ]
        model = [
            Partition(
                partition.name,
                tokens[partition.margin],
                cells[tokens[partition.margin]].published,
                tuple(cells[tokens[key]] for key in partition.cells),
                reveals_person_gap=any(raw[key].person_sensitive for key in partition.cells),
            )
            for partition in active_partitions
        ]
        try:
            bounds = shared_bounds(cells, model)
        except RuntimeError:
            # An incomplete audit cannot authorize publication or row existence.
            return SharedMask(facility.pk, threshold, dict.fromkeys(raw), frozenset(raw))
        local_findings = {token for _, token in findings(model, threshold=threshold)}
        alternatives = shared_alternatives(cells, model, {tokens[key]: cell.value for key, cell in raw.items()})
        unsafe = set()
        for key in hidden - dropped:
            cell = raw[key]
            lower, upper = bounds[tokens[key]]
            if cell.value == 0 or (cell.value >= threshold and not cell.person_sensitive):
                continue
            if (
                tokens[key] in local_findings
                or (upper is not None and upper - lower + 1 < MIN_FEASIBLE_SPAN)
                or len(alternatives[tokens[key]]) < MIN_FEASIBLE_SPAN
            ):
                unsafe.add(key)
        if not unsafe:
            return SharedMask(
                facility.pk,
                threshold,
                {key: None if key in hidden else cell.value for key, cell in raw.items()},
                frozenset(dropped),
            )
        connected = _component(unsafe, active_partitions)
        candidates = connected - hidden
        if candidates:
            victim = min(candidates, key=lambda key: (raw[key].suppression_rank, raw[key].value, key))
            hidden.add(victim)
        else:
            dropped.update(connected)
            hidden.update(connected)
    raise RuntimeError("Shared suppression lost monotonicity")


@dataclass(frozen=True)
class TemporalPopulation:
    """Raw unit/date memberships for one facility-scoped population.

    Unit identities are transient inputs, never publication keys. Repeated
    memberships of one person are unioned, including across calendar months.
    Supply all canonical populations together, so overlapping subsets share
    the same disjoint membership atoms.
    """

    dimension: str
    identity: str
    members: tuple[tuple[str, date], ...]
    point_in_time: bool = False


def population_units(population: TemporalPopulation, start: date, end: date) -> frozenset[str]:
    """Count actual unit unions; current stocks ignore the requested interval."""
    return frozenset(unit for unit, day in population.members if population.point_in_time or start <= day <= end)


def protect_temporal_counts(
    facility,
    populations: Sequence[TemporalPopulation],
    windows: Sequence[tuple[date, date]],
    *,
    hidden_keys: Collection[CountKey] = (),
    person_sensitive_keys: Collection[CountKey] = (),
    dropped_keys: Collection[CountKey] = (),
    observations: Sequence[RawCount] = (),
) -> SharedMask:
    """Protect canonical time/subset unions and their shared residuals.

    Membership signatures produce disjoint atoms per dimension. Each interval
    is an exact union of those atoms, rather than a sum of monthly unique
    person counts. The caller supplies a stable, request-independent family
    of canonical windows; arbitrary earlier publications are outside this
    in-memory contract.

    Local observations add public row-existence bounds and stricter masks.
    Their raw values must match the computed union counts; a projection may
    not claim zero as an alternative for a publicly listed positive row.
    """
    _require_facility(facility)
    memberships: dict[str, dict[str, dict[str, set[date]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))
    identities = set()
    current = {(p.dimension, p.identity) for p in populations if p.point_in_time}
    for population in populations:
        population_key = population.dimension, population.identity
        if population_key in identities:
            raise ValueError("Duplicate temporal population identity")
        identities.add(population_key)
        for unit, day in population.members:
            memberships[population.dimension][unit][population.identity].add(day)
    targets = {
        CountKey(dimension, identity, start, end) for dimension, identity in identities for start, end in windows
    }
    if (
        set(hidden_keys) | set(person_sensitive_keys) | set(dropped_keys) | {cell.key for cell in observations}
    ) - targets:
        raise ValueError("Unknown temporal suppression identity")
    # Group units by the populations/dates in which they occur, not by IDs.
    atoms: dict[tuple[str, str], tuple[dict[str, set[date]], int]] = {}
    for dimension, units in memberships.items():
        for membership in units.values():
            signature = json.dumps(
                [(identity, sorted(day.isoformat() for day in days)) for identity, days in sorted(membership.items())]
            )
            token = hashlib.sha256(signature.encode()).hexdigest()
            previous = atoms.get((dimension, token))
            atoms[dimension, token] = membership, 1 if previous is None else previous[1] + 1
    counts = []
    atom_memberships = {}
    for (dimension, token), (membership, value) in atoms.items():
        days = [day for dates in membership.values() for day in dates]
        atom = CountKey(dimension, f"canonical:{token}", min(days), max(days))
        counts.append(RawCount(atom, value, hidden=True))
        atom_memberships[atom] = membership
    partitions = []
    representatives: dict[tuple[str, str, tuple[CountKey, ...]], CountKey] = {}
    aliases: dict[CountKey, CountKey] = {}
    for dimension, identity in sorted(identities):
        for start, end in sorted(set(windows)):
            target = CountKey(dimension, identity, start, end)
            members = tuple(
                atom
                for atom, membership in atom_memberships.items()
                if atom.dimension == dimension
                and identity in membership
                and ((dimension, identity) in current or any(start <= day <= end for day in membership[identity]))
            )
            aliases[target] = representatives.setdefault((dimension, identity, members), target)
            value = sum(atoms[(dimension, atom.population.removeprefix("canonical:"))][1] for atom in members)
            counts.append(
                RawCount(
                    target,
                    value,
                    hidden=target in hidden_keys,
                    person_sensitive=target in person_sensitive_keys,
                    drop=target in dropped_keys,
                )
            )
            partitions.append(CountPartition(target.token(), target, members))
    merged_counts = [replace(cell, key=aliases.get(cell.key, cell.key)) for cell in [*counts, *observations]]
    merged_partitions = {
        (aliases[partition.margin], partition.cells): replace(partition, margin=aliases[partition.margin])
        for partition in partitions
    }
    mask = protect_counts(facility, merged_counts, tuple(merged_partitions.values()))
    return SharedMask(
        mask.facility_id,
        mask.threshold,
        {**mask.published, **{key: mask.published[alias] for key, alias in aliases.items()}},
        mask.dropped | frozenset(key for key, alias in aliases.items() if alias in mask.dropped),
    )


def collect_event_populations(facility, date_from: date, date_to: date) -> tuple[TemporalPopulation, ...]:
    """Collect base live event/person populations with explicit tenant scoping.

    This is the common source for totals, document types, mapped categories,
    contact stages and age groups. Field-value, admission, case and hybrid
    snapshot sources require their own collectors before joining this family.
    Event dates use the database timezone conversion used by the existing
    statistics query. No user or role enters the source.
    """
    from django.db.models import Case, CharField, Value, When
    from django.db.models.functions import TruncDate

    from core.models import Event
    from core.reporting.registry import get_backend
    from core.services.dashboard.statistics import _STAGE_ANONYM_Q, _STAGE_IDENTIFIED_Q, _STAGE_QUALIFIED_Q

    _require_facility(facility)
    sample_binding = get_backend("sample-report-1").typ_bindung
    if sample_binding is None:
        raise ValueError("The sample report requires a category binding")
    if date_from > date_to:
        raise ValueError("Invalid population collection interval")
    rows = (
        Event.objects.for_facility(facility)
        .filter(is_deleted=False, occurred_at__date__gte=date_from, occurred_at__date__lte=date_to)
        .annotate(
            day=TruncDate("occurred_at"),
            stage=Case(
                When(_STAGE_ANONYM_Q, then=Value("anonym")),
                When(_STAGE_IDENTIFIED_Q, then=Value("identifiziert")),
                When(_STAGE_QUALIFIED_Q, then=Value("qualifiziert")),
                default=Value(""),
                output_field=CharField(),
            ),
        )
        .values(
            "pk", "document_type_id", "document_type__report_key", "client_id", "client__age_cluster", "day", "stage"
        )
        .order_by("occurred_at", "pk")
    )
    populations: dict[tuple[str, str], set[tuple[str, date]]] = defaultdict(set)
    populations[("event_counts", "all-events")] = set()
    populations[("person_counts", "active-persons")] = set()
    populations[("event_counts", "mapped-events")] = set()
    populations[("event_counts", "age:__no_person__")] = set()
    for stage in ("anonym", "identifiziert", "qualifiziert"):
        populations[("event_counts", f"contact-stage:{stage}")] = set()
    for row in rows.iterator(chunk_size=1000):
        day = row["day"]
        event = str(row["pk"]), day
        populations[("event_counts", "all-events")].add(event)
        populations[("event_counts", f"document-type:{row['document_type_id']}")].add(event)
        category = sample_binding.resolve(row["document_type__report_key"])
        if category is not None:
            populations[("event_counts", "mapped-events")].add(event)
            populations[("event_counts", f"category:{category}")].add(event)
            populations[("event_counts", f"monthly-category:{day:%Y-%m}:{category}")].add(event)
        if row["stage"]:
            populations[("event_counts", f"contact-stage:{row['stage']}")].add(event)
        cluster = row["client__age_cluster"] if row["client_id"] is not None else "__no_person__"
        populations[("event_counts", f"age:{cluster}")].add(event)
        if row["client_id"] is not None:
            person = str(row["client_id"]), day
            populations[("person_counts", "active-persons")].add(person)
            if row["stage"]:
                populations[("person_counts", f"contact-stage:{row['stage']}")].add(person)
            populations[("person_counts", f"age:{cluster}")].add(person)
    return tuple(
        TemporalPopulation(dimension, identity, tuple(sorted(members, key=lambda member: (member[1], member[0]))))
        for (dimension, identity), members in sorted(populations.items())
    )


@dataclass(frozen=True)
class LiveRelease:
    """Projected general and sample figures; the mask stays internal."""

    general: dict
    sample: dict
    mask: SharedMask
    datasets: dict


@dataclass(frozen=True)
class _LocalInterval:
    date_from: date
    date_to: date
    general: dict
    sample: dict
    raw: dict
    observations: tuple[RawCount, ...]
    dropped: frozenset[CountKey]
    datasets: dict


def _raw_live_statistics(populations, types, date_from, date_to):
    """Aggregate raw unit unions, keeping person counts non-additive."""
    from core.models import Client
    from core.services.dashboard.statistics import PERSONS_KEY

    members = {(p.dimension, p.identity): population_units(p, date_from, date_to) for p in populations}

    def size(identity, dimension="event_counts"):
        return len(members.get((dimension, identity), ()))

    stages = ("anonym", "identifiziert", "qualifiziert")
    labels = dict(Client.AgeCluster.choices)
    ages = [
        {
            "cluster": identity.removeprefix("age:"),
            "count": len(units),
            "label": labels.get(identity.removeprefix("age:"), identity.removeprefix("age:")),
            PERSONS_KEY: size(identity, "person_counts"),
        }
        for (dimension, identity), units in members.items()
        if dimension == "event_counts" and identity.startswith("age:") and identity != "age:__no_person__" and units
    ]
    ages.sort(key=lambda row: -row["count"])
    raw = {
        "total_contacts": size("all-events"),
        "unique_clients": size("active-persons", "person_counts"),
        "by_document_type": [
            {"name": name, "count": size(f"document-type:{pk}")}
            for name, pk in types.items()
            if size(f"document-type:{pk}")
        ],
        "by_age_cluster": ages,
        "by_contact_stage": {stage: size(f"contact-stage:{stage}") for stage in stages},
        "persons_by_contact_stage": {
            stage: size(f"contact-stage:{stage}", "person_counts")
            + (size("age:__no_person__") if stage == "anonym" else 0)
            for stage in stages
        },
    }
    raw["by_document_type"].sort(key=lambda row: -row["count"])
    raw_sample = {
        "total": size("mapped-events"),
        "by_category": [
            (identity.removeprefix("category:"), len(units))
            for (dimension, identity), units in members.items()
            if dimension == "event_counts" and identity.startswith("category:") and units
        ],
        "by_age_cluster": ages,
        "unique_clients": raw["unique_clients"],
    }
    return members, raw, raw_sample


def _prepare_live_interval(
    facility, populations, types, type_categories, snapshots, field_sources, threshold, date_from, date_to
):
    """Import local contracts using raw aggregates, never published numbers."""
    from core.services.dashboard.external_report import _protect_external_statistics, _protect_sample_statistics

    members, raw, raw_sample = _raw_live_statistics(populations, types, date_from, date_to)
    general = _protect_external_statistics(facility, date_from, date_to, raw, threshold=threshold)
    sample = _protect_sample_statistics(raw_sample, threshold=threshold)
    hybrid_raw = _raw_hybrid_sample(populations, types, snapshots, date_from, date_to)
    hybrid = _protect_sample_statistics(hybrid_raw, threshold=threshold)
    hybrid_categories = {row["name"]: row["count"] for row in hybrid.get("by_category", [])}
    observations = []
    local_dropped = set()

    def observe(identity, value, published, *, dimension="event_counts", lower=0, drop=False, person_sensitive=False):
        key = CountKey(dimension, identity, date_from, date_to)
        if drop:
            local_dropped.add(key)
        observations.append(
            RawCount(
                key,
                value,
                hidden=published is None or drop,
                lower=0 if drop else lower,
                person_sensitive=person_sensitive and not drop,
                # A broad total is dispensable when it pins a small excluded
                # remainder. Preserve the useful mapped subtotal if possible.
                suppression_rank=-1 if identity == "all-events" else 0,
            )
        )

    observe("all-events", raw["total_contacts"], general["total_contacts"])
    observe("mapped-events", raw_sample["total"], sample["total"])
    observe("active-persons", raw["unique_clients"], general["unique_clients"], dimension="person_counts")
    observe("active-persons", raw["unique_clients"], sample["unique_clients"], dimension="person_counts")
    for table, original, protected, prefix, label in (
        ("by_document_type", raw["by_document_type"], general.get("by_document_type", []), "document-type:", "name"),
        ("by_age_cluster", raw["by_age_cluster"], general.get("by_age_cluster", []), "age:", "cluster"),
        ("by_age_cluster", raw["by_age_cluster"], sample.get("by_age_cluster", []), "age:", "cluster"),
    ):
        published = {row[label]: row["count"] for row in protected}
        for row in original:
            identity = f"{prefix}{types[row[label]] if table == 'by_document_type' else row[label]}"
            observe(
                identity,
                row["count"],
                published.get(row[label]),
                lower=1,
                drop=not protected,
                person_sensitive=table == "by_age_cluster",
            )
    for row in general.get("by_age_cluster", []):
        if row["cluster"] == "__no_person__":
            value = len(members.get(("event_counts", "age:__no_person__"), ()))
            observe("age:__no_person__", value, row["count"])
    for stage, value in raw["by_contact_stage"].items():
        observe(
            f"contact-stage:{stage}",
            value,
            general.get("by_contact_stage", {}).get(stage),
            person_sensitive=True,
            drop="by_contact_stage" not in general,
        )
    published_categories = {row["name"]: row["count"] for row in sample.get("by_category", [])}
    for category, value in raw_sample["by_category"]:
        observe(
            f"category:{category}", value, published_categories.get(category), lower=1, drop="by_category" not in sample
        )
    # Preserve the existing hybrid-PDF safeguard as a raw input to the shared
    # decision. Snapshot and live counts are distinct sources, never aliases.
    for row in raw["by_document_type"]:
        type_id = types[row["name"]]
        category = type_categories[type_id]
        if category is not None and hybrid_categories.get(category) is None:
            observe(f"document-type:{type_id}", row["count"], None, lower=1)
    from core.services.dashboard.shared_field_sources import prepare_field_interval
    from core.services.dashboard.shared_sources import prepare_additional_interval

    datasets = prepare_additional_interval(populations, date_from, date_to, threshold)
    datasets.update(prepare_field_interval(field_sources, populations, date_from, date_to, threshold, general))
    for binding in datasets.values():
        observations.extend(binding.observations())
        local_dropped.update(binding.dropped())
    return _LocalInterval(
        date_from, date_to, general, sample, raw, tuple(observations), frozenset(local_dropped), datasets
    )


def _raw_hybrid_sample(populations, types, snapshots, date_from, date_to):
    """Reuse snapshot segment semantics without any backend invocation."""
    from core.reporting.data_source import LIVE
    from core.services.dashboard.snapshot import (
        SNAPSHOT_AS_OF_KEY,
        _merge_sample_report_1_stats,
        _newer,
        _split_into_segments,
    )

    segments = []
    as_of = None
    for start, end, eligible in _split_into_segments(date_from, date_to):
        snapshot = snapshots.get((start.year, start.month)) if eligible else None
        raw = snapshot["backend_data"].get("sample-report-1") if snapshot else None
        if raw:
            as_of = _newer(as_of, snapshot["updated_at"])
        else:
            raw = _raw_live_statistics(populations, types, start, end)[2]
        segments.append(raw)
    merged = _merge_sample_report_1_stats(segments)
    merged[SNAPSHOT_AS_OF_KEY] = as_of
    merged["unique_clients"] = _raw_live_statistics(populations, types, date_from, date_to)[1]["unique_clients"]
    merged["unique_clients_data_source"] = LIVE
    return merged


def canonical_live_windows(populations: Sequence[TemporalPopulation]) -> tuple[tuple[date, date], ...]:
    """All contiguous calendar-month unions over the current live population.

    Empty months introduce no new union. The family depends on facility data,
    never on the requested interval, report, format or role. It includes all
    prefixes, overlapping month unions, cross-year unions and the standard
    rolling periods as of today. Other finer intervals can only reuse a
    decision when their exact unit union is already present.
    """
    import calendar

    from django.utils import timezone

    from core.services.dashboard.statistics import ROLLING_WINDOW_DAYS

    months = sorted({(day.year, day.month) for p in populations if not p.point_in_time for _, day in p.members})
    windows = {
        (date(*start, 1), date(*end, calendar.monthrange(*end)[1]))
        for index, start in enumerate(months)
        for end in months[index:]
    }
    if not windows:
        windows.add((date.min, date.min))
    today = timezone.localdate()
    windows.update((today - timedelta(days=days - 1), today) for days in ROLLING_WINDOW_DAYS.values())
    return tuple(sorted(windows))


def build_live_release(facility, date_from: date, date_to: date) -> LiveRelease:
    """Solve one canonical live family before projecting the requested interval.

    This protects current live calendar unions without retaining publication
    history. A finer interval that is not an existing population union is
    withheld rather than adding a request-dependent equation to the graph.
    Local table removal is a projection rule: remove its row-existence bound,
    but retain the raw subset equations when solving other consumers.
    """
    from core.models import DocumentType, StatisticsSnapshot
    from core.reporting.registry import get_backend
    from core.services.dashboard.external_report import _get_threshold, project_protected_rows
    from core.services.dashboard.shared_field_sources import collect_field_sources
    from core.services.dashboard.shared_sources import collect_additional_populations

    field_sources = collect_field_sources(facility)
    populations = (
        *collect_event_populations(facility, date.min, date.max),
        *collect_additional_populations(facility),
        *field_sources.populations,
    )
    type_rows = list(
        DocumentType.objects.for_facility(facility).order_by("name").values_list("name", "pk", "report_key")
    )
    types = {name: pk for name, pk, _key in type_rows}
    sample_binding = get_backend("sample-report-1").typ_bindung
    if sample_binding is None:
        raise ValueError("The sample report requires a category binding")
    type_categories = {pk: sample_binding.resolve(key) for _name, pk, key in type_rows}
    snapshots = {
        (row["year"], row["month"]): row
        for row in StatisticsSnapshot.objects.for_facility(facility).values(
            "year", "month", "backend_data", "updated_at"
        )
    }
    threshold = _get_threshold(facility)
    windows = canonical_live_windows(populations)
    intervals = [
        _prepare_live_interval(
            facility, populations, types, type_categories, snapshots, field_sources, threshold, start, end
        )
        for start, end in windows
    ]
    unions = {
        CountKey(p.dimension, p.identity, start, end): (p.dimension, p.identity, population_units(p, start, end))
        for p in populations
        for start, end in windows
    }
    # A fixed zero cell has no secret unit/row existence to transfer. Its
    # containing table may be absent locally (e.g. no active persons), while
    # another interval legitimately lists that same zero alongside safe rows.
    dropped_unions = {unions[key] for interval in intervals for key in interval.dropped if unions[key][2]}
    local_dropped = frozenset(key for key, union in unions.items() if union in dropped_unions)
    observations = [
        replace(cell, hidden=True, lower=0, person_sensitive=False) if cell.key in local_dropped else cell
        for interval in intervals
        for cell in interval.observations
    ]
    observed = {cell.key for cell in observations}
    internal = {CountKey(p.dimension, p.identity, start, end) for p in populations for start, end in windows} - observed
    mask = protect_temporal_counts(facility, populations, windows, observations=observations, hidden_keys=internal)
    mask = SharedMask(
        mask.facility_id,
        mask.threshold,
        mask.published,
        mask.dropped | local_dropped,
    )
    # Index exact unions, not numerical equality. Two equal-sized populations
    # are never aliases unless their counting units are identical.
    aliases: dict[tuple[str, str, frozenset[str]], CountKey] = {}
    for key, union in unions.items():
        aliases.setdefault(union, key)
    requested = _prepare_live_interval(
        facility, populations, types, type_categories, snapshots, field_sources, threshold, date_from, date_to
    )
    members = {(p.dimension, p.identity): population_units(p, date_from, date_to) for p in populations}

    def resolve(identity, dimension="event_counts"):
        return aliases.get((dimension, identity, members.get((dimension, identity), frozenset())))

    def publish(identity, *, dimension="event_counts"):
        key = resolve(identity, dimension)
        return mask.published[key] if key is not None else None

    general, sample, raw = requested.general, requested.sample, requested.raw
    general["total_contacts"] = publish("all-events")
    general["unique_clients"] = publish("active-persons", dimension="person_counts")
    sample["total"] = publish("mapped-events")
    sample["unique_clients"] = publish("active-persons", dimension="person_counts")
    for report, table, prefix, label in (
        (general, "by_document_type", "document-type:", "name"),
        (general, "by_age_cluster", "age:", "cluster"),
        (sample, "by_age_cluster", "age:", "cluster"),
        (sample, "by_category", "category:", "name"),
    ):
        if table not in report:
            continue
        rows = report[table]
        identities = [f"{prefix}{types[row[label]] if table == 'by_document_type' else row[label]}" for row in rows]
        if any(resolve(identity) is None or resolve(identity) in mask.dropped for identity in identities):
            report.pop(table, None)
        else:
            report[table] = project_protected_rows(rows, [publish(identity) for identity in identities])
    if "by_contact_stage" in general:
        if any(resolve(f"contact-stage:{stage}") in mask.dropped for stage in raw["by_contact_stage"]):
            general.pop("by_contact_stage")
        else:
            general["by_contact_stage"] = {
                stage: publish(f"contact-stage:{stage}") for stage in raw["by_contact_stage"]
            }
    datasets = {
        slug: binding.project(lambda dimension, identity: resolve(identity, dimension), mask)
        for slug, binding in requested.datasets.items()
    }
    return LiveRelease(general, sample, mask, datasets)


def project_shared_type_dataset(facility, result):
    """Project the example report through the shared type/month decision."""
    if result.disclosure_context is None:
        return result
    date_from, date_to, _source = result.disclosure_context
    general = build_live_release(facility, date_from, date_to).general
    published = {row["name"]: row["count"] for row in general.get("by_document_type", [])}
    records = [
        {**row, "count": published[row["document_type"]] if row["count"] is not None else None}
        for row in result.records
        if row["document_type"] in published
    ]
    summary = {**result.summary, "total": general["total_contacts"] if result.summary["total"] is not None else None}
    return replace(result, records=records, summary=summary)


def project_shared_dataset(facility, result, slug):
    """Project a live consumer through its common raw-population contract."""
    if result.disclosure_context is None:
        return result
    start, end, source = result.disclosure_context
    if source != "live":
        raise ValueError("A live population projection requires a live source")
    protected = build_live_release(facility, start, end).datasets[slug]
    return replace(result, records=protected.records, summary={**result.summary, **protected.summary})
