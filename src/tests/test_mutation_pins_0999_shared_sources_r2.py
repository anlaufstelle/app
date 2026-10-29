"""Pins for the additional shared count populations and their bound datasets.

Source: mutation delta of the release-0.99.9 checkpoint, 61 mutants.
Groups: admission and case source populations (34), point-in-time stock flag (4),
monthly residual guard and local table binding (8), age cohort selection (4),
canonical case metric names (2), local suppression import into the shared solver (6),
local table removal (1), projection of the final mask (2).
Related pattern: test_mutation_pins_0998_dashboard_snapshot.py.
"""

from dataclasses import replace
from datetime import date, datetime

import pytest
from django.utils import timezone

from core.models import Case, Client
from core.reporting.base import DatasetResult
from core.services.dashboard.shared_disclosure import CountKey, RawCount, SharedMask, TemporalPopulation
from core.services.dashboard.shared_sources import (
    BoundDataset,
    collect_additional_populations,
    prepare_additional_interval,
)

pytestmark = pytest.mark.django_db

START = date(2026, 1, 1)
END = date(2026, 12, 31)


def _at(month, day):
    return timezone.make_aware(datetime(2026, month, day, 12))


def _client(facility, name, created):
    client = Client.objects.create(facility=facility, pseudonym=name, age_cluster="u18")
    Client.objects.for_facility(facility).filter(pk=client.pk).update(created_at=created)
    return client


def _case(facility, client, created, closed=None, deleted=False):
    case = Case.objects.create(facility=facility, client=client, title="Support")
    Case.objects.for_facility(facility).filter(pk=case.pk).update(
        created_at=created,
        closed_at=closed,
        status=Case.Status.CLOSED if closed else Case.Status.OPEN,
        is_deleted=deleted,
    )
    return case


@pytest.fixture
def source(facility, second_facility):
    """Live and deleted clients and cases in two facilities with shared days."""
    c1 = _client(facility, "C-1", _at(2, 10))
    c2 = _client(facility, "C-2", _at(2, 10))
    c3 = _client(facility, "C-3", _at(3, 12))
    deleted = _client(facility, "C-DEL", _at(2, 10))
    Client.objects.for_facility(facility).filter(pk=deleted.pk).update(is_deleted=True)
    foreign = _client(second_facility, "C-FOREIGN", _at(2, 10))
    a = _case(facility, c1, _at(2, 10))
    b = _case(facility, c2, _at(2, 10))
    c = _case(facility, c1, _at(1, 5), closed=_at(3, 12))
    d = _case(facility, c3, _at(1, 5), closed=_at(1, 20))
    _case(facility, c1, _at(4, 1), deleted=True)
    _case(facility, deleted, _at(5, 1), closed=_at(5, 2), deleted=True)
    _case(second_facility, foreign, _at(2, 10))
    _case(second_facility, foreign, _at(6, 1), closed=_at(6, 2))
    populations = collect_additional_populations(facility)
    return {
        "populations": {(p.dimension, p.identity): p for p in populations},
        "clients": (c1, c2, c3),
        "cases": (a, b, c, d),
    }


def _members(source, dimension, identity):
    """Members as a sorted list: their order is not part of the contract."""
    return sorted(source["populations"][(dimension, identity)].members)


def test_collected_populations_are_exactly_the_five_canonical_keys(source):
    """Only the five canonical dimension/identity pairs exist; the population tuple is sorted."""
    assert sorted(source["populations"]) == [
        ("case_counts", "closed-cases"),
        ("case_counts", "created-cases"),
        ("case_counts", "current-open-cases"),
        ("person_counts", "admissions"),
        ("person_counts", "case-affected-persons"),
    ]


def test_admissions_hold_each_live_client_of_the_requested_facility_once(source):
    """Admissions are the distinct live clients of the facility with their own day.

    Kills x_collect_additional_populations__mutmut_28, x_collect_additional_populations__mutmut_29,
    x_collect_additional_populations__mutmut_30, x_collect_additional_populations__mutmut_41,
    x_collect_additional_populations__mutmut_42, x_collect_additional_populations__mutmut_43,
    x_collect_additional_populations__mutmut_44, x_collect_additional_populations__mutmut_45.
    """
    c1, c2, c3 = source["clients"]
    expected = sorted(
        [(str(c1.pk), date(2026, 2, 10)), (str(c2.pk), date(2026, 2, 10)), (str(c3.pk), date(2026, 3, 12))]
    )
    assert _members(source, "person_counts", "admissions") == expected


