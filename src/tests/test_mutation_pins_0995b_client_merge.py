"""Pins for the answers of the merge services (``core.services.client.merge``).

Source: mutation delta of the release-0.99.5 checkpoint, 12 mutants.
Groups:

* ``_lock_persons_then_merge_request`` — the refusal after repeatedly
  changing persons loses or alters its error code ``version_conflict``.
* ``approve_client_merge`` — the refusal for a person that the database no
  longer assigns to the request's facility loses or alters the same code.
  Callers branch on that code like on every other version conflict; the
  existing pins for both paths check the message only.
* ``_validate_merge`` — the refusal for persons of two facilities loses its
  message. The public entries compare the facilities before they lock, so
  only a direct call reaches this line; it is the boundary the unscoped lock
  of the approval relies on.
* ``create_or_get_client_merge_request`` and ``merge_clients`` — the actor's
  ``facility_id`` is read without a default. An actor without that attribute
  then fails with ``AttributeError`` instead of the facility refusal.

Changing persons are staged deterministically right after the observed person
lock (``tests._row_lock_helpers``), independent of the helper that takes it.
"""

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError

from core.models import AuditLog, Client
from core.models.workitem import DeletionRequest
from core.services.client.main import CLIENT_OTHER_FACILITY_MESSAGE
from core.services.client.merge import (
    _validate_merge,
    approve_client_merge,
    create_or_get_client_merge_request,
    merge_clients,
    request_client_merge,
)
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

CLIENT_TABLE = Client._meta.db_table
CHANGED_MESSAGE = "Der Datensatz wurde zwischenzeitlich bearbeitet. Bitte laden Sie die Seite neu."
SAME_FACILITY_MESSAGE = "Beide Personen müssen zur selben Einrichtung gehören."

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


def _state(*persons):
    return list(
        Client.objects.filter(pk__in=[p.pk for p in persons])
        .order_by("pk")
        .values("pk", "facility_id", "is_deleted", "merged_into_id")
    )


@pytest.fixture
def persons(facility, staff_user):
    """Source, target and a third person of the same facility."""
    return (
        _person(facility, staff_user, "Merge-Source"),
        _person(facility, staff_user, "Merge-Target"),
        _person(facility, staff_user, "Merge-Other"),
    )


@pytest.fixture
def merge_request(persons, staff_user):
    source, target, _other = persons
    return request_client_merge(source, target, staff_user, "Duplicate record")


def _assert_version_conflict(excinfo):
    assert excinfo.value.code == "version_conflict"
    assert excinfo.value.messages == [CHANGED_MESSAGE]


class TestLockPersonsThenMergeRequest:
    """``_lock_persons_then_merge_request``: persons that keep changing end in a version conflict."""

    def test_a_target_that_keeps_changing_is_refused_as_a_version_conflict(self, merge_request, persons, lead_user):
        """x__lock_persons_then_merge_request__mutmut_25 (``code=None``),
        x__lock_persons_then_merge_request__mutmut_27 (code dropped),
        x__lock_persons_then_merge_request__mutmut_32 (``"XXversion_conflictXX"``)
        and x__lock_persons_then_merge_request__mutmut_33 (``"VERSION_CONFLICT"``):
        after the repeated attempts the refusal no longer carries the code.

        Right after every person lock the request is pointed at another
        target, so every attempt finds other persons on the locked request."""
        source, target, other = persons
        before = _state(*persons)
        retargets = []

        with mitschnitt() as log:

            def _retarget_after_the_person_lock():
                DeletionRequest.objects.filter(pk=merge_request.pk).update(merge_into_id=other.pk)
                retargets.append(other.pk)
                log.nach_sperre(CLIENT_TABLE, _retarget_after_the_person_lock)

            log.nach_sperre(CLIENT_TABLE, _retarget_after_the_person_lock)
            with pytest.raises(ValidationError) as excinfo:
                approve_client_merge(merge_request, lead_user)

        _assert_version_conflict(excinfo)
        assert len(retargets) >= 2, "the approval did not start over on the current persons"
        merge_request.refresh_from_db()
        assert merge_request.status == DeletionRequest.Status.PENDING
        assert merge_request.merge_into_id == target.pk
        assert _state(*persons) == before


