"""Tests fuer ``core.services.case.workitems`` und seine internen Helper.

Enthaelt die Cluster (Split aus ``test_workitems.py``):

* ``TestUpdateWorkItemService`` — Public-API ``update_workitem`` Service.
* ``TestApplyStatusTransitionHelper`` — Refs #906: ``_apply_status_transition``.
* ``TestMaybeDuplicateRecurringHelper`` — Refs #906: ``_maybe_duplicate_recurring``.

Fixture ``workitem_open`` ist inline gehalten (kopiert aus dem Original-File),
weil pytest File-spezifische Fixtures nicht ueber Imports entdeckt.
"""

import pytest

from core.models import WorkItem
from core.models.activity import Activity
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


@pytest.fixture
def workitem_unassigned(facility, staff_user):
    """Personenlose Aufgabe (Refs #1786): Attach ist hier weiterhin erlaubt."""
    return WorkItem.objects.create(
        facility=facility,
        client=None,
        created_by=staff_user,
        title="Personenlose Aufgabe",
        status=WorkItem.Status.OPEN,
        priority=WorkItem.Priority.NORMAL,
    )


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

    def test_update_workitem_rejects_unknown_field(self, workitem_open, staff_user):
        with pytest.raises(ValueError, match="Feld"):
            update_workitem(workitem_open, staff_user, status="done")

    def test_updates_client_association(self, facility, staff_user, workitem_unassigned, client_identified):
        """Nachtraegliches Zuordnen an eine personenlose Aufgabe bleibt erlaubt.

        Refs #1786 (Maintainer-Entscheid 2026-08-05): frueher pruefte dieser
        Test den freien Wechsel der Personenzuordnung — der ist jetzt verboten.
        """
        update_workitem(workitem_unassigned, staff_user, client=client_identified)
        workitem_unassigned.refresh_from_db()
        assert workitem_unassigned.client == client_identified

    def test_update_workitem_allows_attach_to_unassigned(self, workitem_unassigned, client_identified, staff_user):
        """Nachtraegliches Zuordnen an personenlose Aufgabe bleibt erlaubt (Maintainer-Entscheid 2026-08-05)."""
        result = update_workitem(workitem_unassigned, staff_user, client=client_identified)
        assert result.client == client_identified

    def test_update_workitem_rejects_client_change(self, workitem_open, client_qualified, staff_user):
        with pytest.raises(ValueError, match="Personenzuordnung"):
            update_workitem(workitem_open, staff_user, client=client_qualified)

    def test_clears_client_association(self, facility, staff_user, workitem_open):
        """Refs #1786: Entfernen der Personenzuordnung ist jetzt verboten."""
        with pytest.raises(ValueError, match="Personenzuordnung"):
            update_workitem(workitem_open, staff_user, client=None)

    def test_update_workitem_same_client_is_noop(self, workitem_open, staff_user):
        result = update_workitem(workitem_open, staff_user, client=workitem_open.client)
        assert result.client == workitem_open.client

    def test_returns_updated_workitem(self, facility, staff_user, workitem_open):
        result = update_workitem(workitem_open, staff_user, description="Neue Beschreibung")
        assert result.pk == workitem_open.pk
        assert result.description == "Neue Beschreibung"