def test_created_cases_hold_each_live_case_of_the_requested_facility_once(source):
    """Created cases are the distinct live cases of the facility with their creation day.

    Kills x_collect_additional_populations__mutmut_61, x_collect_additional_populations__mutmut_62,
    x_collect_additional_populations__mutmut_63, x_collect_additional_populations__mutmut_83,
    x_collect_additional_populations__mutmut_90, x_collect_additional_populations__mutmut_91,
    x_collect_additional_populations__mutmut_92, x_collect_additional_populations__mutmut_93.
    """
    a, b, c, d = source["cases"]
    expected = sorted(
        [
            (str(a.pk), date(2026, 2, 10)),
            (str(b.pk), date(2026, 2, 10)),
            (str(c.pk), date(2026, 1, 5)),
            (str(d.pk), date(2026, 1, 5)),
        ]
    )
    assert _members(source, "case_counts", "created-cases") == expected


def test_closed_cases_hold_each_live_closure_with_its_closing_day(source):
    """Closed cases are the live cases with a closing day, keyed by that day.

    Kills x_collect_additional_populations__mutmut_107, x_collect_additional_populations__mutmut_108,
    x_collect_additional_populations__mutmut_109, x_collect_additional_populations__mutmut_110.
    """
    _, _, c, d = source["cases"]
    expected = sorted([(str(c.pk), date(2026, 3, 12)), (str(d.pk), date(2026, 1, 20))])
    assert _members(source, "case_counts", "closed-cases") == expected


def test_case_affected_persons_union_creation_and_closing_days_per_client(source):
    """Affected persons carry the client of every live case on its creation and closing day.

    Kills x_collect_additional_populations__mutmut_86, x_collect_additional_populations__mutmut_97,
    x_collect_additional_populations__mutmut_98, x_collect_additional_populations__mutmut_99,
    x_collect_additional_populations__mutmut_100, x_collect_additional_populations__mutmut_114,
    x_collect_additional_populations__mutmut_115, x_collect_additional_populations__mutmut_116,
    x_collect_additional_populations__mutmut_117.
    """
    c1, c2, c3 = source["clients"]
    expected = sorted(
        [
            (str(c1.pk), date(2026, 2, 10)),
            (str(c2.pk), date(2026, 2, 10)),
            (str(c1.pk), date(2026, 1, 5)),
            (str(c3.pk), date(2026, 1, 5)),
            (str(c1.pk), date(2026, 3, 12)),
            (str(c3.pk), date(2026, 1, 20)),
        ]
    )
    assert _members(source, "person_counts", "case-affected-persons") == expected


def test_current_open_cases_hold_only_open_live_cases_at_the_earliest_day(source):
    """Current stock lists exactly the open live cases of the facility under date.min.

    Kills x_collect_additional_populations__mutmut_122, x_collect_additional_populations__mutmut_124,
    x_collect_additional_populations__mutmut_125, x_collect_additional_populations__mutmut_126,
    x_collect_additional_populations__mutmut_127.
    """
    a, b, _, _ = source["cases"]
    expected = sorted([(str(a.pk), date.min), (str(b.pk), date.min)])
    assert _members(source, "case_counts", "current-open-cases") == expected


def test_only_the_open_case_stock_is_marked_as_point_in_time(source):
    """The stock flag is True for current-open-cases and False for every other population.

    Kills x_collect_additional_populations__mutmut_132, x_collect_additional_populations__mutmut_136,
    x_collect_additional_populations__mutmut_140, x_collect_additional_populations__mutmut_141.
    """
    flags = {key: population.point_in_time for key, population in source["populations"].items()}
    assert flags[("case_counts", "current-open-cases")] is True
    assert [value for key, value in flags.items() if key != ("case_counts", "current-open-cases")] == [False] * 4


def test_open_stock_survives_a_reporting_window_that_excludes_the_earliest_day(source):
    """Case metrics count the open stock for a modern window and bind the canonical metric names.

    Kills x_collect_additional_populations__mutmut_132, x_collect_additional_populations__mutmut_136,
    x_collect_additional_populations__mutmut_140, x_collect_additional_populations__mutmut_141.
    """
    populations = tuple(source["populations"].values())
    binding = prepare_additional_interval(populations, date(2026, 2, 1), date(2026, 2, 28), 1)["case-metrics"]
    assert binding.raw.summary == {
        "neue_faelle": 2,
        "geschlossene_faelle": 0,
        "offene_faelle_aktuell": 2,
        "betroffene_personen": 2,
    }


