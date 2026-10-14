"""Two distinct eligible reviewers must complete manual retention approval."""

import threading
from datetime import timedelta
from io import StringIO

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import AuditLog, Client, Event, RetentionProposal, User
from core.services.retention import (
    approve_proposal,
    bulk_approve_proposals,
    create_legal_hold,
    create_proposal,
    defer_proposal,
    dismiss_legal_hold,
    reactivate_deferred_proposals,
    reject_proposal,
)
from tests._retention_race_helpers import immediately_lockable, record_timeline, run_threads, session_thread

pytestmark = pytest.mark.django_db


@pytest.fixture
def proposal(facility, settings_obj, client_qualified, doc_type_contact):
    settings_obj.retention_qualified_days = 30
    settings_obj.save(update_fields=["retention_qualified_days"])
    event = Event.objects.create(
        facility=facility,
        client=client_qualified,
        document_type=doc_type_contact,
        occurred_at=timezone.now() - timedelta(days=45),
        data_json={"duration": 20},
    )
    return create_proposal(facility, "Event", event.pk, timezone.localdate() - timedelta(days=15), {}, "qualified")[0]


def decisions(proposal, category):
    return AuditLog.objects.filter(
        facility=proposal.facility, target_id=str(proposal.target_id), detail__category=category
    )


def snapshot(proposal):
    proposal.refresh_from_db()
    return (
        proposal.status,
        proposal.first_approved_by_id,
        proposal.first_approved_at,
        proposal.approved_by_id,
        proposal.approved_at,
    )


def test_first_approval_keeps_pending_and_event(proposal, lead_user):
    approve_proposal(proposal, lead_user)
    assert proposal.status == RetentionProposal.Status.PENDING
    assert proposal.first_approved_by == lead_user
    assert proposal.first_approved_at is not None
    assert proposal.approved_at is None
    assert decisions(proposal, "retention_proposal_first_approved").get().user == lead_user
    output = StringIO()
    call_command("enforce_retention", facility=proposal.facility.name, stdout=output)
    assert "failed=0" in output.getvalue()
    assert not Event.objects.get(pk=proposal.target_id).is_deleted


def test_second_person_finishes_and_command_deletes(proposal, lead_user, admin_user):
    approve_proposal(proposal, lead_user)
    assert proposal.status == RetentionProposal.Status.PENDING
    approve_proposal(proposal, admin_user)
    assert proposal.status == RetentionProposal.Status.APPROVED
    assert proposal.first_approved_by == lead_user
    assert proposal.approved_by == admin_user
    assert proposal.approved_at >= proposal.first_approved_at
    assert decisions(proposal, "retention_proposal_approved").get().user == admin_user
    call_command("enforce_retention", facility=proposal.facility.name, stdout=StringIO())
    assert Event.objects.get(pk=proposal.target_id).is_deleted


def test_same_person_cannot_repeat_in_service_or_bulk(proposal, lead_user):
    approve_proposal(proposal, lead_user)
    assert proposal.status == RetentionProposal.Status.PENDING
    before = snapshot(proposal)
    with pytest.raises(ValidationError):
        approve_proposal(proposal, lead_user)
    assert bulk_approve_proposals([proposal], lead_user) == (0, 1)
    assert snapshot(proposal) == before
    assert decisions(proposal, "retention_proposal_first_approved").count() == 1
    assert not decisions(proposal, "retention_proposal_approved").exists()


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("role", User.Role.STAFF),
        ("role", User.Role.ASSISTANT),
        ("is_active", False),
        ("can_confirm_deletion", False),
        ("facility", None),
    ],
)
def test_ineligible_actor_is_refused(proposal, lead_user, attribute, value):
    setattr(lead_user, attribute, value)
    lead_user.save(update_fields=[attribute])
    with pytest.raises(ValidationError):
        approve_proposal(proposal, lead_user)
    proposal.refresh_from_db()
    assert proposal.status == RetentionProposal.Status.PENDING
    assert not AuditLog.objects.filter(target_id=str(proposal.target_id)).exists()


def test_foreign_actor_is_refused(proposal, second_facility):
    reviewer = User.objects.create_user(
        username="foreign-retention-reviewer",
        facility=second_facility,
        role=User.Role.LEAD,
        can_confirm_deletion=True,
    )
    with pytest.raises(ValidationError):
        approve_proposal(proposal, reviewer)
    assert proposal.status == RetentionProposal.Status.PENDING


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("role", User.Role.STAFF),
        ("role", User.Role.ASSISTANT),
        ("is_active", False),
        ("can_confirm_deletion", False),
        ("facility", None),
    ],
)
def test_first_person_must_still_be_eligible(proposal, lead_user, admin_user, attribute, value):
    approve_proposal(proposal, lead_user)
    assert proposal.status == RetentionProposal.Status.PENDING
    setattr(lead_user, attribute, value)
    lead_user.save(update_fields=[attribute])
    before = snapshot(proposal)
    with pytest.raises(ValidationError):
        approve_proposal(proposal, admin_user)
    assert snapshot(proposal) == before


