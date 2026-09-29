"""Coverage-Tests fuer ``core.views.workitem_bulk`` Branches.

Deckt die Branches:

* HX-Request -> HTMX-Redirect (Lines 83-85).
* Bulk-Priority mit ungueltigem Wert (Line 107).
* Bulk-Assign mit unbekannter User-PK (Lines 123-124).

Refs #949.
"""

import pytest
from django.contrib.messages import get_messages
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
    def test_invalid_priority_redirects_with_message(self, client, staff_user, workitem):
        """Line 107: priority not in ``WorkItem.Priority.values`` -> redirect
        + flash (deliberately backported, ticket #2385: previously a
        plain-text 400 — ``inbox.html`` calls this view via an ordinary
        form without ``hx-*``)."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_bulk_priority"),
            {"workitem_ids": [str(workitem.pk)], "priority": "ULTRA_HIGH"},
        )
        assert response.status_code == 302
        assert "Priorit" in str(list(get_messages(response.wsgi_request))[-1])  # "Ungültige Priorität"


@pytest.mark.django_db
class TestWorkItemBulkAssignUnknownUser:
    def test_unknown_assignee_redirects_with_message(self, client, staff_user, workitem):
        """Lines 123-124: ``User.DoesNotExist`` -> redirect + flash with
        "Unbekannte Benutzerin/Benutzer" (deliberately backported, ticket
        #2385: previously a plain-text 400)."""
        import uuid

        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_bulk_assign"),
            {"workitem_ids": [str(workitem.pk)], "assigned_to": str(uuid.uuid4())},
        )
        assert response.status_code == 302
        assert "Unbekannte" in str(list(get_messages(response.wsgi_request))[-1])


@pytest.mark.django_db
class TestWorkItemBulkNoExceptionLeak:
    """Refs #1011 (CodeQL #4, py/stack-trace-exposure).

    Eine *unerwartete* Service-Exception darf ihre Message nicht als 400 in
    die Antwort gespiegelt werden — nur kontrollierte Validierungs-Fehler
    werden gefangen, der Rest propagiert als 500 (geloggt, nicht exponiert).
    """

    def test_unexpected_service_error_is_not_reflected(self, client, staff_user, workitem, monkeypatch):
        client.force_login(staff_user)
        client.raise_request_exception = False

        def _boom(*args, **kwargs):
            raise ValueError("INTERNAL-LEAK-MARKER-xyz")

        monkeypatch.setattr("core.views.workitem_bulk.bulk_update_workitem_status", _boom)

        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": [str(workitem.pk)], "status": "done"},
        )

        assert response.status_code == 500
        assert b"INTERNAL-LEAK-MARKER" not in response.content