class TestApproveClientMergePersonOutsideTheFacility:
    """``approve_client_merge``: a person the database moved out of the facility ends in a version conflict."""

    @pytest.mark.parametrize("moved", ["source", "target"])
    def test_moved_person_is_refused_as_a_version_conflict(
        self, merge_request, persons, second_facility, lead_user, moved
    ):
        """x_approve_client_merge__mutmut_52 (``code=None``),
        x_approve_client_merge__mutmut_54 (code dropped),
        x_approve_client_merge__mutmut_59 (``"XXversion_conflictXX"``) and
        x_approve_client_merge__mutmut_60 (``"VERSION_CONFLICT"``): the
        refusal after the facility-scoped person lookup no longer carries the
        code."""
        source, target, _other = persons
        person = source if moved == "source" else target
        Client.objects.filter(pk=person.pk).update(facility=second_facility)
        before = _state(*persons)
        audit_rows = AuditLog.objects.count()

        with pytest.raises(ValidationError) as excinfo:
            approve_client_merge(merge_request, lead_user)

        _assert_version_conflict(excinfo)
        merge_request.refresh_from_db()
        assert merge_request.status == DeletionRequest.Status.PENDING
        assert _state(*persons) == before
        assert AuditLog.objects.count() == audit_rows


class TestValidateMergeFacilityBoundary:
    """``_validate_merge``: persons of two facilities are refused with the domain message."""

    def test_persons_of_two_facilities_are_refused_with_the_message(
        self, facility, second_facility, persons, staff_user
    ):
        """x__validate_merge__mutmut_8 (``ValidationError(None)``) and
        x__validate_merge__mutmut_9 (``ValidationError(_(None))``): the refusal
        loses its message."""
        source, _target, _other = persons
        foreign = _person(second_facility, None, "Merge-Foreign")

        with pytest.raises(ValidationError) as excinfo:
            _validate_merge(source, foreign)
        assert excinfo.value.messages == [SAME_FACILITY_MESSAGE]

        with pytest.raises(ValidationError) as excinfo:
            _validate_merge(foreign, source)
        assert excinfo.value.messages == [SAME_FACILITY_MESSAGE]


class TestCreateOrGetClientMergeRequestActorWithoutFacility:
    """``create_or_get_client_merge_request``: an actor without ``facility_id`` files nothing and locks nothing."""

    @pytest.mark.parametrize("actor", ACTORS_WITHOUT_FACILITY)
    def test_is_refused_with_the_facility_message(self, persons, actor):
        """x_create_or_get_client_merge_request__mutmut_6: ``getattr(user,
        "facility_id")`` without default raises ``AttributeError`` instead of
        the refusal."""
        source, target, _other = persons

        with mitschnitt() as log, pytest.raises(ValidationError) as excinfo:
            create_or_get_client_merge_request(source, target, actor, "Filed without an actor")

        assert excinfo.value.messages == [str(CLIENT_OTHER_FACILITY_MESSAGE)]
        assert log.sperrfolge() == []
        assert not DeletionRequest.objects.filter(target_id=source.pk).exists()


class TestMergeClientsActorWithoutFacility:
    """``merge_clients``: a lead object without ``facility_id`` merges nothing and locks nothing."""

    def test_is_refused_with_the_facility_message(self, persons):
        """x_merge_clients__mutmut_12: ``getattr(user, "facility_id")`` without
        default raises ``AttributeError`` instead of the refusal. No person and
        an anonymous user already fail at the role check, so the actor is an
        object that passes the role check but carries no facility."""
        source, target, _other = persons
        before = _state(*persons)

        with mitschnitt() as log, pytest.raises(ValidationError) as excinfo:
            merge_clients(source, target, SimpleNamespace(is_lead_or_admin=True))

        assert excinfo.value.messages == [str(CLIENT_OTHER_FACILITY_MESSAGE)]
        assert log.sperrfolge() == []
        assert _state(*persons) == before
