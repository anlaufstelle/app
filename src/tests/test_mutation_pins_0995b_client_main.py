"""Pins for the facility boundary of the person services (``core.services.client.main``).

Source: mutation delta of the release-0.99.5 checkpoint, 8 mutants.
Groups:

* ``_lock_client_then_deletion_request`` — the person lock of a deletion
  approval loses its facility scope (``facility=None`` or the argument
  dropped). The result stays the same; only the locked rows differ.
* ``create_client``, ``update_client``, ``create_or_get_client_deletion_request``,
  ``restore_client`` and ``anonymize_client_now`` — the actor check reads
  ``facility_id`` without a default. An actor without that attribute (no
  person, an anonymous user, a bare object) then fails with
  ``AttributeError`` instead of the domain refusal.
* ``update_client`` — the "not found" answer for a person that the database
  already assigns to another facility loses its message.

Which person rows a statement locks is read from the executed SQL
(``tests._row_lock_helpers``: PostgreSQL plans the captured statement; what
counts is the locked table, the lock strength and the filter values per
column, not alias, quoting, projection, order or literal form).
"""

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError

from core.models import AuditLog, Client
from core.models.workitem import DeletionRequest
from core.services.client.main import (
    CLIENT_OTHER_FACILITY_MESSAGE,
    anonymize_client_now,
    approve_client_deletion,
    create_client,
    create_or_get_client_deletion_request,
    request_client_deletion,
    restore_client,
    update_client,
)
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

CLIENT_TABLE = Client._meta.db_table

#: Actors without a ``facility_id`` attribute: no person, an anonymous user
#: and a bare object. All of them must be refused like a person without a
#: facility.
ACTORS_WITHOUT_FACILITY = [
    pytest.param(None, id="no-actor"),
    pytest.param(AnonymousUser(), id="anonymous-user"),
    pytest.param(SimpleNamespace(), id="bare-object"),
]


def _person(facility, creator, pseudonym):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym=pseudonym,
        created_by=creator,
    )


def _snapshot(client):
    return Client.objects.filter(pk=client.pk).values().get()


@pytest.fixture
def neighbours(facility, second_facility, staff_user):
    """One more person in the request's facility and one in another facility."""
    return (
        _person(facility, staff_user, "Neighbour-Same-Facility"),
        _person(second_facility, None, "Neighbour-Other-Facility"),
    )


@pytest.fixture
def trashed_person(client_identified, staff_user):
    client_identified.soft_delete(user=staff_user)
    client_identified.refresh_from_db()
    return client_identified


class TestLockClientThenDeletionRequest:
    """``_lock_client_then_deletion_request``: the person lock stays inside the request's facility."""

    def test_every_person_lock_of_an_approval_is_bound_to_the_request_facility(
        self, facility, client_identified, neighbours, staff_user, lead_user
    ):
        """x__lock_client_then_deletion_request__mutmut_12 (``facility=None``) and
        x__lock_client_then_deletion_request__mutmut_14 (argument dropped): the
        person lock taken before the request lock no longer names the facility."""
        dr = request_client_deletion(client_identified, staff_user, "Duplicate record")

        with mitschnitt() as log:
            approve_client_deletion(dr, lead_user)

        locks = log.sperren(CLIENT_TABLE)
        assert locks, "the approval locked no person row"
        assert [lock.traegt(id=[client_identified.pk], facility_id=[facility.pk]) for lock in locks] == [True] * len(
            locks
        ), locks
        client_identified.refresh_from_db()
        assert client_identified.is_deleted is True
        assert all(not person.is_deleted for person in Client.objects.filter(pk__in=[n.pk for n in neighbours]))

    def test_a_person_moved_to_another_facility_is_not_locked_by_the_approval(
        self, facility, second_facility, client_identified, neighbours, staff_user, lead_user
    ):
        """x__lock_client_then_deletion_request__mutmut_12 and
        x__lock_client_then_deletion_request__mutmut_14: the database assigns
        the target person to another facility after the request was filed. The
        approval ends with ``DoesNotExist`` either way; without the facility
        scope it locks the foreign person row on its way there."""
        dr = request_client_deletion(client_identified, staff_user, "Duplicate record")
        Client.objects.filter(pk=client_identified.pk).update(facility=second_facility)
        foreign_row = _snapshot(client_identified)

        with mitschnitt() as log, pytest.raises(Client.DoesNotExist):
            approve_client_deletion(dr, lead_user)

        locks = log.sperren(CLIENT_TABLE)
        assert [lock.traegt(facility_id=[facility.pk]) for lock in locks] == [True] * len(locks), locks
        assert _snapshot(client_identified) == foreign_row
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING


