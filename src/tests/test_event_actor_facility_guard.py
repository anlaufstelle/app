"""The event services draw the facility boundary of the acting person themselves.

Over HTTP the views already scope the event to the facility of the logged-in
person, and in production the database row security adds a second line; in the
tests it is not active, so the service is the only boundary. A member of staff
from another facility and one without any facility (fail-closed) are refused
with the same message — before any row lock: nothing is locked across the
tenant boundary, nothing is written or changed, and no event history, activity
or audit row is created. The counter-check with staff of the event's own
facility writes as before.

Observed behaviour only: message, stored data, written rows and the tables
actually locked (taken from the executed SQL, not from its wording).
"""

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Activity, AuditLog, Event, EventHistory, User
from core.services.events.crud import create_event, soft_delete_event, update_event
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

_EVENT_OTHER_FACILITY = "Ereignis gehört zu einer anderen Einrichtung."
_ACTORS = ["foreign_staff", "facilityless_staff"]


@pytest.fixture
def facilityless_staff():
    return User.objects.create_user(
        username="facilityless-event-staff",
        role=User.Role.LEAD,
        facility=None,
        is_staff=True,
    )


@pytest.fixture
def live_event(facility, staff_user, doc_type_contact, client_identified):
    return create_event(
        facility=facility,
        user=staff_user,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={"dauer": 15},
        client=client_identified,
    )


@pytest.fixture
def anonymous_event(facility, staff_user, doc_type_contact):
    return create_event(
        facility=facility,
        user=staff_user,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={"dauer": 5},
        is_anonymous=True,
    )


def _traces():
    return (
        Event.objects.count(),
        EventHistory.objects.count(),
        Activity.objects.count(),
        AuditLog.objects.count(),
    )


def _stored(event_pk):
    return Event.objects.values("data_json", "search_text", "is_deleted", "updated_at").get(pk=event_pk)


def _refused_before_any_lock(call):
    """Calls the service and checks message, missing traces and missing row locks."""
    before = _traces()
    with mitschnitt() as recording, pytest.raises(ValidationError) as excinfo:
        call()
    assert excinfo.value.messages == [_EVENT_OTHER_FACILITY]
    assert _traces() == before
    assert recording.sperrfolge() == []


class TestUpdateEvent:
    @pytest.mark.parametrize("actor", _ACTORS)
    def test_foreign_actor_is_refused_before_any_lock(self, request, actor, live_event):
        user = request.getfixturevalue(actor)
        before = _stored(live_event.pk)
        event = Event.objects.get(pk=live_event.pk)

        _refused_before_any_lock(lambda: update_event(event, user, {"dauer": 99}))

        assert _stored(live_event.pk) == before

    def test_local_actor_updates(self, staff_user, live_event):
        event = Event.objects.get(pk=live_event.pk)

        update_event(event, staff_user, {"dauer": 30})

        assert EventHistory.objects.filter(
            event=live_event, action=EventHistory.Action.UPDATE, changed_by=staff_user
        ).exists()
        assert AuditLog.objects.filter(action=AuditLog.Action.EVENT_UPDATE, target_id=str(live_event.pk)).exists()


class TestCreateEvent:
    @pytest.mark.parametrize("actor", _ACTORS)
    @pytest.mark.parametrize("shape", ["anonymous", "person", "person_with_case"])
    def test_foreign_actor_is_refused_before_any_lock(
        self, request, actor, shape, facility, doc_type_contact, client_identified, case_open
    ):
        user = request.getfixturevalue(actor)
        client = None if shape == "anonymous" else client_identified
        case = case_open if shape == "person_with_case" else None

        _refused_before_any_lock(
            lambda: create_event(
                facility=facility,
                user=user,
                document_type=doc_type_contact,
                occurred_at=timezone.now(),
                data_json={"dauer": 15},
                client=client,
                is_anonymous=client is None,
                case=case,
            )
        )

    def test_local_actor_creates(self, facility, staff_user, doc_type_contact, client_identified, case_open):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            client=client_identified,
            case=case_open,
        )

        assert Event.objects.filter(pk=event.pk, created_by=staff_user, case_id=case_open.pk).exists()
        assert AuditLog.objects.filter(action=AuditLog.Action.EVENT_CREATE, target_id=str(event.pk)).exists()


class TestSoftDeleteEvent:
    _EVENTS = {"person": "live_event", "anonymous": "anonymous_event", "attachment": "sample_event"}

    @pytest.mark.parametrize("actor", _ACTORS)
    @pytest.mark.parametrize("shape", ["person", "anonymous", "attachment"])
    def test_foreign_actor_is_refused_before_any_lock(self, request, actor, shape):
        user = request.getfixturevalue(actor)
        event = request.getfixturevalue(self._EVENTS[shape])
        attachment = request.getfixturevalue("authz_attachment") if shape == "attachment" else None
        before = _stored(event.pk)
        stale = Event.objects.get(pk=event.pk)

        _refused_before_any_lock(lambda: soft_delete_event(stale, user))

        assert _stored(event.pk) == before
        if attachment is not None:
            assert type(attachment).objects.filter(pk=attachment.pk).exists()

    def test_local_actor_deletes(self, staff_user, live_event):
        soft_delete_event(Event.objects.get(pk=live_event.pk), staff_user)

        assert Event.objects.filter(pk=live_event.pk, is_deleted=True, deleted_by=staff_user).exists()
        assert AuditLog.objects.filter(action=AuditLog.Action.DELETE, target_id=str(live_event.pk)).exists()
