"""Pin field-cohort disclosure and projection contracts.

Source: mutation delta of the release-0.99.9 checkpoint, 59 mutants.
Groups: source cohorts (16), interval contracts (23), field projections (20).
"""

from dataclasses import replace
from datetime import date, datetime
from fractions import Fraction
from uuid import UUID

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate
from core.reporting.base import DatasetResult
from core.services.dashboard.shared_disclosure import SharedMask, TemporalPopulation
from core.services.dashboard.shared_field_sources import (
    FieldDataset,
    FieldSources,
    collect_field_sources,
    prepare_field_interval,
)
from tests._factories import make_document_type, make_event

START, END = date(2026, 1, 1), date(2026, 1, 31)


@pytest.fixture
def field_world(facility, admin_user):
    """Create real field bindings and event payloads for the public collector."""

    slugs = {}

    def document(name, report_key=None, fields=(), **kwargs):
        document_type = make_document_type(facility=facility, name=name, report_key=report_key, **kwargs)
        for key in fields:
            template = FieldTemplate.objects.create(
                facility=facility,
                name=key,
                report_key=key,
                field_type="multi_select" if key == "leistungen" else "number",
                options_json=[{"slug": "support", "label": "Support"}] if key == "leistungen" else [],
            )
            DocumentTypeField.objects.create(document_type=document_type, field_template=template)
            slugs[(document_type.pk, key)] = template.slug
        return document_type

    def event(document_type, data, day=15, **kwargs):
        return make_event(
            facility=facility,
            client=None,
            document_type=document_type,
            created_by=admin_user,
            occurred_at=timezone.make_aware(datetime(2026, 1, day, 12)),
            data_json={slugs.get((document_type.pk, key), key): value for key, value in data.items()},
            **kwargs,
        )

    return facility, document, event


def interval(sources, threshold=3, general=None):
    """Prepare the public interval API from a collected or explicit source snapshot."""
    return prepare_field_interval(sources, sources.populations, START, END, threshold, general or {})


def population(sources, identity):
    return next(item for item in sources.populations if item.identity == identity)


def project(contract, hidden=(), dropped=()):
    """Project a shared decision with independent hidden and dropped populations."""
    published = {contract.key(identity): len(units) for identity, units in contract.populations.items()}
    published.update({contract.key(identity): None for identity in hidden})
    mask = SharedMask(1, 3, published, frozenset(contract.key(identity) for identity in dropped))

    def resolve(dimension, identity):
        key = contract.key(identity)
        return key if key in published else None

    return contract.project(resolve, mask)


@pytest.mark.django_db
@pytest.mark.parametrize("contributors", [1, 3])
def test_needle_missing_events_do_not_contribute(field_world, contributors):
    """Missing values stay separate.

    Mutants:
    x_collect_field_sources__mutmut_196
    x_collect_field_sources__mutmut_201
    x_collect_field_sources__mutmut_202
    """
    facility, document, event = field_world
    kind = document("Needles", "needle_exchange", ("ausgabe_stueck", "rueckgabe_stueck"))
    for _ in range(contributors):
        event(kind, {"ausgabe_stueck": 2, "rueckgabe_stueck": 1})
    for _ in range(3):
        event(kind, {})
    sources = collect_field_sources(facility)
    assert len(population(sources, "needle-missing").members) == 3
    assert len(population(sources, "needle-contributors").members) == contributors
    summary = interval(sources)["needle-exchange-balance"].local.summary
    assert summary["events_ohne_werte"] == (3 if contributors == 3 else None)
    assert summary["summe_ausgabe"] == (6 if contributors == 3 else None)


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["needle", "counseling"])
def test_empty_contributor_identity_keeps_safe_event_rows(field_world, kind):
    """Empty contributors remain resolvable.

    Mutants:
    x_collect_field_sources__mutmut_78
    x_collect_field_sources__mutmut_79
    x_collect_field_sources__mutmut_285
    """
    facility, document, event = field_world
    document_type = document("Empty values", "needle_exchange" if kind == "needle" else "contact")
    for _ in range(3):
        event(document_type, {})
    sources = collect_field_sources(facility)
    slug = "needle-exchange-balance" if kind == "needle" else "counseling-hours"
    result = project(interval(sources)[slug])
    assert len(result.records) == 1
    assert result.records[0]["events"] == 3
    assert result.records[0]["summe_ausgabe" if kind == "needle" else "minuten"] is None


