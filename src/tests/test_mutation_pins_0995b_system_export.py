"""Pins for the joined document type of the event CSV export (``core.services.system.export``).

Source: mutation delta of checkpoint release-0.99.5, 1 mutant in
``_get_events_queryset``: ``select_related`` loses ``document_type``. The rows
stay identical, but every CSV row reads the type's name and sensitivity, so the
export issues one extra query per event. The pin compares the number of
queries of an export with one event against an export with three events of
three different document types (two persons): a row-independent query count is
the contract, the absolute number is not pinned.

The export promises CSV data in chunks, not a chunk layout: the pin joins the
chunks and parses the result, so a generator that groups several rows into one
chunk (or splits a row) produces the same observation.
"""

import csv
import io
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Client, DocumentType, Event
from core.services.system.export import export_events_csv

pytestmark = pytest.mark.django_db

# Static leading columns of every record row (see ``_build_event_row``).
DOCUMENT_TYPE_COLUMN = 2
PERSON_COLUMN = 3


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
    """Run the export and return ``(query count, header, record rows)`` of the joined CSV."""
    today = timezone.localdate()
    with CaptureQueriesContext(connection) as ctx:
        content = "".join(export_events_csv(facility, today - timedelta(days=1), today + timedelta(days=1), user=user))
    metadata, header, *records = csv.reader(io.StringIO(content))
    assert metadata[0].startswith("# Export: ")
    assert all(len(record) == len(header) for record in records)
    return len(ctx.captured_queries), header, records


def _type_and_person(records):
    return [(record[DOCUMENT_TYPE_COLUMN], record[PERSON_COLUMN]) for record in records]


class TestGetEventsQueryset:
    """``_get_events_queryset``: the export joins the document type once for all rows."""

    def test_query_count_independent_of_row_count(
        self, facility, staff_user, client_identified, second_person, event_types
    ):
        """x__get_events_queryset__mutmut_20: without the join each row fetches its
        document type separately (three events cost two more queries than one).

        Events are created oldest first, so the newest-first export order is the
        reverse of the primary key order.
        """
        _event(facility, client_identified, event_types[0], staff_user, minutes_ago=30)
        _export(facility, staff_user)
        single_count, single_header, single_records = _export(facility, staff_user)

        _event(facility, second_person, event_types[1], staff_user, minutes_ago=20)
        _event(facility, client_identified, event_types[2], staff_user, minutes_ago=10)
        triple_count, triple_header, triple_records = _export(facility, staff_user)

        assert _type_and_person(single_records) == [(event_types[0].name, client_identified.pseudonym)]
        assert triple_header == single_header
        assert _type_and_person(triple_records) == [
            (event_types[2].name, client_identified.pseudonym),
            (event_types[1].name, second_person.pseudonym),
            (event_types[0].name, client_identified.pseudonym),
        ]
        assert triple_count == single_count
