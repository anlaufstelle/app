"""Public shared-mask contract and recipient reconstruction attempts."""

from datetime import date
from itertools import product

import pytest

from core.services.dashboard import shared_disclosure

START = date(2026, 1, 1)
END = date(2026, 3, 31)


def key(population, start=START, end=END, dimension="event_counts"):
    return shared_disclosure.CountKey(dimension, population, start, end)


def count(population, value, **kwargs):
    return shared_disclosure.RawCount(key(population), value, **kwargs)


def possible_targets(mask, target, equations, *, maximum=40):
    """Enumerate recipient worlds using only released values and equations."""
    keys = sorted({cell for margin, cells in equations for cell in (margin, *cells)})
    hidden = [cell for cell in keys if mask.published[cell] is None]
    visible = {cell: mask.published[cell] for cell in keys if cell not in hidden}
    values = set()
    for candidate in product(range(maximum + 1), repeat=len(hidden)):
        world = {**visible, **dict(zip(hidden, candidate, strict=True))}
        if all(world[margin] == sum(world[cell] for cell in cells) for margin, cells in equations):
            values.add(world[target])
    return values


@pytest.mark.django_db
def test_common_margin_does_not_recover_a_hidden_cell_across_reports(facility):
    counts = [count("all", 12), count("alpha", 7), count("bravo", 1), count("outside", 4), count("mapped", 8)]
    equations = [
        (key("all"), (key("alpha"), key("bravo"), key("outside"))),
        (key("mapped"), (key("alpha"), key("bravo"))),
    ]
    partitions = [
        shared_disclosure.CountPartition(str(index), margin, cells) for index, (margin, cells) in enumerate(equations)
    ]
    mask = shared_disclosure.protect_counts(facility, counts, partitions)
    possible = possible_targets(mask, key("bravo"), equations, maximum=12)
    assert 1 in possible
    assert len(possible) >= 2, "Combining the two report margins must not recover Bravo"
    assert mask.published[key("bravo")] is None
    assert mask.published[key("alpha")] is None
    assert mask.threshold == 5
    assert mask.facility_id == facility.pk


@pytest.mark.django_db
def test_aliases_share_secondary_suppression_and_input_order_is_irrelevant(facility):
    counts = [count("all", 11), count("alpha", 10), count("bravo", 1)]
    relation = shared_disclosure.CountPartition("types", key("all"), (key("alpha"), key("bravo")))
    forward = shared_disclosure.protect_counts(facility, counts, [relation])
    reverse = shared_disclosure.protect_counts(facility, list(reversed(counts)), [relation, relation])
    assert forward.published == reverse.published
    assert forward.published[key("alpha")] is None
    assert len(possible_targets(forward, key("bravo"), [(relation.margin, relation.cells)], maximum=11)) >= 2


@pytest.mark.django_db
def test_bound_restrictions_propagate_through_overlapping_margins(facility):
    # A=U+V and B=V+W: knowing A=11, B=10 and U=1 forces W=0.
    # Replacing W by a sensitive singleton makes the difference attack explicit.
    counts = [count("a", 12), count("b", 11), count("u", 1), count("v", 11), count("w", 0)]
    relations = [
        shared_disclosure.CountPartition("a", key("a"), (key("u"), key("v"))),
        shared_disclosure.CountPartition("b", key("b"), (key("v"), key("w"))),
    ]
    mask = shared_disclosure.protect_counts(facility, counts, relations)
    possible = possible_targets(mask, key("u"), [(r.margin, r.cells) for r in relations], maximum=12)
    assert 1 in possible and len(possible) >= 2


