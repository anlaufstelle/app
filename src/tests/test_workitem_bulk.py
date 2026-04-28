"""Tests für Bulk-Edit von WorkItems (Refs #267)."""

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
