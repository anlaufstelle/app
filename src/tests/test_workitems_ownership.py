"""Tests fuer WorkItem-Ownership-Checks und Optimistic Locking.

Enthaelt die Cluster (Split aus ``test_workitems.py``):

* ``TestWorkItemStatusUpdateOwnership`` — Status-Pfad: nur Owner/Assignee/Lead/Admin.
* ``TestWorkItemUpdateOwnership`` — Refs #735: Edit-Pfad mit gleicher Policy.
* ``TestOptimisticLockingWorkItem`` — Optimistic-Locking auf Updates (Refs #531).

Fixture ``workitem_open`` ist inline gehalten (kopiert aus dem Original-File),
weil pytest File-spezifische Fixtures nicht ueber Imports entdeckt.
"""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.models import WorkItem
from core.services.case import update_workitem


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
        """Assistant who neither created nor is assigned gets 403.

        Refs #1125: Das Item muss einer *anderen* Person zugewiesen sein —
        nicht zugewiesene Items sind Teamaufgaben und damit bewusst mutierbar.
        """
        wi = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            assigned_to=staff_user,
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
class TestWorkItemUpdateOwnership:
    """Refs #735: Voller Edit-Pfad muss dieselbe Owner/Assignee/Lead/Admin-
    Policy wie der Status-Pfad erzwingen. Frueher prueften wir nur StaffMixin —
    Staff konnte fremde WorkItems editieren.
    """

    def test_staff_cannot_edit_others_workitem_get(self, client, staff_user, facility):
        # Erzeuge einen zweiten Staff-User in derselben Facility und ein
        # WorkItem fuer ihn — staff_user ist weder owner noch assignee.
        from core.models import User

        other_staff = User.objects.create_user(
            username="otherstaff",
            password="x",
            facility=facility,
            role=User.Role.STAFF,
        )
        # Refs #1125: einem Dritten zugewiesen → echte Ownership-Grenze
        # (unassigned waere eine mutierbare Teamaufgabe).
        wi = WorkItem.objects.create(
            facility=facility,
            created_by=other_staff,
            assigned_to=other_staff,
            title="Fremde Edit-Aufgabe",
            status=WorkItem.Status.OPEN,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_update", kwargs={"pk": wi.pk}))
        assert response.status_code == 403

    def test_staff_cannot_edit_others_workitem_post(self, client, staff_user, facility):
        from core.models import User

        other_staff = User.objects.create_user(
            username="otherstaff2",
            password="x",
            facility=facility,
            role=User.Role.STAFF,
        )
        # Refs #1125: einem Dritten zugewiesen → echte Ownership-Grenze.
        wi = WorkItem.objects.create(
            facility=facility,
            created_by=other_staff,
            assigned_to=other_staff,
            title="Fremde Edit-Aufgabe POST",
            status=WorkItem.Status.OPEN,
        )
        client.force_login(staff_user)
        response = client.post(
            reverse("core:workitem_update", kwargs={"pk": wi.pk}),
            {
                "item_type": "task",
                "title": "Veraendert!",
                "priority": "normal",
            },
        )
        assert response.status_code == 403
        wi.refresh_from_db()
        assert wi.title == "Fremde Edit-Aufgabe POST"

    def test_staff_can_edit_own_workitem(self, client, staff_user, facility):
        wi = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="Eigene Edit-Aufgabe",
            status=WorkItem.Status.OPEN,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_update", kwargs={"pk": wi.pk}))
        assert response.status_code == 200

    def test_staff_can_edit_assigned_workitem(self, client, staff_user, facility):
        from core.models import User

        creator = User.objects.create_user(
            username="creator",
            password="x",
            facility=facility,
            role=User.Role.STAFF,
        )
        wi = WorkItem.objects.create(
            facility=facility,
            created_by=creator,
            assigned_to=staff_user,
            title="Zugewiesene Edit-Aufgabe",
            status=WorkItem.Status.OPEN,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_update", kwargs={"pk": wi.pk}))
        assert response.status_code == 200

    def test_lead_can_edit_any_workitem(self, client, lead_user, staff_user, facility):
        wi = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="Lead-Edit-Aufgabe",
            status=WorkItem.Status.OPEN,
        )
        client.force_login(lead_user)
        response = client.get(reverse("core:workitem_update", kwargs={"pk": wi.pk}))
        assert response.status_code == 200


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
