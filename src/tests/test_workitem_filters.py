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

    def test_htmx_request_returns_partial(self, client, staff_user, facility):
        """HTMX-Requests liefern nur das Inbox-Content-Partial."""
        client.force_login(staff_user)
        response = client.get(
            reverse("core:workitem_inbox"),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert "core/workitems/partials/inbox_content.html" in [t.name for t in response.templates]
