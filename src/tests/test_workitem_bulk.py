"""Tests für Bulk-Edit von WorkItems (Refs #267)."""

import uuid
from datetime import date

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from core.constants import WORKITEM_BULK_MAX_IDS
from core.models import AuditLog, Client, WorkItem
from core.services.case import (
    bulk_assign_workitems,
    bulk_update_workitem_priority,
    bulk_update_workitem_status,
)


@pytest.mark.django_db
@pytest.mark.parametrize("revocation", ["trashed", "inactive"])
@pytest.mark.parametrize(
    ("route", "payload"),
    [
        ("core:workitem_bulk_status", {"status": WorkItem.Status.DONE}),
        ("core:workitem_bulk_priority", {"priority": WorkItem.Priority.URGENT}),
        ("core:workitem_bulk_assign", {"assigned_to": ""}),
    ],
)
def test_bulk_hidden_task_matches_unknown_id(client, lead_user, facility, revocation, route, payload):
    person = Client.objects.create(facility=facility, pseudonym="Hidden person", created_by=lead_user)
    task = WorkItem.objects.create(
        facility=facility, client=person, title="Hidden task", created_by=lead_user, assigned_to=lead_user
    )
    Client.objects.filter(pk=person.pk).update(
        **({"is_deleted": True} if revocation == "trashed" else {"is_active": False})
    )
    client.force_login(lead_user)
    replies = []
    for task_id in (uuid.uuid4(), task.pk):
        client.cookies.pop("messages", None)
        response = client.post(reverse(route), {"workitem_ids": [str(task_id)], **payload}, HTTP_HX_REQUEST="true")
        replies.append(
            (response.status_code, response.get("HX-Redirect"), [str(m) for m in get_messages(response.wsgi_request)])
        )
    assert replies[0] == replies[1]
    assert replies[1][2] == ["Keine gültigen Aufgaben gefunden."]
    task.refresh_from_db()
    assert task.status == WorkItem.Status.OPEN
    assert task.priority == WorkItem.Priority.NORMAL
    assert task.assigned_to_id == lead_user.pk


@pytest.mark.django_db
def test_bulk_mixed_selection_hides_revoked_task_like_unknown_id(client, lead_user, facility):
    person = Client.objects.create(facility=facility, pseudonym="Hidden person", created_by=lead_user)
    hidden = WorkItem.objects.create(facility=facility, client=person, title="Hidden task", created_by=lead_user)
    visible = WorkItem.objects.create(facility=facility, title="Visible task", created_by=lead_user)
    Client.objects.filter(pk=person.pk).update(is_deleted=True)
    client.force_login(lead_user)
    replies = []
    for task_id in (uuid.uuid4(), hidden.pk):
        WorkItem.objects.filter(pk=visible.pk).update(priority=WorkItem.Priority.NORMAL)
        client.cookies.pop("messages", None)
        response = client.post(
            reverse("core:workitem_bulk_priority"),
            {"workitem_ids": [str(visible.pk), str(task_id)], "priority": WorkItem.Priority.URGENT},
            HTTP_HX_REQUEST="true",
        )
        replies.append(
            (response.status_code, response.get("HX-Redirect"), [str(m) for m in get_messages(response.wsgi_request)])
        )
        visible.refresh_from_db()
        assert visible.priority == WorkItem.Priority.URGENT
    assert replies[0] == replies[1]
    hidden.refresh_from_db()
    assert hidden.priority == WorkItem.Priority.NORMAL


@pytest.fixture
def workitems_open(facility, staff_user):
    return [
        WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title=f"Aufgabe {i}",
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.NORMAL,
        )
        for i in range(3)
    ]


@pytest.mark.django_db
class TestBulkStatusService:
    def test_bulk_update_status_changes_all(self, facility, staff_user, workitems_open):
        count = bulk_update_workitem_status(workitems_open, staff_user, WorkItem.Status.DONE)

        assert count == 3
        for wi in workitems_open:
            wi.refresh_from_db()
            assert wi.status == WorkItem.Status.DONE
            assert wi.completed_at is not None

        audit_entries = AuditLog.objects.filter(
            action=AuditLog.Action.WORKITEM_UPDATE,
            target_type="WorkItem",
        )
        assert audit_entries.count() == 3
        for entry in audit_entries:
            assert entry.detail == {"changed_fields": ["status"], "bulk": True}

    def test_bulk_update_status_skips_unchanged(self, facility, staff_user, workitems_open):
        workitems_open[0].status = WorkItem.Status.DONE
        workitems_open[0].save()
        count = bulk_update_workitem_status(workitems_open, staff_user, WorkItem.Status.DONE)
        assert count == 2

    def test_bulk_update_status_done_to_in_progress_clears_completed_at(self, facility, staff_user, workitems_open):
        """Refs #1134: Erledigt → In Bearbeitung per Bulk hebt die Erledigt-
        Markierung konsistent auf — Status wechselt und ``completed_at`` wird
        zurückgesetzt, sodass kein widersprüchlicher Zustand zurückbleibt."""
        bulk_update_workitem_status(workitems_open, staff_user, WorkItem.Status.DONE)
        for wi in workitems_open:
            wi.refresh_from_db()
            assert wi.status == WorkItem.Status.DONE
            assert wi.completed_at is not None

        count = bulk_update_workitem_status(workitems_open, staff_user, WorkItem.Status.IN_PROGRESS)
        assert count == 3
        for wi in workitems_open:
            wi.refresh_from_db()
            assert wi.status == WorkItem.Status.IN_PROGRESS
            assert wi.completed_at is None

    def test_bulk_update_status_rejects_invalid(self, staff_user, workitems_open):
        with pytest.raises(ValueError):
            bulk_update_workitem_status(workitems_open, staff_user, "bogus")


@pytest.mark.django_db
class TestBulkPriorityService:
    def test_bulk_update_priority_changes_all(self, facility, staff_user, workitems_open):
        count = bulk_update_workitem_priority(workitems_open, staff_user, WorkItem.Priority.URGENT)
        assert count == 3
        for wi in workitems_open:
            wi.refresh_from_db()
            assert wi.priority == WorkItem.Priority.URGENT
        assert (
            AuditLog.objects.filter(
                action=AuditLog.Action.WORKITEM_UPDATE,
                detail__changed_fields=["priority"],
            ).count()
            == 3
        )

    def test_bulk_update_priority_rejects_invalid(self, staff_user, workitems_open):
        with pytest.raises(ValueError):
            bulk_update_workitem_priority(workitems_open, staff_user, "bogus")


