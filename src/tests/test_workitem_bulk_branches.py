"""Coverage-Tests fuer ``core.views.workitem_bulk`` Branches.

Deckt die Branches:

* HX-Request -> HTMX-Redirect (Lines 83-85).
* Bulk-Priority mit ungueltigem Wert (Line 107).
* Bulk-Assign mit unbekannter User-PK (Lines 123-124).

Refs Welle 10 / Bucket D — siehe #949.
"""

import pytest
from django.urls import reverse


@pytest.fixture
def workitem(facility, staff_user):
    from core.models import WorkItem

    return WorkItem.objects.create(
        facility=facility,
        title="Test-Aufgabe",
        created_by=staff_user,
        assigned_to=staff_user,
    )


@pytest.mark.django_db
class TestWorkItemBulkStatusViewHTMX:
    def test_htmx_request_returns_hx_redirect(self, client, staff_user, workitem):
        """Lines 83-85: HX-Request -> redirect mit ``HX-Redirect``-Header."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": [str(workitem.pk)], "status": "done"},
            HTTP_HX_REQUEST="true",
        )
        # 302 + HX-Redirect-Header
        assert response.status_code == 302
        assert "HX-Redirect" in response.headers


@pytest.mark.django_db
class TestWorkItemBulkPriorityValidation:
    def test_invalid_priority_returns_400(self, client, staff_user, workitem):
        """Line 107: Priority nicht in ``WorkItem.Priority.values`` -> 400."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_bulk_priority"),
            {"workitem_ids": [str(workitem.pk)], "priority": "ULTRA_HIGH"},
        )
        assert response.status_code == 400
        assert b"Priorit" in response.content  # "Ungültige Priorität"


@pytest.mark.django_db
class TestWorkItemBulkAssignUnknownUser:
    def test_unknown_assignee_returns_400(self, client, staff_user, workitem):
        """Lines 123-124: ``User.DoesNotExist`` -> 400 mit "Unbekannte Benutzerin/Benutzer"."""
        import uuid

        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_bulk_assign"),
            {"workitem_ids": [str(workitem.pk)], "assigned_to": str(uuid.uuid4())},
        )
        assert response.status_code == 400
        assert b"Unbekannte" in response.content