@pytest.mark.django_db
@pytest.mark.parametrize("invalid", ["inactive", "expired", "missing", "corrupt"])
def test_invalid_active_bans_do_not_enlarge_current_cohort(field_world, monkeypatch, invalid, _encryption_key):
    """Only valid active bans count.

    Mutants:
    x_collect_field_sources__mutmut_217
    x_collect_field_sources__mutmut_221
    x_collect_field_sources__mutmut_228
    x_collect_field_sources__mutmut_229
    """
    facility, document, event = field_world
    today = date(2026, 1, 20)
    monkeypatch.setattr(timezone, "localdate", lambda: today)
    kind = document("Bans", system_type="ban")
    for _ in range(2):
        event(kind, {"aktiv": True})
    data = {
        "inactive": {"aktiv": False},
        "expired": {"aktiv": True, "bis": "2026-01-19"},
        "missing": {},
        "corrupt": {"aktiv": {"__encrypted__": True, "value": "broken-token"}},
    }[invalid]
    event(kind, data)
    sources = collect_field_sources(facility)
    assert len(population(sources, "current-active-bans").members) == 2
    assert interval(sources)["ban-overview"].local.summary["aktiv_aktuell"] is None


@pytest.mark.django_db
def test_ban_expiring_today_remains_current(field_world, monkeypatch):
    """Expiry is inclusive.

    Mutants:
    x_collect_field_sources__mutmut_232
    """
    facility, document, event = field_world
    monkeypatch.setattr(timezone, "localdate", lambda: date(2026, 1, 20))
    kind = document("Bans", system_type="ban")
    for _ in range(2):
        event(kind, {"aktiv": True})
    event(kind, {"aktiv": True, "bis": "2026-01-20"})
    sources = collect_field_sources(facility)
    assert len(population(sources, "current-active-bans").members) == 3
    assert interval(sources)["ban-overview"].local.summary["aktiv_aktuell"] == 3


@pytest.mark.django_db
def test_corrupt_service_selection_is_not_a_contribution(field_world, _encryption_key):
    """Corrupt choices do not publish placeholders.

    Mutants:
    x_collect_field_sources__mutmut_269
    """
    facility, document, event = field_world
    kind = document("Contacts", "contact", ("leistungen",))
    for _ in range(2):
        event(kind, {"leistungen": ["support"]})
    corrupt = event(kind, {})
    Event.objects.for_facility(facility).filter(pk=corrupt.pk).update(
        data_json={"leistungen": {"__encrypted__": True, "value": "broken-token"}}
    )
    sources = collect_field_sources(facility)
    assert len(population(sources, "service-values:leistungen").members) == 2
    assert next(row for row in sources.rows if row["unit"] == str(corrupt.pk))["options"]["leistungen"] == []
    assert interval(sources)["service-distribution"].local.summary["events_leistungen"] is None


@pytest.mark.django_db
def test_legacy_non_event_type_cannot_supply_quantities(field_world):
    """Quantity bindings require event types.

    Mutants:
    x_collect_field_sources__mutmut_30
    x_collect_field_sources__mutmut_32
    """
    facility, document, event = field_world
    kind = document("Legacy case", fields=("quantity_units",), entity_type=DocumentType.EntityType.CASE)
    event(kind, {"quantity_units": 42})
    sources = collect_field_sources(facility)
    assert kind.pk not in sources.quantity_types
    assert "quantity" not in sources.rows[0]
    assert not population(sources, "quantity-contributors").members


def counseling_sources(counts, duration=1):
    """Make explicit category and contributor cohorts for interval protection."""
    rows, populations = [], []
    all_units = []
    for category, amount in counts.items():
        units = tuple((f"{category}-{index}", START) for index in range(amount))
        all_units.extend(units)
        populations.extend(
            TemporalPopulation("event_counts", identity, units)
            for identity in (f"counseling-category:{category}", f"counseling-contributors:{category}")
        )
        rows.extend(
            {"day": START, "type_id": category, "counseling": category, "duration": duration, "options": {}}
            for _ in units
        )
    populations.append(TemporalPopulation("event_counts", "counseling-events", tuple(all_units)))
    return FieldSources(tuple(populations), tuple(rows), {}, (), frozenset())