@pytest.mark.django_db
def test_temporal_prefix_difference_cannot_recover_a_small_remainder(facility):
    population = shared_disclosure.TemporalPopulation(
        "event_counts",
        "all-events",
        tuple([(str(i), date(2026, 1, 15)) for i in range(10)] + [("last", date(2026, 3, 15))]),
    )
    windows = ((START, date(2026, 2, 28)), (START, END))
    mask = shared_disclosure.protect_temporal_counts(facility, [population], windows)
    prefix = mask.published[key("all-events", *windows[0])]
    whole = mask.published[key("all-events", *windows[1])]
    possible = {whole - prefix} if whole is not None and prefix is not None else set(range(1, 5))
    assert 1 in possible and len(possible) >= 2, "Jan–Mar minus Jan–Feb must not reveal one event"


@pytest.mark.django_db
def test_temporal_person_populations_use_distinct_union_not_monthly_sums(facility):
    population = shared_disclosure.TemporalPopulation(
        "person_counts",
        "active-persons",
        tuple((str(i), day) for day in (date(2026, 1, 15), date(2026, 2, 15)) for i in range(6)),
    )
    mask = shared_disclosure.protect_temporal_counts(facility, [population], ((START, END),))
    assert mask.published[key("active-persons", dimension="person_counts")] == 6


@pytest.mark.django_db
def test_invalid_raw_partition_is_rejected_instead_of_treating_it_as_safe(facility):
    with pytest.raises(ValueError, match="partition"):
        shared_disclosure.protect_counts(
            facility,
            [count("all", 8), count("alpha", 7), count("bravo", 2)],
            [shared_disclosure.CountPartition("bad", key("all"), (key("alpha"), key("bravo")))],
        )


@pytest.mark.django_db
def test_person_sensitive_secondary_cell_keeps_two_feasible_values(facility):
    counts = [count("all", 6), count("alpha", 5, hidden=True, person_sensitive=True), count("bravo", 1)]
    relation = shared_disclosure.CountPartition("persons", key("all"), (key("alpha"), key("bravo")))
    mask = shared_disclosure.protect_counts(facility, counts, [relation])
    assert len(possible_targets(mask, key("alpha"), [(relation.margin, relation.cells)], maximum=6)) >= 2


@pytest.mark.django_db
def test_no_process_cache_reuses_another_facility_threshold(facility, second_facility):
    from core.models import Settings

    Settings.objects.update_or_create(facility=second_facility, defaults={"k_anonymity_threshold": 1})
    counts = [count("all", 3)]
    first = shared_disclosure.protect_counts(facility, counts, [])
    second = shared_disclosure.protect_counts(second_facility, counts, [])
    assert first.published[key("all")] is None
    assert second.published[key("all")] == 3
    assert first.facility_id != second.facility_id


@pytest.mark.django_db
def test_three_overlapping_partitions_do_not_reveal_a_hidden_atom(facility):
    # Recipient calculation: z = (xz + yz - xy) / 2 = 1.
    counts = [
        count("x", 20, hidden=True),
        count("y", 20, hidden=True),
        count("z", 1),
        count("xy", 40),
        count("xz", 21),
        count("yz", 21),
    ]
    relations = [
        shared_disclosure.CountPartition("xy", key("xy"), (key("x"), key("y"))),
        shared_disclosure.CountPartition("xz", key("xz"), (key("x"), key("z"))),
        shared_disclosure.CountPartition("yz", key("yz"), (key("y"), key("z"))),
    ]
    mask = shared_disclosure.protect_counts(facility, counts, relations)
    xy, xz, yz = (mask.published[key(name)] for name in ("xy", "xz", "yz"))
    possibilities = {(xz + yz - xy) // 2} if all(value is not None for value in (xy, xz, yz)) else set(range(1, 5))
    assert 1 in possibilities and len(possibilities) >= 2


@pytest.mark.django_db
def test_unprotectable_row_existence_requires_table_removal(facility):
    from core.models import Settings

    Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 2})
    singleton = count("singleton", 1, lower=1)
    mask = shared_disclosure.protect_counts(facility, [singleton], [])
    assert mask.published[singleton.key] is None
    assert singleton.key in mask.dropped


