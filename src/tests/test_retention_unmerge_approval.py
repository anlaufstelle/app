"""Regression checks for approval protection across merge and unmerge."""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.models import Case, Client, DeletionRequest, RetentionProposal
from core.services.client import update_client
from core.services.client.merge import merge_clients, unmerge_client
from core.services.events.crud import create_event

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("event_age_days", [400, 4000])
def test_unmerge_cannot_remove_approval_requirement_from_later_qualified_event(
    facility, settings_obj, client_identified, admin_user, doc_type_contact, event_age_days
):
    source = Client.objects.create(facility=facility, pseudonym="Duplicate", created_by=admin_user)
    target = client_identified
    merge_clients(source, target, admin_user)
    target.refresh_from_db()
    update_client(target, admin_user, contact_stage=Client.ContactStage.QUALIFIED)
    target.refresh_from_db()
    assert target.contact_stage == Client.ContactStage.QUALIFIED
    event = create_event(
        facility,
        admin_user,
        doc_type_contact,
        timezone.now() - timedelta(days=event_age_days),
        {"dauer": 20},
        client=target,
    )
    source.refresh_from_db()
    unmerge_client(source, admin_user)
    event.refresh_from_db()
    assert event.client_id == target.pk
    assert not DeletionRequest.objects.filter(facility=facility).exists()
    assert not RetentionProposal.objects.filter(facility=facility, target_id=event.pk).exists()

    call_command("enforce_retention", stdout=StringIO())

    event.refresh_from_db()
    target.refresh_from_db()
    assert target.contact_stage == Client.ContactStage.QUALIFIED
    assert RetentionProposal.objects.filter(facility=facility, target_id=event.pk).exists() == (event_age_days == 4000)
    assert not event.is_deleted, (
        "A single actor removed qualified protection through unmerge; retention erased the event"
    )


@pytest.mark.parametrize("event_age_days", [400, 4000])
def test_unmerge_keeps_approval_for_new_events_following_a_returned_case(
    facility, settings_obj, client_identified, admin_user, doc_type_contact, event_age_days
):
    source = Client.objects.create(facility=facility, pseudonym="Duplicate", created_by=admin_user)
    case = Case.objects.create(facility=facility, client=source, title="Moved case", created_by=admin_user)
    merge_clients(source, client_identified, admin_user)
    client_identified.refresh_from_db()
    case.refresh_from_db()
    update_client(client_identified, admin_user, contact_stage=Client.ContactStage.QUALIFIED)
    doc_type_contact.retention_days = event_age_days - 1
    doc_type_contact.save(update_fields=["retention_days"])
    event = create_event(
        facility,
        admin_user,
        doc_type_contact,
        timezone.now() - timedelta(days=event_age_days),
        {"dauer": 20},
        client=client_identified,
        case=case,
    )
    source.refresh_from_db()
    unmerge_client(source, admin_user)
    event.refresh_from_db()
    assert event.client_id == source.pk
    assert not DeletionRequest.objects.filter(facility=facility).exists()
    call_command("enforce_retention", stdout=StringIO())
    event.refresh_from_db()
    assert not event.is_deleted, "Returning a case must retain approval protection for its later qualified events"
    source.refresh_from_db()
    assert source.contact_stage == Client.ContactStage.QUALIFIED
    assert RetentionProposal.objects.filter(facility=facility, target_id=event.pk).exists()