@pytest.mark.django_db
class TestBulkAssignService:
    def test_bulk_assign_sets_assignee_on_all(self, facility, staff_user, lead_user, workitems_open):
        count = bulk_assign_workitems(workitems_open, staff_user, lead_user)
        assert count == 3
        for wi in workitems_open:
            wi.refresh_from_db()
            assert wi.assigned_to == lead_user

    def test_bulk_assign_none_clears_assignment(self, facility, staff_user, lead_user, workitems_open):
        for wi in workitems_open:
            wi.assigned_to = lead_user
            wi.save()
        count = bulk_assign_workitems(workitems_open, staff_user, None)
        assert count == 3
        for wi in workitems_open:
            wi.refresh_from_db()
            assert wi.assigned_to is None


@pytest.mark.django_db
class TestBulkViews:
    def test_bulk_view_requires_login(self, client, workitems_open):
        ids = [str(wi.pk) for wi in workitems_open]
        for url in [
            reverse("core:workitem_bulk_status"),
            reverse("core:workitem_bulk_priority"),
            reverse("core:workitem_bulk_assign"),
        ]:
            response = client.post(url, {"workitem_ids": ids, "status": "done"})
            assert response.status_code == 302
            assert "/login" in response.url or "login" in response.url

    def test_bulk_status_view_changes_items(self, client, staff_user, workitems_open):
        client.force_login(staff_user)
        ids = [str(wi.pk) for wi in workitems_open]
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": ids, "status": "done"},
        )
        assert response.status_code == 302
        for wi in workitems_open:
            wi.refresh_from_db()
            assert wi.status == WorkItem.Status.DONE

    def test_bulk_priority_view_changes_items(self, client, staff_user, workitems_open):
        client.force_login(staff_user)
        ids = [str(wi.pk) for wi in workitems_open]
        response = client.post(
            reverse("core:workitem_bulk_priority"),
            {"workitem_ids": ids, "priority": "urgent"},
        )
        assert response.status_code == 302
        for wi in workitems_open:
            wi.refresh_from_db()
            assert wi.priority == WorkItem.Priority.URGENT

    def test_bulk_assign_view_sets_assignee(self, client, staff_user, lead_user, workitems_open):
        client.force_login(staff_user)
        ids = [str(wi.pk) for wi in workitems_open]
        response = client.post(
            reverse("core:workitem_bulk_assign"),
            {"workitem_ids": ids, "assigned_to": str(lead_user.pk)},
        )
        assert response.status_code == 302
        for wi in workitems_open:
            wi.refresh_from_db()
            assert wi.assigned_to == lead_user

    def test_bulk_assign_view_clears_assignee(self, client, staff_user, lead_user, workitems_open):
        for wi in workitems_open:
            wi.assigned_to = lead_user
            wi.save()
        client.force_login(staff_user)
        ids = [str(wi.pk) for wi in workitems_open]
        response = client.post(
            reverse("core:workitem_bulk_assign"),
            {"workitem_ids": ids, "assigned_to": ""},
        )
        assert response.status_code == 302
        for wi in workitems_open:
            wi.refresh_from_db()
            assert wi.assigned_to is None

    def test_bulk_no_ids_redirects_with_message(self, client, staff_user):
        """Refs #2385: an ordinary form (no ``hx-*`` in ``inbox.html``) would
        otherwise get a full-page plain-text response instead of the inbox
        back — now redirect + flash like the other rejections in this view
        family (deliberately backported, ticket #2385: the assertion
        previously checked the raw 400 plain text)."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"status": "done"},
        )
        assert response.status_code == 302
        assert response.url.startswith(reverse("core:workitem_inbox"))
        assert "Keine Aufgaben ausgewählt." in str(list(get_messages(response.wsgi_request))[-1])

    def test_bulk_invalid_status_redirects_with_message(self, client, staff_user, workitems_open):
        """Deliberately backported (ticket #2385): previously a plain-text
        400, now redirect + flash (see ``test_bulk_no_ids_redirects_with_message``)."""
        client.force_login(staff_user)
        ids = [str(wi.pk) for wi in workitems_open]
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": ids, "status": "bogus"},
        )
        assert response.status_code == 302
        assert "Ungültiger Status" in str(list(get_messages(response.wsgi_request))[-1])
        for wi in workitems_open:
            wi.refresh_from_db()
            assert wi.status == WorkItem.Status.OPEN

    def test_bulk_facility_scope_enforced(self, client, staff_user, second_facility, workitems_open):
        """WorkItems from another facility must not be mutated.

        Deliberately backported (ticket #2385): previously a plain-text 400,
        now redirect + flash (see ``test_bulk_no_ids_redirects_with_message``)."""
        other_user = workitems_open[0].created_by  # staff_user belongs to facility
        foreign_wi = WorkItem.objects.create(
            facility=second_facility,
            created_by=other_user,
            title="Fremde Facility",
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.NORMAL,
        )
        client.force_login(staff_user)
        ids = [str(foreign_wi.pk)]
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": ids, "status": "done"},
        )
        # No matching items (foreign_wi is scoped away) -> redirect + flash
        assert response.status_code == 302
        assert "Keine gültigen Aufgaben gefunden." in str(list(get_messages(response.wsgi_request))[-1])
        foreign_wi.refresh_from_db()
        assert foreign_wi.status == WorkItem.Status.OPEN

    def test_bulk_facility_scope_filters_foreign_items(self, client, staff_user, second_facility, workitems_open):
        """Bei gemischtem Request werden nur eigene Facility-Items verändert."""
        foreign_wi = WorkItem.objects.create(
            facility=second_facility,
            created_by=workitems_open[0].created_by,
            title="Fremde Facility",
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.NORMAL,
        )
        client.force_login(staff_user)
        ids = [str(wi.pk) for wi in workitems_open] + [str(foreign_wi.pk)]
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": ids, "status": "done"},
        )
        assert response.status_code == 302
        for wi in workitems_open:
            wi.refresh_from_db()
            assert wi.status == WorkItem.Status.DONE
        foreign_wi.refresh_from_db()
        assert foreign_wi.status == WorkItem.Status.OPEN

    def test_bulk_rejects_items_without_ownership(self, client, facility, staff_user, lead_user, assistant_user):
        """Assistenz darf Bulk-Mutation auf nicht eigene/zugewiesene Items nicht
        ausführen — Bulk-Route muss dieselbe Ownership-Policy wie die
        Single-Route durchsetzen (Refs #583).

        Refs #1125: Die fremde Aufgabe muss einer *dritten* Person zugewiesen
        sein. Nicht zugewiesene Aufgaben sind Teamaufgaben und damit bewusst
        mutierbar (siehe ``TestBulkUnassignedTeamTasks``)."""
        # Aufgabe ist lead_user zugewiesen, assistant ist nicht Ersteller/Assignee
        foreign = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=lead_user,
            title="Fremde Aufgabe",
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.NORMAL,
        )
        client.force_login(assistant_user)
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": [str(foreign.pk)], "status": "done"},
        )
        # Refs #1148: Ablehnung erfolgt als Flash-Redirect in die Inbox (kein
        # rohes 403), die Ownership-Policy bleibt aber strikt: keine Mutation.
        assert response.status_code == 302
        foreign.refresh_from_db()
        assert foreign.status == WorkItem.Status.OPEN

    def test_bulk_allows_assigned_items(self, client, facility, staff_user, assistant_user):
        """Assistenz darf Items bulk-mutieren, die ihr zugewiesen sind."""
        mine = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=assistant_user,
            title="Mir zugewiesen",
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.NORMAL,
        )
        client.force_login(assistant_user)
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": [str(mine.pk)], "status": "done"},
        )
        assert response.status_code == 302
        mine.refresh_from_db()
        assert mine.status == WorkItem.Status.DONE

    def test_bulk_mixed_ownership_rejects_whole_batch(self, client, facility, staff_user, lead_user, assistant_user):
        """Bei gemischtem Batch (eigene + fremde) wird der ganze Request
        abgelehnt — kein Partial-Success, damit der Erfolgstext nicht lügt.

        Refs #1125: Die fremde Aufgabe ist einer dritten Person zugewiesen
        (echte Ownership-Grenze), nicht bloß unassigned."""
        own = WorkItem.objects.create(
            facility=facility,
            created_by=assistant_user,
            title="Eigene",
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.NORMAL,
        )
        foreign = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=lead_user,
            title="Fremde",
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.NORMAL,
        )
        client.force_login(assistant_user)
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": [str(own.pk), str(foreign.pk)], "status": "done"},
        )
        # Refs #1148: Ablehnung als Flash-Redirect in die Inbox (kein rohes
        # 403). Alles-oder-nichts bleibt: kein Item wird verändert.
        assert response.status_code == 302
        own.refresh_from_db()
        foreign.refresh_from_db()
        # Kein Item darf verändert worden sein.
        assert own.status == WorkItem.Status.OPEN
        assert foreign.status == WorkItem.Status.OPEN

    def test_bulk_status_view_empty_workitem_ids_field_redirects_with_message(self, client, staff_user, workitems_open):
        """Explicit empty ID list (``workitem_ids=[]``) → redirect + flash.

        Complements ``test_bulk_no_ids_redirects_with_message`` (which omits
        the field entirely) with the case where the field is present but
        empty (Refs #591, WP1). Deliberately backported (ticket #2385):
        previously a plain-text 400."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": [], "status": "done"},
        )
        assert response.status_code == 302
        assert "Keine Aufgaben ausgewählt." in str(list(get_messages(response.wsgi_request))[-1])


@pytest.mark.django_db
class TestBulkPreservesFilterState:
    """Refs #1132: Nach einer Bulk-Aktion muss der aktive Filter erhalten
    bleiben.

    Vorher leitete der Bulk-Endpoint stur auf ``/workitems/`` ohne
    Query-String um — die Tabelle kam ungefiltert zurück, während das
    Filter-Feld (clientseitig restauriert) weiterhin z.B. ``Lena Weber``
    zeigte. Der Fix reicht die Filter-Parameter aus dem POST in das
    Redirect-Ziel durch, sodass dieselbe gefilterte Liste serverseitig
    erneut gerendert wird.
    """

    def _filter_fields(self, assignee_id):
        return {
            "filter_item_type": "task",
            "filter_priority": "urgent",
            "filter_assigned_to": str(assignee_id),
            "filter_due": "today",
        }

    def test_bulk_status_redirect_preserves_filters(self, client, staff_user, lead_user, workitems_open):
        client.force_login(staff_user)
        ids = [str(wi.pk) for wi in workitems_open]
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": ids, "status": "done", **self._filter_fields(lead_user.pk)},
        )
        assert response.status_code == 302
        location = response.url
        assert "item_type=task" in location
        assert "priority=urgent" in location
        assert f"assigned_to={lead_user.pk}" in location
        assert "due=today" in location

    def test_bulk_priority_redirect_preserves_filters(self, client, staff_user, lead_user, workitems_open):
        client.force_login(staff_user)
        ids = [str(wi.pk) for wi in workitems_open]
        response = client.post(
            reverse("core:workitem_bulk_priority"),
            {"workitem_ids": ids, "priority": "urgent", **self._filter_fields(lead_user.pk)},
        )
        assert response.status_code == 302
        assert f"assigned_to={lead_user.pk}" in response.url

    def test_bulk_assign_redirect_preserves_filters(self, client, staff_user, lead_user, workitems_open):
        client.force_login(staff_user)
        ids = [str(wi.pk) for wi in workitems_open]
        response = client.post(
            reverse("core:workitem_bulk_assign"),
            {"workitem_ids": ids, "assigned_to": str(lead_user.pk), **self._filter_fields(lead_user.pk)},
        )
        assert response.status_code == 302
        assert f"assigned_to={lead_user.pk}" in response.url

    def test_bulk_without_filters_redirects_to_plain_inbox(self, client, staff_user, workitems_open):
        """Ohne Filter-Felder bleibt das Ziel der nackte Inbox-Pfad."""
        client.force_login(staff_user)
        ids = [str(wi.pk) for wi in workitems_open]
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": ids, "status": "done"},
        )
        assert response.status_code == 302
        assert response.url == reverse("core:workitem_inbox")

    def test_bulk_status_redirect_preserves_explicit_all_assignee_filter(self, client, staff_user, workitems_open):
        """Refs #1134: Die "Alle"-Sicht (``assigned_to`` leer, aber gesetzt) muss
        nach dem Bulk-Submit erhalten bleiben.

        Das Bulk-Form sendet das versteckte ``filter_assigned_to`` immer mit; in
        der "Alle"-Sicht ist sein Wert der Leerstring. Wurde dieser als "kein
        Filter" verworfen, landete die Nutzerin nach einem Statuswechsel auf der
        Default-Sicht ("Mir & unzugewiesene"). Eine fremd-zugewiesene, gerade von
        Erledigt → In Bearbeitung gesetzte Aufgabe verschwand dort aus der Liste,
        obwohl ihr Status korrekt geändert wurde — Liste und Status liefen
        auseinander. Der explizite Leerstring muss als ``assigned_to=`` (= "Alle")
        zurückgereicht werden.
        """
        client.force_login(staff_user)
        ids = [str(wi.pk) for wi in workitems_open]
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {
                "workitem_ids": ids,
                "status": "in_progress",
                "filter_item_type": "",
                "filter_priority": "",
                "filter_assigned_to": "",
                "filter_due": "",
            },
        )
        assert response.status_code == 302
        # Die "Alle"-Sicht round-trippt als expliziter (leerer) assigned_to-Param,
        # NICHT als nackter Inbox-Pfad (der die Default-Eingrenzung aktivierte).
        assert response.url != reverse("core:workitem_inbox")
        assert "assigned_to=" in response.url

    def test_bulk_status_htmx_redirect_preserves_explicit_all_assignee_filter(self, client, staff_user, workitems_open):
        """Refs #1134: Auch der HX-Redirect erhält die explizite "Alle"-Sicht."""
        client.force_login(staff_user)
        ids = [str(wi.pk) for wi in workitems_open]
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {
                "workitem_ids": ids,
                "status": "in_progress",
                "filter_assigned_to": "",
            },
            headers={"HX-Request": "true"},
        )
        assert response.status_code == 200
        assert "assigned_to=" in response["HX-Redirect"]

    def test_bulk_ignores_unknown_filter_keys(self, client, staff_user, workitems_open):
        """Nur bekannte Filter-Parameter werden durchgereicht (kein Open-Redirect
        via beliebiger Query-Parameter)."""
        client.force_login(staff_user)
        ids = [str(wi.pk) for wi in workitems_open]
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {
                "workitem_ids": ids,
                "status": "done",
                "filter_evil": "http://example.com",
                "next": "http://example.com",
            },
        )
        assert response.status_code == 302
        assert response.url == reverse("core:workitem_inbox")
        assert "example.com" not in response.url

    def test_bulk_htmx_redirect_preserves_filters(self, client, staff_user, lead_user, workitems_open):
        """HX-Request-Pfad setzt HX-Redirect inkl. Filter-Query."""
        client.force_login(staff_user)
        ids = [str(wi.pk) for wi in workitems_open]
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": ids, "status": "done", **self._filter_fields(lead_user.pk)},
            headers={"HX-Request": "true"},
        )
        assert response.status_code == 200
        assert f"assigned_to={lead_user.pk}" in response["HX-Redirect"]

    def test_bulk_status_redirect_preserves_status_and_done_period_filter(self, client, staff_user, workitems_open):
        """Refs #1570: Der neue Status-/Zeitraumfilter (erledigte Aufgaben)
        round-trippt nach einer Bulk-Aktion wie die übrigen Filter (#1132)."""
        client.force_login(staff_user)
        ids = [str(wi.pk) for wi in workitems_open]
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {
                "workitem_ids": ids,
                "status": "done",
                "filter_status": "in_progress",
                "filter_done_period": "30",
            },
        )
        assert response.status_code == 302
        assert "status=in_progress" in response.url
        assert "done_period=30" in response.url


