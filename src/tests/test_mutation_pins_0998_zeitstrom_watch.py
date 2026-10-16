"""Pins for current-shift counts and nullable feed identities.

Source: mutation delta of the release-0.99.8 checkpoint, 10 mutants.
Groups: shift visibility and counts (4), absent people (2), absent actors (4).
Follows test_mutation_pins_0997_dashboard_external_report.py.
The public validator API runs real queries and HMAC generation. A wrapping
spy observes semantic projections because the returned HMAC is opaque.
"""

from datetime import datetime, time, timedelta
from unittest.mock import patch

import pytest
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from core.models import Activity, Client, TimeFilter
from core.services import zeitstrom_watch as watch
from core.services.zeitstrom_filters import resolve_zeitstrom_state
from tests._factories import make_client, make_document_type, make_event

pytestmark = pytest.mark.django_db


@pytest.fixture
def shift_state(facility):
    time_filter = TimeFilter.objects.create(facility=facility, label="Morning", start_time=time(6), end_time=time(14))
    return resolve_zeitstrom_state(
        {"date": "2026-06-15", "time_filter": str(time_filter.pk)},
        facility,
        today=datetime(2026, 6, 15).date(),
        now=timezone.make_aware(datetime(2026, 6, 15, 12)),
    )


def test_current_shift_counts_and_highlights_only_eligible_people(facility, staff_user, shift_state):
    """Only active events and new active, undeleted clients inside the shift count.

    Kills x__current_shift_projection__mutmut_16,
    x__current_shift_projection__mutmut_52, x__current_shift_projection__mutmut_53
    and x__current_shift_projection__mutmut_54 through the public validator.
    """
    document_type = make_document_type(facility=facility, name="Crisis", system_type="crisis")
    moment = shift_state.live_context.window_start + timedelta(hours=1)
    people = [
        make_client(facility=facility, created_by=staff_user, pseudonym=name, **flags)
        for name, flags in [
            ("Deleted", {"is_deleted": True}),
            ("Inactive", {"is_active": False}),
            ("Outside", {}),
            ("Eligible", {}),
        ]
    ]
    Client.objects.filter(pk__in=[person.pk for person in people]).update(created_at=moment)
    Client.objects.filter(pk=people[2].pk).update(created_at=moment - timedelta(days=1))
    events = [
        make_event(
            facility=facility,
            client=person,
            document_type=document_type,
            occurred_at=moment,
            data_json={},
            created_by=staff_user,
        )
        for person in (people[1], people[3])
    ]
    with patch.object(watch, "_opaque_validator", wraps=watch._opaque_validator) as signer:
        validator = watch.build_zeitstrom_component_validator(staff_user, facility, shift_state, "current_shift")
    assert validator
    projection = signer.call_args.kwargs["projection"]
    assert projection["stats"]["clients_new"] == 1
    assert projection["stats"]["events_total"] == 1
    assert [(kind, value[0]) for kind, _, value in projection["highlights"]] == [("crisis", str(events[1].pk))]


@pytest.mark.parametrize("feed_type,system_type,person_slot", [("events", "", 4), ("bans", "ban", 2)])
def test_anonymous_feed_cards_have_no_person(facility, staff_user, shift_state, feed_type, system_type, person_slot):
    """Anonymous event and ban cards project an absent person as None.

    Kills x__event_projection__mutmut_11 and x__ban_card_projection__mutmut_7
    through the public feed validator.
    """
    document_type = make_document_type(facility=facility, name="Anonymous card", system_type=system_type)
    event = make_event(
        facility=facility,
        client=None,
        document_type=document_type,
        occurred_at=shift_state.live_context.window_start,
        data_json={},
        created_by=staff_user,
        is_anonymous=True,
    )
    state = resolve_zeitstrom_state(
        {**shift_state.params, "type": feed_type},
        facility,
        today=shift_state.target_date,
        now=shift_state.live_context.window_start + timedelta(hours=1),
    )
    with patch.object(watch, "_opaque_validator", wraps=watch._opaque_validator) as signer:
        assert watch.build_zeitstrom_component_validator(staff_user, facility, state, "feed")
    projection = signer.call_args.kwargs["projection"]
    assert [(kind, value[0]) for kind, value in projection] == [
        ("event" if feed_type == "events" else "ban", str(event.pk))
    ]
    assert projection[0][1][person_slot] is None


def test_activity_without_actor_has_an_absent_actor_slot(facility, staff_user, sample_workitem, shift_state):
    """A visible activity without an actor builds a validator and a None actor slot.

    Kills x__feed_projection__mutmut_91, x__feed_projection__mutmut_95,
    x__feed_projection__mutmut_99 and x__feed_projection__mutmut_103.
    """
    activity = Activity.objects.create(
        facility=facility,
        actor=None,
        verb=Activity.Verb.UPDATED,
        target_type=ContentType.objects.get_for_model(sample_workitem),
        target_id=sample_workitem.pk,
        occurred_at=shift_state.live_context.window_start + timedelta(hours=1),
    )
    state = resolve_zeitstrom_state(
        {**shift_state.params, "type": "activities"},
        facility,
        today=shift_state.target_date,
        now=shift_state.live_context.window_start + timedelta(hours=1),
    )
    with patch.object(watch, "_opaque_validator", wraps=watch._opaque_validator) as signer:
        assert watch.build_zeitstrom_component_validator(staff_user, facility, state, "feed")
    projection = signer.call_args.kwargs["projection"]
    assert [(kind, value[0]) for kind, value in projection] == [("activity", str(activity.pk))]
    assert projection[0][1][4] is None