@pytest.mark.django_db
def test_conflicting_aliases_cannot_silently_overwrite_a_raw_count(facility):
    with pytest.raises(ValueError, match="Conflicting"):
        shared_disclosure.protect_counts(facility, [count("same", 5), count("same", 6)], [])


@pytest.mark.django_db
def test_raw_event_populations_are_facility_scoped_and_use_database_calendar_dates(
    facility, second_facility, admin_user, second_facility_user
):
    from datetime import datetime

    from django.utils import timezone

    from tests._factories import make_client, make_document_type, make_event

    document_type = make_document_type(facility=facility, name="Local")
    person = make_client(facility=facility, pseudonym="local-person", created_by=admin_user, age_cluster="18_26")
    other_type = make_document_type(facility=second_facility, name="Foreign")
    for scope, creator, event_type, client, deleted in (
        (facility, admin_user, document_type, person, False),
        (facility, admin_user, document_type, person, True),
        (second_facility, second_facility_user, other_type, None, False),
    ):
        make_event(
            facility=scope,
            client=client,
            document_type=event_type,
            created_by=creator,
            occurred_at=timezone.make_aware(datetime(2026, 1, 15, 12)),
            data_json={},
            is_deleted=deleted,
        )
    populations = shared_disclosure.collect_event_populations(facility, START, END)
    mask = shared_disclosure.protect_temporal_counts(facility, populations, ((START, END),))
    # Use raw memberships too: suppression must not conceal a scoping mistake.
    by_identity = {(population.dimension, population.identity): population.members for population in populations}
    assert len(by_identity[("event_counts", "all-events")]) == 1
    assert by_identity[("person_counts", "active-persons")] == ((str(person.pk), date(2026, 1, 15)),)
    assert ("event_counts", f"document-type:{other_type.pk}") not in by_identity
    assert key(f"document-type:{document_type.pk}") in mask.published
    assert mask.published[key("all-events")] is None
    assert all(day == date(2026, 1, 15) for members in by_identity.values() for _, day in members)


@pytest.mark.django_db
def test_raw_person_populations_preserve_repeated_memberships_for_distinct_union(facility, admin_user):
    from datetime import datetime

    from django.utils import timezone

    from tests._factories import make_client, make_document_type, make_event

    document_type = make_document_type(facility=facility, name="Repeated contacts")
    person = make_client(facility=facility, pseudonym="same-person", created_by=admin_user)
    for month in (1, 2):
        make_event(
            facility=facility,
            client=person,
            document_type=document_type,
            created_by=admin_user,
            occurred_at=timezone.make_aware(datetime(2026, month, 15, 12)),
            data_json={},
        )
    populations = shared_disclosure.collect_event_populations(facility, START, END)
    person_population = next(population for population in populations if population.identity == "active-persons")
    assert person_population.members == ((str(person.pk), date(2026, 1, 15)), (str(person.pk), date(2026, 2, 15)))


@pytest.mark.django_db
def test_temporal_subset_margin_cannot_recover_an_excluded_unit(facility):
    all_members = tuple((str(i), date(2026, 1, 15)) for i in range(11))
    all_events = shared_disclosure.TemporalPopulation("event_counts", "all-events", all_members)
    mapped_events = shared_disclosure.TemporalPopulation("event_counts", "mapped-events", all_members[:10])
    mask = shared_disclosure.protect_temporal_counts(facility, [all_events, mapped_events], ((START, END),))
    total = mask.published[key("all-events")]
    mapped = mask.published[key("mapped-events")]
    possibilities = {total - mapped} if total is not None and mapped is not None else set(range(1, 5))
    assert 1 in possibilities and len(possibilities) >= 2


@pytest.mark.django_db
def test_table_suppression_from_one_consumer_is_shared_with_its_alias(facility):
    counts = [count("all", 8), count("alpha", 7, drop=True), count("alpha", 7), count("bravo", 1)]
    partition = shared_disclosure.CountPartition("table", key("all"), (key("alpha"), key("bravo")))
    mask = shared_disclosure.protect_counts(facility, counts, [partition])
    assert mask.dropped == frozenset({key("all"), key("alpha"), key("bravo")})
    assert set(mask.published.values()) == {None}


