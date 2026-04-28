"""Tests für WorkItem-Inbox, CRUD und Context Processor."""

import datetime

import pytest
from django.urls import reverse

from core.models import WorkItem
from core.models.activity import Activity
from core.services.workitems import update_workitem


@pytest.fixture
def workitem_open(facility, client_identified, staff_user):
    return WorkItem.objects.create(
        facility=facility,
        client=client_identified,
        created_by=staff_user,
        title="Offene Aufgabe",
        status=WorkItem.Status.OPEN,
        priority=WorkItem.Priority.NORMAL,
    )


@pytest.fixture
def workitem_urgent(facility, staff_user):
    return WorkItem.objects.create(
        facility=facility,
        created_by=staff_user,
        title="Dringende Aufgabe",
        status=WorkItem.Status.OPEN,
        priority=WorkItem.Priority.URGENT,
    )


@pytest.fixture
def workitem_in_progress(facility, staff_user):
    return WorkItem.objects.create(
        facility=facility,
        created_by=staff_user,
        assigned_to=staff_user,
        title="In Bearbeitung",
        status=WorkItem.Status.IN_PROGRESS,
    )


@pytest.fixture
def workitem_done(facility, staff_user):
    return WorkItem.objects.create(
        facility=facility,
        created_by=staff_user,
        title="Erledigte Aufgabe",
        status=WorkItem.Status.DONE,
    )


@pytest.mark.django_db
class TestWorkItemInbox:
    def test_inbox_renders(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"))
        assert response.status_code == 200
        assert "Aufgaben" in response.content.decode()

    def test_inbox_requires_auth(self, client):
        response = client.get(reverse("core:workitem_inbox"))
        assert response.status_code == 302

    def test_inbox_groups_items(self, client, staff_user, workitem_open, workitem_in_progress, workitem_done):
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"))
        ctx = response.context
        assert workitem_open in ctx["open_items"]
        assert workitem_in_progress in ctx["in_progress_items"]
        assert workitem_done in ctx["done_items"]

    def test_inbox_priority_sorting(self, client, staff_user, workitem_open, workitem_urgent):
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"))
        open_items = list(response.context["open_items"])
        assert open_items[0] == workitem_urgent
        assert open_items[1] == workitem_open

    def test_inbox_facility_scoped(self, client, staff_user, facility, organization):
        from core.models import Facility

        other_facility = Facility.objects.create(organization=organization, name="Andere")
        WorkItem.objects.create(
            facility=other_facility,
            created_by=staff_user,
            title="Andere Facility",
            status=WorkItem.Status.OPEN,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"))
        open_items = list(response.context["open_items"])
        assert all(wi.facility == facility for wi in open_items)

    def test_assistant_can_access_inbox(self, client, assistant_user):
        client.force_login(assistant_user)
        response = client.get(reverse("core:workitem_inbox"))
        assert response.status_code == 200


@pytest.mark.django_db
class TestWorkItemStatusUpdate:
    def test_status_update(self, client, staff_user, workitem_open):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_status_update", kwargs={"pk": workitem_open.pk}),
            {"status": "in_progress"},
        )
        assert response.status_code == 302
        workitem_open.refresh_from_db()
        assert workitem_open.status == WorkItem.Status.IN_PROGRESS
        assert workitem_open.assigned_to == staff_user

    def test_status_update_htmx(self, client, staff_user, workitem_open):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_status_update", kwargs={"pk": workitem_open.pk}),
            {"status": "in_progress"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert "Erledigt" in response.content.decode()

    def test_invalid_status(self, client, staff_user, workitem_open):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_status_update", kwargs={"pk": workitem_open.pk}),
            {"status": "invalid"},
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestWorkItemStatusUpdateOwnership:
    """Ownership check: only created_by, assigned_to, or Lead/Admin may change status."""

    def test_assistant_can_update_own_workitem(self, client, assistant_user, facility):
        """Assistant who created the WorkItem may change its status."""
        wi = WorkItem.objects.create(
            facility=facility,
            created_by=assistant_user,
            title="Eigene Aufgabe",
            status=WorkItem.Status.OPEN,
        )
        client.force_login(assistant_user)
        response = client.post(
            reverse("core:workitem_status_update", kwargs={"pk": wi.pk}),
            {"status": "in_progress"},
        )
        assert response.status_code == 302
        wi.refresh_from_db()
        assert wi.status == WorkItem.Status.IN_PROGRESS

    def test_assistant_can_update_assigned_workitem(self, client, assistant_user, staff_user, facility):
        """Assistant who is assigned_to the WorkItem may change its status."""
        wi = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=assistant_user,
            title="Zugewiesene Aufgabe",
            status=WorkItem.Status.OPEN,
        )
        client.force_login(assistant_user)
        response = client.post(
            reverse("core:workitem_status_update", kwargs={"pk": wi.pk}),
            {"status": "in_progress"},
        )
        assert response.status_code == 302
        wi.refresh_from_db()
        assert wi.status == WorkItem.Status.IN_PROGRESS

    def test_assistant_cannot_update_others_workitem(self, client, assistant_user, staff_user, facility):
        """Assistant who neither created nor is assigned gets 403."""
        wi = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="Fremde Aufgabe",
            status=WorkItem.Status.OPEN,
        )
        client.force_login(assistant_user)
        response = client.post(
            reverse("core:workitem_status_update", kwargs={"pk": wi.pk}),
            {"status": "in_progress"},
        )
        assert response.status_code == 403
        wi.refresh_from_db()
        assert wi.status == WorkItem.Status.OPEN

    def test_lead_can_update_any_workitem(self, client, lead_user, staff_user, facility):
        """Lead may change status of any WorkItem in the facility."""
        wi = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="Fremde Aufgabe fuer Lead",
            status=WorkItem.Status.OPEN,
        )
        client.force_login(lead_user)
        response = client.post(
            reverse("core:workitem_status_update", kwargs={"pk": wi.pk}),
            {"status": "done"},
        )
        assert response.status_code == 302
        wi.refresh_from_db()
        assert wi.status == WorkItem.Status.DONE

    def test_admin_can_update_any_workitem(self, client, admin_user, staff_user, facility):
        """Admin may change status of any WorkItem in the facility."""
        wi = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="Fremde Aufgabe fuer Admin",
            status=WorkItem.Status.OPEN,
        )
        client.force_login(admin_user)
        response = client.post(
            reverse("core:workitem_status_update", kwargs={"pk": wi.pk}),
            {"status": "done"},
        )
        assert response.status_code == 302
        wi.refresh_from_db()
        assert wi.status == WorkItem.Status.DONE


