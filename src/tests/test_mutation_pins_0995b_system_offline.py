"""Pins for the joined document type of the person offline bundle (``core.services.system.offline``).

Source: mutation delta of checkpoint release-0.99.5, 1 mutant in
``build_client_offline_bundle``: ``select_related`` loses ``document_type``.
The bundle content stays identical, but every serialised event reads the type's
name, so building the bundle issues one extra query per event (up to the bundle
limit). The pin compares the number of queries for a bundle with one event
against a bundle with three events of three different document types: a
query count independent of the number of events is the contract, the absolute
number is not pinned. All document types exist before the first measurement,
so the type list of the bundle is the same in both measurements; the events
carry no field values, so no per-event field lookup is involved.
"""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import DocumentType, Event
from core.services.system.offline import build_client_offline_bundle

pytestmark = pytest.mark.django_db


@pytest.fixture
def event_types(facility):
    return [DocumentType.objects.create(facility=facility, name=f"Offline type {n}") for n in range(3)]


def _event(facility, person, doc_type, user, minutes_ago):
    return Event.objects.create(
        facility=facility,
        client=person,
        document_type=doc_type,
        occurred_at=timezone.now() - timedelta(minutes=minutes_ago),
        data_json={},
        created_by=user,
    )


def _bundle(user, facility, person):
    with CaptureQueriesContext(connection) as ctx:
        bundle = build_client_offline_bundle(user, facility, person)
    return len(ctx.captured_queries), bundle


class TestBuildClientOfflineBundle:
    """``build_client_offline_bundle``: the events are loaded with their document type."""

    def test_query_count_independent_of_event_count(self, facility, staff_user, client_identified, event_types):
        """x_build_client_offline_bundle__mutmut_10: without the join each serialised
        event fetches its document type separately (three events cost two more queries
        than one).
        """
        _event(facility, client_identified, event_types[0], staff_user, minutes_ago=30)
        _bundle(staff_user, facility, client_identified)
        single_count, single = _bundle(staff_user, facility, client_identified)

        _event(facility, client_identified, event_types[1], staff_user, minutes_ago=20)
        _event(facility, client_identified, event_types[2], staff_user, minutes_ago=10)
        triple_count, triple = _bundle(staff_user, facility, client_identified)

        assert len(single["events"]) == 1
        assert [ev["document_type_name"] for ev in triple["events"]] == [dt.name for dt in reversed(event_types)]
        assert triple_count == single_count