@pytest.mark.django_db
class TestBulkConcurrencyReload:
    """Refs #1022 (B2): Die Bulk-Pfade laden+locken die Items innerhalb der
    Transaktion per ``select_for_update`` neu (analog Single-Pfad in
    ``update_workitem_status``), statt auf der aus der View geladenen —
    potenziell veralteten — Instanz zu operieren. Hier ueber eine stale
    Instanz + ein concurrent DB-Update simuliert: der frische DB-Stand
    entspricht bereits dem Ziel, also muss der Bulk-Pfad das Item
    ueberspringen (kein erneuter Write, kein doppelter Audit-Eintrag)."""

    def _make(self, facility, staff_user, **kwargs):
        return WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="WI",
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.NORMAL,
            **kwargs,
        )

    def test_bulk_status_reads_fresh_db_state(self, facility, staff_user):
        wi = self._make(facility, staff_user)
        stale = WorkItem.objects.get(pk=wi.pk)  # haelt OPEN im Speicher
        WorkItem.objects.filter(pk=wi.pk).update(status=WorkItem.Status.DONE)

        count = bulk_update_workitem_status([stale], staff_user, WorkItem.Status.DONE)

        assert count == 0
        assert not AuditLog.objects.filter(action=AuditLog.Action.WORKITEM_UPDATE).exists()

    def test_bulk_priority_reads_fresh_db_state(self, facility, staff_user):
        wi = self._make(facility, staff_user)
        stale = WorkItem.objects.get(pk=wi.pk)  # haelt NORMAL im Speicher
        WorkItem.objects.filter(pk=wi.pk).update(priority=WorkItem.Priority.URGENT)

        count = bulk_update_workitem_priority([stale], staff_user, WorkItem.Priority.URGENT)

        assert count == 0
        assert not AuditLog.objects.filter(action=AuditLog.Action.WORKITEM_UPDATE).exists()

    def test_bulk_assign_reads_fresh_db_state(self, facility, staff_user, lead_user):
        wi = self._make(facility, staff_user)
        stale = WorkItem.objects.get(pk=wi.pk)  # assigned_to None im Speicher
        WorkItem.objects.filter(pk=wi.pk).update(assigned_to=lead_user)

        count = bulk_assign_workitems([stale], staff_user, lead_user)

        assert count == 0
        assert not AuditLog.objects.filter(action=AuditLog.Action.WORKITEM_UPDATE).exists()