@pytest.mark.django_db
class TestApplyStatusTransitionHelper:
    """Refs #906: isolierte Tests fuer ``_apply_status_transition``.

    Die Funktion mutiert das ``workitem``-Objekt in-place, **ohne**
    ``save()`` — der Caller (single oder bulk) entscheidet ueber
    Persistenz. Tests verifizieren jede der gemeinsamen Regeln.
    """

    def _make(self, facility, user, *, status, assigned_to=None, completed_at=None, recurrence=None):
        from core.models import WorkItem

        return WorkItem.objects.create(
            facility=facility,
            created_by=user,
            item_type=WorkItem.ItemType.TASK,
            status=status,
            assigned_to=assigned_to,
            completed_at=completed_at,
            recurrence=recurrence or WorkItem.Recurrence.NONE,
            title="Test",
        )

    def test_returns_false_on_identical_status(self, facility, staff_user):
        from core.models import WorkItem
        from core.services.case import _apply_status_transition

        wi = self._make(facility, staff_user, status=WorkItem.Status.OPEN)
        changed = _apply_status_transition(wi, WorkItem.Status.OPEN, staff_user, auto_assign=True)
        assert changed is False
        # In-place keine Mutation.
        assert wi.status == WorkItem.Status.OPEN
        assert wi.assigned_to is None

    def test_auto_assigns_to_user_on_in_progress(self, facility, staff_user):
        from core.models import WorkItem
        from core.services.case import _apply_status_transition

        wi = self._make(facility, staff_user, status=WorkItem.Status.OPEN)
        changed = _apply_status_transition(wi, WorkItem.Status.IN_PROGRESS, staff_user, auto_assign=True)
        assert changed is True
        assert wi.status == WorkItem.Status.IN_PROGRESS
        assert wi.assigned_to == staff_user

    def test_no_auto_assign_when_disabled(self, facility, staff_user):
        from core.models import WorkItem
        from core.services.case import _apply_status_transition

        wi = self._make(facility, staff_user, status=WorkItem.Status.OPEN)
        _apply_status_transition(wi, WorkItem.Status.IN_PROGRESS, staff_user, auto_assign=False)
        assert wi.assigned_to is None

    def test_preserves_existing_assignee_on_in_progress(self, facility, staff_user, lead_user):
        from core.models import WorkItem
        from core.services.case import _apply_status_transition

        wi = self._make(facility, staff_user, status=WorkItem.Status.OPEN, assigned_to=lead_user)
        _apply_status_transition(wi, WorkItem.Status.IN_PROGRESS, staff_user, auto_assign=True)
        # Vorhandener Assignee bleibt — Auto-Assign nur, wenn noch keiner gesetzt.
        assert wi.assigned_to == lead_user

    def test_sets_completed_at_on_done(self, facility, staff_user):
        from core.models import WorkItem
        from core.services.case import _apply_status_transition

        wi = self._make(facility, staff_user, status=WorkItem.Status.IN_PROGRESS)
        assert wi.completed_at is None
        _apply_status_transition(wi, WorkItem.Status.DONE, staff_user, auto_assign=True)
        assert wi.completed_at is not None

    def test_sets_completed_at_on_dismissed(self, facility, staff_user):
        from core.models import WorkItem
        from core.services.case import _apply_status_transition

        wi = self._make(facility, staff_user, status=WorkItem.Status.OPEN)
        _apply_status_transition(wi, WorkItem.Status.DISMISSED, staff_user, auto_assign=True)
        assert wi.completed_at is not None

    def test_clears_completed_at_on_reopen(self, facility, staff_user):
        from django.utils import timezone as dj_timezone

        from core.models import WorkItem
        from core.services.case import _apply_status_transition

        wi = self._make(facility, staff_user, status=WorkItem.Status.DONE, completed_at=dj_timezone.now())
        _apply_status_transition(wi, WorkItem.Status.OPEN, staff_user, auto_assign=True)
        assert wi.completed_at is None


@pytest.mark.django_db
class TestMaybeDuplicateRecurringHelper:
    """Refs #906: isolierte Tests fuer ``_maybe_duplicate_recurring``."""

    def _make(self, facility, user, *, recurrence, due_date=None):
        from core.models import WorkItem

        return WorkItem.objects.create(
            facility=facility,
            created_by=user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            recurrence=recurrence,
            due_date=due_date,
            title="Test",
        )

    def test_no_duplicate_when_status_is_not_done(self, facility, staff_user):
        from core.models import WorkItem
        from core.services.case import _maybe_duplicate_recurring

        wi = self._make(facility, staff_user, recurrence=WorkItem.Recurrence.WEEKLY)
        before = WorkItem.objects.count()
        _maybe_duplicate_recurring(wi, staff_user, WorkItem.Status.IN_PROGRESS)
        assert WorkItem.objects.count() == before

    def test_no_duplicate_when_recurrence_is_none(self, facility, staff_user):
        from core.models import WorkItem
        from core.services.case import _maybe_duplicate_recurring

        wi = self._make(facility, staff_user, recurrence=WorkItem.Recurrence.NONE)
        before = WorkItem.objects.count()
        _maybe_duplicate_recurring(wi, staff_user, WorkItem.Status.DONE)
        assert WorkItem.objects.count() == before

    def test_duplicates_when_done_and_recurring(self, facility, staff_user):
        from datetime import date

        from core.models import WorkItem
        from core.services.case import _maybe_duplicate_recurring

        wi = self._make(facility, staff_user, recurrence=WorkItem.Recurrence.WEEKLY, due_date=date(2026, 1, 1))
        before = WorkItem.objects.count()
        _maybe_duplicate_recurring(wi, staff_user, WorkItem.Status.DONE)
        assert WorkItem.objects.count() == before + 1
