"""Public contact-stage downgrade pins.

Source: mutation delta of the release-0.99.7 checkpoint, 52 candidate mutants.
Groups:

* ``_lock_person`` (16): facility scope, availability and protection messages.
* ``request_stage_downgrade`` (14): authorization, pending-slot identity,
  reason and attributable audit evidence.
* ``review_stage_downgrade`` (22): facility scope, request identity, four eyes,
  availability, preserved dynamic data, decision timestamp and attributable audit evidence.

Of these candidates, 49 are pinned and three only change redundant locking.
The redundant-locking candidates are not pinned. These tests exercise public services without ``rls_test_role``.
"""

from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.utils import timezone
from django.utils.translation import gettext as _

from core.models import AuditLog, Client, DeletionRequest, DocumentType, DocumentTypeField, FieldTemplate, LegalHold
from core.services.client import stage_downgrade

pytestmark = pytest.mark.django_db


def _request(person, actor, **overrides):
    """Create a pending request with independently selectable identity fields."""
    values = {
        "facility": person.facility,
        "target_type": "Client",
        "target_id": person.pk,
        "stage_downgrade": True,
        "requested_by": actor,
        "reason": "Minimize stored information",
    }
    values.update(overrides)
    return DeletionRequest.objects.create(**values)


def _assert_unchanged(person, request=None):
    """Failed decisions leave the person, pending slot and audit trail intact."""
    person.refresh_from_db()
    assert person.contact_stage == Client.ContactStage.QUALIFIED
    if request is not None:
        request.refresh_from_db()
        assert request.status == DeletionRequest.Status.PENDING
        assert request.reviewed_by_id is None
        assert request.reviewed_at is None
    assert not AuditLog.objects.filter(target_id=person.pk).exists()


@pytest.mark.parametrize("operation", ["request", "review"])
def test_foreign_facility_is_rejected_before_target_access(
    client_qualified, lead_user, admin_user, second_facility, operation
):
    """Reject foreign actors before reading or locking either target table.

    Kills x__lock_person__mutmut_1, x__lock_person__mutmut_4,
    x__lock_person__mutmut_5, x_review_stage_downgrade__mutmut_1,
    x_review_stage_downgrade__mutmut_4 and x_review_stage_downgrade__mutmut_5.
    """
    request = _request(client_qualified, lead_user)
    actor = lead_user if operation == "request" else admin_user
    actor.facility = second_facility
    target_access = []

    def record(execute, sql, params, many, context):
        if 'FROM "core_client"' in sql or 'FROM "core_deletionrequest"' in sql:
            target_access.append(sql)
        return execute(sql, params, many, context)

    expected = (
        "Die Person gehört zu einer anderen Einrichtung."
        if operation == "request"
        else "Antrag gehört zu einer anderen Einrichtung."
    )
    with connection.execute_wrapper(record), pytest.raises(ValidationError) as error:
        if operation == "request":
            stage_downgrade.request_stage_downgrade(client_qualified, actor, "Minimize")
        else:
            stage_downgrade.review_stage_downgrade(request, actor, approve=True)
    assert error.value.messages == [_(expected)]
    assert target_access == []
    _assert_unchanged(client_qualified, request)


@pytest.fixture
def foreign_person(second_facility):
    """A persisted qualified person outside the actors' facility."""
    return Client.objects.create(
        facility=second_facility, contact_stage=Client.ContactStage.QUALIFIED, pseudonym="FOREIGN-01"
    )


def test_review_rejects_cross_facility_target(foreign_person, lead_user, admin_user):
    """Reject a local request targeting a foreign person without writing a decision.

    Kills x_review_stage_downgrade__mutmut_23 and x_review_stage_downgrade__mutmut_25.
    """
    request = _request(foreign_person, lead_user, facility=lead_user.facility)
    with pytest.raises(ValidationError) as error:
        stage_downgrade.review_stage_downgrade(request, admin_user, approve=False)
    assert error.value.messages == [_("Die Person ist nicht mehr verfügbar.")]
    _assert_unchanged(foreign_person, request)
    assert not DeletionRequest.objects.exclude(pk=request.pk).exists()