@pytest.mark.django_db
class TestBulkStatusRecurrence:
    """Integrationstest Bulk-Status DONE + Recurrence (Refs #593).

    Wenn Bulk-Status DONE auf recurring Items gesetzt wird, wird pro Item
    analog zum Single-Update-Pfad (``update_workitem_status``) eine
    Auto-Duplizierung via ``duplicate_recurring_workitem`` erzeugt.
    Idempotenz stellt der ``recurrence_duplicated_at``-Marker sicher
    (Refs #596).
    """

    @pytest.fixture
    def recurring_pair(self, facility, staff_user):
        return [
            WorkItem.objects.create(
                facility=facility,
                created_by=staff_user,
                title=f"Monatsauftrag {i}",
                status=WorkItem.Status.OPEN,
                priority=WorkItem.Priority.NORMAL,
                due_date=date(2026, 5, 15),
                recurrence=WorkItem.Recurrence.MONTHLY,
            )
            for i in range(2)
        ]

    def test_bulk_done_on_recurring_items_creates_followups(self, facility, staff_user, recurring_pair):
        """Nach Bulk-DONE auf 2 monatliche Items sollen 2 Folgeaufgaben mit
        ``due_date=2026-06-15`` und Status OPEN entstehen."""
        original_pks = [wi.pk for wi in recurring_pair]

        count = bulk_update_workitem_status(recurring_pair, staff_user, WorkItem.Status.DONE)
        assert count == 2

        # Originale sind DONE.
        for wi in recurring_pair:
            wi.refresh_from_db()
            assert wi.status == WorkItem.Status.DONE

        # Folgeaufgaben existieren: je 1 pro Original mit due_date 2026-06-15
        # und Status OPEN. Wir filtern über title (Kopie des Originals) und
        # schließen die Originale via pk__not in aus.
        follow_ups = WorkItem.objects.filter(
            facility=facility,
            due_date=date(2026, 6, 15),
            status=WorkItem.Status.OPEN,
            recurrence=WorkItem.Recurrence.MONTHLY,
        ).exclude(pk__in=original_pks)
        assert follow_ups.count() == 2, (
            "Pro recurring DONE-Item muss eine Folgeaufgabe mit "
            "due_date = ursprüngliches due_date + 1 Monat existieren."
        )


