"""Tests für Bulk-Edit von WorkItems (Refs #267)."""

from datetime import date

import pytest
from django.urls import reverse

from core.models import AuditLog, WorkItem
from core.services.case import (
    bulk_assign_workitems,
    bulk_update_workitem_priority,
    bulk_update_workitem_status,
)


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

    def test_bulk_no_ids_returns_400(self, client, staff_user):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"status": "done"},
        )
        assert response.status_code == 400

    def test_bulk_invalid_status_returns_400(self, client, staff_user, workitems_open):
        client.force_login(staff_user)
        ids = [str(wi.pk) for wi in workitems_open]
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": ids, "status": "bogus"},
        )
        assert response.status_code == 400

    def test_bulk_facility_scope_enforced(self, client, staff_user, second_facility, workitems_open):
        """WorkItems aus anderer Facility dürfen nicht verändert werden."""
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
        # Keine passenden Items (foreign_wi ist gescoped weg) -> 400
        assert response.status_code == 400
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
        assert response.status_code == 403
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
        assert response.status_code == 403
        own.refresh_from_db()
        foreign.refresh_from_db()
        # Kein Item darf verändert worden sein.
        assert own.status == WorkItem.Status.OPEN
        assert foreign.status == WorkItem.Status.OPEN

    def test_bulk_status_view_empty_workitem_ids_field_returns_400(self, client, staff_user, workitems_open):
        """Explizite leere ID-Liste (``workitem_ids=[]``) → 400.

        Ergänzt ``test_bulk_no_ids_returns_400`` (der das Feld komplett
        weglässt) um den Fall, dass das Feld vorhanden aber leer ist
        (Refs #591, WP1)."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_bulk_status"),
            {"workitem_ids": [], "status": "done"},
        )
        assert response.status_code == 400


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
        assert response.status_code == 302
        assert f"assigned_to={lead_user.pk}" in response["HX-Redirect"]


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
        assert response.status_code == 403
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
