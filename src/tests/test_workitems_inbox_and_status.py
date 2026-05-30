"""Tests für WorkItem-Inbox, Status-Update, Context Processor und Wiedervorlage.

Enthaelt die Cluster (Split aus ``test_workitems.py``):

* ``TestWorkItemInbox`` — Inbox-Rendering, Gruppierung, Sortierung.
* ``TestWorkItemStatusUpdate`` — Status-Update HTTP-Pfad (POST + HTMX).
* ``TestWorkItemRemindAt`` — Wiedervorlage-Feld (Refs #265).
* ``TestWorkItemContextProcessor`` — Badge-Count im Context Processor.

Fixtures (``workitem_open/urgent/in_progress/done``) sind inline gehalten;
sie werden parallel auch in den anderen Split-Files genutzt (kopiert), weil
pytest File-spezifische Fixtures nicht ueber Imports entdeckt.
"""

import datetime

import pytest
from django.urls import reverse

from core.models import WorkItem


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

    def test_workitem_form_rejects_remind_at_after_due_date(self, client, staff_user, facility):
        """Soll-Verhalten: remind_at > due_date wird vom Form abgelehnt.

        Die clean()-Validation in core/forms/workitems.py stellt sicher, dass
        remind_at <= due_date. Refs #597.
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
        # Form-Validation lehnt ab → 200 (Form re-rendered mit Fehler),
        # kein Redirect, kein persistiertes WorkItem.
        assert response.status_code == 200
        assert not WorkItem.objects.filter(title="remind_at nach due_date").exists()

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

    def test_workitem_form_rejects_due_date_far_future(self, client, staff_user, facility):
        """Refs #708: due_date > 31.12. Folgejahr wird vom Form abgelehnt."""
        far_future = datetime.date(datetime.date.today().year + 5, 5, 5)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_create"),
            {
                "item_type": "task",
                "title": "Aufgabe in ferner Zukunft",
                "priority": "normal",
                "due_date": far_future.isoformat(),
                "recurrence": "none",
            },
        )
        assert response.status_code == 200
        assert not WorkItem.objects.filter(title="Aufgabe in ferner Zukunft").exists()

    def test_workitem_form_accepts_due_date_at_max(self, client, staff_user, facility):
        """Refs #708: due_date = 31.12. Folgejahr wird akzeptiert (Grenzwert)."""
        max_date = datetime.date(datetime.date.today().year + 1, 12, 31)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_create"),
            {
                "item_type": "task",
                "title": "Aufgabe am Stichtag",
                "priority": "normal",
                "due_date": max_date.isoformat(),
                "recurrence": "none",
            },
        )
        assert response.status_code == 302
        wi = WorkItem.objects.get(title="Aufgabe am Stichtag")
        assert wi.due_date == max_date

    def test_workitem_form_rejects_remind_at_far_future(self, client, staff_user, facility):
        """Refs #708: remind_at > 31.12. Folgejahr wird vom Form abgelehnt."""
        far_future = datetime.date(datetime.date.today().year + 5, 5, 5)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_create"),
            {
                "item_type": "task",
                "title": "Erinnerung in ferner Zukunft",
                "priority": "normal",
                "remind_at": far_future.isoformat(),
                "recurrence": "none",
            },
        )
        assert response.status_code == 200
        assert not WorkItem.objects.filter(title="Erinnerung in ferner Zukunft").exists()

    def test_workitem_form_rejects_due_date_in_past(self, client, staff_user, facility):
        """Refs #711: due_date in der Vergangenheit wird beim Anlegen abgelehnt."""
        past = datetime.date.today() - datetime.timedelta(days=1)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_create"),
            {
                "item_type": "task",
                "title": "Aufgabe in Vergangenheit",
                "priority": "normal",
                "due_date": past.isoformat(),
                "recurrence": "none",
            },
        )
        assert response.status_code == 200
        assert not WorkItem.objects.filter(title="Aufgabe in Vergangenheit").exists()

    def test_workitem_form_rejects_remind_at_in_past(self, client, staff_user, facility):
        """Refs #711: remind_at in der Vergangenheit wird beim Anlegen abgelehnt."""
        past = datetime.date.today() - datetime.timedelta(days=1)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_create"),
            {
                "item_type": "task",
                "title": "Erinnerung in Vergangenheit",
                "priority": "normal",
                "remind_at": past.isoformat(),
                "recurrence": "none",
            },
        )
        assert response.status_code == 200
        assert not WorkItem.objects.filter(title="Erinnerung in Vergangenheit").exists()

    def test_workitem_form_accepts_due_date_today(self, client, staff_user, facility):
        """Refs #711: heute als due_date ist erlaubt (Grenzwert)."""
        today = datetime.date.today()
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_create"),
            {
                "item_type": "task",
                "title": "Aufgabe heute",
                "priority": "normal",
                "due_date": today.isoformat(),
                "recurrence": "none",
            },
        )
        assert response.status_code == 302
        wi = WorkItem.objects.get(title="Aufgabe heute")
        assert wi.due_date == today

    def test_workitem_form_edit_keeps_existing_past_due_date(self, client, staff_user, facility):
        """Refs #711: Edit eines überfälligen Items darf nicht durch die
        Vergangenheits-Validierung blockieren, solange due_date unverändert
        bleibt (sonst kann der User das Item nie wieder bearbeiten)."""
        past = datetime.date.today() - datetime.timedelta(days=10)
        wi = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="Bereits überfällig",
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.NORMAL,
            due_date=past,
        )
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": wi.pk}),
            {
                "item_type": "task",
                "title": "Bereits überfällig — Beschreibung ergänzt",
                "priority": "normal",
                "due_date": past.isoformat(),
                "recurrence": "none",
            },
        )
        assert response.status_code == 302
        wi.refresh_from_db()
        assert wi.title == "Bereits überfällig — Beschreibung ergänzt"
        assert wi.due_date == past
