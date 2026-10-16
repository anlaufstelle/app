"""Pins for ban ownership and relation loading.

Source: mutation delta of the release-0.99.8 checkpoint, 5 mutants.
Groups: revoked owners (1), facility relations (2), client relations (2).
"""

import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Client, DocumentType, Event
from core.services.system.bans import get_active_bans, get_active_bans_for_client

pytestmark = pytest.mark.django_db


@pytest.fixture
def ban_events(facility, client_identified, staff_user):
    doc = DocumentType.objects.create(facility=facility, name="Safety ban", system_type="ban")
    return [
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc,
            occurred_at=timezone.now(),
            created_by=staff_user,
            data_json={"aktiv": True, "grund": "Safety reason"},
        )
        for _ in range(3)
    ]


@pytest.mark.parametrize("state", ["is_deleted", "is_active"], ids=["deleted", "inactive"])
def test_client_bans_exclude_revoked_owners(facility, client_identified, staff_user, ban_events, state):
    """Revoked owners have no client bans: x_get_active_bans_for_client__mutmut_22."""
    assert {entry["event"].pk for entry in get_active_bans_for_client(client_identified, staff_user)} == {
        event.pk for event in ban_events
    }
    Client.objects.filter(pk=client_identified.pk).update(**{state: state == "is_deleted"})
    assert get_active_bans_for_client(client_identified, staff_user) == []


@pytest.mark.parametrize("scope", ["facility", "client"])
def test_ban_relations_need_no_individual_fetches(facility, client_identified, staff_user, ban_events, scope):
    """Returned bans load readable relations without individual lookups.

    x_get_active_bans__mutmut_15, x_get_active_bans__mutmut_16,
    x_get_active_bans_for_client__mutmut_16, x_get_active_bans_for_client__mutmut_17.
    """
    with CaptureQueriesContext(connection) as queries:
        entries = (
            get_active_bans(facility, staff_user)
            if scope == "facility"
            else get_active_bans_for_client(client_identified, staff_user)
        )
        owners = [entry["event"].client.pk for entry in entries]
        reasons = [entry["grund"] for entry in entries]
    assert {entry["event"].pk for entry in entries} == {event.pk for event in ban_events}
    assert owners == [client_identified.pk] * len(ban_events)
    assert reasons == ["Safety reason"] * len(ban_events)
    individual_fetches = [
        query["sql"]
        for query in queries
        if re.search(r'FROM "(core_client|core_documenttype)" WHERE .*"\1"\."id" =', query["sql"])
    ]
    assert not individual_fetches