def _population(dimension, identity, count, day=date(2026, 2, 10)):
    members = tuple((f"{identity}-{index}", day) for index in range(count))
    return TemporalPopulation(dimension, identity, members, point_in_time=identity == "current-open-cases")


def test_case_metrics_use_canonical_row_and_summary_names():
    """The case binding names its four metrics canonically in rows, summary and keys.

    Kills x_prepare_additional_interval__mutmut_232, x_prepare_additional_interval__mutmut_233.
    """
    populations = (
        _population("case_counts", "created-cases", 4),
        _population("case_counts", "closed-cases", 3),
        _population("case_counts", "current-open-cases", 5),
        _population("person_counts", "case-affected-persons", 6),
    )
    binding = prepare_additional_interval(populations, START, END, 3)["case-metrics"]
    names = ["neue_faelle", "geschlossene_faelle", "offene_faelle_aktuell", "betroffene_personen"]
    assert [row["kennzahl"] for row in binding.raw.records] == names
    assert list(binding.raw.summary) == names
    assert list(binding.summary_keys) == names
    assert binding.local.summary["offene_faelle_aktuell"] == 5


def _monthly_populations(buckets):
    populations = [
        TemporalPopulation(
            "event_counts",
            f"monthly-category:{month}:{category}",
            tuple((f"{month}-{category}-{index}", date(2026, 2, 10)) for index in range(amount)),
        )
        for month, category, amount in buckets
    ]
    total = sum(amount for _, _, amount in buckets)
    populations.append(
        TemporalPopulation(
            "event_counts", "mapped-events", tuple((f"event-{index}", date(2026, 2, 10)) for index in range(total))
        )
    )
    return tuple(populations)


@pytest.fixture
def single_event_months():
    """Three monthly buckets with one event each, below the threshold of three."""
    buckets = (("2026-01", "alpha", 1), ("2026-02", "beta", 1), ("2026-03", "gamma", 1))
    return prepare_additional_interval(_monthly_populations(buckets), START, END, 3)["monthly-trend"]


def test_monthly_total_is_withheld_when_it_would_reveal_every_suppressed_row(single_event_months):
    """The residual guard finds the monthly table and withholds the reconstructing total.

    Kills x_prepare_additional_interval__mutmut_72, x_prepare_additional_interval__mutmut_73,
    x_prepare_additional_interval__mutmut_78, x_prepare_additional_interval__mutmut_79,
    x_prepare_additional_interval__mutmut_80, x_prepare_additional_interval__mutmut_81.
    """
    assert single_event_months.raw.summary["total"] == 3
    assert single_event_months.local.summary["total"] is None


def test_monthly_binding_carries_the_protected_rows_and_summary(single_event_months):
    """The local monthly dataset holds the suppressed rows and the stamped summary.

    Kills x_prepare_additional_interval__mutmut_107, x_prepare_additional_interval__mutmut_108.
    """
    assert [row["count"] for row in single_event_months.raw.records] == [1, 1, 1]
    assert [row["count"] for row in single_event_months.local.records] == [None, None, None]
    assert single_event_months.local.summary["k_anonymity_threshold"] == 3
    assert [obs.hidden for obs in single_event_months.observations()] == [True, True, True, True]


def test_age_cohorts_of_the_person_dimension_become_client_rows():
    """Populated age cohorts of the canonical person dimension become ordered client rows.

    Kills x_prepare_additional_interval__mutmut_127, x_prepare_additional_interval__mutmut_128,
    x_prepare_additional_interval__mutmut_130, x_prepare_additional_interval__mutmut_131.
    """
    populations = (
        _population("person_counts", "age:u18", 4),
        _population("person_counts", "age:18-26", 5),
        _population("person_counts", "age:other", 3, day=date(2025, 5, 1)),
        _population("person_counts", "active-persons", 9),
        _population("person_counts", "admissions", 3),
        _population("event_counts", "age:ignored", 6),
        _population("person_counts", "unrelated", 7),
    )
    binding = prepare_additional_interval(populations, START, END, 3)["client-metrics"]
    assert binding.raw.records == [
        {"alterscluster": "18-26", "personen": 5},
        {"alterscluster": "u18", "personen": 4},
    ]
    assert binding.row_keys == (
        CountKey("person_counts", "age:18-26", START, END),
        CountKey("person_counts", "age:u18", START, END),
    )
    assert [row["personen"] for row in binding.local.records] == [5, 4]