def test_counseling_residual_total_is_withheld():
    """Two hidden categories protect their total.

    Mutants:
    x_prepare_field_interval__mutmut_122
    x_prepare_field_interval__mutmut_123
    x_prepare_field_interval__mutmut_130
    x_prepare_field_interval__mutmut_131
    x_prepare_field_interval__mutmut_132
    x_prepare_field_interval__mutmut_133
    """
    contract = interval(counseling_sources({"contact": 1, "counseling": 1}), threshold=2)["counseling-hours"]
    assert contract.local.summary["events_gesamt"] is None


def test_counseling_minutes_follow_shared_contributor_suppression():
    """Hidden contributors withhold minutes.

    Mutants:
    x_prepare_field_interval__mutmut_142
    x_prepare_field_interval__mutmut_143
    x_prepare_field_interval__mutmut_149
    x_prepare_field_interval__mutmut_150
    """
    contract = interval(counseling_sources({"contact": 3}, duration=7))["counseling-hours"]
    assert contract.local.records[0]["minuten"] == 21
    assert contract.local.summary["minuten_gesamt"] == 21
    result = project(contract, hidden=("counseling-contributors:contact",))
    assert result.records[0]["events"] == 3
    assert result.records[0]["minuten"] is None
    assert result.summary["minuten_gesamt"] is None


def needle_sources():
    """Include numeric zero and missing sides among three valid contributors."""
    units = tuple((str(index), START) for index in range(3))
    populations = tuple(
        TemporalPopulation("event_counts", identity, units) for identity in ("needle-events", "needle-contributors")
    )
    rows = tuple(
        {"day": START, "type_id": 1, "needle": values, "options": {}} for values in ((0, 2), (4, 0), (None, 3))
    )
    return FieldSources(populations, rows, {}, (), frozenset())


def test_needle_totals_follow_shared_contributor_suppression():
    """Shared contributor decisions gate all sums.

    Mutants:
    x_prepare_field_interval__mutmut_259
    x_prepare_field_interval__mutmut_260
    x_prepare_field_interval__mutmut_266
    x_prepare_field_interval__mutmut_267
    """
    contract = interval(needle_sources())["needle-exchange-balance"]
    assert contract.local.summary["summe_ausgabe"] == 4
    result = project(contract, hidden=("needle-contributors",))
    assert result.records[0]["events"] == 3
    for key in ("summe_ausgabe", "summe_rueckgabe", "saldo"):
        assert result.records[0][key] is None
        assert result.summary[key] is None


def test_needle_zero_and_missing_sides_add_nothing():
    """Zero and missing sides preserve totals.

    Mutants:
    x_prepare_field_interval__mutmut_171
    x_prepare_field_interval__mutmut_181
    """
    result = interval(needle_sources())["needle-exchange-balance"].local
    assert {key: result.summary[key] for key in ("summe_ausgabe", "summe_rueckgabe", "saldo")} == {
        "summe_ausgabe": 4,
        "summe_rueckgabe": 5,
        "saldo": -1,
    }
    assert result.records[0]["summe_ausgabe"] == 4
    assert result.records[0]["summe_rueckgabe"] == 5


def quantity_sources(overflow=False):
    """Earlier irrelevant and empty types must not hide later populated types."""
    values = (Fraction(10**310) + Fraction(1, 2), -Fraction(10**310) + Fraction(1, 2)) if overflow else (Fraction(2),)
    types = {0: {"name": "A unrelated"}, 1: {"name": "B empty"}}
    rows, populations, all_units = [], [], []
    for pk, value in enumerate(values, start=2):
        types[pk] = {"name": f"C populated {pk}"}
        units = tuple((f"{pk}-{index}", START) for index in range(3))
        all_units.extend(units)
        populations.extend(
            TemporalPopulation("event_counts", identity, units)
            for identity in (f"document-type:{pk}", f"quantity-contributors:{pk}")
        )
        rows.extend({"day": START, "type_id": pk, "quantity": value, "options": {}} for _ in units)
    populations.append(TemporalPopulation("event_counts", "quantity-contributors", tuple(all_units)))
    sources = FieldSources(tuple(populations), tuple(rows), types, (), frozenset(range(1, len(types))))
    general = {"by_document_type": [{"name": metadata["name"], "count": 3} for metadata in types.values()]}
    return sources, general


