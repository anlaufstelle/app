"""Creating an event on a case reads the case fresh and under a row lock.

The form only offers live open cases of the facility, but the case instance
handed to ``create_event`` stems from a read before the transaction. Between
that read and the write the case may have gone to the trash, been anonymised,
lost its person (trash or deactivation) or been attached to another person.
The service therefore locks person -> case, re-reads the case under the lock
with the person of the event in the condition and refuses with the message of
the case write contract: no event, no event history, no activity and no audit
row. A case that now belongs to another person is refused without locking that
other person's row.

Observed behaviour only: result and message, written rows, audit and activity
rows, and the tables actually locked (taken from the executed SQL, not from its
wording).
"""

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Activity, AuditLog, Case, Client, Event, EventHistory
from core.services.case.state_contract import CASE_WRITE_GONE_MESSAGE, CASE_WRITE_REDACTED_MESSAGE
from core.services.client.main import CLIENT_WRITE_REVOKED_MESSAGE
from core.services.events.crud import create_event
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db


@pytest.fixture
def other_person(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Other-Person-01",
        created_by=staff_user,
    )


def _traces():
    return (
        Event.objects.count(),
        EventHistory.objects.count(),
        Activity.objects.count(),
        AuditLog.objects.count(),
    )


def _create(facility, user, document_type, client, case):
    return create_event(
        facility=facility,
        user=user,
        document_type=document_type,
        occurred_at=timezone.now(),
        data_json={"dauer": 15},
        client=client,
        case=case,
    )


def _assert_refused(call, message):
    """Calls the service and checks message and missing traces; returns the recording."""
    before = _traces()
    with mitschnitt() as recording, pytest.raises(ValidationError) as excinfo:
        call()
    assert excinfo.value.messages == [message]
    assert _traces() == before
    return recording


class TestStaleCaseIsRefused:
    def test_case_moved_to_trash(self, facility, staff_user, doc_type_contact, client_identified, case_open):
        Case.objects.filter(pk=case_open.pk).update(is_deleted=True, deleted_at=timezone.now())

        recording = _assert_refused(
            lambda: _create(facility, staff_user, doc_type_contact, client_identified, case_open),
            CASE_WRITE_REDACTED_MESSAGE,
        )

        assert recording.sperrfolge() == ["core_client", "core_case"]

    def test_case_anonymised(self, facility, staff_user, doc_type_contact, client_identified, case_open):
        Case.objects.filter(pk=case_open.pk).update(anonymized_at=timezone.now())

        recording = _assert_refused(
            lambda: _create(facility, staff_user, doc_type_contact, client_identified, case_open),
            CASE_WRITE_REDACTED_MESSAGE,
        )

        assert recording.sperrfolge() == ["core_client", "core_case"]

    @pytest.mark.parametrize(
        "revocation",
        [{"is_deleted": True}, {"is_active": False}],
        ids=["person_in_trash", "person_deactivated"],
    )
    def test_person_revoked(self, facility, staff_user, doc_type_contact, client_identified, case_open, revocation):
        Client.objects.filter(pk=client_identified.pk).update(**revocation)

        recording = _assert_refused(
            lambda: _create(facility, staff_user, doc_type_contact, client_identified, case_open),
            CLIENT_WRITE_REVOKED_MESSAGE,
        )

        assert recording.sperrfolge() == ["core_client"]

    def test_case_attached_to_another_person(
        self, facility, staff_user, doc_type_contact, client_identified, case_open, other_person
    ):
        Case.objects.filter(pk=case_open.pk).update(client=other_person)

        recording = _assert_refused(
            lambda: _create(facility, staff_user, doc_type_contact, client_identified, case_open),
            CASE_WRITE_GONE_MESSAGE,
        )

        assert recording.sperrfolge() == ["core_client", "core_case"]
        person_locks = recording.sperren("core_client")
        assert person_locks
        assert all(access.traegt(id=[client_identified.pk]) for access in person_locks), person_locks
        assert Case.objects.get(pk=case_open.pk).client_id == other_person.pk


class TestLiveCaseIsLinked:
    def test_case_is_locked_after_the_person_and_before_the_event_is_written(
        self, facility, staff_user, doc_type_contact, client_identified, case_open
    ):
        event_written_when_case_locked = []
        with mitschnitt() as recording:
            recording.nach_sperre(
                "core_case",
                lambda: event_written_when_case_locked.append(Event.objects.filter(case_id=case_open.pk).exists()),
            )
            event = _create(facility, staff_user, doc_type_contact, client_identified, case_open)

        assert Event.objects.get(pk=event.pk).case_id == case_open.pk
        assert EventHistory.objects.filter(event=event).count() == 1
        order = recording.sperrfolge()
        assert order.index("core_client") < order.index("core_case"), order
        assert event_written_when_case_locked == [False]
        assert recording.offene_schranken() == []

    def test_without_case_no_case_row_is_locked(self, facility, staff_user, doc_type_contact, client_identified):
        with mitschnitt() as recording:
            event = _create(facility, staff_user, doc_type_contact, client_identified, None)

        assert Event.objects.get(pk=event.pk).case_id is None
        assert "core_case" not in recording.sperrfolge()
