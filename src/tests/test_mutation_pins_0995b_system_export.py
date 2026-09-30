"""Pins for the joined document type of the event CSV export (``core.services.system.export``).

Source: mutation delta of checkpoint release-0.99.5, 1 mutant in
``_get_events_queryset``: ``select_related`` loses ``document_type``. The rows
stay identical, but every CSV row reads the type's name and sensitivity, so the
export issues one extra query per event. The pin compares the number of
queries of an export with one event against an export with three events of
three different document types (two persons): a row-independent query count is
the contract, the absolute number is not pinned.
"""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Client, DocumentType, Event
from core.services.system.export import export_events_csv

pytestmark = pytest.mark.django_db


@pytest.fixture
def event_types(facility):
    return [DocumentType.objects.create(facility=facility, name=f"Export type {n}") for n in range(3)]


@pytest.fixture
def second_person(facility, staff_user):
    return Client.objects.create(facility=facility, pseudonym="Second-Person", created_by=staff_user)


def _event(facility, person, doc_type, user, minutes_ago):
    return Event.objects.create(
        facility=facility,
        client=person,
        document_type=doc_type,
        occurred_at=timezone.now() - timedelta(minutes=minutes_ago),
        data_json={},
        created_by=user,
    )


def _export(facility, user):
    today = timezone.localdate()
    with CaptureQueriesContext(connection) as ctx:
        chunks = list(export_events_csv(facility, today - timedelta(days=1), today + timedelta(days=1), user=user))
    return len(ctx.captured_queries), chunks


class TestGetEventsQueryset:
    """``_get_events_queryset``: the export joins the document type once for all rows."""

    def test_query_count_independent_of_row_count(
        self, facility, staff_user, client_identified, second_person, event_types
    ):
        """x__get_events_queryset__mutmut_20: without the join each row fetches its
        document type separately (three events cost two more queries than one).
        """
        _event(facility, client_identified, event_types[0], staff_user, minutes_ago=30)
        _export(facility, staff_user)
        single_count, single_chunks = _export(facility, staff_user)

        _event(facility, second_person, event_types[1], staff_user, minutes_ago=20)
        _event(facility, client_identified, event_types[2], staff_user, minutes_ago=10)
        triple_count, triple_chunks = _export(facility, staff_user)

        assert len(triple_chunks) == len(single_chunks) + 2
        assert all(any(dt.name in chunk for chunk in triple_chunks) for dt in event_types)
        assert triple_count == single_count