@pytest.mark.django_db
class TestWorkItemCRUD:
    def test_create_get(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_create"))
        assert response.status_code == 200

    def test_create_post(self, client, staff_user, facility):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_create"),
            {
                "item_type": "task",
                "title": "Neue Test-Aufgabe",
                "priority": "normal",
            },
        )
        assert response.status_code == 302
        assert WorkItem.objects.filter(title="Neue Test-Aufgabe", facility=facility).exists()

    def test_create_with_client(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_create"),
            {
                "item_type": "task",
                "title": "Aufgabe mit Klientel",
                "priority": "normal",
                "client": str(client_identified.pk),
            },
        )
        assert response.status_code == 302
        wi = WorkItem.objects.get(title="Aufgabe mit Klientel")
        assert wi.client == client_identified

    def test_update_get(self, client, staff_user, workitem_open):
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_update", kwargs={"pk": workitem_open.pk}))
        assert response.status_code == 200

    def test_update_post(self, client, staff_user, workitem_open):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": workitem_open.pk}),
            {
                "item_type": "task",
                "title": "Aktualisierter Titel",
                "priority": "urgent",
            },
        )
        assert response.status_code == 302
        workitem_open.refresh_from_db()
        assert workitem_open.title == "Aktualisierter Titel"
        assert workitem_open.priority == WorkItem.Priority.URGENT

    def test_detail_view(self, client, staff_user, workitem_open):
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_detail", kwargs={"pk": workitem_open.pk}))
        assert response.status_code == 200
        assert "Offene Aufgabe" in response.content.decode()


