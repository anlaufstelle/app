"""Opaque snapshot cohorts and actual live fallback memberships.

Snapshot category and age counters retain separate source identities. Cohort
weights describe additive event counts, never invented person assignments or
links to current events with the same numerical value.
"""

from collections import defaultdict
from datetime import date

from django.utils import timezone

from core.services.dashboard.shared_disclosure import TemporalPopulation


def collect_hybrid_populations(populations, snapshots):
    """Build calendar-month hybrid populations without reading a backend."""
    members = defaultdict(set)
    weights = defaultdict(dict)
    members["hybrid-mapped-events"] = set()
    usable = {month: row["backend_data"].get("sample-report-1") for month, row in snapshots.items()}
    today = timezone.localdate()
    usable = {month: raw for month, raw in usable.items() if raw and month < (today.year, today.month)}
    for population in populations:
        if population.dimension != "event_counts":
            continue
        identity = population.identity
        if identity != "mapped-events" and not identity.startswith(("category:", "age:")):
            continue
        target = f"hybrid-{identity}"
        members[target].update((unit, day) for unit, day in population.members if (day.year, day.month) not in usable)

    def add(identity, token, day, amount):
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise ValueError("Invalid snapshot event cohort count")
        members[identity]  # Retain fixed empty cells as well.
        if amount:
            members[identity].add((token, day))
            weights[identity][token] = amount

    for (year, month), raw in usable.items():
        day = date(year, month, 1)
        categories = defaultdict(int)
        for name, amount in raw.get("by_category", []):
            categories[name] += amount
        prefix = f"snapshot:{year}:{month}:"
        for name, amount in categories.items():
            token = f"{prefix}category:{name}"
            add(f"hybrid-category:{name}", token, day, amount)
            if raw.get("total", 0) == sum(categories.values()):
                add("hybrid-mapped-events", token, day, amount)
        if raw.get("total", 0) != sum(categories.values()):
            # An inconsistent legacy payload cannot assert a partition.
            add("hybrid-mapped-events", f"{prefix}total", day, raw.get("total", 0))
        ages = defaultdict(int)
        for row in raw.get("by_age_cluster", []):
            ages[row["cluster"]] += row["count"]
        for cluster, amount in ages.items():
            add(f"hybrid-age:{cluster}", f"{prefix}age:{cluster}", day, amount)
    return tuple(
        TemporalPopulation(
            "event_counts", identity, tuple(sorted(units)), unit_weights=tuple(sorted(weights[identity].items()))
        )
        for identity, units in sorted(members.items())
    )
