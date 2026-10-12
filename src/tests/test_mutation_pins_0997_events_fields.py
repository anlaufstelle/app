"""Pins for the public event-index rebuild contract.

Source: mutation delta of the release-0.99.7 checkpoint, 6 mutants.
Groups: write default and continuation (2), locking indexed rows (1), shared
document-type loading (1). The two chunk-size mutations preserve the public
result; no exact query counts or cursor batch size are part of these pins.
"""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate
from core.services.events.fields import rebuild_event_search_text
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db


def test_shared_type_reads_do_not_grow_with_the_number_of_events(facility, sensitive_events):
    """More events sharing one type cannot cause more standalone type reads.

    Kills x_rebuild_event_search_text__mutmut_4. Compare growth rather than a
    fixed query count; joins, prefetching and a shared type cache all pass.
    """
    correct, stale = sensitive_events

    def type_reads():
        with CaptureQueriesContext(connection) as queries:
            rebuild_event_search_text(facility, dry_run=True)
        return sum(
            '"core_documenttype"' in query["sql"]
            and '"core_event"' not in query["sql"]
            and '"core_documenttypefield"' not in query["sql"]
            for query in queries
        )

    before = type_reads()
    for offset in (2, 3):
        Event.objects.create(
            facility=facility,
            document_type=correct.document_type,
            occurred_at=stale.occurred_at - timedelta(days=offset),
            data_json=correct.data_json,
        )
    assert type_reads() <= before, "Shared type loading must not become an N+1 read"


@pytest.fixture
def sensitive_events(facility, staff_user):
    """A correct first index and a later stale sensitive index, in model order."""
    document_type = DocumentType.objects.create(facility=facility, name="Elevated records", sensitivity="elevated")
    field = FieldTemplate.objects.create(facility=facility, name="Protected text", sensitivity="elevated")
    DocumentTypeField.objects.create(document_type=document_type, field_template=field)
    latest = timezone.now()
    correct = Event.objects.create(
        facility=facility,
        document_type=document_type,
        occurred_at=latest,
        data_json={field.slug: "Protected current value"},
        created_by=staff_user,
    )
    stale = Event.objects.create(
        facility=facility,
        document_type=document_type,
        occurred_at=latest - timedelta(days=1),
        data_json={field.slug: "Protected stale value"},
        created_by=staff_user,
    )
    Event.objects.filter(pk=stale.pk).update(search_text="Protected stale value")
    assert Event.objects.for_facility(facility).first().pk == correct.pk
    assert correct.search_text == ""
    return correct, stale


def test_default_rebuild_continues_after_a_correct_index(facility, sensitive_events):
    """The default rebuild clears later sensitive indexes despite an earlier match.

    Kills x_rebuild_event_search_text__mutmut_1 and
    x_rebuild_event_search_text__mutmut_26.
    """
    correct, stale = sensitive_events
    source = Event.objects.get(pk=stale.pk).data_json

    assert rebuild_event_search_text(facility) == (2, 1)

    correct.refresh_from_db()
    stale.refresh_from_db()
    assert correct.search_text == stale.search_text == ""
    assert stale.data_json == source


@pytest.mark.postgres
def test_write_rebuild_locks_the_scoped_event_rows(facility, sensitive_events):
    """A write rebuild locks event rows in its facility before changing indexes.

    Kills x_rebuild_event_search_text__mutmut_8. Redundant locks and any
    equivalent query shape are accepted; no lock count is asserted.
    """
    with mitschnitt() as queries:
        assert rebuild_event_search_text(facility, dry_run=False) == (2, 1)
    locks = queries.sperren("core_event")
    assert locks
    assert all(lock.sperrart in {"UPDATE", "NO KEY UPDATE"} for lock in locks)
    assert any(lock.traegt(facility_id=[facility.pk]) for lock in locks)