@pytest.mark.django_db
class TestWorkItemContextProcessor:
    def test_badge_count_in_response(self, client, staff_user, workitem_open, workitem_urgent):
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        assert response.context["open_workitems_count"] == 2

    def test_badge_count_zero_when_all_done(self, client, staff_user, workitem_done):
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        assert response.context["open_workitems_count"] == 0

    def test_badge_not_present_for_unauthenticated(self, client):
        response = client.get(reverse("core:zeitstrom"), follow=False)
        assert response.status_code == 302

    def test_overdue_count_with_past_due_date(self, client, staff_user, facility):
        """WorkItem mit due_date gestern wird als ueberfaellig gezaehlt."""
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="Ueberfaellige Aufgabe",
            status=WorkItem.Status.OPEN,
            due_date=datetime.date.today() - datetime.timedelta(days=1),
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        assert response.context["overdue_workitems_count"] == 1

    def test_overdue_count_zero_with_future_due_date(self, client, staff_user, facility):
        """WorkItem mit due_date morgen ist nicht ueberfaellig."""
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="Zukuenftige Aufgabe",
            status=WorkItem.Status.OPEN,
            due_date=datetime.date.today() + datetime.timedelta(days=1),
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        assert response.context["overdue_workitems_count"] == 0

    def test_overdue_count_zero_when_done(self, client, staff_user, facility):
        """Erledigtes WorkItem mit due_date gestern ist nicht ueberfaellig."""
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="Erledigte ueberfaellige Aufgabe",
            status=WorkItem.Status.DONE,
            due_date=datetime.date.today() - datetime.timedelta(days=1),
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        assert response.context["overdue_workitems_count"] == 0


@pytest.mark.django_db
class TestDeletionRequestList:
    def test_lead_can_access(self, client, lead_user):
        client.force_login(lead_user)
        response = client.get(reverse("core:deletion_request_list"))
        assert response.status_code == 200
        assert "Löschanträge" in response.content.decode()

    def test_staff_cannot_access(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:deletion_request_list"))
        assert response.status_code == 403

    def test_admin_can_access(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:deletion_request_list"))
        assert response.status_code == 200

    def test_lists_pending_requests(self, client, lead_user, facility, staff_user, sample_event):
        from core.models import DeletionRequest

        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type="Event",
            target_id=sample_event.pk,
            reason="Testgrund",
            requested_by=staff_user,
        )
        client.force_login(lead_user)
        response = client.get(reverse("core:deletion_request_list"))
        assert dr in response.context["pending_requests"]


@pytest.mark.django_db
class TestUpdateWorkItemService:
    """Tests for the update_workitem service function."""

    def test_updates_fields(self, facility, staff_user, workitem_open):
        updated = update_workitem(
            workitem_open,
            staff_user,
            title="Neuer Titel",
            priority=WorkItem.Priority.URGENT,
        )
        updated.refresh_from_db()
        assert updated.title == "Neuer Titel"
        assert updated.priority == WorkItem.Priority.URGENT

    def test_logs_updated_activity(self, facility, staff_user, workitem_open):
        update_workitem(workitem_open, staff_user, title="Geaendert")
        assert Activity.objects.filter(
            verb=Activity.Verb.UPDATED,
            target_id=workitem_open.pk,
        ).exists()

    def test_updates_client_association(self, facility, staff_user, workitem_open, client_qualified):
        update_workitem(workitem_open, staff_user, client=client_qualified)
        workitem_open.refresh_from_db()
        assert workitem_open.client == client_qualified

    def test_clears_client_association(self, facility, staff_user, workitem_open):
        update_workitem(workitem_open, staff_user, client=None)
        workitem_open.refresh_from_db()
        assert workitem_open.client is None

    def test_returns_updated_workitem(self, facility, staff_user, workitem_open):
        result = update_workitem(workitem_open, staff_user, description="Neue Beschreibung")
        assert result.pk == workitem_open.pk
        assert result.description == "Neue Beschreibung"