def test_quantity_processing_survives_earlier_unrelated_and_empty_types():
    """Later quantity rows remain visible.

    Mutants:
    x_prepare_field_interval__mutmut_436
    x_prepare_field_interval__mutmut_442
    """
    sources, general = quantity_sources()
    result = interval(sources, general=general)["quantity-sum"].local
    assert result.records == [{"document_type": "C populated 2", "contributors": 3, "quantity": 6}]
    assert result.summary["quantity_total"] == 6


def test_overflowing_quantity_subtotals_withhold_finite_total():
    """Unrepresentable subtotals withhold their combined total.

    Mutants:
    x_prepare_field_interval__mutmut_506
    """
    sources, general = quantity_sources(overflow=True)
    contract = interval(sources, general=general)["quantity-sum"]
    assert all(row["quantity"] is None for row in contract.raw.records)
    assert contract.raw.summary["quantity_total"] is None
    assert contract.local.summary["quantity_total"] is None


def service_contract():
    """Use real service labels with shared option names in separate fields."""
    rows = [
        {"feld": "first", "option": "shared", "count": 3},
        {"feld": "first", "option": "other", "count": 4},
        {"feld": "second", "option": "shared", "count": 5},
    ]
    raw = DatasetResult("rows", rows, {}, [])
    counts = {(row["feld"], row["option"]): {"count": f"option-{index}"} for index, row in enumerate(rows)}
    populations = {f"option-{index}": frozenset(range(row["count"])) for index, row in enumerate(rows)}
    return FieldDataset(raw, raw, ("feld", "option"), counts, {}, {}, {}, populations, START, END)


def test_dropped_service_option_removes_only_its_whole_field():
    """Independent fields survive a dropped first group.

    Mutants:
    xǁFieldDatasetǁgroup__mutmut_1
    xǁFieldDatasetǁgroup__mutmut_3
    xǁFieldDatasetǁgroup__mutmut_5
    xǁFieldDatasetǁgroup__mutmut_6
    xǁFieldDatasetǁgroup__mutmut_7
    xǁFieldDatasetǁgroup__mutmut_8
    xǁFieldDatasetǁproject__mutmut_15
    xǁFieldDatasetǁproject__mutmut_31
    """
    contract = service_contract()
    result = project(contract, dropped=("option-0",))
    assert result.records == [{"feld": "second", "option": "shared", "count": 5}]


def test_shared_projection_keeps_local_secondary_suppression():
    """A visible shared alias cannot restore a local hidden count.

    Mutants:
    xǁFieldDatasetǁproject__mutmut_40
    """
    contract = service_contract()
    contract = replace(contract, local=replace(contract.local, records=[{**contract.raw.records[0], "count": None}]))
    assert project(contract).records[0]["count"] is None


def test_observations_preserve_local_hidden_counts_and_public_row_bounds():
    """Local suppression and row existence reach the solver.

    Mutants:
    xǁFieldDatasetǁobservations__mutmut_11
    xǁFieldDatasetǁobservations__mutmut_15
    xǁFieldDatasetǁobservations__mutmut_16
    xǁFieldDatasetǁobservations__mutmut_24
    xǁFieldDatasetǁobservations__mutmut_28
    """
    contract = service_contract()
    contract = replace(contract, local=replace(contract.local, records=[{**contract.raw.records[0], "count": None}]))
    observations = {item.key.population: item for item in contract.observations()}
    assert observations["option-0"].hidden is True
    assert observations["option-0"].value == 3
    assert observations["option-0"].lower == 1
    assert observations["option-1"].hidden is True
    assert observations["option-1"].lower == 0


def test_omitted_rows_produce_hidden_observations_and_valid_dropped_keys():
    """Removed rows keep valid identities.

    Mutants:
    xǁFieldDatasetǁobservations__mutmut_20
    xǁFieldDatasetǁobservations__mutmut_22
    xǁFieldDatasetǁdropped__mutmut_4
    """
    contract = service_contract()
    contract = replace(contract, local=replace(contract.local, records=[]))
    assert set(contract.dropped()) == {contract.key(f"option-{index}") for index in range(3)}
    observations = list(contract.observations())
    assert {item.key for item in observations} == set(contract.dropped())
    assert all(item.hidden is True and item.lower == 0 for item in observations)


