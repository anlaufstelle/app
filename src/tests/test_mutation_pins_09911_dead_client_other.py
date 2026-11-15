"""Pins from the targeted mutation delta DEAD, batch client_other.

Source: delta DEAD survivors of ``core.services.client.main``: erasure of a
person's event timeline, the trash-expiry preview and the lock of a deletion
approval. The remaining survivors of this batch are equivalent mutants.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from core.models import Client, DeletionRequest, Event
from core.services.client import (
    anonymize_client,
    anonymize_eligible_soft_deleted_clients,
    approve_client_deletion,
    create_or_get_client_deletion_request,
)
from core.services.client import main as client_main
from tests._row_lock_helpers import mitschnitt as capture_locks

pytestmark = pytest.mark.django_db

BERLIN = ZoneInfo("Europe/Berlin")


def test_timestamp_generalization_continues_after_an_already_generalized_event(
    facility, client_identified, doc_type_contact, staff_user
):
    """An event already at its month start does not stop the other events from being generalized.

    core.services.client.main.x__generalize_event_timestamps__mutmut_32
    """
    already_bucketed = datetime(2026, 5, 1, 0, 0, tzinfo=BERLIN)
    middle = datetime(2026, 4, 17, 10, 15, tzinfo=BERLIN)
    oldest = datetime(2026, 3, 9, 12, 40, tzinfo=BERLIN)
    events = {
        key: Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=moment,
            created_by=staff_user,
            data_json={},
        )
        for key, moment in (("bucketed", already_bucketed), ("middle", middle), ("oldest", oldest))
    }

    anonymize_client(client_identified, user=staff_user)

    stored = {key: timezone.localtime(Event.objects.get(pk=event.pk).occurred_at) for key, event in events.items()}
    assert stored["bucketed"] == already_bucketed
    assert stored["middle"] == datetime(2026, 4, 1, 0, 0, tzinfo=BERLIN)
    assert stored["oldest"] == datetime(2026, 3, 1, 0, 0, tzinfo=BERLIN)


def test_client_sweep_preview_ignores_people_inside_the_trash_period(facility, settings_obj, staff_user, lead_user):
    """Recently trashed people are neither counted nor reported as deferred by the preview.

    core.services.client.main.x_anonymize_eligible_soft_deleted_clients__mutmut_4
    """
    days = settings_obj.client_trash_days

    def trashed(pseudonym, days_ago, *, restricted=False):
        person = Client.objects.create(
            facility=facility,
            pseudonym=pseudonym,
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=staff_user,
        )
        Client.objects.filter(pk=person.pk).update(
            is_deleted=True,
            deleted_at=timezone.now() - timedelta(days=days_ago),
            processing_restricted=restricted,
        )
        return person

    trashed("Fresh person", 1)
    trashed("Fresh restricted person", 1, restricted=True)
    trashed("Expired person", days + 5)
    expired_restricted = trashed("Expired restricted person", days + 5, restricted=True)
    deferred = set()

    count = anonymize_eligible_soft_deleted_clients(facility, settings_obj, dry_run=True, deferred_ids=deferred)

    assert count == 1
    assert deferred == {expired_restricted.pk}


def test_approval_ignores_a_request_moved_to_another_facility_after_the_person_lock(
    facility, second_facility, client_identified, staff_user, lead_user
):
    """A request that left the facility while the person was locked is not found, and nothing changes.

    core.services.client.main.x__lock_client_then_deletion_request__mutmut_18
    """
    request = create_or_get_client_deletion_request(client_identified, staff_user, "Reason")[0]

    with capture_locks() as captured:
        captured.nach_sperre(
            Client._meta.db_table,
            lambda: DeletionRequest.objects.filter(pk=request.pk).update(facility=second_facility),
        )
        with pytest.raises(DeletionRequest.DoesNotExist):
            approve_client_deletion(request, lead_user)

    assert captured.offene_schranken() == []
    client_identified.refresh_from_db()
    request.refresh_from_db()
    assert client_identified.is_deleted is False
    assert request.status == DeletionRequest.Status.PENDING


def test_document_type_kwarg_helper_replaces_the_id_with_the_object_in_place(doc_type_client_profile):
    """The helper rewrites the caller's data so exactly one spelling of the type remains.

    core.services.client.main.x__normalize_document_type_kwarg__mutmut_21,
    core.services.client.main.x__normalize_document_type_kwarg__mutmut_33
    """
    by_id = {"document_type_id": doc_type_client_profile.pk}
    cleared = {"document_type_id": None}

    resolved = client_main._normalize_document_type_kwarg(by_id)
    cleared_result = client_main._normalize_document_type_kwarg(cleared)

    assert resolved == doc_type_client_profile
    assert by_id == {"document_type": doc_type_client_profile}
    assert cleared_result is None
    assert cleared == {"document_type": None}
    assert cleared["document_type"] is None