@pytest.mark.django_db
class TestBulkUnassignedTeamTasks:
    """Unassigned WorkItems sind Teamaufgaben (Refs #1125).

    Die Inbox zeigt jeder Fachkraft/Assistenz offene + nicht zugewiesene
    Items mit Status-Buttons ("Annehmen") und Bulk-Auswahl an. Genau diese
    nicht zugewiesenen Team-Items musste die Mutations-Policy bislang ab —
    daraus entstand die irritierende 403-Meldung "Keine Berechtigung für
    ausgewählte Aufgaben." obwohl die Items sichtbar und mit Buttons versehen
    sind. Sichtbarkeit (Inbox/Zeitstrom) und Mutierbarkeit müssen für
    Teamaufgaben konsistent sein.
    """

    def test_bulk_allows_unassigned_item_for_other_creator(self, client, facility, staff_user, assistant_user):
        """Eine nicht zugewiesene Aufgabe, die jemand anderes erstellt hat,
        ist eine Teamaufgabe und darf von der Assistenz bulk-mutiert werden."""
        team_task = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=None,
            title="Teamaufgabe",
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.NORMAL,
        )
        client.force_login(assistant_user)
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": [str(team_task.pk)], "status": "in_progress"},
        )
        assert response.status_code == 302
        team_task.refresh_from_db()
        assert team_task.status == WorkItem.Status.IN_PROGRESS

    def test_bulk_still_rejects_item_assigned_to_other_user(
        self, client, facility, staff_user, lead_user, assistant_user
    ):
        """Eine Aufgabe, die einer dritten Person zugewiesen ist, bleibt
        geschützt — die Assistenz darf sie nicht bulk-mutieren."""
        foreign = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=lead_user,
            title="Fremd zugewiesen",
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.NORMAL,
        )
        client.force_login(assistant_user)
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": [str(foreign.pk)], "status": "done"},
        )
        # Refs #1148: Ablehnung als Flash-Redirect in die Inbox (kein rohes
        # 403); der Schutz fremd-zugewiesener Items bleibt: keine Mutation.
        assert response.status_code == 302
        foreign.refresh_from_db()
        assert foreign.status == WorkItem.Status.OPEN

    def test_bulk_mixed_unassigned_and_own_succeeds(self, client, facility, staff_user, assistant_user):
        """Gemischter Batch aus eigener + nicht zugewiesener Teamaufgabe wird
        komplett verarbeitet (kein 403)."""
        own = WorkItem.objects.create(
            facility=facility,
            created_by=assistant_user,
            title="Eigene",
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.NORMAL,
        )
        team_task = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=None,
            title="Teamaufgabe",
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.NORMAL,
        )
        client.force_login(assistant_user)
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": [str(own.pk), str(team_task.pk)], "status": "in_progress"},
        )
        assert response.status_code == 302
        own.refresh_from_db()
        team_task.refresh_from_db()
        assert own.status == WorkItem.Status.IN_PROGRESS
        assert team_task.status == WorkItem.Status.IN_PROGRESS

    def test_single_status_update_allows_unassigned_team_task(self, client, facility, staff_user):
        """Single-Status-Pfad (z.B. 'Annehmen'-Button) erlaubt das Annehmen
        einer nicht zugewiesenen Teamaufgabe durch eine andere Fachkraft."""
        other_staff = staff_user
        team_task = WorkItem.objects.create(
            facility=facility,
            created_by=other_staff,
            assigned_to=None,
            title="Offene Teamaufgabe",
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.NORMAL,
        )
        # Eine zweite Fachkraft, die weder Ersteller noch Assignee ist.
        from core.models.user import User

        picker = User.objects.create_user(
            username="picker",
            password="x",
            facility=facility,
            role=User.Role.STAFF,
        )
        client.force_login(picker)
        response = client.post(
            reverse("core:workitem_status_update", args=[team_task.pk]),
            {"status": "in_progress"},
        )
        assert response.status_code in (200, 302)
        team_task.refresh_from_db()
        assert team_task.status == WorkItem.Status.IN_PROGRESS


@pytest.mark.django_db
class TestCanUserMutateWorkitem:
    """Direkte Unit-Tests der Policy-Hilfsfunktion (Refs #1125)."""

    def _make(self, facility, creator, assignee):
        return WorkItem.objects.create(
            facility=facility,
            created_by=creator,
            assigned_to=assignee,
            title="WI",
            status=WorkItem.Status.OPEN,
        )

    def test_creator_may_mutate(self, facility, staff_user):
        from core.views.workitems import can_user_mutate_workitem

        wi = self._make(facility, staff_user, None)
        assert can_user_mutate_workitem(staff_user, wi) is True

    def test_assignee_may_mutate(self, facility, staff_user, assistant_user):
        from core.views.workitems import can_user_mutate_workitem

        wi = self._make(facility, staff_user, assistant_user)
        assert can_user_mutate_workitem(assistant_user, wi) is True

    def test_lead_may_mutate_foreign_assigned(self, facility, staff_user, lead_user, assistant_user):
        from core.views.workitems import can_user_mutate_workitem

        wi = self._make(facility, staff_user, assistant_user)
        assert can_user_mutate_workitem(lead_user, wi) is True

    def test_unassigned_team_task_is_mutable_by_anyone(self, facility, staff_user, assistant_user):
        """Nicht zugewiesen = Teamaufgabe → für jede sichtberechtigte Rolle
        mutierbar (Refs #1125)."""
        from core.views.workitems import can_user_mutate_workitem

        wi = self._make(facility, staff_user, None)
        assert can_user_mutate_workitem(assistant_user, wi) is True

    def test_foreign_assigned_task_not_mutable_by_unrelated_user(self, facility, staff_user, lead_user, assistant_user):
        """Einer dritten Person zugewiesen → für Unbeteiligte nicht
        mutierbar (Ownership-Grenze bleibt erhalten)."""
        from core.views.workitems import can_user_mutate_workitem

        wi = self._make(facility, staff_user, lead_user)
        assert can_user_mutate_workitem(assistant_user, wi) is False


