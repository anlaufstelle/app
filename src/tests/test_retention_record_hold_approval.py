"""Every record hold discards affected first approvals before writing its audit."""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from core.models import Case, Event, LegalHold, RetentionProposal
from core.services.retention import approve_proposal
from tests.test_retention_two_person_approval import decisions
from tests.test_retention_two_person_approval import proposal as proposal

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("target_type", ["Event", "Client", "Case"])
@pytest.mark.parametrize("release", ["dismiss", "expire"])
@pytest.mark.parametrize("stage", ["first", "final"])
def test_record_hold_restarts_both_approvals(proposal, lead_user, admin_user, client, target_type, release, stage):
    event = Event.objects.get(pk=proposal.target_id)
    case = Case.objects.create(
        facility=proposal.facility,
        client=event.client,
        title="Preservation case",
        created_by=lead_user,
        status=Case.Status.CLOSED,
        closed_at=timezone.now() - timedelta(days=45),
    )
    Event.objects.filter(pk=event.pk).update(case=case)
    target_id = {"Event": event.pk, "Client": event.client_id, "Case": case.pk}[target_type]
    approve_proposal(proposal, lead_user)
    if stage == "final":
        approve_proposal(proposal, admin_user)
    client.force_login(admin_user)
    prefix = target_type.lower()
    response = client.post(reverse(f"core:{prefix}_legal_hold", args=[target_id]), {"reason": "Preserve records"})
    assert response.status_code == 302
    proposal.refresh_from_db()
    assert proposal.first_approved_by_id is None
    assert proposal.first_approved_at is None
    assert decisions(proposal, "retention_proposal_first_approval_cleared").get().user_id == admin_user.pk
    call_command("enforce_retention", facility=proposal.facility.name, stdout=StringIO())
    assert not Event.objects.get(pk=event.pk).is_deleted
    hold = LegalHold.objects.get(facility=proposal.facility, target_type=target_type, target_id=target_id)
    if release == "dismiss":
        response = client.post(reverse(f"core:{prefix}_legal_hold_dismiss", args=[target_id, hold.pk]))
        assert response.status_code == 302
    else:
        LegalHold.objects.filter(pk=hold.pk).update(expires_at=timezone.localdate() - timedelta(days=1))
    approve_proposal(proposal, admin_user)
    assert proposal.status == RetentionProposal.Status.PENDING
    call_command("enforce_retention", facility=proposal.facility.name, stdout=StringIO())
    assert not Event.objects.get(pk=event.pk).is_deleted
    approve_proposal(proposal, lead_user)
    assert proposal.status == RetentionProposal.Status.APPROVED
    call_command("enforce_retention", facility=proposal.facility.name, stdout=StringIO())
    assert Event.objects.get(pk=event.pk).is_deleted


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("target_type", ["Event", "Client", "Case"])
def test_record_hold_locks_all_affected_proposals_before_clear_audit(
    proposal, lead_user, admin_user, client, target_type
):
    from core.models import Client
    from core.services.retention import create_proposal
    from tests._retention_race_helpers import immediately_lockable, record_timeline

    event = Event.objects.get(pk=proposal.target_id)
    case = Case.objects.create(
        facility=proposal.facility, client=event.client, title="Preservation scope", created_by=lead_user
    )
    Event.objects.filter(pk=event.pk).update(case=case)
    other = Event.objects.create(
        facility=proposal.facility,
        client=event.client,
        case=case,
        document_type=event.document_type,
        occurred_at=event.occurred_at,
    )
    sibling = Event.objects.create(
        facility=proposal.facility,
        client=event.client,
        document_type=event.document_type,
        occurred_at=event.occurred_at,
    )
    outsider = Client.objects.create(facility=proposal.facility, pseudonym="Unrelated retention record")
    unrelated = Event.objects.create(
        facility=proposal.facility, client=outsider, document_type=event.document_type, occurred_at=event.occurred_at
    )
    rows = [proposal] + [
        create_proposal(proposal.facility, "Event", row.pk, timezone.localdate(), {}, "qualified")[0]
        for row in (other, sibling, unrelated)
    ]
    for row in rows:
        approve_proposal(row, lead_user)
    affected = {"Event": rows[:1], "Client": rows[:3], "Case": rows[:2]}[target_type]
    target_id = {"Event": event.pk, "Client": event.client_id, "Case": case.pk}[target_type]
    client.force_login(admin_user)
    observed = []

    def action():
        assert (
            client.post(
                reverse(f"core:{target_type.lower()}_legal_hold", args=[target_id]), {"reason": "Preserve record scope"}
            ).status_code
            == 302
        )

    record_timeline(
        proposal.facility_id,
        {str(row.pk): row.pk for row in rows},
        action,
        lambda: observed.append(immediately_lockable([(str(row.pk), RetentionProposal, row.pk) for row in affected])),
    )
    assert observed == [[]]
    for row in rows:
        row.refresh_from_db()
        assert (row.first_approved_by_id is None) == (row in affected)
        assert decisions(row, "retention_proposal_first_approval_cleared").count() == int(row in affected)
