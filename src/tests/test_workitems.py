"""Tests für WorkItem-Inbox, CRUD und Context Processor."""

import datetime

import pytest
from django.core.exceptions import ValidationError
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


@pytest.mark.django_db
class TestWorkItemRemindAt:
    """Tests für Wiedervorlage (remind_at) — getrennt von due_date. Refs #265."""

    def test_workitem_remind_at_stored_separately_from_due_date(self, facility, staff_user):
        """remind_at und due_date sind unabhängige Felder."""
        due = datetime.date.today() + datetime.timedelta(days=14)
        remind = datetime.date.today() + datetime.timedelta(days=3)
        wi = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="Aufgabe mit Wiedervorlage",
            status=WorkItem.Status.OPEN,
            due_date=due,
            remind_at=remind,
        )
        wi.refresh_from_db()
        assert wi.due_date == due
        assert wi.remind_at == remind
        assert wi.due_date != wi.remind_at

    def test_workitem_remind_at_defaults_to_none(self, facility, staff_user):
        """remind_at ist optional und defaultet auf None."""
        wi = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="Ohne Wiedervorlage",
            status=WorkItem.Status.OPEN,
        )
        wi.refresh_from_db()
        assert wi.remind_at is None

    def test_workitem_form_accepts_remind_at(self, client, staff_user, facility):
        """Das WorkItemForm akzeptiert das remind_at-Feld über POST."""
        remind = datetime.date.today() + datetime.timedelta(days=5)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_create"),
            {
                "item_type": "task",
                "title": "Aufgabe mit WV via Form",
                "priority": "normal",
                "remind_at": remind.isoformat(),
                "recurrence": "none",
            },
        )
        assert response.status_code == 302
        wi = WorkItem.objects.get(title="Aufgabe mit WV via Form")
        assert wi.remind_at == remind

    def test_workitem_form_remind_at_optional(self, client, staff_user, facility):
        """Das WorkItemForm akzeptiert das Fehlen von remind_at."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_create"),
            {
                "item_type": "task",
                "title": "Aufgabe ohne WV via Form",
                "priority": "normal",
                "recurrence": "none",
            },
        )
        assert response.status_code == 302
        wi = WorkItem.objects.get(title="Aufgabe ohne WV via Form")
        assert wi.remind_at is None

    def test_workitem_form_remind_at_equal_to_due_date(self, client, staff_user, facility):
        """remind_at == due_date ist erlaubt (kein Validation-Fehler). Refs #591 WP3."""
        same_day = datetime.date.today() + datetime.timedelta(days=7)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_create"),
            {
                "item_type": "task",
                "title": "remind_at == due_date",
                "priority": "normal",
                "due_date": same_day.isoformat(),
                "remind_at": same_day.isoformat(),
                "recurrence": "none",
            },
        )
        assert response.status_code == 302
        wi = WorkItem.objects.get(title="remind_at == due_date")
        assert wi.due_date == same_day
        assert wi.remind_at == same_day

    def test_workitem_form_remind_at_after_due_date_is_persisted(self, client, staff_user, facility):
        """Ist-Stand: remind_at > due_date wird ohne Validation akzeptiert (kein silent-swap).

        Es gibt keine Form-Validation in core/forms/workitems.py, die remind_at
        gegen due_date prüft. Daher dokumentiert dieser Test den aktuellen
        Datenbank-Zustand. Refs #591 WP3.
        """
        due = datetime.date(2026, 5, 15)
        remind = datetime.date(2026, 6, 1)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_create"),
            {
                "item_type": "task",
                "title": "remind_at nach due_date",
                "priority": "normal",
                "due_date": due.isoformat(),
                "remind_at": remind.isoformat(),
                "recurrence": "none",
            },
        )
        assert response.status_code == 302
        wi = WorkItem.objects.get(title="remind_at nach due_date")
        # Werte werden exakt so persistiert, kein silent-swap.
        assert wi.due_date == due
        assert wi.remind_at == remind
        assert wi.remind_at > wi.due_date

    @pytest.mark.xfail(
        strict=True,
        reason="remind_at > due_date wird nicht validiert - Refs #591 WP3",
    )
    def test_workitem_form_rejects_remind_at_after_due_date(self, client, staff_user, facility):
        """Soll-Verhalten: remind_at > due_date wird vom Form abgelehnt.

        Aktuell fehlt in core/forms/workitems.py eine clean()-Validation,
        die sicherstellt, dass remind_at <= due_date. Dieser Test ist xfail,
        bis die Regel eingeführt ist.
        """
        due = datetime.date(2026, 5, 15)
        remind = datetime.date(2026, 6, 1)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_create"),
            {
                "item_type": "task",
                "title": "remind_at nach due_date (xfail)",
                "priority": "normal",
                "due_date": due.isoformat(),
                "remind_at": remind.isoformat(),
                "recurrence": "none",
            },
        )
        # Erwartet: Form-Validation lehnt ab → 200 (Form re-rendered mit Fehler),
        # kein Redirect, kein persistiertes WorkItem.
        assert response.status_code == 200
        assert not WorkItem.objects.filter(title="remind_at nach due_date (xfail)").exists()

    def test_workitem_update_sets_remind_at(self, client, staff_user, workitem_open):
        """Beim Update lässt sich remind_at setzen, ohne due_date zu berühren."""
        remind = datetime.date.today() + datetime.timedelta(days=1)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": workitem_open.pk}),
            {
                "item_type": "task",
                "title": workitem_open.title,
                "priority": "normal",
                "remind_at": remind.isoformat(),
                "recurrence": "none",
            },
        )
        assert response.status_code == 302
        workitem_open.refresh_from_db()
        assert workitem_open.remind_at == remind
        assert workitem_open.due_date is None


@pytest.mark.django_db
class TestOptimisticLockingWorkItem:
    """Tests for optimistic locking on WorkItem updates (Refs #531)."""

    def test_optimistic_locking_workitem_conflict(self, workitem_open, staff_user):
        stale = "2000-01-01T00:00:00+00:00"
        with pytest.raises(ValidationError):
            update_workitem(
                workitem_open,
                staff_user,
                title="Parallel-Titel",
                expected_updated_at=stale,
            )
        workitem_open.refresh_from_db()
        assert workitem_open.title != "Parallel-Titel"

    def test_optimistic_locking_workitem_success_with_current_timestamp(self, workitem_open, staff_user):
        workitem_open.refresh_from_db()
        current = workitem_open.updated_at.isoformat()
        update_workitem(
            workitem_open,
            staff_user,
            title="OK-Titel",
            expected_updated_at=current,
        )
        workitem_open.refresh_from_db()
        assert workitem_open.title == "OK-Titel"

    def test_optimistic_locking_workitem_none_disables_check(self, workitem_open, staff_user):
        update_workitem(
            workitem_open,
            staff_user,
            title="Legacy-Titel",
            expected_updated_at=None,
        )
        workitem_open.refresh_from_db()
        assert workitem_open.title == "Legacy-Titel"
