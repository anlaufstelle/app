"""Pins for batch scoping, revocation gate and refusals of the work item services (``core.services.case.workitems``).

Source: mutation delta of the checkpoint release-0.99.5, 23 mutants.
Groups:

* ``_locked_workitems`` — the unlocked pre-read of the persons and the work
  item lock each lose their ``pk`` or their facility condition; creator or
  assignee are no longer joined into the locked read; an actor without a
  facility attribute breaks an empty batch.
* ``update_workitem_status`` — the revocation gate receives no person any
  more; the permission refusal at the locked row loses its message.
* ``duplicate_recurring_workitem`` — the caller's instance does not receive
  the idempotency marker that was just stored.
* ``update_workitem`` — the assignee is no longer joined into the locked
  read; the refusals for a row outside the facility scope and for a missing
  permission lose their message.
* ``_assert_actor_in_facility`` and ``create_workitem`` — an actor without a
  facility attribute, a missing facility, or a raw person or facility key
  must reach the documented refusal or the model, never ``AttributeError``
  or a wrong refusal.

The scoping pins only fail with a second work item in the facility that is
not part of the batch, or with a tampered instance (facility in memory = the
actor's, database row in another facility). Besides the outcome, they record
which rows the services read and lock (``tests._row_lock_helpers``:
PostgreSQL plans the captured statement; what counts is the locked table, the
lock strength and the filter values per column, not alias, quoting,
projection, ordering or literal form). No pin fixes how many reads or locks a
service issues: an additional redundant lock on the same rows stays valid.
Messages are compared in full against the module constant or the exact
message text.
"""

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from core.models import AuditLog, Client, WorkItem
from core.services.case.workitems import (
    bulk_update_workitem_status,
    create_workitem,
    duplicate_recurring_workitem,
    update_workitem,
    update_workitem_status,
)
from core.services.client.main import CLIENT_WRITE_REVOKED_MESSAGE
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

WORKITEM_TABLE = "core_workitem"
CLIENT_TABLE = "core_client"
USER_TABLE = "core_user"
REVOCATIONS = ["trashed", "inactive", "merged"]
FOREIGN_FACILITY_TEXT = "Aufgabe gehört zu einer anderen Einrichtung."
NO_PERMISSION_TEXT = "Keine Berechtigung für diese Aufgabe."


def _revoke(person, kind):
    """Revoke the person in the database; loaded instances stay stale."""
    rows = Client.objects.filter(pk=person.pk)
    if kind == "trashed":
        rows.update(is_deleted=True, deleted_at=timezone.now())
    elif kind == "inactive":
        rows.update(is_active=False)
    elif kind == "merged":
        target = Client.objects.create(facility_id=person.facility_id, pseudonym="Merge target")
        rows.update(merged_into=target, is_deleted=True, deleted_at=timezone.now())
    else:
        rows.update(processing_restricted=True)


def _workitem(facility, user, *, client=None, **extra):
    return WorkItem.objects.create(facility=facility, client=client, created_by=user, title="Task", **extra)


def _state(workitem):
    columns = ("status", "priority", "assigned_to_id", "updated_at")
    return WorkItem.objects.filter(pk=workitem.pk).values_list(*columns).get()


def _standalone_user_reads(queries):
    """Statements that read the user table on its own (a lazy creator/assignee load)."""
    return [query for query in queries if query.liest(USER_TABLE) and query.tabellen == {USER_TABLE}]


@pytest.fixture
def other_person(facility, staff_user):
    return Client.objects.create(facility=facility, pseudonym="Other person", created_by=staff_user)


@pytest.fixture
def tampered_foreign_workitem(foreign_workitem, facility):
    """A foreign work item with a person, its facility changed in memory to the actor's."""
    foreign_workitem.facility_id = facility.pk
    return foreign_workitem