@pytest.mark.django_db
def test_integer_worlds_not_only_real_interval_bounds_protect_hidden_counts(facility):
    # a+b=10, b+c=11, a+c+d=10 has one integer solution inside
    # a,c,d in [0,4]: b=7, a=3, c=4, d=3. Real bounds alone miss it.
    counts = [
        count("a", 3),
        count("b", 7, hidden=True),
        count("c", 4),
        count("d", 3),
        count("ab", 10),
        count("bc", 11),
        count("acd", 10),
    ]
    equations = [
        (key("ab"), (key("a"), key("b"))),
        (key("bc"), (key("b"), key("c"))),
        (key("acd"), (key("a"), key("c"), key("d"))),
    ]
    relations = [
        shared_disclosure.CountPartition(str(index), margin, cells) for index, (margin, cells) in enumerate(equations)
    ]
    mask = shared_disclosure.protect_counts(facility, counts, relations)
    possibilities = set()
    for a, b, c, d in product(range(5), range(13), range(5), range(5)):
        world = {
            key("a"): a,
            key("b"): b,
            key("c"): c,
            key("d"): d,
            key("ab"): a + b,
            key("bc"): b + c,
            key("acd"): a + c + d,
        }
        if all(published is None or world[cell] == published for cell, published in mask.published.items()):
            possibilities.add(d)
    assert 3 in possibilities and len(possibilities) >= 2


@pytest.mark.django_db
def test_unknown_temporal_suppression_identity_is_rejected(facility):
    population = shared_disclosure.TemporalPopulation("event_counts", "all-events", (("unit", START),))
    with pytest.raises(ValueError, match="Unknown"):
        shared_disclosure.protect_temporal_counts(
            facility, [population], ((START, END),), hidden_keys={key("misspelled-population")}
        )


def test_missing_facility_is_rejected_before_any_database_access():
    with pytest.raises(ValueError, match="facility"):
        shared_disclosure.protect_counts(None, [], [])


@pytest.mark.django_db
def test_temporal_table_drop_is_part_of_the_complete_mask(facility):
    population = shared_disclosure.TemporalPopulation(
        "event_counts", "all-events", tuple((str(i), START) for i in range(8))
    )
    target = key("all-events")
    mask = shared_disclosure.protect_temporal_counts(facility, [population], ((START, END),), dropped_keys={target})
    assert target in mask.dropped
    assert mask.published[target] is None


@pytest.mark.django_db
def test_temporal_row_existence_cannot_use_zero_as_a_false_alternative(facility):
    from core.models import Settings

    Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 2})
    population = shared_disclosure.TemporalPopulation("event_counts", "all-events", (("unit", START),))
    target = key("all-events")
    mask = shared_disclosure.protect_temporal_counts(
        facility, [population], ((START, END),), observations=[shared_disclosure.RawCount(target, 1, lower=1)]
    )
    assert target in mask.dropped, "A listed positive row cannot claim that zero is a feasible recipient world"
    assert mask.published[target] is None


@pytest.mark.django_db
def test_equal_unit_unions_share_stricter_local_masks_across_calendar_aliases(facility):
    population = shared_disclosure.TemporalPopulation(
        "event_counts", "all-events", tuple((str(i), date(2026, 1, 15)) for i in range(7))
    )
    prefix = key("all-events", START, date(2026, 2, 28))
    whole = key("all-events")
    mask = shared_disclosure.protect_temporal_counts(
        facility,
        [population],
        ((prefix.date_from, prefix.date_to), (whole.date_from, whole.date_to)),
        observations=[shared_disclosure.RawCount(prefix, 7, hidden=True)],
    )
    assert mask.published[prefix] is None
    assert mask.published[whole] is None, "The same unit union must not bypass a stricter alias decision"
