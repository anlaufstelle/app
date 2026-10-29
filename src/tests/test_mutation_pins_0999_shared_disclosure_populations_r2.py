"""Pins for exact live event and person population memberships.

Source: mutation delta of the release-0.99.9 checkpoint, 14 mutants.
Groups: inclusive interval and diagnostics (4), anonymous stage (6),
unknown stage (1), chronological and identifier ordering (3).
"""

from datetime import date, datetime
from uuid import UUID

import pytest
from django.utils import timezone

from core.services.dashboard.shared_disclosure import collect_event_populations
from tests._factories import make_client, make_document_type, make_event

pytestmark = pytest.mark.django_db
DAY = date(2026, 1, 15)


@pytest.fixture
def population_event(facility, admin_user):
    """Create real source events with explicit local calendar dates."""
    document_type = make_document_type(facility=facility, name="Population contacts", report_key="contact")

    def create(day=DAY, person=None, **overrides):
        return make_event(
            facility=facility,
            client=person,
            document_type=document_type,
            created_by=admin_user,
            occurred_at=timezone.make_aware(datetime.combine(day, datetime.min.time()).replace(hour=12)),
            data_json={},
            **overrides,
        )

    return create


def test_single_day_interval_includes_only_its_calendar_day(facility, population_event):
    """Inclusive boundaries exclude adjacent days: x_collect_event_populations__mutmut_11,
    x_collect_event_populations__mutmut_43, x_collect_event_populations__mutmut_44.
    """
    population_event(date(2026, 1, 14))
    included = population_event()
    population_event(date(2026, 1, 16))

    populations = collect_event_populations(facility, DAY, DAY)
    members = {(p.dimension, p.identity): p.members for p in populations}

    assert members[("event_counts", "all-events")] == ((str(included.pk), DAY),)
    assert all(day == DAY for population in populations for _, day in population.members)


def test_anonymous_stage_keeps_linked_and_personless_events(facility, admin_user, population_event):
    """Anonymous memberships use the canonical key for both counting units:
    x_collect_event_populations__mutmut_55, x_collect_event_populations__mutmut_61,
    x_collect_event_populations__mutmut_63, x_collect_event_populations__mutmut_64,
    x_collect_event_populations__mutmut_65, x_collect_event_populations__mutmut_66.
    """
    person = make_client(facility=facility, pseudonym="Linked anonymous", created_by=admin_user)
    linked = population_event(person=person, is_anonymous=True)
    personless = population_event(is_anonymous=False)
    identified = population_event(person=person, is_anonymous=False)

    members = {(p.dimension, p.identity): p.members for p in collect_event_populations(facility, DAY, DAY)}

    assert set(members[("event_counts", "all-events")]) == {
        (str(event.pk), DAY) for event in (linked, personless, identified)
    }
    assert set(members[("event_counts", "contact-stage:anonym")]) == {
        (str(linked.pk), DAY),
        (str(personless.pk), DAY),
    }
    assert members[("person_counts", "contact-stage:anonym")] == ((str(person.pk), DAY),)
    assert {identity for _, identity in members if identity.startswith("contact-stage:")} == {
        "contact-stage:anonym",
        "contact-stage:identifiziert",
        "contact-stage:qualifiziert",
    }


def test_unknown_stored_stage_does_not_invent_a_population(facility, admin_user, population_event):
    """Unrecognised stages retain totals without artificial stage keys: x_collect_event_populations__mutmut_82."""
    person = make_client(facility=facility, pseudonym="Legacy stage", created_by=admin_user, contact_stage="legacy")
    event = population_event(person=person, is_anonymous=False)

    members = {(p.dimension, p.identity): p.members for p in collect_event_populations(facility, DAY, DAY)}

    assert members[("event_counts", "all-events")] == ((str(event.pk), DAY),)
    assert members[("person_counts", "active-persons")] == ((str(person.pk), DAY),)
    assert {identity for _, identity in members if identity.startswith("contact-stage:")} == {
        "contact-stage:anonym",
        "contact-stage:identifiziert",
        "contact-stage:qualifiziert",
    }
    assert all(not units for (_, identity), units in members.items() if identity.startswith("contact-stage:"))


def test_members_are_chronological_with_identifier_ties(facility, admin_user, population_event):
    """Dates precede identifiers and same-day identifiers are stable:
    x_collect_event_populations__mutmut_218, x_collect_event_populations__mutmut_220,
    x_collect_event_populations__mutmut_223.
    """
    early_ids = [UUID(int=number) for number in range(32, 64)]
    late_id = UUID(int=1)
    for identifier in [late_id, *reversed(early_ids)]:
        person = make_client(facility=facility, pseudonym=f"Person {identifier}", created_by=admin_user, id=identifier)
        population_event(date(2026, 1, 16) if identifier == late_id else DAY, person=person)

    populations = collect_event_populations(facility, DAY, date(2026, 1, 16))
    active = next(p for p in populations if p.dimension == "person_counts" and p.identity == "active-persons")

    assert active.members == tuple((str(identifier), DAY) for identifier in early_ids) + (
        (str(late_id), date(2026, 1, 16)),
    )


def test_reversed_interval_explains_the_invalid_input(facility):
    """Reversed dates retain the exact diagnostic: x_collect_event_populations__mutmut_12."""
    with pytest.raises(ValueError) as error:
        collect_event_populations(facility, date(2026, 1, 16), DAY)

    assert str(error.value) == "Invalid population collection interval"