def test_request_rechecks_persisted_person_facility(foreign_person, lead_user, second_facility):
    """Reject a foreign row even when its in-memory instance claims the actor's facility.

    Kills x__lock_person__mutmut_12 and x__lock_person__mutmut_14.
    """
    foreign_person.facility = lead_user.facility
    with pytest.raises(ValidationError) as error:
        stage_downgrade.request_stage_downgrade(foreign_person, lead_user, "Minimize")
    assert error.value.messages == [_("Diese Person kann nicht herabgestuft werden.")]
    assert not DeletionRequest.objects.exists()
    _assert_unchanged(foreign_person)
    assert foreign_person.facility_id == second_facility.pk


@pytest.mark.parametrize("state", ["inactive", "merged", "missing"])
def test_unavailable_person_has_domain_error(client_qualified, client_identified, lead_user, state):
    """Unavailable stale records fail without creating a request or audit entry.

    Kills x__lock_person__mutmut_15, x__lock_person__mutmut_16,
    x__lock_person__mutmut_17, x__lock_person__mutmut_21 and x__lock_person__mutmut_22.
    """
    if state == "missing":
        Client.objects.filter(pk=client_qualified.pk).delete()
    else:
        changes = {"is_active": False} if state == "inactive" else {"merged_into": client_identified}
        Client.objects.filter(pk=client_qualified.pk).update(**changes)
    with pytest.raises(ValidationError) as error:
        stage_downgrade.request_stage_downgrade(client_qualified, lead_user, "Minimize")
    assert error.value.messages == [_("Diese Person kann nicht herabgestuft werden.")]
    assert not DeletionRequest.objects.exists()
    assert not AuditLog.objects.filter(target_id=client_qualified.pk).exists()


@pytest.mark.parametrize("protection", ["pseudonym", "hold"])
def test_stage_and_hold_blockers_explain_rejection(client_qualified, lead_user, protection):
    """Explain an already minimized stage or an active legal hold to the requester.

    Kills x__lock_person__mutmut_32, x__lock_person__mutmut_33 and x__lock_person__mutmut_38.
    """
    if protection == "pseudonym":
        Client.objects.filter(pk=client_qualified.pk).update(contact_stage=Client.ContactStage.IDENTIFIED)
        expected = "Die Person hat bereits die Kontaktstufe „Nur Pseudonym“."
    else:
        LegalHold.objects.create(
            facility=client_qualified.facility,
            target_type="Client",
            target_id=client_qualified.pk,
            reason="Preserve evidence",
            created_by=lead_user,
        )
        expected = "Die Person steht unter einem aktiven Legal Hold."
    with pytest.raises(ValidationError) as error:
        stage_downgrade.request_stage_downgrade(client_qualified, lead_user, "Minimize")
    assert error.value.messages == [_(expected)]
    assert not DeletionRequest.objects.exists()
    assert not AuditLog.objects.filter(target_id=client_qualified.pk).exists()


@pytest.mark.parametrize("actor_kind", ["staff", "inactive_lead"])
def test_request_requires_active_lead_or_admin(client_qualified, lead_user, staff_user, actor_kind):
    """Reject both an active staff member and an inactive lead with the role message.

    Kills x_request_stage_downgrade__mutmut_1, x_request_stage_downgrade__mutmut_4
    and x_request_stage_downgrade__mutmut_5.
    """
    actor = staff_user if actor_kind == "staff" else lead_user
    if actor_kind == "inactive_lead":
        actor.is_active = False
    with pytest.raises(ValidationError) as error:
        stage_downgrade.request_stage_downgrade(client_qualified, actor, "Minimize")
    assert error.value.messages == [_("Nur Leitung und Anwendungsbetreuung dürfen eine Herabstufung beantragen.")]
    assert not DeletionRequest.objects.exists()
    _assert_unchanged(client_qualified)


@pytest.mark.parametrize("reason", ["", "   "])
def test_empty_reason_has_required_message(client_qualified, lead_user, reason):
    """An empty or whitespace-only reason is rejected with its translated message.

    Kills x_request_stage_downgrade__mutmut_10 and x_request_stage_downgrade__mutmut_11.
    """
    with pytest.raises(ValidationError) as error:
        stage_downgrade.request_stage_downgrade(client_qualified, lead_user, reason)
    assert error.value.messages == [_("Begründung ist erforderlich.")]
    assert not DeletionRequest.objects.exists()
    _assert_unchanged(client_qualified)