@pytest.mark.django_db
class TestBulkForbiddenMessageIsConcrete:
    """Refs #1136 + #1148: Bei fremd-zugewiesenen Items bleibt die Nutzerin in
    der Aufgabenliste und sieht die konkrete Berechtigungsmeldung als Flash-
    Alert — keine pauschale Meldung, keine rohe 403-Seite.

    Hintergrund: Seit #1125 zeigt die Inbox bei explizitem Filter (z.B. "Alle"
    oder ein Personenfilter) auch fremd-zugewiesene Aufgaben an und macht sie
    per "Alle sichtbaren auswählen" auswählbar. Eine Fachkraft (Miriam) wählt
    sie damit unbeabsichtigt mit aus.

    #1136 hat die Meldung inhaltlich konkretisiert (nennt die Anzahl der
    blockierenden Items), sie aber weiterhin als nackte
    ``HttpResponseForbidden``-Textseite ausgeliefert — eine leere weiße Seite
    mit Text in der Ecke, die wie ein technischer Abbruch wirkte (#1148).
    Stattdessen leitet die Aktion jetzt — wie der Erfolgsfall — in die
    (gefilterte) Inbox zurück und hinterlegt die konkrete Meldung als
    ``messages.warning``-Flash, sodass sie als Alert oberhalb der Liste
    erscheint und die Nutzerin direkt weiterarbeiten kann.

    Die Alles-oder-nichts-Semantik (kein stiller Teil-Erfolg, Refs #583) bleibt
    bewusst erhalten: Es darf nichts verändert werden.
    """

    def _messages_for(self, response):
        return [m.message for m in get_messages(response.wsgi_request)]

    def _foreign(self, facility, creator, assignee):
        return WorkItem.objects.create(
            facility=facility,
            created_by=creator,
            assigned_to=assignee,
            title="Fremd zugewiesen",
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.NORMAL,
        )

    def test_no_raw_forbidden_page_redirects_to_inbox(self, client, facility, staff_user, lead_user, assistant_user):
        """#1148: Statt einer rohen 403-Seite leitet die Aktion in die Inbox
        zurück (Nutzerin bleibt im Aufgaben-Kontext).

        Refs #1148 (Folge-Feedback): Das Ziel ist der Inbox-Pfad, ergänzt um die
        ``forbidden``-Query, die die abgelehnte Auswahl wieder markiert.
        """
        foreign = self._foreign(facility, staff_user, lead_user)
        client.force_login(assistant_user)
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": [str(foreign.pk)], "status": "done"},
        )
        assert response.status_code == 302
        assert response.url.split("?")[0] == reverse("core:workitem_inbox")

    def test_message_names_count_of_foreign_items(self, client, facility, staff_user, lead_user, assistant_user):
        """Bei 1 fremd-zugewiesenen von 2 ausgewählten Aufgaben nennt der
        Flash-Hinweis die Anzahl der blockierenden Items und ist nicht die
        pauschale Alt-Meldung."""
        own = WorkItem.objects.create(
            facility=facility,
            created_by=assistant_user,
            title="Eigene",
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.NORMAL,
        )
        foreign = self._foreign(facility, staff_user, lead_user)
        client.force_login(assistant_user)
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": [str(own.pk), str(foreign.pk)], "status": "done"},
        )
        assert response.status_code == 302
        messages = self._messages_for(response)
        assert len(messages) == 1
        text = str(messages[0])
        # Pauschale Alt-Meldung darf nicht mehr erscheinen.
        assert "Keine Berechtigung für ausgewählte Aufgaben." not in text
        # Konkret: nennt die Anzahl der blockierenden (fremd-zugewiesenen) Items.
        assert "1" in text
        # Erklärt die Einschränkung (Zuweisung an andere Personen).
        assert "zugewiesen" in text
        # Keine Mutation.
        own.refresh_from_db()
        foreign.refresh_from_db()
        assert own.status == WorkItem.Status.OPEN
        assert foreign.status == WorkItem.Status.OPEN

    def test_forbidden_flash_uses_warning_level(self, client, facility, staff_user, lead_user, assistant_user):
        """#1148: Der Hinweis ist ein Warning-Level-Flash (Alert-Box, kein
        Erfolg) — nicht ``success``."""
        from django.contrib.messages import constants as message_constants

        foreign = self._foreign(facility, staff_user, lead_user)
        client.force_login(assistant_user)
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": [str(foreign.pk)], "status": "done"},
        )
        stored = list(get_messages(response.wsgi_request))
        assert len(stored) == 1
        assert stored[0].level == message_constants.WARNING

    def test_forbidden_redirect_preserves_filters(self, client, facility, staff_user, lead_user, assistant_user):
        """#1148/#1132: Die abgelehnte Aktion behält die gefilterte Sicht bei,
        damit die markierten Aufgaben nach dem Zurückleiten sichtbar bleiben."""
        foreign = self._foreign(facility, staff_user, lead_user)
        client.force_login(assistant_user)
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {
                "workitem_ids": [str(foreign.pk)],
                "status": "done",
                "filter_assigned_to": str(lead_user.pk),
                "filter_priority": "urgent",
            },
        )
        assert response.status_code == 302
        assert f"assigned_to={lead_user.pk}" in response.url
        assert "priority=urgent" in response.url

    def test_forbidden_htmx_uses_hx_redirect(self, client, facility, staff_user, lead_user, assistant_user):
        """#1148: Bei HX-Request setzt die abgelehnte Aktion ``HX-Redirect`` auf
        die Inbox, damit HTMX einen echten Seitenwechsel auslöst statt das rohe
        403-Fragment in den DOM zu swappen."""
        foreign = self._foreign(facility, staff_user, lead_user)
        client.force_login(assistant_user)
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": [str(foreign.pk)], "status": "done"},
            headers={"HX-Request": "true"},
        )
        assert "HX-Redirect" in response
        # Refs #1148 (Folge-Feedback): Der HX-Redirect zielt auf den Inbox-Pfad,
        # ergänzt um die ``forbidden``-Query (abgelehnte Auswahl bleibt markiert).
        assert response["HX-Redirect"].split("?")[0].endswith(reverse("core:workitem_inbox"))

    def test_message_plural_for_multiple_foreign_items(self, client, facility, staff_user, lead_user, assistant_user):
        """Mehrere fremd-zugewiesene Items → der Hinweis nennt die korrekte
        (Mehrzahl-)Anzahl."""
        foreign_a = self._foreign(facility, staff_user, lead_user)
        foreign_b = self._foreign(facility, staff_user, lead_user)
        client.force_login(assistant_user)
        response = client.post(
            reverse("core:workitem_bulk_priority"),
            {"workitem_ids": [str(foreign_a.pk), str(foreign_b.pk)], "priority": "urgent"},
        )
        assert response.status_code == 302
        text = str(self._messages_for(response)[0])
        assert "2" in text
        assert "Keine Berechtigung für ausgewählte Aufgaben." not in text

    def test_message_applies_to_assign_endpoint(self, client, facility, staff_user, lead_user, assistant_user):
        """Refs #1136: Auch der Zuweisungs-Endpoint liefert die konkrete
        Meldung (alle Bulk-Felder betroffen, nicht nur Status/Priorität)."""
        foreign = self._foreign(facility, staff_user, lead_user)
        client.force_login(assistant_user)
        response = client.post(
            reverse("core:workitem_bulk_assign"),
            {"workitem_ids": [str(foreign.pk)], "assigned_to": str(assistant_user.pk)},
        )
        assert response.status_code == 302
        text = str(self._messages_for(response)[0])
        assert "zugewiesen" in text
        assert "Keine Berechtigung für ausgewählte Aufgaben." not in text