def _bound(raw_records, local_records, raw_summary=None, local_summary=None, count_key="count"):
    raw_summary = raw_summary or {}
    local_summary = raw_summary if local_summary is None else local_summary
    raw = DatasetResult("rows", raw_records, raw_summary, [])
    keys = tuple(CountKey("event_counts", f"row-{index}", START, END) for index in range(len(raw_records)))
    summary_keys = {field: CountKey("event_counts", f"sum-{field}", START, END) for field in raw_summary}
    return BoundDataset(raw, replace(raw, records=local_records, summary=local_summary), keys, count_key, summary_keys)


def test_locally_suppressed_row_is_imported_as_hidden_with_row_lower_bound():
    """A row above the threshold that the local contract suppressed is hidden; visible rows are not.

    Kills xǁBoundDatasetǁobservations__mutmut_20, xǁBoundDatasetǁobservations__mutmut_24.
    """
    bound = _bound([{"count": 10}, {"count": 12}], [{"count": None}, {"count": 12}])
    first, second = list(bound.observations())
    assert first.hidden is True
    assert second.hidden is False


def test_visible_table_rows_carry_lower_bound_one_but_metric_rows_and_removed_tables_zero():
    """Ordinary rows prove existence (lower 1); fixed metric rows and removed tables stay at zero.

    Kills xǁBoundDatasetǁobservations__mutmut_25, xǁBoundDatasetǁobservations__mutmut_28.
    """
    visible = list(_bound([{"count": 4}], [{"count": 4}]).observations())
    metric = list(_bound([{"kennzahl": "a", "count": 4}], [{"kennzahl": "a", "count": 4}]).observations())
    removed = list(_bound([{"count": 4}], []).observations())
    assert [obs.lower for obs in visible] == [1]
    assert [obs.lower for obs in metric] == [0]
    assert [obs.lower for obs in removed] == [0]
    assert [obs.hidden for obs in removed] == [True]


def test_locally_suppressed_summary_is_imported_as_hidden():
    """A summary above the threshold that was suppressed locally is hidden; a visible one is not.

    Kills xǁBoundDatasetǁobservations__mutmut_37, xǁBoundDatasetǁobservations__mutmut_40.
    """
    bound = _bound([], [], raw_summary={"hidden": 9, "shown": 8}, local_summary={"hidden": None, "shown": 8})
    observations = {obs.key.population: obs for obs in bound.observations()}
    assert isinstance(observations["sum-hidden"], RawCount)
    assert observations["sum-hidden"].hidden is True
    assert observations["sum-shown"].hidden is False


def test_removed_local_table_withholds_every_row_identity():
    """A table that is nonempty raw but empty locally reports all of its row keys as dropped.

    Kills xǁBoundDatasetǁdropped__mutmut_1.
    """
    removed = _bound([{"count": 4}, {"count": 6}], [])
    kept = _bound([{"count": 4}], [{"count": 4}])
    assert removed.dropped() == removed.row_keys
    assert len(removed.dropped()) == 2
    assert kept.dropped() == ()


def _resolver(keys):
    lookup = {(key.dimension, key.population): key for key in keys}
    return lambda dimension, population: lookup.get((dimension, population))


def test_projection_keeps_a_local_suppression_that_the_mask_would_show():
    """A row suppressed locally stays None even when the shared mask holds a number for it.

    Kills xǁBoundDatasetǁproject__mutmut_13.
    """
    bound = _bound([{"count": 10}, {"count": 12}], [{"count": None}, {"count": 12}])
    mask = SharedMask(1, 3, {bound.row_keys[0]: 10, bound.row_keys[1]: 12}, frozenset())
    projected = bound.project(_resolver(bound.row_keys), mask)
    assert [row["count"] for row in projected.records] == [None, 12]


def test_projection_returns_the_masked_records_and_drops_a_removed_key():
    """The projection carries the mask values; a dropped or unresolved key empties the table.

    Kills xǁBoundDatasetǁproject__mutmut_39.
    """
    bound = _bound([{"count": 10}, {"count": 12}], [{"count": 10}, {"count": 12}])
    published = {bound.row_keys[0]: 7, bound.row_keys[1]: 9}
    shown = bound.project(_resolver(bound.row_keys), SharedMask(1, 3, published, frozenset()))
    assert [row["count"] for row in shown.records] == [7, 9]
    dropped = bound.project(_resolver(bound.row_keys), SharedMask(1, 3, published, frozenset({bound.row_keys[1]})))
    assert dropped.records == []
    unresolved = bound.project(lambda dimension, population: None, SharedMask(1, 3, published, frozenset()))
    assert unresolved.records == []
