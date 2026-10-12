"""Pins for public merge approval and undo services.

Source: mutation delta of the release-0.99.7 checkpoint, 16 mutants.
Groups: facility-scoped locks (3), conditional source promotion (2), promotion
audit details (4), defensive reviewer messages (4), and an actor without a
facility (1). Redundant consistency reads and full-save validation (2) are
not pinned by query counts.
"""

from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.utils.translation import gettext as _

from core.models import AuditLog, Case, Client, DeletionRequest, Event
from core.services.client import merge
from core.services.client.main import CLIENT_OTHER_FACILITY_MESSAGE
from tests._row_lock_helpers import mitschnitt as capture_locks

pytestmark = pytest.mark.django_db


@pytest.fixture
def people(facility, staff_user):
    return tuple(
        Client.objects.create(facility=facility, created_by=staff_user, pseudonym=name)
        for name in ["Merge source", "Merge target"]
    )


def test_preliminary_person_lock_excludes_moved_person(people, staff_user, lead_user, second_facility):
    """Approval never locks a foreign person:
    x__lock_persons_then_merge_request__mutmut_14,
    x__lock_persons_then_merge_request__mutmut_16.
    """
    source, target = people
    request = merge.request_client_merge(source, target, staff_user, "Duplicate")
    Client.objects.filter(pk=target.pk).update(facility=second_facility)
    audits = AuditLog.objects.count()

    with capture_locks() as captured, pytest.raises(ValidationError) as error:
        merge.approve_client_merge(request, lead_user)

    assert error.value.code == "version_conflict"
    person_locks = captured.sperren(Client._meta.db_table)
    assert person_locks
    assert all(
        lock.sperrart in {"UPDATE", "NO KEY UPDATE"} and lock.traegt(facility_id=[source.facility_id])
        for lock in person_locks
    )
    request.refresh_from_db()
    source.refresh_from_db()
    target.refresh_from_db()
    assert request.status == DeletionRequest.Status.PENDING
    assert source.merged_into_id is None
    assert target.facility_id == second_facility.pk
    assert AuditLog.objects.count() == audits


def test_request_moved_after_person_lock_is_not_locked(people, staff_user, lead_user, second_facility):
    """A moved merge request is absent from the scoped lock:
    x__lock_persons_then_merge_request__mutmut_21.
    """
    source, target = people
    request = merge.request_client_merge(source, target, staff_user, "Duplicate")
    audits = AuditLog.objects.count()
    with capture_locks() as captured:
        captured.nach_sperre(
            Client._meta.db_table,
            lambda: DeletionRequest.objects.filter(pk=request.pk).update(facility=second_facility),
        )
        with pytest.raises(DeletionRequest.DoesNotExist):
            merge.approve_client_merge(request, lead_user)

    assert captured.offene_schranken() == []
    request_locks = captured.sperren(DeletionRequest._meta.db_table)
    assert request_locks
    assert all(
        lock.sperrart in {"UPDATE", "NO KEY UPDATE"} and lock.traegt(id=[request.pk], facility_id=[source.facility_id])
        for lock in request_locks
    )
    request.refresh_from_db()
    source.refresh_from_db()
    assert request.facility_id == source.facility_id
    assert request.status == DeletionRequest.Status.PENDING
    assert source.merged_into_id is None
    assert AuditLog.objects.count() == audits


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_reviewer_moved_after_request_lock_keeps_refusal_message(
    people, staff_user, lead_user, second_facility, decision
):
    """Both decision paths keep the defensive facility refusal before writes:
    x_approve_client_merge__mutmut_24, x_approve_client_merge__mutmut_25,
    x_reject_client_merge__mutmut_27, x_reject_client_merge__mutmut_28.
    """
    source, target = people
    request = merge.request_client_merge(source, target, staff_user, "Duplicate")
    audits = AuditLog.objects.count()
    with capture_locks() as captured:
        captured.nach_sperre(
            DeletionRequest._meta.db_table, lambda: setattr(lead_user, "facility_id", second_facility.pk)
        )
        with pytest.raises(ValidationError) as error:
            getattr(merge, f"{decision}_client_merge")(request, lead_user)

    assert captured.offene_schranken() == []
    assert error.value.messages == [_("Antrag gehört zu einer anderen Einrichtung.")]
    request.refresh_from_db()
    source.refresh_from_db()
    assert request.status == DeletionRequest.Status.PENDING
    assert request.reviewed_by_id is None
    assert source.merged_into_id is None
    assert not source.is_deleted
    assert AuditLog.objects.count() == audits