@pytest.mark.django_db
class TestBulkForbiddenPreservesSelection:
    """Refs #1148 (Folge-Feedback): Nach einer wegen fehlender Berechtigung
    abgelehnten Bulk-Aktion darf die vorherige Auswahl nicht kommentarlos
    verloren gehen.

    Beobachtung aus dem UI-Monitoring: Die konkrete Meldung erscheint zwar als Inline-Alert,
    aber die zuvor ausgewählte (blockierende) Aufgabe ist danach abgewählt — die
    Nutzerin sieht nicht mehr, welche Aufgabe gemeint ist, und kann die Auswahl
    nicht direkt korrigieren. Die abgelehnte Aktion reicht daher die PKs der
    blockierenden (fremd-zugewiesenen) Items als ``forbidden``-Query in das
    Redirect-Ziel durch; die Inbox markiert genau diese wieder als ausgewählt
    und hebt sie optisch hervor, sodass die Meldung fachlich mit der konkreten
    Auswahl verbunden bleibt.
    """

    def _foreign(self, facility, creator, assignee):
        return WorkItem.objects.create(
            facility=facility,
            created_by=creator,
            assigned_to=assignee,
            title="Fremd zugewiesen",
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.NORMAL,
        )

    def test_forbidden_redirect_carries_blocking_ids(self, client, facility, staff_user, lead_user, assistant_user):
        """Die abgelehnte Aktion hängt die PKs der blockierenden Items als
        ``forbidden``-Parameter an das Inbox-Redirect-Ziel."""
        foreign = self._foreign(facility, staff_user, lead_user)
        client.force_login(assistant_user)
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": [str(foreign.pk)], "status": "done"},
        )
        assert response.status_code == 302
        assert f"forbidden={foreign.pk}" in response.url

    def test_forbidden_redirect_carries_only_blocking_ids_not_whole_selection(
        self, client, facility, staff_user, lead_user, assistant_user
    ):
        """Bei gemischter Auswahl wird NUR die blockierende (fremd-zugewiesene)
        Aufgabe als ``forbidden`` zurückgereicht — sie ist die, die abgewählt
        werden muss; die eigene bleibt außen vor."""
        own = WorkItem.objects.create(
            facility=facility,
            created_by=assistant_user,
            title="Eigene",
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.NORMAL,
        )
        foreign = self._foreign(facility, staff_user, lead_user)
        client.force_login(assistant_user)
        response = client.post(
            reverse("core:workitem_bulk_priority"),
            {"workitem_ids": [str(own.pk), str(foreign.pk)], "priority": "urgent"},
        )
        assert response.status_code == 302
        assert f"forbidden={foreign.pk}" in response.url
        assert f"forbidden={own.pk}" not in response.url

    def test_forbidden_redirect_preserves_original_selection_order(
        self, client, facility, staff_user, lead_user, assistant_user
    ):
        """Refs #2385: several blocking items appear in the ``forbidden``
        redirect in the original selection order (the ``workitem_ids``
        field), not the DB read order — otherwise the inbox would mark the
        selection in a swapped order."""
        first = self._foreign(facility, staff_user, lead_user)
        second = self._foreign(facility, staff_user, lead_user)
        third = self._foreign(facility, staff_user, lead_user)
        client.force_login(assistant_user)
        submitted_order = [third.pk, first.pk, second.pk]
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": [str(pk) for pk in submitted_order], "status": "done"},
        )
        assert response.status_code == 302
        forbidden_params = [
            part.split("=")[1] for part in response.url.split("?", 1)[1].split("&") if part.startswith("forbidden=")
        ]
        assert forbidden_params == [str(pk) for pk in submitted_order]

    def test_forbidden_htmx_redirect_carries_blocking_ids(
        self, client, facility, staff_user, lead_user, assistant_user
    ):
        """Auch der HX-Redirect transportiert die ``forbidden``-PKs."""
        foreign = self._foreign(facility, staff_user, lead_user)
        client.force_login(assistant_user)
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": [str(foreign.pk)], "status": "done"},
            headers={"HX-Request": "true"},
        )
        assert f"forbidden={foreign.pk}" in response["HX-Redirect"]

    def test_inbox_prechecks_and_highlights_forbidden_item(
        self, client, facility, staff_user, lead_user, assistant_user
    ):
        """Die Inbox liest die ``forbidden``-PKs und rendert die zugehörige
        Checkbox als angewählt und die Karte optisch hervorgehoben."""
        foreign = self._foreign(facility, staff_user, lead_user)
        client.force_login(assistant_user)
        # "Alle"-Sicht, damit die fremd-zugewiesene Aufgabe sichtbar ist.
        response = client.get(
            reverse("core:workitem_inbox"),
            {"assigned_to": "", "forbidden": str(foreign.pk)},
        )
        assert response.status_code == 200
        html = response.content.decode()
        # Die Checkbox des blockierenden Items wird vorab angewählt.
        assert f'value="{foreign.pk}"' in html
        # Genau diese Zeile ist als blockierend markiert (Hervorhebung + Hook).
        assert f'data-forbidden-id="{foreign.pk}"' in html

    def test_inbox_ignores_forbidden_ids_not_in_visible_list(
        self, client, facility, staff_user, lead_user, assistant_user
    ):
        """Nur tatsächlich gerenderte Items werden vorab markiert — eine
        ``forbidden``-PK, die in der aktuellen (gefilterten) Sicht nicht
        vorkommt, wird ignoriert (kein Pre-Check für unsichtbare Items)."""
        # Fremd-zugewiesen → in der Default-Sicht NICHT sichtbar.
        foreign = self._foreign(facility, staff_user, lead_user)
        client.force_login(assistant_user)
        response = client.get(
            reverse("core:workitem_inbox"),
            {"forbidden": str(foreign.pk)},  # Default-Sicht (kein assigned_to)
        )
        assert response.status_code == 200
        html = response.content.decode()
        assert f'data-forbidden-id="{foreign.pk}"' not in html

    def test_inbox_forbidden_ids_in_context_are_visible_only(
        self, client, facility, staff_user, lead_user, assistant_user
    ):
        """Der View exponiert ``forbidden_ids`` im Kontext, beschränkt auf die
        in der aktuellen Sicht tatsächlich gerenderten PKs."""
        foreign = self._foreign(facility, staff_user, lead_user)
        client.force_login(assistant_user)
        response = client.get(
            reverse("core:workitem_inbox"),
            {"assigned_to": "", "forbidden": [str(foreign.pk), "999999"]},
        )
        assert response.status_code == 200
        forbidden_ids = response.context["forbidden_ids"]
        assert str(foreign.pk) in forbidden_ids
        # Nicht existierende / unsichtbare PK ist nicht enthalten.
        assert "999999" not in forbidden_ids

    def test_inbox_without_forbidden_param_marks_nothing(self, client, facility, staff_user, assistant_user):
        """Ohne ``forbidden``-Parameter wird nichts vorab markiert (Normalfall)."""
        own = WorkItem.objects.create(
            facility=facility,
            created_by=assistant_user,
            title="Eigene",
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.NORMAL,
        )
        client.force_login(assistant_user)
        response = client.get(reverse("core:workitem_inbox"))
        assert response.status_code == 200
        html = response.content.decode()
        assert "data-forbidden-id=" not in html
        assert response.context["forbidden_ids"] == set()
        assert str(own.pk) in html  # Liste rendert normal


