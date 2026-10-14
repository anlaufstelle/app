"""Approval authority must be current and locked through the decision."""

import threading
from io import StringIO

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import transaction

from core.models import Client, Event, RetentionProposal, User
from core.services.retention import approve_proposal, bulk_approve_proposals
from tests._retention_race_helpers import (
    immediately_lockable,
    park_until_the_other_session_waits,
    record_timeline,
    run_threads,
    session_thread,
    waits_for_a_lock,
)
from tests.test_retention_two_person_approval import decisions
from tests.test_retention_two_person_approval import proposal as proposal

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("stage", ["first", "second"])
@pytest.mark.parametrize("mode", ["single", "bulk"])
@pytest.mark.parametrize(
    "field,value",
    [("is_active", False), ("role", User.Role.STAFF), ("can_confirm_deletion", False), ("facility_id", None)],
)
def test_stale_actor_is_refused(proposal, lead_user, admin_user, stage, mode, field, value):
    if stage == "second":
        approve_proposal(proposal, lead_user)
    stale = User.objects.get(pk=admin_user.pk)
    User.objects.filter(pk=stale.pk).update(**{field: value})
    if mode == "single":
        with pytest.raises(ValidationError):
            approve_proposal(proposal, stale)
    else:
        assert bulk_approve_proposals([proposal], stale) == (0, 1)
    proposal.refresh_from_db()
    assert proposal.status == RetentionProposal.Status.PENDING
    assert not decisions(proposal, "retention_proposal_approved").exists()
    call_command("enforce_retention", facility=proposal.facility.name, stdout=StringIO())
    assert not Event.objects.get(pk=proposal.target_id).is_deleted


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("stage", ["first", "second"])
@pytest.mark.parametrize("revoked", ["actor", "first"])
def test_revocation_during_target_lock_wait(proposal, lead_user, admin_user, stage, revoked):
    if stage == "second" or revoked == "first":
        approve_proposal(proposal, lead_user)
    stale = User.objects.get(pk=admin_user.pk)
    event = Event.objects.get(pk=proposal.target_id)
    ready = threading.Event()
    session, results, errors = {}, {}, {}

    def revoke():
        with transaction.atomic():
            Client.objects.select_for_update().get(pk=event.client_id)
            park_until_the_other_session_waits(ready, session)
            assert waits_for_a_lock(session["pid"])
            User.objects.filter(pk=stale.pk if revoked == "actor" else lead_user.pk).update(is_active=False)

    def request():
        assert ready.wait(10)
        approve_proposal(RetentionProposal.objects.get(pk=proposal.pk), stale)

    run_threads(
        [
            session_thread("revoke", revoke, results, errors),
            session_thread("approve", request, results, errors, session=session),
        ]
    )
    assert not errors, errors
    assert isinstance(results["approve"], ValidationError)
    proposal.refresh_from_db()
    assert proposal.status == RetentionProposal.Status.PENDING
    assert not decisions(proposal, "retention_proposal_approved").exists()
    call_command("enforce_retention", facility=proposal.facility.name, stdout=StringIO())
    assert not Event.objects.get(pk=event.pk).is_deleted


@pytest.mark.django_db(transaction=True)
def test_both_reviewers_locked_before_decision_audit(proposal, lead_user, admin_user):
    approve_proposal(proposal, lead_user)
    observed = []
    record_timeline(
        proposal.facility_id,
        {"proposal": proposal.pk},
        lambda: approve_proposal(proposal, admin_user),
        lambda: observed.append(
            immediately_lockable(
                [
                    ("actor", User, admin_user.pk),
                    ("first", User, lead_user.pk),
                ]
            )
        ),
    )
    assert observed == [[]]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mode", ["service", "view"])
def test_bulk_locks_every_reviewer_before_first_audit(proposal, lead_user, admin_user, client, mode):
    from django.urls import reverse
    from django.utils import timezone

    from core.services.retention import create_proposal

    third = User.objects.create_user(
        username="bulk-first-reviewer",
        facility=proposal.facility,
        role=User.Role.LEAD,
        can_confirm_deletion=True,
    )
    event = Event.objects.get(pk=proposal.target_id)
    event.pk = None
    event.save()
    other = create_proposal(proposal.facility, "Event", event.pk, timezone.localdate(), {}, "qualified")[0]
    approve_proposal(proposal, lead_user)
    approve_proposal(other, third)
    client.force_login(admin_user)
    observed = []

    def action():
        if mode == "service":
            assert bulk_approve_proposals([proposal, other], admin_user) == (2, 0)
        else:
            assert (
                client.post(
                    reverse("core:retention_bulk_approve"), {"proposal_ids": [proposal.pk, other.pk]}
                ).status_code
                == 302
            )

    record_timeline(
        proposal.facility_id,
        {"proposal": proposal.pk, "other": other.pk},
        action,
        lambda: observed.append(
            immediately_lockable(
                [
                    ("actor", User, admin_user.pk),
                    ("first", User, lead_user.pk),
                    ("other_first", User, third.pk),
                ]
            )
        ),
    )
    assert observed == [[]]
    assert RetentionProposal.objects.filter(pk__in=[proposal.pk, other.pk], status="approved").count() == 2
