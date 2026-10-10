"""Expired proposal holds restore review while active protection still blocks it."""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.models import Case, Client, RetentionProposal
from core.retention.legal_holds import create_hold_for_target, create_legal_hold, has_active_hold
from core.retention.proposals import approve_proposal
from core.services.client.restriction import restrict_processing
from core.services.events.crud import create_event

pytestmark = pytest.mark.django_db


@pytest.fixture
def held_proposal(facility, settings_obj, client_qualified, admin_user, doc_type_contact):
    now = timezone.now()
    doc_type_contact.retention_days = 30
    doc_type_contact.save(update_fields=["retention_days"])
    with patch("django.utils.timezone.now", return_value=now - timedelta(days=5000)):
        case = Case.objects.create(
            facility=facility,
            client=client_qualified,
            title="Old case",
            created_by=admin_user,
        )
    event = create_event(
        facility,
        admin_user,
        doc_type_contact,
        now - timedelta(days=4000),
        {"dauer": 20},
        client=client_qualified,
        case=case,
    )
    call_command("enforce_retention", stdout=StringIO())
    proposal = RetentionProposal.objects.get(facility=facility, target_id=event.pk)
    hold = create_legal_hold(proposal, admin_user, "Preserve until deadline", expires_at=timezone.localdate(now))
    return now, event, proposal, hold


def test_expired_proposal_hold_restores_explicit_review(facility, settings_obj, held_proposal, admin_user):
    now, event, proposal, hold = held_proposal
    settings_obj.retention_auto_approve_after_defer = True
    settings_obj.save(update_fields=["retention_auto_approve_after_defer"])
    with patch("django.utils.timezone.now", return_value=now + timedelta(days=2)):
        assert not has_active_hold(facility, "Event", event.pk)
        call_command("enforce_retention", stdout=StringIO())
        proposal.refresh_from_db()
        event.refresh_from_db()
        assert not event.is_deleted
        approve_proposal(proposal, admin_user)
        call_command("enforce_retention", stdout=StringIO())
        event.refresh_from_db()
        assert event.is_deleted


@pytest.mark.parametrize("protection", ["Event", "Client", "Case", "restriction", "same_day"])
def test_expired_proposal_hold_does_not_override_remaining_protection(facility, held_proposal, admin_user, protection):
    now, event, proposal, hold = held_proposal
    if protection == "restriction":
        restrict_processing(event.client, admin_user, reason=Client.RestrictionReason.LEGAL_CLAIMS)
    elif protection == "Event":
        hold.expires_at = timezone.localdate(now) + timedelta(days=5)
        hold.save(update_fields=["expires_at"])
    elif protection != "same_day":
        target = {"Event": event, "Client": event.client, "Case": event.case}[protection]
        create_hold_for_target(
            facility,
            target_type=protection,
            target_id=target.pk,
            user=admin_user,
            reason="Still preserve evidence",
        )
    clock = now if protection == "same_day" else now + timedelta(days=2)
    with patch("django.utils.timezone.now", return_value=clock):
        call_command("enforce_retention", stdout=StringIO())
    proposal.refresh_from_db()
    event.refresh_from_db()
    assert proposal.status == RetentionProposal.Status.HELD
    assert not event.is_deleted