def test_missing_first_person_does_not_count(proposal, lead_user, admin_user):
    approve_proposal(proposal, lead_user)
    assert proposal.status == RetentionProposal.Status.PENDING
    RetentionProposal.objects.filter(pk=proposal.pk).update(first_approved_by=None)
    approve_proposal(proposal, admin_user)
    assert proposal.status == RetentionProposal.Status.PENDING
    assert proposal.first_approved_by == admin_user
    assert proposal.approved_by is None


@pytest.mark.parametrize("action", ["defer", "reject", "hold"])
def test_decisions_clear_first_approval_with_audit(proposal, lead_user, admin_user, action):
    approve_proposal(proposal, lead_user)
    assert proposal.status == RetentionProposal.Status.PENDING
    if action == "defer":
        defer_proposal(proposal, admin_user, days=1)
    elif action == "reject":
        reject_proposal(proposal, admin_user)
    else:
        create_legal_hold(proposal, admin_user, "Preservation duty")
    proposal.refresh_from_db()
    assert proposal.first_approved_by is None
    assert proposal.first_approved_at is None
    assert decisions(proposal, "retention_proposal_first_approval_cleared").get().user == admin_user
    if action == "reject":
        return
    if action == "defer":
        RetentionProposal.objects.filter(pk=proposal.pk).update(deferred_until=timezone.localdate())
        reactivate_deferred_proposals(proposal.facility)
    else:
        dismiss_legal_hold(proposal.facility.legal_holds.get(target_id=proposal.target_id), admin_user)
    approve_proposal(proposal, admin_user)
    assert proposal.status == RetentionProposal.Status.PENDING
    approve_proposal(proposal, lead_user)
    assert proposal.status == RetentionProposal.Status.APPROVED


def test_manual_deferral_override_starts_at_one(proposal, lead_user):
    defer_proposal(proposal, lead_user, days=30)
    approve_proposal(proposal, lead_user)
    assert proposal.status == RetentionProposal.Status.PENDING
    assert proposal.first_approved_by == lead_user


def test_direct_write_cannot_use_same_person_twice(proposal, lead_user):
    with pytest.raises(IntegrityError), transaction.atomic():
        RetentionProposal.objects.filter(pk=proposal.pk).update(
            first_approved_by=lead_user,
            approved_by=lead_user,
        )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("same_actor", [False, True])
def test_concurrent_approval_serializes_both_stages(proposal, lead_user, admin_user, same_actor):
    barrier = threading.Barrier(2, timeout=30)
    results, errors = {}, {}

    def work(reviewer):
        current = RetentionProposal.objects.get(pk=proposal.pk)
        barrier.wait()
        approve_proposal(current, reviewer)
        return current.status

    threads = [
        session_thread(name, lambda u=user: work(u), results, errors)
        for name, user in [("first", lead_user), ("second", lead_user if same_actor else admin_user)]
    ]
    run_threads(threads)
    assert not errors, errors
    proposal.refresh_from_db()
    assert decisions(proposal, "retention_proposal_first_approved").count() == 1
    if same_actor:
        assert sum(isinstance(result, ValidationError) for result in results.values()) == 1
        assert proposal.status == RetentionProposal.Status.PENDING
        assert not decisions(proposal, "retention_proposal_approved").exists()
    else:
        assert sorted(results.values()) == [RetentionProposal.Status.APPROVED, RetentionProposal.Status.PENDING]
        assert proposal.status == RetentionProposal.Status.APPROVED
        assert proposal.first_approved_by_id != proposal.approved_by_id
        assert decisions(proposal, "retention_proposal_approved").count() == 1


def test_previous_final_reviewer_can_start_again_after_hold(proposal, lead_user, admin_user):
    approve_proposal(proposal, lead_user)
    approve_proposal(proposal, admin_user)
    hold = create_legal_hold(proposal, lead_user, "Preserve pending deletion")
    dismiss_legal_hold(hold, lead_user)
    approve_proposal(proposal, admin_user)
    assert proposal.status == RetentionProposal.Status.PENDING
    assert proposal.first_approved_by == admin_user
    assert proposal.approved_by is None
    assert proposal.approved_at is None
    approve_proposal(proposal, lead_user)
    assert proposal.status == RetentionProposal.Status.APPROVED


@pytest.mark.django_db(transaction=True)
def test_first_approval_locks_targets_then_proposal_before_audit(proposal, lead_user):
    event = Event.objects.get(pk=proposal.target_id)
    observed = {}

    def on_audit():
        current = RetentionProposal.objects.get(pk=proposal.pk)
        observed["first"] = current.first_approved_by_id
        observed["status"] = current.status
        observed["free"] = immediately_lockable(
            [
                ("client", Client, event.client_id),
                ("event", Event, event.pk),
                ("proposal", RetentionProposal, proposal.pk),
            ]
        )

    timeline, _result = record_timeline(
        proposal.facility_id,
        {"proposal": proposal.pk},
        lambda: approve_proposal(proposal, lead_user),
        on_audit,
    )
    assert observed == {"first": lead_user.pk, "status": RetentionProposal.Status.PENDING, "free": []}
    events = timeline.compact()
    assert events.index("lock core_client") < events.index("lock core_event")
    assert events.index("lock core_event") < events.index("lock core_retentionproposal") < events.index("audit-chain")
