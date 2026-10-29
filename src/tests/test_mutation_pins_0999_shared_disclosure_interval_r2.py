"""Pins for local disclosure contracts imported into shared interval releases.

Source: mutation delta of the release-0.99.9 checkpoint, 18 reviewed mutants; 16 pinned.
Groups: live age sensitivity (5), anonymous remainder (1), category bounds (1),
hybrid age sensitivity (5), hybrid bounds (1), event sensitivity (2), dropped bounds (1).
"""

from datetime import date

import pytest

from core.services.dashboard import shared_disclosure
from core.services.dashboard.shared_field_sources import FieldSources
from tests._factories import make_document_type

START, END = date(2026, 1, 1), date(2026, 1, 31)
DAY = date(2026, 1, 15)
pytestmark = pytest.mark.django_db


def population(identity, units, *, dimension="event_counts", day=DAY):
    return shared_disclosure.TemporalPopulation(dimension, identity, tuple((str(unit), day) for unit in units))


@pytest.fixture
def release_world(facility, monkeypatch):
    from core.services.dashboard import shared_field_sources, shared_sources

    contact = make_document_type(facility=facility, name="Contact", report_key="contact")
    monkeypatch.setattr(
        shared_field_sources, "collect_field_sources", lambda facility: FieldSources((), (), {}, (), frozenset())
    )
    monkeypatch.setattr(shared_sources, "collect_additional_populations", lambda facility: ())
    monkeypatch.setattr(shared_sources, "prepare_additional_interval", lambda *args: {})
    monkeypatch.setattr(shared_field_sources, "prepare_field_interval", lambda *args: {})

    def build(populations, *, snapshots=()):
        from core.models import StatisticsSnapshot

        populations = list(populations)
        identities = {(p.dimension, p.identity) for p in populations}
        for dimension, identity in [
            ("event_counts", "age:__no_person__"),
            *(
                (dimension, f"contact-stage:{stage}")
                for dimension in ("event_counts", "person_counts")
                for stage in ("anonym", "identifiziert", "qualifiziert")
            ),
        ]:
            if (dimension, identity) not in identities:
                populations.append(population(identity, (), dimension=dimension))
        monkeypatch.setattr(shared_disclosure, "collect_event_populations", lambda *args: tuple(populations))
        for month, raw in snapshots:
            StatisticsSnapshot.objects.create(
                facility=facility, year=2026, month=month, backend_data={"sample-report-1": raw}
            )
        return shared_disclosure.build_live_release(facility, START, END)

    return build, contact


def age_populations(contact, *, sensitive):
    populations = []
    for month in (1, 2):
        day = date(2026, month, 15)
        events = range(month * 100, month * 100 + 12)
        persons = range(month * 100, month * 100 + (7 if sensitive and month == 1 else 12))
        for identity in (
            "all-events",
            "mapped-events",
            f"document-type:{contact.pk}",
            "category:Kontakte",
            "contact-stage:identifiziert",
        ):
            populations.append(population(identity, events, day=day))
        populations.extend(
            [
                population("age:u18", list(events)[:6], day=day),
                population("age:18_26", list(events)[6:], day=day),
                population("active-persons", persons, dimension="person_counts", day=day),
                population("contact-stage:identifiziert", persons, dimension="person_counts", day=day),
                population(
                    "age:u18", list(persons)[: 1 if sensitive and month == 1 else 6], dimension="person_counts", day=day
                ),
                population("age:18_26", list(persons)[-6:], dimension="person_counts", day=day),
            ]
        )
    grouped = {}
    for item in populations:
        grouped.setdefault((item.dimension, item.identity), []).extend(item.members)
    return [
        shared_disclosure.TemporalPopulation(dimension, identity, tuple(members))
        for (dimension, identity), members in grouped.items()
    ]


def test_live_age_count_cannot_be_reconstructed_by_subtracting_calendar_windows(release_world):
    """Keep live age differences private: x__prepare_live_interval__mutmut_210,
    x__prepare_live_interval__mutmut_216, x__prepare_live_interval__mutmut_222,
    x__prepare_live_interval__mutmut_223, x__prepare_live_interval__mutmut_224.
    """
    build, contact = release_world
    snapshots = [(month, {"total": 12, "by_category": [["Kontakte", 12]], "by_age_cluster": []}) for month in (1, 2)]
    release = build(age_populations(contact, sensitive=True), snapshots=snapshots)
    first = shared_disclosure.CountKey("event_counts", "age:u18", START, END)
    second = shared_disclosure.CountKey("event_counts", "age:u18", date(2026, 2, 1), date(2026, 2, 28))
    whole = shared_disclosure.CountKey("event_counts", "age:u18", START, date(2026, 2, 28))
    assert release.mask.published[first] is None
    assert None in (release.mask.published[second], release.mask.published[whole])


def test_hybrid_age_count_cannot_be_reconstructed_by_subtracting_calendar_windows(release_world):
    """Keep hybrid age differences private: x__prepare_live_interval__mutmut_366,
    x__prepare_live_interval__mutmut_372, x__prepare_live_interval__mutmut_378,
    x__prepare_live_interval__mutmut_379, x__prepare_live_interval__mutmut_380.
    """
    build, contact = release_world
    snapshots = [
        (
            month,
            {
                "total": 12,
                "by_category": [["Kontakte", 12]],
                "by_age_cluster": [
                    {"cluster": "u18", "count": 6, "persons": 1 if month == 1 else 6},
                    {"cluster": "18_26", "count": 6, "persons": 6},
                ],
            },
        )
        for month in (1, 2)
    ]
    release = build(age_populations(contact, sensitive=False), snapshots=snapshots)
    first = shared_disclosure.CountKey("event_counts", "hybrid-age:u18", START, END)
    second = shared_disclosure.CountKey("event_counts", "hybrid-age:u18", date(2026, 2, 1), date(2026, 2, 28))
    whole = shared_disclosure.CountKey("event_counts", "hybrid-age:u18", START, date(2026, 2, 28))
    assert release.mask.published[first] is None
    assert None in (release.mask.published[second], release.mask.published[whole])