@pytest.mark.django_db
class TestBulkSelectionCap:
    """Refs #2035: Obergrenze der IDs pro Bulk-Submit.

    Die Auswahl kam bis dahin ungecappt aus ``request.POST.getlist`` und landete
    roh in ``filter(pk__in=...)``. Das Ratelimit (30/h) begrenzt die Frequenz,
    nicht die Payload — die einzige faktische Schranke war Djangos
    ``DATA_UPLOAD_MAX_NUMBER_FIELDS`` (Default 1000, im Repo nicht gesetzt).
    Zusaetzlich schlug ein nicht parsbarer Wert als 500 durch, weil
    ``UUIDField`` beim Query-Aufbau eine ``ValidationError`` wirft — die ist
    **keine** ``ValueError``-Unterklasse und wurde nirgends gefangen.
    """

    # Zusatzfeld je Endpunkt, damit ``perform_action`` nicht schon an der
    # eigenen Eingabevalidierung (BulkValidationError → 400) haengenbleibt und
    # der Test wirklich den Cap misst.
    ENDPOINTS = [
        ("core:workitem_bulk_status", {"status": "done"}),
        ("core:workitem_bulk_priority", {"priority": "urgent"}),
        ("core:workitem_bulk_assign", {"assigned_to": ""}),
    ]

    @pytest.mark.parametrize(("url_name", "extra"), ENDPOINTS)
    def test_oversize_selection_redirects_with_message_and_mutates_nothing(
        self, client, staff_user, workitems_open, url_name, extra
    ):
        """Selection > cap → redirect + flash with its own message, without
        any mutation.

        The three **real** IDs must be included in the list: without them
        the request would already fall into the existing rejection path
        ``"Keine gueltigen Aufgaben gefunden."`` today, and the test would be
        green both before and after the fix. Deliberately backported
        (ticket #2385): previously a plain-text 400 instead of a redirect
        (``inbox.html`` calls these views via ordinary forms without
        ``hx-*``).
        """
        client.force_login(staff_user)
        ids = [str(wi.pk) for wi in workitems_open] + [str(uuid.uuid4()) for _ in range(WORKITEM_BULK_MAX_IDS)]
        assert len(ids) > WORKITEM_BULK_MAX_IDS

        response = client.post(reverse(url_name), {"workitem_ids": ids, **extra})

        assert response.status_code == 302
        assert "Zu viele" in str(list(get_messages(response.wsgi_request))[-1])
        for wi in workitems_open:
            wi.refresh_from_db()
            assert wi.status == WorkItem.Status.OPEN
            assert wi.priority == WorkItem.Priority.NORMAL
            assert wi.assigned_to is None

    def test_selection_of_exactly_cap_is_allowed(self, client, staff_user, workitems_open):
        """Grenze ist ``>``, nicht ``>=`` — genau ``WORKITEM_BULK_MAX_IDS`` IDs
        laufen durch (sonst beschnitte der Cap still den legitimen Fluss von
        bis zu 150 sichtbaren Aufgaben)."""
        client.force_login(staff_user)
        ids = [str(wi.pk) for wi in workitems_open] + [
            str(uuid.uuid4()) for _ in range(WORKITEM_BULK_MAX_IDS - len(workitems_open))
        ]
        assert len(ids) == WORKITEM_BULK_MAX_IDS

        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": ids, "status": "done"},
        )

        assert response.status_code == 302
        for wi in workitems_open:
            wi.refresh_from_db()
            assert wi.status == WorkItem.Status.DONE

    def test_malformed_uuid_redirects_with_message_not_500(self, client, staff_user, workitems_open):
        """A broken ID value → redirect + flash instead of 500, nothing gets
        changed. Deliberately backported (ticket #2385): previously a
        plain-text 400 instead of a redirect."""
        client.force_login(staff_user)

        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": [str(workitems_open[0].pk), "nicht-eine-uuid"], "status": "done"},
        )

        assert response.status_code == 302
        assert "Ungültige ID im Payload." in str(list(get_messages(response.wsgi_request))[-1])
        workitems_open[0].refresh_from_db()
        assert workitems_open[0].status == WorkItem.Status.OPEN
