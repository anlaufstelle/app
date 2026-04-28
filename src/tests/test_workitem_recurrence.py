"""Tests for recurring WorkItems (Refs #266)."""

from datetime import date, timedelta

import pytest

from core.models import WorkItem
from core.services.workitems import (
    _add_months,
    _next_due_date,
    duplicate_recurring_workitem,
    update_workitem_status,
)


@pytest.fixture
def recurring_monthly_workitem(facility, client_identified, staff_user):
    return WorkItem.objects.create(
        facility=facility,
        client=client_identified,
        created_by=staff_user,
        assigned_to=staff_user,
        item_type=WorkItem.ItemType.TASK,
        title="Monatliche Statistik",
        description="Statistik zum Monatsende erstellen",
        priority=WorkItem.Priority.IMPORTANT,
        status=WorkItem.Status.IN_PROGRESS,
        due_date=date(2026, 4, 30),
        recurrence=WorkItem.Recurrence.MONTHLY,
    )


@pytest.fixture
def recurring_weekly_workitem(facility, staff_user):
    return WorkItem.objects.create(
        facility=facility,
        created_by=staff_user,
        item_type=WorkItem.ItemType.TASK,
        title="Teambesprechung",
        priority=WorkItem.Priority.NORMAL,
        status=WorkItem.Status.IN_PROGRESS,
        due_date=date(2026, 4, 17),
        recurrence=WorkItem.Recurrence.WEEKLY,
    )


@pytest.fixture
def non_recurring_workitem(facility, staff_user):
    return WorkItem.objects.create(
        facility=facility,
        created_by=staff_user,
        item_type=WorkItem.ItemType.TASK,
        title="Einmalige Aufgabe",
        priority=WorkItem.Priority.NORMAL,
        status=WorkItem.Status.IN_PROGRESS,
        due_date=date(2026, 4, 17),
        recurrence=WorkItem.Recurrence.NONE,
    )


@pytest.mark.django_db
class TestWorkItemRecurrence:
    """Behaviour for the recurrence field and auto-duplicate-on-done hook."""

    def test_monthly_recurrence_creates_new_workitem_on_done(self, staff_user, recurring_monthly_workitem):
        update_workitem_status(recurring_monthly_workitem, WorkItem.Status.DONE, staff_user)

        follow_ups = WorkItem.objects.filter(
            title=recurring_monthly_workitem.title,
            status=WorkItem.Status.OPEN,
        )
        assert follow_ups.count() == 1
        follow_up = follow_ups.first()
        # 30.04. + 1 month = 30.05.
        assert follow_up.due_date == date(2026, 5, 30)
        assert follow_up.recurrence == WorkItem.Recurrence.MONTHLY

    def test_none_recurrence_does_not_duplicate(self, staff_user, non_recurring_workitem):
        before = WorkItem.objects.count()
        update_workitem_status(non_recurring_workitem, WorkItem.Status.DONE, staff_user)
        after = WorkItem.objects.count()
        assert after == before, "Non-recurring tasks must not spawn duplicates."

    def test_duplicate_copies_all_relevant_fields(self, staff_user, recurring_monthly_workitem):
        new_wi = duplicate_recurring_workitem(recurring_monthly_workitem, staff_user)

        assert new_wi is not None
        assert new_wi.pk != recurring_monthly_workitem.pk
        assert new_wi.title == recurring_monthly_workitem.title
        assert new_wi.description == recurring_monthly_workitem.description
        assert new_wi.priority == recurring_monthly_workitem.priority
        assert new_wi.assigned_to_id == recurring_monthly_workitem.assigned_to_id
        assert new_wi.client_id == recurring_monthly_workitem.client_id
        assert new_wi.item_type == recurring_monthly_workitem.item_type
        assert new_wi.facility_id == recurring_monthly_workitem.facility_id
        assert new_wi.recurrence == recurring_monthly_workitem.recurrence
        # Initial state of the duplicate: open, not completed.
        assert new_wi.status == WorkItem.Status.OPEN
        assert new_wi.completed_at is None
        # Due date advanced by one interval.
        assert new_wi.due_date == date(2026, 5, 30)

    def test_weekly_adds_7_days_to_due_date(self, staff_user, recurring_weekly_workitem):
        update_workitem_status(recurring_weekly_workitem, WorkItem.Status.DONE, staff_user)

        follow_up = WorkItem.objects.filter(
            title=recurring_weekly_workitem.title,
            status=WorkItem.Status.OPEN,
        ).first()
        assert follow_up is not None
        assert follow_up.due_date == recurring_weekly_workitem.due_date + timedelta(days=7)


@pytest.mark.django_db
class TestRecurrenceHelpers:
    """Unit tests for the date-math helpers."""

    def test_add_months_clamps_end_of_month(self):
        # 31.01. + 1 month -> 28.02. (non-leap year 2026).
        assert _add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)

    def test_add_months_leap_year(self):
        # 31.01.2024 + 1 month -> 29.02.2024 (leap year).
        assert _add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)

    def test_add_months_rolls_over_year(self):
        assert _add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)

    def test_next_due_date_quarterly(self):
        assert _next_due_date(date(2026, 1, 15), WorkItem.Recurrence.QUARTERLY) == date(2026, 4, 15)

    def test_next_due_date_yearly(self):
        assert _next_due_date(date(2026, 4, 17), WorkItem.Recurrence.YEARLY) == date(2027, 4, 17)

    def test_next_due_date_none_returns_none(self):
        assert _next_due_date(date(2026, 4, 17), WorkItem.Recurrence.NONE) is None
