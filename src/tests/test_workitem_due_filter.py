"""Tests for WorkItem due-date filter."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import WorkItem


@pytest.mark.django_db
class TestWorkItemDueDateFilter:
    """WorkItem inbox filters by due-date parameter."""

    def _create_workitem(self, facility, user, title, due_date=None, status=WorkItem.Status.OPEN):
        return WorkItem.objects.create(
            facility=facility,
            created_by=user,
            assigned_to=user,
            title=title,
            due_date=due_date,
            status=status,
        )

    def test_filter_overdue(self, client, staff_user, facility):
        """due=overdue shows only items with due_date < today and active status."""
        today = timezone.localdate()
        wi_overdue = self._create_workitem(facility, staff_user, "Overdue", due_date=today - timedelta(days=3))
        self._create_workitem(facility, staff_user, "Today", due_date=today)
        self._create_workitem(facility, staff_user, "Future", due_date=today + timedelta(days=5))
        self._create_workitem(facility, staff_user, "No date", due_date=None)

        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"), {"due": "overdue"})
        assert response.status_code == 200
        all_items = list(response.context["open_items"]) + list(response.context["in_progress_items"])
        assert wi_overdue in all_items
        assert len(all_items) == 1

    def test_filter_overdue_excludes_done(self, client, staff_user, facility):
        """due=overdue excludes items that are done/dismissed even if past due."""
        today = timezone.localdate()
        self._create_workitem(
            facility,
            staff_user,
            "Done overdue",
            due_date=today - timedelta(days=3),
            status=WorkItem.Status.DONE,
        )
        wi_active_overdue = self._create_workitem(
            facility,
            staff_user,
            "Active overdue",
            due_date=today - timedelta(days=1),
        )

        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"), {"due": "overdue"})
        assert response.status_code == 200
        all_items = list(response.context["open_items"]) + list(response.context["in_progress_items"])
        assert wi_active_overdue in all_items
        assert len(all_items) == 1

    def test_filter_today(self, client, staff_user, facility):
        """due=today shows only items due today."""
        today = timezone.localdate()
        self._create_workitem(facility, staff_user, "Yesterday", due_date=today - timedelta(days=1))
        wi_today = self._create_workitem(facility, staff_user, "Today", due_date=today)
        self._create_workitem(facility, staff_user, "Tomorrow", due_date=today + timedelta(days=1))

        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"), {"due": "today"})
        assert response.status_code == 200
        all_items = list(response.context["open_items"]) + list(response.context["in_progress_items"])
        assert wi_today in all_items
        assert len(all_items) == 1

    def _seed_week_window(self, facility, staff_user):
        today = timezone.localdate()
        self._create_workitem(facility, staff_user, "Yesterday", due_date=today - timedelta(days=1))
        self._create_workitem(facility, staff_user, "Today", due_date=today)
        self._create_workitem(facility, staff_user, "In 3 days", due_date=today + timedelta(days=3))
        self._create_workitem(facility, staff_user, "In 6 days", due_date=today + timedelta(days=6))
        self._create_workitem(facility, staff_user, "In 7 days", due_date=today + timedelta(days=7))

    def _titles(self, response):
        items = list(response.context["open_items"]) + list(response.context["in_progress_items"])
        return {wi.title for wi in items}

    def test_filter_next7(self, client, staff_user, facility):
        """Z2/Z4 (Refs #1804): ``due=next7`` heisst „Naechste 7 Tage" und umfasst
        genau sieben — ``heute … heute+6``. Der Vorgaenger ``due=week`` trug ein
        Kalenderwort ueber einem rollierenden 8-Tage-Fenster; im selben Screen
        stand darunter ein ISO-kalendarisches Badge „Naechste Woche (Mo)" fuer
        eine Aufgabe, die der Filter zeigte."""
        self._seed_week_window(facility, staff_user)

        client.force_login(staff_user)
        titles = self._titles(client.get(reverse("core:workitem_inbox"), {"due": "next7"}))
        assert "Today" in titles
        assert "In 3 days" in titles
        assert "In 6 days" in titles
        assert "Yesterday" not in titles
        assert "In 7 days" not in titles, "Der siebte Folgetag ist der achte Tag — ausserhalb des Fensters."

    def test_alter_week_wert_bleibt_stiller_alias(self, client, staff_user, facility):
        """F5 (Refs #1804): ``filter-persistence.js`` haelt Select-Werte im
        sessionStorage. Ein reiner Rename liesse gespeicherte ``week``-Werte in
        ein Select ohne passende Option zurueckfliessen — der Alias bleibt
        dauerhaft, wird aber nirgends mehr angeboten."""
        self._seed_week_window(facility, staff_user)

        client.force_login(staff_user)
        alias = client.get(reverse("core:workitem_inbox"), {"due": "week"})
        kanonisch = client.get(reverse("core:workitem_inbox"), {"due": "next7"})
        assert self._titles(alias) == self._titles(kanonisch)
        assert alias.context["selected_due"] == "next7", (
            "Das Select muss den kanonischen Wert zeigen, sonst faellt die Auswahl auf 'Alle' zurueck."
        )

    def test_week_wird_nicht_mehr_angeboten(self, client, staff_user, facility):
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"))
        choice_keys = [c[0] for c in response.context["due_filter_choices"]]
        assert "week" not in choice_keys
        assert "next7" in choice_keys

    def test_filter_none(self, client, staff_user, facility):
        """due=none shows only items without a due date."""
        today = timezone.localdate()
        self._create_workitem(facility, staff_user, "With date", due_date=today)
        wi_none = self._create_workitem(facility, staff_user, "No date", due_date=None)

        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"), {"due": "none"})
        assert response.status_code == 200
        all_items = list(response.context["open_items"]) + list(response.context["in_progress_items"])
        assert wi_none in all_items
        assert len(all_items) == 1

    def test_no_due_filter_returns_all(self, client, staff_user, facility):
        """Without due filter all items are returned."""
        today = timezone.localdate()
        self._create_workitem(facility, staff_user, "Past", due_date=today - timedelta(days=1))
        self._create_workitem(facility, staff_user, "Today", due_date=today)
        self._create_workitem(facility, staff_user, "Future", due_date=today + timedelta(days=5))
        self._create_workitem(facility, staff_user, "None", due_date=None)

        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"))
        assert response.status_code == 200
        all_items = list(response.context["open_items"]) + list(response.context["in_progress_items"])
        assert len(all_items) == 4

    def test_invalid_due_value_ignored(self, client, staff_user, facility):
        """Invalid due filter value is ignored (no error, all items shown)."""
        today = timezone.localdate()
        self._create_workitem(facility, staff_user, "T1", due_date=today)
        self._create_workitem(facility, staff_user, "T2", due_date=None)

        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"), {"due": "invalid_value"})
        assert response.status_code == 200
        all_items = list(response.context["open_items"]) + list(response.context["in_progress_items"])
        assert len(all_items) == 2

    def test_selected_due_in_context(self, client, staff_user, facility):
        """The selected_due value is passed to the template context."""
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"), {"due": "today"})
        assert response.status_code == 200
        assert response.context["selected_due"] == "today"

    def test_due_filter_choices_in_context(self, client, staff_user, facility):
        """due_filter_choices are available in template context."""
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"))
        assert response.status_code == 200
        choices = response.context["due_filter_choices"]
        choice_keys = [c[0] for c in choices]
        assert "overdue" in choice_keys
        assert "today" in choice_keys
        assert "next7" in choice_keys
        assert "none" in choice_keys

    def test_due_combined_with_priority_filter(self, client, staff_user, facility):
        """Due filter works in combination with priority filter."""
        today = timezone.localdate()
        self._create_workitem(facility, staff_user, "Urgent today", due_date=today)
        WorkItem.objects.filter(title="Urgent today").update(priority=WorkItem.Priority.URGENT)
        self._create_workitem(facility, staff_user, "Normal today", due_date=today)

        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"), {"due": "today", "priority": "urgent"})
        assert response.status_code == 200
        all_items = list(response.context["open_items"]) + list(response.context["in_progress_items"])
        assert len(all_items) == 1
        assert all_items[0].title == "Urgent today"
