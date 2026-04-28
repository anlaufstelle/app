"""Tests für WorkItem-Inbox-Filter."""

import pytest
from django.urls import reverse

from core.models import WorkItem


@pytest.mark.django_db
class TestWorkItemInboxFilters:
    """WorkItem-Inbox filtert nach Typ, Priorität und Zuweisung."""

    def test_filter_by_item_type(self, client, staff_user, facility):
        """Nur WorkItems des gewählten Typs werden angezeigt."""
        client.force_login(staff_user)
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            title="Aufgabe 1",
            assigned_to=staff_user,
        )
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            item_type=WorkItem.ItemType.HINT,
            title="Hinweis 1",
            assigned_to=staff_user,
        )

        response = client.get(reverse("core:workitem_inbox"), {"item_type": "task"})
        assert response.status_code == 200
        all_items = list(response.context["open_items"]) + list(response.context["in_progress_items"])
        assert all(wi.item_type == WorkItem.ItemType.TASK for wi in all_items)

    def test_filter_by_priority(self, client, staff_user, facility):
        """Nur WorkItems der gewählten Priorität werden angezeigt."""
        client.force_login(staff_user)
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            priority=WorkItem.Priority.URGENT,
            title="Dringend",
            assigned_to=staff_user,
        )
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            priority=WorkItem.Priority.NORMAL,
            title="Normal",
            assigned_to=staff_user,
        )

        response = client.get(reverse("core:workitem_inbox"), {"priority": "urgent"})
        assert response.status_code == 200
        all_items = list(response.context["open_items"]) + list(response.context["in_progress_items"])
        assert all(wi.priority == WorkItem.Priority.URGENT for wi in all_items)

    def test_filter_by_assigned_to(self, client, staff_user, lead_user, facility):
        """Nur WorkItems des zugewiesenen Users werden angezeigt."""
        client.force_login(staff_user)
        wi_staff = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=staff_user,
            title="Für Staff",
        )
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=lead_user,
            title="Für Lead",
        )

        response = client.get(reverse("core:workitem_inbox"), {"assigned_to": str(staff_user.pk)})
        assert response.status_code == 200
        open_items = list(response.context["open_items"])
        assert wi_staff in open_items

    def test_inbox_me_filter_shows_own_items_only(self, client, staff_user, lead_user, facility):
        """Sentinel 'me' zeigt nur eigene zugewiesene WorkItems."""
        client.force_login(staff_user)
        wi_self = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=staff_user,
            title="Mir",
        )
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=lead_user,
            title="Anderem",
        )

        response = client.get(reverse("core:workitem_inbox"), {"assigned_to": "me"})
        assert response.status_code == 200
        open_items = list(response.context["open_items"])
        assert wi_self in open_items
        assert all(wi.assigned_to_id == staff_user.id for wi in open_items)

    def test_inbox_me_filter_excludes_other_assigned_items(self, client, staff_user, lead_user, facility):
        """Sentinel 'me' schließt Items anderer Zuweisungen (inkl. unassigned) aus."""
        client.force_login(staff_user)
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=lead_user,
            title="Für Lead",
        )
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=None,
            title="Unassigned",
        )

        response = client.get(reverse("core:workitem_inbox"), {"assigned_to": "me"})
        assert response.status_code == 200
        open_items = list(response.context["open_items"])
        in_progress_items = list(response.context["in_progress_items"])
        done_items = list(response.context["done_items"])
        assert all(wi.assigned_to_id == staff_user.id for wi in open_items)
        assert all(wi.assigned_to_id == staff_user.id for wi in in_progress_items)
        assert all(wi.assigned_to_id == staff_user.id for wi in done_items)
        assert len(open_items) == 0

    def test_inbox_default_remains_all(self, client, staff_user, lead_user, facility):
        """Default-Verhalten ohne Filter bleibt 'Alle' (eigene + unassigned in Open/In-Progress)."""
        client.force_login(staff_user)
        wi_self = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=staff_user,
            title="Mir",
        )
        wi_unassigned = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=None,
            title="Unassigned",
        )
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=lead_user,
            title="Für Lead",
        )

        response = client.get(reverse("core:workitem_inbox"))
        assert response.status_code == 200
        open_items = list(response.context["open_items"])
        # Implicit filter: eigene + unassigned sichtbar, fremde nicht
        assert wi_self in open_items
        assert wi_unassigned in open_items
        assert len(open_items) == 2
        assert response.context["selected_assigned_to"] == ""

    def test_no_filter_returns_all(self, client, staff_user, facility):
        """Ohne Filter werden alle eigenen WorkItems angezeigt."""
        client.force_login(staff_user)
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            title="T1",
            assigned_to=staff_user,
        )
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            item_type=WorkItem.ItemType.HINT,
            title="H1",
            assigned_to=staff_user,
        )

        response = client.get(reverse("core:workitem_inbox"))
        assert response.status_code == 200
        all_items = list(response.context["open_items"])
        assert len(all_items) == 2

    def test_invalid_filter_value_ignored(self, client, staff_user, facility):
        """Ungültige Filterwerte werden ignoriert (kein Fehler)."""
        client.force_login(staff_user)
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="T1",
            assigned_to=staff_user,
        )

        response = client.get(reverse("core:workitem_inbox"), {"item_type": "invalid"})
        assert response.status_code == 200

    def test_combined_filters_me_task_urgent(self, client, staff_user, lead_user, facility):
        """Kombi-Filter ?assigned_to=me&item_type=task&priority=urgent schneidet alle drei Kriterien.

        Refs #591 WP3.
        """
        client.force_login(staff_user)

        # 1) me + task + urgent → matcht
        wi_match = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=staff_user,
            item_type=WorkItem.ItemType.TASK,
            priority=WorkItem.Priority.URGENT,
            title="Match Me+Task+Urgent",
        )
        # 2) me + task + normal → item_type OK, priority falsch
        wi_wrong_priority = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=staff_user,
            item_type=WorkItem.ItemType.TASK,
            priority=WorkItem.Priority.NORMAL,
            title="Me+Task+Normal",
        )
        # 3) me + hint + urgent → priority OK, item_type falsch
        wi_wrong_type = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=staff_user,
            item_type=WorkItem.ItemType.HINT,
            priority=WorkItem.Priority.URGENT,
            title="Me+Hint+Urgent",
        )
        # 4) lead + task + urgent → item_type+priority OK, Zuweisung falsch
        wi_wrong_assignee = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=lead_user,
            item_type=WorkItem.ItemType.TASK,
            priority=WorkItem.Priority.URGENT,
            title="Lead+Task+Urgent",
        )

        response = client.get(
            reverse("core:workitem_inbox"),
            {
                "assigned_to": "me",
                "item_type": "task",
                "priority": "urgent",
            },
        )
        assert response.status_code == 200

        all_items = (
            list(response.context["open_items"])
            + list(response.context["in_progress_items"])
            + list(response.context["done_items"])
        )
        assert wi_match in all_items
        assert wi_wrong_priority not in all_items
        assert wi_wrong_type not in all_items
        assert wi_wrong_assignee not in all_items

    def test_htmx_request_returns_partial(self, client, staff_user, facility):
        """HTMX-Requests liefern nur das Inbox-Content-Partial."""
        client.force_login(staff_user)
        response = client.get(
            reverse("core:workitem_inbox"),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert "core/workitems/partials/inbox_content.html" in [t.name for t in response.templates]
