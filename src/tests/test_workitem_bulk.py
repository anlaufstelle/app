"""Tests für Bulk-Edit von WorkItems (Refs #267)."""

from datetime import date

import pytest
from django.urls import reverse

from core.models import AuditLog, WorkItem
from core.services.workitems import (
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

    def test_bulk_rejects_items_without_ownership(self, client, facility, staff_user, assistant_user):
        """Assistenz darf Bulk-Mutation auf nicht eigene/zugewiesene Items nicht
        ausführen — Bulk-Route muss dieselbe Ownership-Policy wie die
        Single-Route durchsetzen (Refs #583)."""
        # Aufgabe gehört staff_user, assistant ist nicht Ersteller/Assignee
        foreign = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
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

    def test_bulk_mixed_ownership_rejects_whole_batch(self, client, facility, staff_user, assistant_user):
        """Bei gemischtem Batch (eigene + fremde) wird der ganze Request
        abgelehnt — kein Partial-Success, damit der Erfolgstext nicht lügt."""
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
class TestBulkStatusRecurrence:
    """Integrationstest Bulk-Status DONE + Recurrence (Refs #591, WP1).

    Erwartung aus dem Plan: Wenn Bulk-Status DONE auf recurring Items gesetzt
    wird, sollte pro Item eine Auto-Duplizierung wie im Single-Update-Pfad
    (``update_workitem_status``) erfolgen.

    Aktueller Stand: ``bulk_update_workitem_status`` triggert die Recurrence
    NICHT — es ruft weder ``duplicate_recurring_workitem`` noch
    ``update_workitem_status`` auf (siehe ``src/core/services/workitems.py``).
    Damit ist das ein aufgedeckter Prod-Gap, den wir per ``xfail(strict=True)``
    dokumentieren, ohne Produktivcode anzufassen.
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

    @pytest.mark.xfail(
        reason=(
            "Prod-Gap (Refs #591): bulk_update_workitem_status triggert "
            "Recurrence-Duplizierung nicht. Der Service ruft weder "
            "duplicate_recurring_workitem noch update_workitem_status auf. "
            "Erst nach Fix darf dieser xfail entfernt werden."
        ),
        strict=True,
    )
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
