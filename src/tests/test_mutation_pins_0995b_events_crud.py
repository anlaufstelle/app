"""Pins for deleting events and for the actor check of the event services (``core.services.events.crud``).

Source: mutation delta of the release-0.99.5 checkpoint, 2 mutants.
Groups:

* ``soft_delete_event`` — the trash check on the locked row loses ``pk`` and
  reads the deletion state of the first event of the facility instead of the
  target. The pins place a second event of the same facility in front of the
  target under both orders a lookup without ``pk`` could take: it is the
  newer one (default order ``-occurred_at``) and carries the smaller
  primary key. Once it is in the trash while the target is live, once the
  other way round.
* ``_assert_actor_in_facility`` — ``facility_id`` is read without a default.
  An actor without that attribute (no person, an anonymous user, a bare
  object) then fails with ``AttributeError`` instead of the fail-closed
  refusal. The pins go through every service that runs the check: creating,
  editing and deleting an event and filing a deletion request.
"""

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Activity, AuditLog, DeletionRequest, Event, EventHistory
from core.services.events.crud import (
    _EVENT_OTHER_FACILITY_MESSAGE,
    create_event,
    soft_delete_event,
    update_event,
)
from core.services.events.deletion import create_or_get_event_deletion_request
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

ALREADY_DELETED_MESSAGE = "Dieses Ereignis liegt bereits im Papierkorb."

#: Primary keys that put the neighbour first and the target last in pk order.
SMALLEST_PK = uuid.UUID(int=1)
LARGEST_PK = uuid.UUID(int=(1 << 128) - 1)

ACTORS_WITHOUT_FACILITY = [
    pytest.param(None, id="no-actor"),
    pytest.param(AnonymousUser(), id="anonymous-user"),
    pytest.param(SimpleNamespace(), id="bare-object"),
]


def _event(facility, client, doc_type, user, *, pk, days_ago):
    return Event.objects.create(
        pk=pk,
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=timezone.now() - timedelta(days=days_ago),
        data_json={"dauer": 10},
        created_by=user,
    )


def _deletion_trail(event):
    return (
        EventHistory.objects.filter(event_id=event.pk, action=EventHistory.Action.DELETE).count(),
        AuditLog.objects.filter(action=AuditLog.Action.DELETE, target_type="Event", target_id=str(event.pk)).count(),
    )


def _counts():
    return (
        Event.objects.count(),
        EventHistory.objects.count(),
        Activity.objects.count(),
        AuditLog.objects.count(),
        DeletionRequest.objects.count(),
    )


@pytest.fixture
def target_and_neighbour(facility, client_identified, doc_type_contact, staff_user):
    """The older target and a newer neighbour of the same facility with the smaller pk."""
    target = _event(facility, client_identified, doc_type_contact, staff_user, pk=LARGEST_PK, days_ago=5)
    neighbour = _event(facility, client_identified, doc_type_contact, staff_user, pk=SMALLEST_PK, days_ago=0)
    first_by_default_order = Event.objects.filter(facility=facility).values_list("pk", flat=True).first()
    first_by_pk = Event.objects.filter(facility=facility).order_by("pk").values_list("pk", flat=True).first()
    assert first_by_default_order == first_by_pk == neighbour.pk
    return target, neighbour


class TestSoftDeleteEventTrashCheck:
    """``soft_delete_event``: the trash check reads the target row, not a neighbour."""

    def test_live_target_is_deleted_next_to_a_trashed_neighbour(self, target_and_neighbour, staff_user):
        """x_soft_delete_event__mutmut_29: without ``pk`` the check reads the
        trashed neighbour and refuses the live target as already deleted."""
        target, neighbour = target_and_neighbour
        soft_delete_event(neighbour, staff_user)

        soft_delete_event(target, staff_user)

        target.refresh_from_db()
        assert target.is_deleted is True
        assert _deletion_trail(target) == (1, 1)

    def test_trashed_target_is_refused_next_to_a_live_neighbour(self, target_and_neighbour, staff_user, lead_user):
        """x_soft_delete_event__mutmut_29: without ``pk`` the check reads the
        live neighbour and deletes the trashed target a second time, with a
        second history and audit entry."""
        target, neighbour = target_and_neighbour
        soft_delete_event(target, staff_user)
        trail = _deletion_trail(target)
        counts = _counts()

        with pytest.raises(ValidationError) as excinfo:
            soft_delete_event(target, lead_user)

        assert excinfo.value.code == "event_already_deleted"
        assert excinfo.value.messages == [ALREADY_DELETED_MESSAGE]
        assert trail == (1, 1)
        assert _deletion_trail(target) == trail
        assert _counts() == counts
        target.refresh_from_db()
        assert target.deleted_by_id == staff_user.pk
        neighbour.refresh_from_db()
        assert neighbour.is_deleted is False


class TestAssertActorInFacility:
    """``_assert_actor_in_facility``: an actor without ``facility_id`` is refused, nothing is locked or written."""

    @pytest.fixture
    def event(self, target_and_neighbour):
        return target_and_neighbour[0]

    def _assert_refused_without_traces(self, call):
        counts = _counts()
        with mitschnitt() as log, pytest.raises(ValidationError) as excinfo:
            call()
        assert excinfo.value.messages == [str(_EVENT_OTHER_FACILITY_MESSAGE)]
        assert log.sperrfolge() == []
        assert _counts() == counts

    @pytest.mark.parametrize("actor", ACTORS_WITHOUT_FACILITY)
    def test_create_event(self, facility, client_identified, doc_type_contact, actor):
        """x__assert_actor_in_facility__mutmut_5: ``getattr(user, "facility_id")``
        without default raises ``AttributeError`` instead of the refusal."""
        self._assert_refused_without_traces(
            lambda: create_event(
                facility,
                actor,
                doc_type_contact,
                timezone.now(),
                {"dauer": 5},
                client=client_identified,
            )
        )

    @pytest.mark.parametrize("actor", ACTORS_WITHOUT_FACILITY)
    def test_update_event(self, event, actor):
        """x__assert_actor_in_facility__mutmut_5: editing runs the same check."""
        before = Event.objects.filter(pk=event.pk).values().get()
        self._assert_refused_without_traces(lambda: update_event(event, actor, {"dauer": 99}))
        assert Event.objects.filter(pk=event.pk).values().get() == before

    @pytest.mark.parametrize("actor", ACTORS_WITHOUT_FACILITY)
    def test_soft_delete_event(self, event, actor):
        """x__assert_actor_in_facility__mutmut_5: deleting runs the same check."""
        self._assert_refused_without_traces(lambda: soft_delete_event(event, actor))
        event.refresh_from_db()
        assert event.is_deleted is False

    @pytest.mark.parametrize("actor", ACTORS_WITHOUT_FACILITY)
    def test_create_or_get_event_deletion_request(self, event, actor):
        """x__assert_actor_in_facility__mutmut_5: filing a deletion request
        runs the same check before the lookup of a running request."""
        self._assert_refused_without_traces(
            lambda: create_or_get_event_deletion_request(event, actor, "Filed without an actor")
        )