@pytest.mark.parametrize("location", ["row", "summary"])
@pytest.mark.parametrize("visible", [False, True])
def test_only_visible_numeric_values_expose_contributor_observations(location, visible):
    """Visible sums expose contributing cohorts.

    Mutants:
    xǁFieldDatasetǁobservations__mutmut_43
    xǁFieldDatasetǁobservations__mutmut_48
    xǁFieldDatasetǁobservations__mutmut_54
    """
    contract = service_contract()
    row = {"feld": "first", "option": "shared", "sum": 12 if visible else None}
    local = DatasetResult("rows", [row], {"total": 12 if visible else None}, [])
    contract = replace(
        contract,
        raw=local,
        local=local,
        row_counts={},
        row_gates={("first", "shared"): {"sum": ("contributors",)}} if location == "row" else {},
        summary_gates={"total": ("contributors",)} if location == "summary" else {},
        populations={"contributors": frozenset({"a", "b", "c"})},
    )
    observations = list(contract.observations())
    assert {item.key.population: item.value for item in observations} == ({"contributors": 3} if visible else {})


def test_counseling_and_quantity_results_keep_dataset_metadata():
    """Dataset metadata remains iterable and row-based.

    Mutants:
    x_prepare_field_interval__mutmut_81
    x_prepare_field_interval__mutmut_84
    x_prepare_field_interval__mutmut_513
    x_prepare_field_interval__mutmut_516
    """
    sources, general = quantity_sources()
    contracts = (
        interval(sources, general=general)["quantity-sum"],
        interval(counseling_sources({"contact": 3}))["counseling-hours"],
    )
    for contract in contracts:
        for result in (contract.raw, contract.local):
            assert result.mode == "rows"
            assert result.completeness == []


def select_columns(sql):
    """Split the top-level select list of a query into its column expressions."""
    clause = sql.split("SELECT ", 1)[1].split(" FROM ", 1)[0]
    columns, depth, start = [], 0, 0
    for index, char in enumerate(clause):
        depth += {"(": 1, ")": -1}.get(char, 0)
        if char == "," and depth == 0:
            columns.append(clause[start:index].strip())
            start = index + 1
    columns.append(clause[start:].strip())
    return columns


@pytest.mark.django_db
@pytest.mark.parametrize("tied", [False, True])
def test_collector_orders_by_event_time_then_primary_key(field_world, tied):
    """Stable SQL ordering fixes float aggregation order.

    Mutants:
    x_collect_field_sources__mutmut_97
    x_collect_field_sources__mutmut_98
    """
    facility, document, event = field_world
    kind = document("Contacts", "contact", ("dauer_minuten",))
    # Insertion order and primary-key order both disagree with chronological order.
    latest = event(kind, {"dauer_minuten": 1}, day=15 if tied else 17, pk=UUID(int=3 if tied else 1))
    earliest = event(kind, {"dauer_minuten": 1e16}, day=15, pk=UUID(int=1 if tied else 3))
    middle = event(kind, {"dauer_minuten": -1e16}, day=15 if tied else 16, pk=UUID(int=2))
    with CaptureQueriesContext(connection) as queries:
        sources = collect_field_sources(facility)
    assert [row["unit"] for row in sources.rows] == [str(earliest.pk), str(middle.pk), str(latest.pk)]
    event_reads = [query["sql"] for query in queries if 'FROM "core_event"' in query["sql"]]
    assert event_reads
    for sql in event_reads:
        ordering = sql.rsplit("ORDER BY", 1)[1]
        assert '"core_event"."occurred_at" ASC' in ordering
        primary_key_order = '"core_event"."id" ASC'
        if primary_key_order not in ordering:
            # PostgreSQL also accepts the selected primary key by its ordinal, wherever it is selected.
            columns = select_columns(sql)
            primary_key = [index for index, column in enumerate(columns, 1) if column.startswith('"core_event"."id"')]
            assert primary_key
            primary_key_order = f"{primary_key[0]} ASC"
        assert primary_key_order in ordering
        assert ordering.index('"occurred_at"') < ordering.index(primary_key_order)
    assert interval(sources)["counseling-hours"].local.summary["minuten_gesamt"] == 1