def test_event_only_hidden_counts_do_not_gain_person_sensitivity(release_world):
    """Keep safe event equations: x__prepare_live_interval__mutmut_59,
    x__prepare_live_interval__mutmut_88.
    """
    build, contact = release_world
    snapshots = [
        (month, {"total": amount, "by_category": [["Kontakte", amount]], "by_age_cluster": []})
        for month, amount in ((1, 1), (2, 12))
    ]
    release = build(age_populations(contact, sensitive=False), snapshots=snapshots)
    identity = f"document-type:{contact.pk}"
    first = shared_disclosure.CountKey("event_counts", identity, START, END)
    second = shared_disclosure.CountKey("event_counts", identity, date(2026, 2, 1), date(2026, 2, 28))
    whole = shared_disclosure.CountKey("event_counts", identity, START, date(2026, 2, 28))
    assert release.mask.published[first] is None
    assert release.mask.published[second] == 12
    assert release.mask.published[whole] == 24


def test_anonymous_remainder_keeps_its_own_local_decision(release_world):
    """Do not import another age row's mask: x__prepare_live_interval__mutmut_233."""
    build, contact = release_world
    populations = []
    for item in age_populations(contact, sensitive=True):
        members = tuple((unit, day) for unit, day in item.members if day.month == 1)
        if item.dimension == "event_counts" and item.identity in {
            "all-events",
            "mapped-events",
            f"document-type:{contact.pk}",
            "category:Kontakte",
        }:
            members += tuple((str(unit), DAY) for unit in range(300, 307))
        populations.append(shared_disclosure.TemporalPopulation(item.dimension, item.identity, members))
    populations.extend(
        [
            population("age:__no_person__", range(300, 307)),
            population("contact-stage:anonym", range(300, 307)),
        ]
    )
    release = build(populations)
    remainder = next(row for row in release.general["by_age_cluster"] if row["cluster"] == "__no_person__")
    assert remainder["count"] == 7
    assert remainder["suppressed"] is False


def test_category_row_existence_constrains_overlapping_windows(release_world, facility):
    """Reject a false zero alternative: x__prepare_live_interval__mutmut_291."""
    from core.models import Settings

    Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 3})
    build, contact = release_world
    types = [contact] + [
        make_document_type(facility=facility, name=f"Other {index}", report_key="counseling") for index in range(14)
    ]
    memberships = {}

    def add(identity, units, day, dimension="event_counts"):
        memberships.setdefault((dimension, identity), []).extend((str(unit), day) for unit in units)

    for month in (1, 2):
        day = date(2026, month, 15)
        units = list(range(month * 100, month * 100 + 15))
        for identity in ("all-events", "mapped-events", "age:18_26"):
            add(identity, units, day)
        add("active-persons", units, day, "person_counts")
        add("age:18_26", units, day, "person_counts")
        add("category:Kontakte", units[:1], day)
        add("category:Beratung", units[1:], day)
        for document_type, unit in zip(types, units, strict=True):
            add(f"document-type:{document_type.pk}", [unit], day)
        for index, stage in enumerate(("anonym", "identifiziert", "qualifiziert")):
            stage_units = units[index * 5 : (index + 1) * 5]
            add(f"contact-stage:{stage}", stage_units, day)
            add(f"contact-stage:{stage}", stage_units, day, "person_counts")
    populations = [
        shared_disclosure.TemporalPopulation(dimension, identity, tuple(members))
        for (dimension, identity), members in memberships.items()
    ]
    snapshots = [
        (month, {"total": 12, "by_category": [["Kontakte", 6], ["Beratung", 6]], "by_age_cluster": []})
        for month in (1, 2)
    ]
    release = build(populations, snapshots=snapshots)
    first = shared_disclosure.CountKey("event_counts", "category:Kontakte", START, END)
    assert "by_document_type" not in release.general
    assert first in release.mask.dropped


def test_hybrid_row_existence_constrains_overlapping_windows(release_world, facility):
    """Retain positive hybrid rows: x__prepare_live_interval__mutmut_370."""
    from core.models import Settings

    Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 3})
    build, contact = release_world
    snapshots = [(month, {"total": 1, "by_category": [["Kontakte", 1]], "by_age_cluster": []}) for month in (1, 2)]
    release = build(age_populations(contact, sensitive=False), snapshots=snapshots)
    first = shared_disclosure.CountKey("event_counts", "hybrid-category:Kontakte", START, END)
    assert first in release.mask.dropped


def test_dropped_zero_snapshot_age_row_does_not_keep_a_positive_bound(release_world):
    """Permit a removed zero row: x__prepare_live_interval__mutmut_85."""
    build, contact = release_world
    populations = [population("active-persons", (), dimension="person_counts")]
    for identity in ("all-events", "mapped-events", f"document-type:{contact.pk}", "category:Kontakte"):
        populations.append(population(identity, range(6)))
    snapshots = [
        (
            1,
            {
                "total": 6,
                "by_category": [["Kontakte", 6]],
                "by_age_cluster": [{"cluster": "u18", "count": 0, "persons": 0}],
            },
        )
    ]
    release = build(populations, snapshots=snapshots)
    assert "by_age_cluster" not in release.hybrid