@pytest.mark.parametrize("decoy", ["target_type", "target_id", "status"])
def test_pending_slot_excludes_wrong_identity(client_qualified, client_identified, lead_user, decoy):
    """A sole wrong-type, wrong-person or closed request cannot occupy the pending slot.

    Kills x_request_stage_downgrade__mutmut_24, x_request_stage_downgrade__mutmut_25
    and x_request_stage_downgrade__mutmut_26.
    """
    overrides = {
        "target_type": {"target_type": "Event", "stage_downgrade": False},
        "target_id": {"target_id": client_identified.pk},
        "status": {"status": DeletionRequest.Status.REJECTED},
    }[decoy]
    wrong = _request(client_qualified, lead_user, **overrides)
    result = stage_downgrade.request_stage_downgrade(client_qualified, lead_user, "Minimize")
    assert result.pk != wrong.pk
    assert result.target_type == "Client"
    assert result.target_id == client_qualified.pk
    assert result.status == DeletionRequest.Status.PENDING
    assert result.stage_downgrade
    assert stage_downgrade.request_stage_downgrade(client_qualified, lead_user, "Repeated").pk == result.pk


def test_other_pending_request_has_conflict_message(client_qualified, lead_user):
    """A normal deletion request blocks a downgrade with a useful conflict message.

    Kills x_request_stage_downgrade__mutmut_33.
    """
    request = _request(client_qualified, lead_user, stage_downgrade=False)
    with pytest.raises(ValidationError) as error:
        stage_downgrade.request_stage_downgrade(client_qualified, lead_user, "Minimize")
    assert error.value.messages == [_("Für diese Person liegt bereits ein anderer Antrag vor.")]
    _assert_unchanged(client_qualified, request)


def test_request_persists_reason_and_attributable_audit(client_qualified, lead_user):
    """Persist the trimmed reason and link request audit evidence to its actor and UUID.

    Kills x_request_stage_downgrade__mutmut_50, x_request_stage_downgrade__mutmut_56,
    x_request_stage_downgrade__mutmut_58, x_request_stage_downgrade__mutmut_62
    and x_request_stage_downgrade__mutmut_63.
    """
    request = stage_downgrade.request_stage_downgrade(client_qualified, lead_user, "  Minimize stored information  ")
    request.refresh_from_db()
    assert request.reason == "Minimize stored information"
    entry = AuditLog.objects.get(target_id=client_qualified.pk, action=AuditLog.Action.CLIENT_STAGE_DOWNGRADE_REQUESTED)
    assert entry.user_id == lead_user.pk
    assert entry.detail["deletion_request"] == str(request.pk)


def test_review_requires_confirmation_right(client_qualified, lead_user, staff_user):
    """A reviewer without confirmation rights gets the translated permission message.

    Kills x_review_stage_downgrade__mutmut_12.
    """
    request = _request(client_qualified, lead_user)
    with pytest.raises(ValidationError) as error:
        stage_downgrade.review_stage_downgrade(request, staff_user, approve=True)
    assert error.value.messages == [_("Für die Freigabe ist das Recht „Löschbestätigung“ erforderlich.")]
    _assert_unchanged(client_qualified, request)


def test_review_rejects_normal_deletion_request(client_qualified, lead_user, admin_user):
    """A normal client deletion request cannot be approved as a stage downgrade.

    Kills x_review_stage_downgrade__mutmut_30, x_review_stage_downgrade__mutmut_37
    and x_review_stage_downgrade__mutmut_38.
    """
    request = _request(client_qualified, lead_user, stage_downgrade=False)
    with pytest.raises(ValidationError) as error:
        stage_downgrade.review_stage_downgrade(request, admin_user, approve=True)
    assert error.value.messages == [_("Dies ist kein Herabstufungsantrag.")]
    _assert_unchanged(client_qualified, request)


def test_review_of_closed_request_has_finality_message(client_qualified, lead_user, admin_user):
    """A closed request is refused with the prior-processing message.

    Kills x_review_stage_downgrade__mutmut_43.
    """
    request = _request(client_qualified, lead_user, status=DeletionRequest.Status.REJECTED)
    with pytest.raises(ValidationError) as error:
        stage_downgrade.review_stage_downgrade(request, admin_user, approve=True)
    assert error.value.messages == [_("Der Antrag wurde bereits bearbeitet.")]
    request.refresh_from_db()
    assert request.status == DeletionRequest.Status.REJECTED
    _assert_unchanged(client_qualified)