@pytest.fixture
def tampered_foreign_personless_workitem(second_facility, foreign_staff, facility):
    """A foreign work item without a person, its facility changed in memory to the actor's."""
    workitem = _workitem(second_facility, foreign_staff)
    workitem.facility_id = facility.pk
    return workitem


class TestLockedWorkitemsPersonPreRead:
    """``_locked_workitems``: the unlocked pre-read that decides which persons are gated and locked."""

    @pytest.mark.parametrize("kind", ["trashed", "restricted"])
    def test_person_of_an_unselected_work_item_is_neither_gated_nor_locked(
        self, facility, client_identified, other_person, staff_user, kind
    ):
        """x__locked_workitems__mutmut_22: without ``pk`` the pre-read took the
        persons of every work item of the facility; the trashed or restricted
        person of the unselected neighbour refused the whole batch and her row
        was locked."""
        selected = _workitem(facility, staff_user, client=client_identified)
        _workitem(facility, staff_user, client=other_person)
        _revoke(other_person, kind)
        with mitschnitt() as queries:
            count = bulk_update_workitem_status([selected], staff_user, WorkItem.Status.DONE)
        assert count == 1
        selected.refresh_from_db()
        assert selected.status == WorkItem.Status.DONE
        person_locks = queries.sperren(CLIENT_TABLE)
        assert person_locks
        assert all(lock.traegt(id=[client_identified.pk]) for lock in person_locks), person_locks

    def test_tampered_instance_does_not_reach_the_foreign_person(
        self, tampered_foreign_workitem, foreign_client, staff_user, facility
    ):
        """x__locked_workitems__mutmut_23: without the facility condition the
        pre-read returned the person of the foreign row, the gate locked her
        and refused the batch with the revocation message. Observed: nothing
        is changed, the foreign person row is never locked, and every read of
        the work item table carries the actor's facility."""
        before = _state(tampered_foreign_workitem)
        with mitschnitt() as queries:
            count = bulk_update_workitem_status([tampered_foreign_workitem], staff_user, WorkItem.Status.DONE)
        assert count == 0
        assert _state(tampered_foreign_workitem) == before
        assert not any(lock.traegt(id=[foreign_client.pk]) for lock in queries.sperren(CLIENT_TABLE))
        reads = queries.lesungen(WORKITEM_TABLE)
        assert reads
        assert all(read.traegt(id=[tampered_foreign_workitem.pk], facility_id=[facility.pk]) for read in reads), reads


class TestLockedWorkitemsRowLock:
    """``_locked_workitems``: the locked read of the work item rows of the batch."""

    def test_unselected_neighbours_stay_untouched(self, facility, client_identified, staff_user):
        """x__locked_workitems__mutmut_42: without ``pk`` the lock took every
        work item of the facility; the neighbours without a person or with a
        person of the batch were changed and audited along with it."""
        selected = _workitem(facility, staff_user, client=client_identified)
        neighbours = [
            _workitem(facility, staff_user),
            _workitem(facility, staff_user, client=client_identified),
        ]
        before = [_state(neighbour) for neighbour in neighbours]
        with mitschnitt() as queries:
            count = bulk_update_workitem_status([selected], staff_user, WorkItem.Status.DONE)
        assert count == 1
        assert [_state(neighbour) for neighbour in neighbours] == before
        locks = queries.sperren(WORKITEM_TABLE)
        assert locks
        assert all(lock.sperrart == "UPDATE" and lock.traegt(id=[selected.pk]) for lock in locks), locks

    def test_tampered_personless_instance_stays_untouched(
        self, tampered_foreign_personless_workitem, staff_user, facility
    ):
        """x__locked_workitems__mutmut_43: without the facility condition the
        lock took the foreign row; without a person it passed the
        revalidation and was set to done. Observed: nothing is changed and
        every lock on the work item table carries the actor's facility."""
        workitem = tampered_foreign_personless_workitem
        before = _state(workitem)
        with mitschnitt() as queries:
            count = bulk_update_workitem_status([workitem], staff_user, WorkItem.Status.DONE)
        assert count == 0
        assert _state(workitem) == before
        locks = queries.sperren(WORKITEM_TABLE)
        assert locks
        assert all(lock.traegt(id=[workitem.pk], facility_id=[facility.pk]) for lock in locks), locks


