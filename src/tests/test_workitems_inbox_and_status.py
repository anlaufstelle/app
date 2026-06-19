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

    def test_inbox_bulk_forms_use_csp_safe_handlers(self, client, staff_user):
        """Refs #1132: Item-Checkboxen rufen ``onToggleItem`` (bare Method),
        nicht das im CSP-Build nicht interpretierbare ``toggle('<pk>')``.

        Die Bulk-Forms reichen zudem den aktiven Filter per ``@submit`` und
        versteckten ``filter_*``-Feldern durch.
        """
        WorkItem.objects.create(
            facility=staff_user.facility,
            created_by=staff_user,
            title="Auswählbar",
            status=WorkItem.Status.OPEN,
        )
        client.force_login(staff_user)
        html = client.get(reverse("core:workitem_inbox")).content.decode()
        assert '@change="onToggleItem"' in html
        assert "toggle(" not in html
        assert '@submit="syncFilters"' in html
        assert 'name="filter_assigned_to"' in html
        assert 'name="filter_due"' in html

    def test_inbox_bulk_filter_fields_carry_active_filter(self, client, staff_user, lead_user):
        """Refs #1132: Der aktive Filter landet als Wert in den versteckten
        Bulk-Filter-Feldern (No-JS-Fallback / Initialstand)."""
        client.force_login(staff_user)
        html = client.get(
            reverse("core:workitem_inbox"),
            {"assigned_to": str(lead_user.pk), "due": "today"},
        ).content.decode()
        assert f'name="filter_assigned_to" value="{lead_user.pk}"' in html
        assert 'name="filter_due" value="today"' in html


@pytest.mark.django_db
class TestWorkItemInboxDateLabeling:
    """Refs #1133: In der Aufgabenliste ist das angezeigte Datum eindeutig das
    Fälligkeitsdatum; das Erstellungsdatum erscheint nicht mehr (missverständlich)
    in der Übersicht. Es bleibt nur in der Einzelansicht erhalten.
    """

    def test_inbox_due_date_is_clearly_labelled(self, client, staff_user, facility):
        """Das Fälligkeitsdatum in der Liste trägt eine eindeutige Bezeichnung
        ('Fällig:' sichtbar + Tooltip 'Zu erledigen bis'), damit es nicht mit dem
        Erstellungsdatum verwechselt wird.

        Die Frist liegt 30 Tage in der Zukunft, sodass die relative Anzeige ein
        absolutes Datum ohne Schlüsselwort liefert — die Bezeichnung muss daher
        aus dem Markup der Zeile stammen, nicht aus dem Filter ('Fälligkeit')."""
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="Aufgabe mit Frist",
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.NORMAL,
            due_date=datetime.date.today() + datetime.timedelta(days=30),
        )
        client.force_login(staff_user)
        html = client.get(reverse("core:workitem_inbox")).content.decode()
        # Tooltip am Fälligkeits-Badge der Zeile (nur dort, nicht im Filter).
        assert 'title="Zu erledigen bis"' in html
        # Sichtbares Präfix vor der relativen Frist-Anzeige.
        assert "Fällig:" in html

    def test_inbox_does_not_show_creation_date(self, client, staff_user, facility):
        """Das Erstellungsdatum wird in der Listenübersicht nicht mehr angezeigt.

        ``created_at`` wird zurückdatiert auf ein eindeutiges Datum (März 2021),
        dessen lokalisierte Darstellung im gerenderten HTML nicht auftauchen darf.
        """
        wi = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="Aufgabe ohne sichtbares Erstelldatum",
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.NORMAL,
            due_date=datetime.date.today() + datetime.timedelta(days=30),
        )
        # created_at ist auto_now_add → nachträglich auf ein klar abgegrenztes
        # Datum setzen, damit die Abwesenheit eindeutig prüfbar ist.
        backdated = datetime.datetime(2021, 3, 14, 9, 5, tzinfo=datetime.UTC)
        WorkItem.objects.filter(pk=wi.pk).update(created_at=backdated)
        client.force_login(staff_user)
        html = client.get(reverse("core:workitem_inbox")).content.decode()
        assert "14. März 2021" not in html
        assert "2021" not in html

    def test_inbox_creation_date_still_in_detail(self, client, staff_user, facility):
        """Gegenprobe: Das Erstellungsdatum bleibt in der Einzelansicht sichtbar."""
        wi = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="Detail-Aufgabe",
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.NORMAL,
        )
        backdated = datetime.datetime(2021, 3, 14, 9, 5, tzinfo=datetime.UTC)
        WorkItem.objects.filter(pk=wi.pk).update(created_at=backdated)
        client.force_login(staff_user)
        html = client.get(reverse("core:workitem_detail", kwargs={"pk": wi.pk})).content.decode()
        assert "Erstellt am" in html
        assert "14. März 2021" in html


@pytest.mark.django_db
class TestWorkItemDoneConfirmation:
    """Refs #1147: Vor dem Statuswechsel auf *erledigt* erscheint eine
    Bestätigung — konsistent in der Übersicht (Tabelle), in der Bulk-Aktion und
    in der Detailansicht (Detail siehe ``TestWorkItemDetailActions``).

    Die Übersichts-Buttons sind HTMX-Aktionen, daher läuft die Bestätigung dort
    über ``hx-confirm`` (wie bei Goals/Retention). Die Bulk-Statusänderung ist
    bewusst nur dann zu bestätigen, wenn der Zielstatus *erledigt* gewählt wird;
    das übernimmt ein dedizierter ``@submit``-Handler.
    """

    def test_inbox_done_button_has_confirmation(self, client, staff_user, workitem_in_progress):
        """Der Erledigt-Button einer *In Bearbeitung*-Aufgabe in der Übersicht
        fragt vor dem Statuswechsel nach (hx-confirm)."""
        client.force_login(staff_user)
        html = client.get(reverse("core:workitem_inbox")).content.decode()
        # hx-confirm muss am Erledigt-Button hängen und die Folge erklären.
        assert "hx-confirm=" in html
        marker = 'hx-confirm="Aufgabe als erledigt markieren?'
        assert marker in html
        confirm_text = html.split(marker, 1)[1].split('"', 1)[0]
        assert "als erledigt gespeichert" in confirm_text
        assert "offenen Aufgaben" in confirm_text

    def test_inbox_open_actions_have_no_done_confirmation(self, client, staff_user, workitem_open):
        """Gegenprobe: Eine *offene* Aufgabe bietet in der Übersicht keinen
        direkten Erledigen-Pfad (nur Annehmen/Verwerfen) und löst daher auch
        keine Erledigt-Bestätigung aus."""
        client.force_login(staff_user)
        html = client.get(reverse("core:workitem_inbox")).content.decode()
        assert 'hx-confirm="Aufgabe als erledigt markieren?' not in html

    def test_bulk_status_form_uses_done_confirmation_handler(self, client, staff_user, workitem_open):
        """Die Bulk-Statusänderung wird vor dem Submit über
        ``confirmBulkStatus`` geleitet, der nur beim Zielstatus *erledigt*
        bestätigt (und weiterhin die Filter synchronisiert)."""
        client.force_login(staff_user)
        html = client.get(reverse("core:workitem_inbox")).content.decode()
        # Das Status-Bulk-Form nutzt den dedizierten Handler (statt nur syncFilters).
        assert '@submit="confirmBulkStatus"' in html


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
