"""Pins from the targeted mutation delta DEAD, batch events_dashboard.

Source: delta DEAD survivors of ``core.services.dashboard.main``
(live workitem counters), ``core.services.events.deletion`` (lock of an event
deletion approval) and ``core.services.events.tally`` (tally-origin total).
The remaining survivors of this batch are equivalent mutants.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from core.models import DeletionRequest, Event, WorkItem
from core.models.client import Client
from core.services.dashboard.main import zeitstrom_workitem_counts
from core.services.events.deletion import approve_deletion
from core.services.events.tally import build_tally_summary, current_tally_context
from tests._row_lock_helpers import mitschnitt as capture_locks

pytestmark = pytest.mark.django_db

BERLIN = ZoneInfo("Europe/Berlin")


def test_team_total_counts_every_open_item_not_only_the_users(facility, staff_user, lead_user):
    """The team total covers open and in-progress items of everyone; personal counters stay personal.

    core.services.dashboard.main.x_zeitstrom_workitem_counts__mutmut_11
    """
    today = date(2026, 5, 12)

    def item(title, assignee, status, due):
        return WorkItem.objects.create(
            facility=facility, title=title, assigned_to=assignee, created_by=lead_user, status=status, due_date=due
        )

    item("Mine overdue", staff_user, WorkItem.Status.OPEN, today - timedelta(days=1))
    item("Mine upcoming", staff_user, WorkItem.Status.IN_PROGRESS, today + timedelta(days=1))
    item("Mine done", staff_user, WorkItem.Status.DONE, today - timedelta(days=3))
    item("Colleague open", lead_user, WorkItem.Status.OPEN, today - timedelta(days=2))
    item("Unassigned open", None, WorkItem.Status.OPEN, None)

    counts = zeitstrom_workitem_counts(staff_user, facility, today=today)

    assert counts == {"total_open": 4, "my_open": 2, "my_overdue": 1}


def test_event_deletion_approval_ignores_a_request_moved_to_another_facility_after_the_locks(
    facility, second_facility, client_identified, doc_type_contact, staff_user, lead_user
):
    """A request that left the facility while the person was locked is not found, and nothing changes.

    core.services.events.deletion.x__lock_person_and_event_then_request__mutmut_36
    """
    event = Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=datetime(2026, 5, 12, 10, 0, tzinfo=BERLIN),
        created_by=staff_user,
        data_json={},
    )
    request = DeletionRequest.objects.create(
        facility=facility,
        target_type=DeletionRequest.TargetType.EVENT,
        target_id=event.pk,
        reason="Pin reason",
        requested_by=staff_user,
    )

    with capture_locks() as captured:
        captured.nach_sperre(
            Client._meta.db_table,
            lambda: DeletionRequest.objects.filter(pk=request.pk).update(facility=second_facility),
        )
        with pytest.raises(DeletionRequest.DoesNotExist):
            approve_deletion(request, lead_user)

    assert captured.offene_schranken() == []
    event.refresh_from_db()
    request.refresh_from_db()
    assert event.is_deleted is False
    assert request.status == DeletionRequest.Status.PENDING


def test_tally_total_counts_tally_origin_events_that_are_not_anonymous(facility, doc_type_contact, staff_user):
    """The tally-origin total does not depend on the anonymous flag of the entry.

    core.services.events.tally.x_build_tally_summary__mutmut_29
    """
    moment = datetime(2026, 5, 12, 10, 0, tzinfo=BERLIN)
    context = current_tally_context(facility, now=moment)

    def entry(created_via, *, anonymous):
        return Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=moment,
            created_by=staff_user,
            data_json={},
            is_anonymous=anonymous,
            created_via=created_via,
        )

    entry(Event.CreatedVia.TALLY, anonymous=True)
    entry(Event.CreatedVia.TALLY, anonymous=False)
    entry(Event.CreatedVia.TALLY, anonymous=False)
    entry(Event.CreatedVia.STANDARD, anonymous=True)
    entry(Event.CreatedVia.STANDARD, anonymous=False)

    summary = build_tally_summary(facility, staff_user, context=context)

    assert summary["tally_total"] == 3
    assert [row["count"] for row in summary["rows"] if row["document_type"].pk == doc_type_contact.pk] == [2]