@pytest.mark.parametrize(
    ("target_stage", "returned_event"),
    [(Client.ContactStage.IDENTIFIED, True), (Client.ContactStage.QUALIFIED, False)],
    ids=["identified-target-with-event", "qualified-target-without-event"],
)
def test_undo_promotes_only_when_both_conditions_hold(
    people, lead_user, doc_type_contact, target_stage, returned_event
):
    """A qualified target and returned events are both needed for promotion:
    x_unmerge_client__mutmut_171, x_unmerge_client__mutmut_172.
    """
    source, target = people
    if returned_event:
        event = Event.objects.create(
            facility=source.facility,
            client=source,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
        )
    merge.merge_clients(source, target, lead_user)
    Client.objects.filter(pk=target.pk).update(contact_stage=target_stage)
    source.refresh_from_db()
    audits = AuditLog.objects.filter(action=AuditLog.Action.STAGE_CHANGE, target_id=str(source.pk)).count()

    merge.unmerge_client(source, lead_user)

    source.refresh_from_db()
    assert source.contact_stage == Client.ContactStage.IDENTIFIED
    assert source.merged_into_id is None
    assert not source.is_deleted
    assert AuditLog.objects.filter(action=AuditLog.Action.STAGE_CHANGE, target_id=str(source.pk)).count() == audits
    if returned_event:
        event.refresh_from_db()
        assert event.client_id == source.pk
    else:
        assert not source.events.exists()


def test_returned_case_event_promotion_audits_both_stages_and_actor(people, lead_user, doc_type_contact):
    """Undo records the source promotion with old stage, new stage and actor:
    x_unmerge_client__mutmut_175, x_unmerge_client__mutmut_183,
    x_unmerge_client__mutmut_184, x_unmerge_client__mutmut_185.
    """
    source, target = people
    case = Case.objects.create(facility=source.facility, client=source, title="Returning case")
    merge.merge_clients(source, target, lead_user)
    Client.objects.filter(pk=target.pk).update(contact_stage=Client.ContactStage.QUALIFIED)
    event = Event.objects.create(
        facility=source.facility,
        client=target,
        case=case,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
    )
    source.refresh_from_db()

    merge.unmerge_client(source, lead_user)

    source.refresh_from_db()
    event.refresh_from_db()
    case.refresh_from_db()
    assert source.contact_stage == Client.ContactStage.QUALIFIED
    assert event.client_id == source.pk
    assert case.client_id == source.pk
    audit = AuditLog.objects.get(action=AuditLog.Action.STAGE_CHANGE, target_id=str(source.pk))
    assert audit.detail["old_stage"] == Client.ContactStage.IDENTIFIED
    assert audit.detail["new_stage"] == Client.ContactStage.QUALIFIED
    assert audit.user_id == lead_user.pk


def test_actor_without_facility_is_refused_before_row_access(people):
    """An authorized actor without a facility gets the domain refusal without data queries:
    x_unmerge_client__mutmut_12.
    """
    source, _target = people
    with CaptureQueriesContext(connection) as captured, pytest.raises(ValidationError) as error:
        merge.unmerge_client(source, SimpleNamespace(is_lead_or_admin=True))

    assert error.value.messages == [str(CLIENT_OTHER_FACILITY_MESSAGE)]
    assert not any(
        query["sql"].lstrip().upper().startswith(("SELECT", "WITH", "INSERT", "UPDATE", "DELETE"))
        for query in captured.captured_queries
    )