class TestLockedWorkitemsJoinedPermissionData:
    """``_locked_workitems``: creator and assignee come with the locked read, no query per work item."""

    def test_permission_check_reads_no_user_row_per_work_item(self, facility, lead_user, staff_user):
        """x__locked_workitems__mutmut_46, x__locked_workitems__mutmut_47:
        without the creator or the assignee in ``select_related`` the
        permission check of a non-lead actor loaded that user per work item.
        The work items are created by someone else and assigned to the actor,
        so the check reads both relations. Observed: the number of separate
        user reads does not grow with the batch size."""

        def run(size):
            items = [_workitem(facility, lead_user, assigned_to=staff_user) for _ in range(size)]
            with mitschnitt() as queries:
                assert bulk_update_workitem_status(items, staff_user, WorkItem.Status.DONE) == size
            return len(_standalone_user_reads(queries))

        assert run(3) == run(1)


class TestUpdateWorkitemStatusRevocation:
    """``update_workitem_status``: the revocation gate on the locked person row."""

    @pytest.mark.parametrize("kind", REVOCATIONS)
    def test_revoked_person_refuses_the_status_change(self, facility, client_identified, staff_user, kind):
        """x_update_workitem_status__mutmut_23: the gate received no person
        and let the status change of a revoked person's work item through.
        A non-recurring work item and ``in_progress`` keep the follow-up path
        (which carries the same message) out of play."""
        workitem = _workitem(facility, staff_user, client=client_identified)
        _revoke(client_identified, kind)
        audit_rows = AuditLog.objects.count()
        with pytest.raises(ValidationError) as excinfo:
            update_workitem_status(workitem, WorkItem.Status.IN_PROGRESS, staff_user)
        assert excinfo.value.messages == [str(CLIENT_WRITE_REVOKED_MESSAGE)]
        workitem.refresh_from_db()
        assert workitem.status == WorkItem.Status.OPEN
        assert workitem.assigned_to_id is None
        assert AuditLog.objects.count() == audit_rows

    def test_revoked_person_refuses_done_on_a_non_recurring_work_item(self, facility, client_identified, staff_user):
        """x_update_workitem_status__mutmut_23: done on a non-recurring work
        item creates no follow-up, so only the gate of the status change
        itself can refuse it."""
        workitem = _workitem(facility, staff_user, client=client_identified)
        _revoke(client_identified, "trashed")
        with pytest.raises(ValidationError) as excinfo:
            update_workitem_status(workitem, WorkItem.Status.DONE, staff_user)
        assert excinfo.value.messages == [str(CLIENT_WRITE_REVOKED_MESSAGE)]
        workitem.refresh_from_db()
        assert workitem.status == WorkItem.Status.OPEN
        assert workitem.completed_at is None


class TestDuplicateRecurringWorkitem:
    """``duplicate_recurring_workitem``: the caller's instance receives the stored marker."""

    def test_status_change_returns_the_stored_marker(self, facility, client_identified, staff_user):
        """x_duplicate_recurring_workitem__mutmut_90: the caller's instance
        kept ``recurrence_duplicated_at`` empty although the row carries it;
        a full save of the returned instance erased the marker and the next
        done transition created a second follow-up."""
        workitem = _workitem(facility, staff_user, client=client_identified, recurrence=WorkItem.Recurrence.WEEKLY)
        returned = update_workitem_status(workitem, WorkItem.Status.DONE, staff_user)
        stored = WorkItem.objects.values_list("recurrence_duplicated_at", flat=True).get(pk=workitem.pk)
        assert stored is not None
        assert returned.recurrence_duplicated_at == stored
        returned.save()
        update_workitem_status(returned, WorkItem.Status.OPEN, staff_user)
        update_workitem_status(returned, WorkItem.Status.DONE, staff_user)
        follow_ups = WorkItem.objects.filter(facility=facility).exclude(pk=workitem.pk)
        assert follow_ups.count() == 1

    def test_direct_call_updates_the_callers_instance(self, facility, client_identified, staff_user):
        """x_duplicate_recurring_workitem__mutmut_90: the direct service call
        leaves the marker on the caller's instance as stored."""
        workitem = _workitem(facility, staff_user, client=client_identified, recurrence=WorkItem.Recurrence.MONTHLY)
        assert duplicate_recurring_workitem(workitem, staff_user) is not None
        stored = WorkItem.objects.values_list("recurrence_duplicated_at", flat=True).get(pk=workitem.pk)
        assert stored is not None
        assert workitem.recurrence_duplicated_at == stored


