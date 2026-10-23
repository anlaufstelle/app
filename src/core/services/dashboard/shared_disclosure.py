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
from dataclasses import dataclass
from datetime import date

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
    """

    key: CountKey
    value: int
    hidden: bool = False
    lower: int = 0
    upper: int | None = None
    person_sensitive: bool = False
    drop: bool = False

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
            victim = min(candidates, key=lambda key: (raw[key].value, key))
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
    for dimension, identity in sorted(identities):
        for start, end in sorted(set(windows)):
            target = CountKey(dimension, identity, start, end)
            members = tuple(
                atom
                for atom, membership in atom_memberships.items()
                if atom.dimension == dimension and any(start <= day <= end for day in membership.get(identity, ()))
            )
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
    return protect_counts(facility, [*counts, *observations], partitions)


def collect_event_populations(facility, date_from: date, date_to: date) -> tuple[TemporalPopulation, ...]:
    """Collect base live event/person populations with explicit tenant scoping.

    This is the common source for totals, document types, contact stages and
    age groups. Backend-specific mapped, field-value, case and hybrid sources
    must extend the family before solving; this function does not substitute
    for those collectors. Event dates use the database timezone conversion
    used by the existing statistics query. No user or role enters the source.
    """
    from django.db.models import Case, CharField, Value, When
    from django.db.models.functions import TruncDate

    from core.models import Event
    from core.services.dashboard.statistics import _STAGE_ANONYM_Q, _STAGE_IDENTIFIED_Q, _STAGE_QUALIFIED_Q

    _require_facility(facility)
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
        .values("pk", "document_type_id", "client_id", "client__age_cluster", "day", "stage")
        .order_by("occurred_at", "pk")
    )
    populations: dict[tuple[str, str], set[tuple[str, date]]] = defaultdict(set)
    populations[("event_counts", "all-events")] = set()
    populations[("person_counts", "active-persons")] = set()
    for stage in ("anonym", "identifiziert", "qualifiziert"):
        populations[("event_counts", f"contact-stage:{stage}")] = set()
    for row in rows.iterator(chunk_size=1000):
        day = row["day"]
        event = str(row["pk"]), day
        populations[("event_counts", "all-events")].add(event)
        populations[("event_counts", f"document-type:{row['document_type_id']}")].add(event)
        if row["stage"]:
            populations[("event_counts", f"contact-stage:{row['stage']}")].add(event)
        cluster = row["client__age_cluster"] if row["client_id"] is not None else "__no_person__"
        populations[("event_counts", f"age:{cluster}")].add(event)
        if row["client_id"] is not None:
            person = str(row["client_id"]), day
            populations[("person_counts", "active-persons")].add(person)
            populations[("person_counts", f"age:{cluster}")].add(person)
    return tuple(
        TemporalPopulation(dimension, identity, tuple(sorted(members, key=lambda member: (member[1], member[0]))))
        for (dimension, identity), members in sorted(populations.items())
    )