@pytest.mark.parametrize("requester", ["self", "missing"])
def test_review_needs_distinct_existing_requester(client_qualified, lead_user, admin_user, requester):
    """Reject self-review and an absent requester with a domain four-eyes error.

    Kills x_review_stage_downgrade__mutmut_48, x_review_stage_downgrade__mutmut_51
    and x_review_stage_downgrade__mutmut_52.
    """
    request = _request(client_qualified, admin_user if requester == "self" else None)
    with pytest.raises(ValidationError) as error:
        stage_downgrade.review_stage_downgrade(request, admin_user, approve=True)
    assert error.value.messages == [_("Die Freigabe erfordert eine andere Person als die antragstellende.")]
    _assert_unchanged(client_qualified, request)


@pytest.mark.parametrize("target_state", ["missing", "changed"])
def test_review_rechecks_locked_target_identity(
    client_qualified, client_identified, lead_user, admin_user, monkeypatch, target_state
):
    """Reject a missing target or a request retargeted between the two database reads.

    Kills x_review_stage_downgrade__mutmut_56, x_review_stage_downgrade__mutmut_59
    and x_review_stage_downgrade__mutmut_60.
    """
    request = _request(
        client_qualified, lead_user, target_id=uuid4() if target_state == "missing" else client_qualified.pk
    )
    if target_state == "changed":
        lock = stage_downgrade.lock_clients

        def retarget_after_lock(*args, **kwargs):
            people = lock(*args, **kwargs)
            DeletionRequest.objects.filter(pk=request.pk).update(target_id=client_identified.pk)
            return people

        monkeypatch.setattr(stage_downgrade, "lock_clients", retarget_after_lock)
    with pytest.raises(ValidationError) as error:
        stage_downgrade.review_stage_downgrade(request, admin_user, approve=True)
    assert error.value.messages == [_("Die Person ist nicht mehr verfügbar.")]
    _assert_unchanged(client_qualified, request)
    client_identified.refresh_from_db()
    assert client_identified.contact_stage == Client.ContactStage.IDENTIFIED


@pytest.mark.parametrize("approve", [True, False])
def test_review_persists_timestamp_and_attributable_audit(client_qualified, lead_user, admin_user, approve):
    """Both decisions persist their timestamp and link audit evidence to reviewer and UUID.

    Kills x_review_stage_downgrade__mutmut_77, x_review_stage_downgrade__mutmut_86,
    x_review_stage_downgrade__mutmut_88, x_review_stage_downgrade__mutmut_92
    and x_review_stage_downgrade__mutmut_93.
    """
    request = _request(client_qualified, lead_user)
    before = timezone.now()
    stage_downgrade.review_stage_downgrade(request, admin_user, approve=approve)
    after = timezone.now()
    request.refresh_from_db()
    assert request.reviewed_at is not None
    assert before <= request.reviewed_at <= after
    assert request.reviewed_by_id == admin_user.pk
    assert request.status == (DeletionRequest.Status.APPROVED if approve else DeletionRequest.Status.REJECTED)
    client_qualified.refresh_from_db()
    assert client_qualified.contact_stage == (
        Client.ContactStage.IDENTIFIED if approve else Client.ContactStage.QUALIFIED
    )
    action = AuditLog.Action.CLIENT_STAGE_DOWNGRADED if approve else AuditLog.Action.CLIENT_STAGE_DOWNGRADE_REJECTED
    entry = AuditLog.objects.get(target_id=client_qualified.pk, action=action)
    assert entry.user_id == admin_user.pk
    assert entry.detail["deletion_request"] == str(request.pk)


def test_approval_preserves_stored_dynamic_data(client_qualified, lead_user, admin_user):
    """Approval changes the contact stage without re-encrypting stored dynamic data.

    Kills x_review_stage_downgrade__mutmut_70.
    """
    document_type = DocumentType.objects.create(facility=client_qualified.facility, name="Profile")
    template = FieldTemplate.objects.create(
        facility=client_qualified.facility,
        name="Secret",
        field_type=FieldTemplate.FieldType.TEXT,
        is_encrypted=True,
    )
    DocumentTypeField.objects.create(document_type=document_type, field_template=template, sort_order=0)
    stored_data = {template.slug: "plain value"}
    # Model a value stored before its field template was marked for encryption.
    Client.objects.filter(pk=client_qualified.pk).update(document_type=document_type, data_json=stored_data)
    client_qualified.refresh_from_db()
    request = stage_downgrade.request_stage_downgrade(client_qualified, lead_user, "Minimize")
    stage_downgrade.review_stage_downgrade(request, admin_user, approve=True)
    client_qualified.refresh_from_db()
    assert client_qualified.contact_stage == Client.ContactStage.IDENTIFIED
    assert client_qualified.data_json == stored_data