class TestUpdateWorkitemJoinedAssignee:
    """``update_workitem``: creator and assignee come with the locked state read."""

    def test_assignee_edit_reads_no_separate_user_row(self, facility, lead_user, staff_user):
        """x_update_workitem__mutmut_69: without the assignee in
        ``select_related`` the permission check of a non-lead assignee who
        did not create the work item loaded the assignee separately."""
        workitem = _workitem(facility, lead_user, assigned_to=staff_user)
        with mitschnitt() as queries:
            update_workitem(workitem, staff_user, title="Renamed")
        workitem.refresh_from_db()
        assert workitem.title == "Renamed"
        assert _standalone_user_reads(queries) == []


@pytest.fixture
def reassigned_workitem(facility, lead_user):
    """Created by and assigned to someone else: a non-lead actor may not change it."""
    return _workitem(facility, lead_user, assigned_to=lead_user)


def _actor_without_facility(kind):
    return AnonymousUser() if kind == "anonymous" else SimpleNamespace()


class TestAssertActorInFacility:
    """``_assert_actor_in_facility``: fail-closed for an actor without a facility attribute."""

    @pytest.mark.parametrize("kind", ["anonymous", "namespace"])
    def test_create_refuses_an_actor_without_facility(self, facility, kind):
        """x__assert_actor_in_facility__mutmut_6: without the ``None`` default
        the attribute lookup raised ``AttributeError`` instead of the
        foreign-facility refusal."""
        with pytest.raises(ValidationError) as excinfo:
            create_workitem(facility, _actor_without_facility(kind), title="Task")
        assert excinfo.value.messages == [FOREIGN_FACILITY_TEXT]
        assert not WorkItem.objects.exists()

    def test_status_change_refuses_an_anonymous_actor(self, sample_workitem):
        """x__assert_actor_in_facility__mutmut_6: the same guard in front of
        the status change; the work item stays open."""
        with pytest.raises(ValidationError) as excinfo:
            update_workitem_status(sample_workitem, WorkItem.Status.DONE, AnonymousUser())
        assert excinfo.value.messages == [FOREIGN_FACILITY_TEXT]
        sample_workitem.refresh_from_db()
        assert sample_workitem.status == WorkItem.Status.OPEN


class TestLockedWorkitemsEmptyBatch:
    """``_locked_workitems``: an empty batch reaches the facility lookup without a prior actor check."""

    @pytest.mark.parametrize("kind", ["anonymous", "namespace"])
    def test_empty_batch_of_an_actor_without_facility_changes_nothing(self, kind):
        """x__locked_workitems__mutmut_10: without the ``None`` default the
        facility lookup raised ``AttributeError`` for an empty batch."""
        assert bulk_update_workitem_status([], _actor_without_facility(kind), WorkItem.Status.DONE) == 0