class TestCreateClientActorWithoutFacility:
    """``create_client``: an actor without ``facility_id`` is refused, nothing is written."""

    @pytest.mark.parametrize("actor", ACTORS_WITHOUT_FACILITY)
    def test_is_refused_with_the_facility_message(self, facility, actor):
        """x_create_client__mutmut_6: ``getattr(user, "facility_id")`` without
        default raises ``AttributeError`` instead of the refusal."""
        audit_rows = AuditLog.objects.count()

        with mitschnitt() as log, pytest.raises(ValidationError) as excinfo:
            create_client(facility, actor, pseudonym="Actorless-New-Person")

        assert excinfo.value.messages == [str(CLIENT_OTHER_FACILITY_MESSAGE)]
        assert log.sperrfolge() == []
        assert not Client.objects.filter(pseudonym="Actorless-New-Person").exists()
        assert AuditLog.objects.count() == audit_rows


class TestUpdateClientActorWithoutFacility:
    """``update_client``: an actor without ``facility_id`` is refused before the person lock."""

    @pytest.mark.parametrize("actor", ACTORS_WITHOUT_FACILITY)
    def test_is_refused_with_the_facility_message(self, client_identified, actor):
        """x_update_client__mutmut_6: ``getattr(user, "facility_id")`` without
        default raises ``AttributeError`` instead of the refusal."""
        before = _snapshot(client_identified)

        with mitschnitt() as log, pytest.raises(ValidationError) as excinfo:
            update_client(client_identified, actor, notes="Written without an actor")

        assert excinfo.value.messages == [str(CLIENT_OTHER_FACILITY_MESSAGE)]
        assert log.sperrfolge() == []
        assert _snapshot(client_identified) == before


class TestUpdateClientMovedPerson:
    """``update_client``: a person the database assigns to another facility is answered as not found."""

    def test_not_found_answer_keeps_its_message(self, second_facility, client_identified, neighbours, staff_user):
        """x_update_client__mutmut_20: ``Client.DoesNotExist("Client matching
        query does not exist.")`` becomes ``Client.DoesNotExist(None)``; the
        existing fresh-state test only checks the exception type."""
        Client.objects.filter(pk=client_identified.pk).update(facility=second_facility)
        foreign_row = _snapshot(client_identified)

        with pytest.raises(Client.DoesNotExist) as excinfo:
            update_client(client_identified, staff_user, notes="Written across the boundary")

        assert excinfo.value.args == ("Client matching query does not exist.",)
        assert str(excinfo.value) == "Client matching query does not exist."
        assert _snapshot(client_identified) == foreign_row


class TestCreateOrGetClientDeletionRequestActorWithoutFacility:
    """``create_or_get_client_deletion_request``: an actor without ``facility_id`` files nothing."""

    @pytest.mark.parametrize("actor", ACTORS_WITHOUT_FACILITY)
    def test_is_refused_with_the_facility_message(self, client_identified, actor):
        """x_create_or_get_client_deletion_request__mutmut_6: ``getattr(user,
        "facility_id")`` without default raises ``AttributeError`` instead of
        the refusal."""
        with mitschnitt() as log, pytest.raises(ValidationError) as excinfo:
            create_or_get_client_deletion_request(client_identified, actor, "Filed without an actor")

        assert excinfo.value.messages == [str(CLIENT_OTHER_FACILITY_MESSAGE)]
        assert log.sperrfolge() == []
        assert not DeletionRequest.objects.filter(target_id=client_identified.pk).exists()


class TestRestoreClientActorWithoutFacility:
    """``restore_client``: an actor without ``facility_id`` leaves the person in the trash."""

    @pytest.mark.parametrize("actor", ACTORS_WITHOUT_FACILITY)
    def test_is_refused_with_the_facility_message(self, trashed_person, actor):
        """x_restore_client__mutmut_6: ``getattr(user, "facility_id")`` without
        default raises ``AttributeError`` instead of the refusal."""
        before = _snapshot(trashed_person)

        with mitschnitt() as log, pytest.raises(ValidationError) as excinfo:
            restore_client(trashed_person, actor)

        assert excinfo.value.messages == [str(CLIENT_OTHER_FACILITY_MESSAGE)]
        assert log.sperrfolge() == []
        assert _snapshot(trashed_person) == before
        assert before["is_deleted"] is True


class TestAnonymizeClientNowActorWithoutFacility:
    """``anonymize_client_now``: an actor without ``facility_id`` anonymises nothing."""

    @pytest.mark.parametrize("actor", ACTORS_WITHOUT_FACILITY)
    def test_is_refused_with_the_facility_message(self, trashed_person, actor):
        """x_anonymize_client_now__mutmut_6: ``getattr(user, "facility_id")``
        without default raises ``AttributeError`` instead of the refusal."""
        before = _snapshot(trashed_person)
        audit_rows = AuditLog.objects.count()

        with mitschnitt() as log, pytest.raises(ValidationError) as excinfo:
            anonymize_client_now(trashed_person, actor)

        assert excinfo.value.messages == [str(CLIENT_OTHER_FACILITY_MESSAGE)]
        assert log.sperrfolge() == []
        assert _snapshot(trashed_person) == before
        assert AuditLog.objects.count() == audit_rows