class TestCreateWorkitemKeys:
    """``create_workitem``: facility and person may arrive as instance, key or ``None``."""

    def test_missing_facility_is_refused_as_foreign(self, staff_user):
        """x_create_workitem__mutmut_7: without the fallback to the argument
        itself, ``None`` raised ``AttributeError`` instead of the
        foreign-facility refusal."""
        with pytest.raises(ValidationError) as excinfo:
            create_workitem(None, staff_user, title="Task")
        assert excinfo.value.messages == [FOREIGN_FACILITY_TEXT]
        assert not WorkItem.objects.exists()

    def test_raw_facility_key_passes_the_facility_guard(self, facility, staff_user):
        """x_create_workitem__mutmut_4, x_create_workitem__mutmut_7: the raw
        key of the actor's own facility is the actor's facility; the guard
        lets it through and only the model constructor rejects the key
        (``ValueError``). With ``None`` as fallback the guard refused it as
        foreign; without a fallback the lookup raised ``AttributeError``."""
        with pytest.raises(ValueError):
            create_workitem(facility.pk, staff_user, title="Task")
        assert not WorkItem.objects.exists()

    def test_raw_person_key_passes_the_revocation_gate(self, facility, client_identified, staff_user):
        """x_create_workitem__mutmut_26, x_create_workitem__mutmut_29: the raw
        key of a live person is that person for the revocation gate; only the
        model constructor rejects the key (``ValueError``). With ``None`` as
        fallback the gate refused a live person as revoked; without a fallback
        the lookup raised ``AttributeError``."""
        with pytest.raises(ValueError):
            create_workitem(facility, staff_user, client=client_identified.pk, title="Task")
        assert not WorkItem.objects.exists()


class TestUpdateWorkitemRefusalMessages:
    """``update_workitem``: the refusals at the locked state keep their message."""

    def test_row_outside_the_facility_scope_is_refused_with_its_message(
        self, tampered_foreign_workitem, foreign_client, staff_user, facility
    ):
        """x_update_workitem__mutmut_42, x_update_workitem__mutmut_43: the
        refusal of a row missing from the facility scope lost its message.
        The actor guard in front carries the same text; the observed scoped
        work item lock shows that the refusal comes from the row check
        behind it."""
        before = _state(tampered_foreign_workitem)
        with mitschnitt() as queries, pytest.raises(ValidationError) as excinfo:
            update_workitem(tampered_foreign_workitem, staff_user, title="Renamed")
        assert excinfo.value.messages == [FOREIGN_FACILITY_TEXT]
        assert _state(tampered_foreign_workitem) == before
        assert any(
            lock.traegt(id=[tampered_foreign_workitem.pk], facility_id=[facility.pk])
            for lock in queries.sperren(WORKITEM_TABLE)
        )
        assert not any(lock.traegt(id=[foreign_client.pk]) for lock in queries.sperren(CLIENT_TABLE))

    def test_missing_permission_is_refused_with_its_message(self, reassigned_workitem, staff_user):
        """x_update_workitem__mutmut_89, x_update_workitem__mutmut_90: the
        permission refusal at the locked state lost its message; the edit
        view answers with its own text, so only a direct caller sees it."""
        with pytest.raises(PermissionDenied) as excinfo:
            update_workitem(reassigned_workitem, staff_user, title="Renamed")
        assert str(excinfo.value) == NO_PERMISSION_TEXT
        reassigned_workitem.refresh_from_db()
        assert reassigned_workitem.title == "Task"


class TestUpdateWorkitemStatusRefusalMessage:
    """``update_workitem_status``: the permission refusal at the locked row keeps its message."""

    def test_missing_permission_is_refused_with_its_message(self, reassigned_workitem, staff_user):
        """x_update_workitem_status__mutmut_45, x_update_workitem_status__mutmut_46:
        the permission refusal lost its message; the status view answers with
        its own text, so only a direct caller sees it."""
        with pytest.raises(PermissionDenied) as excinfo:
            update_workitem_status(reassigned_workitem, WorkItem.Status.IN_PROGRESS, staff_user)
        assert str(excinfo.value) == NO_PERMISSION_TEXT
        reassigned_workitem.refresh_from_db()
        assert reassigned_workitem.status == WorkItem.Status.OPEN
