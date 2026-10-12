"""Pins for public person services.

Source: mutation delta of the release-0.99.7 checkpoint, 21 mutants.
Groups: immediate restriction refusal (1), redundant restriction locks (6,
not pinned), deferred UUIDs and locked rechecks (6), decision messages (6),
and direct downgrade messages (2).
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.utils import timezone
from django.utils.translation import gettext as _

from core.models import AuditLog, Client, DeletionRequest, Event
from core.services.client import main
from core.services.client.restriction import PROCESSING_RESTRICTED_MESSAGE
from tests._row_lock_helpers import mitschnitt as capture_locks

pytestmark = pytest.mark.django_db


def test_restricted_trash_person_keeps_identity(client_trashed, lead_user):
    """Restriction blocks erasure and its audit: x_anonymize_client_now__mutmut_39."""
    Client.objects.filter(pk=client_trashed.pk).update(processing_restricted=True, notes="Keep this text")
    before = Client.objects.filter(pk=client_trashed.pk).values().get()
    audits = AuditLog.objects.count()

    with pytest.raises(ValidationError) as error:
        main.anonymize_client_now(client_trashed, lead_user)

    assert error.value.messages == [str(PROCESSING_RESTRICTED_MESSAGE)]
    assert Client.objects.filter(pk=client_trashed.pk).values().get() == before
    assert AuditLog.objects.count() == audits


def test_deferred_people_are_deduplicated_as_uuids(facility, client_trashed):
    """Overlapping deferrals keep UUIDs: x_anonymize_eligible_soft_deleted_clients__mutmut_22,
    x_anonymize_eligible_soft_deleted_clients__mutmut_24,
    x_anonymize_eligible_soft_deleted_clients__mutmut_29.
    """
    Client.objects.filter(pk=client_trashed.pk).update(
        processing_restricted=True, deleted_at=timezone.now() - timedelta(days=8)
    )
    deferred = {client_trashed.pk}

    count = main.anonymize_eligible_soft_deleted_clients(
        facility, SimpleNamespace(client_trash_days=7), deferred_ids=deferred
    )

    assert count == 0
    assert deferred == {client_trashed.pk}
    client_trashed.refresh_from_db()
    assert client_trashed.anonymized_at is None


@pytest.mark.parametrize("with_set", [True, False], ids=["with-set", "without-set"])
def test_locked_restrictions_defer_and_allow_later_candidates(facility, staff_user, with_set):
    """Fresh restrictions skip only their people and record UUIDs:
    x_anonymize_eligible_soft_deleted_clients__mutmut_57,
    x_anonymize_eligible_soft_deleted_clients__mutmut_58,
    x_anonymize_eligible_soft_deleted_clients__mutmut_59.
    """
    people = [
        Client.objects.create(
            facility=facility,
            created_by=staff_user,
            pseudonym=name,
            is_deleted=True,
            deleted_at=timezone.now() - timedelta(days=8),
        )
        for name in ["A-restricted", "B-restricted", "Z-eligible"]
    ]
    restricted_ids = {person.pk for person in people[:2]}
    deferred = set() if with_set else None
    changed = False

    def restrict_before_first_person_lock(execute, sql, params, many, context):
        nonlocal changed
        if not changed and 'FROM "core_client"' in sql and "FOR UPDATE" in sql:
            changed = True
            Client.objects.filter(pk__in=restricted_ids).update(processing_restricted=True)
        return execute(sql, params, many, context)

    with connection.execute_wrapper(restrict_before_first_person_lock):
        count = main.anonymize_eligible_soft_deleted_clients(
            facility, SimpleNamespace(client_trash_days=7), deferred_ids=deferred
        )

    assert changed
    assert count == 1
    if with_set:
        assert deferred == restricted_ids
    for person in people:
        person.refresh_from_db()
    assert all(person.anonymized_at is None for person in people[:2])
    assert people[2].anonymized_at is not None


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_deletion_decision_rechecks_facility_message(
    client_identified, staff_user, lead_user, second_facility, decision
):
    """A reviewer moved after locking gets the facility refusal before writes:
    x_approve_client_deletion__mutmut_21, x_reject_client_deletion__mutmut_24.
    """
    request = main.request_client_deletion(client_identified, staff_user, "Review required")
    audits = AuditLog.objects.count()
    with capture_locks() as captured:
        captured.nach_sperre(
            DeletionRequest._meta.db_table, lambda: setattr(lead_user, "facility_id", second_facility.pk)
        )
        with pytest.raises(ValidationError) as error:
            getattr(main, f"{decision}_client_deletion")(request, lead_user)

    assert captured.offene_schranken() == []
    assert error.value.messages == [_("Antrag gehört zu einer anderen Einrichtung.")]
    request.refresh_from_db()
    client_identified.refresh_from_db()
    assert request.status == DeletionRequest.Status.PENDING
    assert request.reviewed_by_id is None
    assert not client_identified.is_deleted
    assert AuditLog.objects.count() == audits


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_downgrade_request_keeps_routing_message(client_qualified, staff_user, lead_user, decision):
    """Deletion decisions explain the downgrade route without changing the request:
    x_approve_client_deletion__mutmut_25, x_approve_client_deletion__mutmut_26,
    x_reject_client_deletion__mutmut_28, x_reject_client_deletion__mutmut_29.
    """
    request = DeletionRequest.objects.create(
        facility=client_qualified.facility,
        target_type=DeletionRequest.TargetType.CLIENT,
        target_id=client_qualified.pk,
        requested_by=staff_user,
        reason="Stage review",
        stage_downgrade=True,
    )
    with pytest.raises(ValidationError) as error:
        getattr(main, f"{decision}_client_deletion")(request, lead_user)

    assert error.value.messages == [_("Dies ist ein Herabstufungsantrag — bitte den Herabstufungs-Weg verwenden.")]
    request.refresh_from_db()
    client_qualified.refresh_from_db()
    assert request.status == DeletionRequest.Status.PENDING
    assert request.reviewed_by_id is None
    assert client_qualified.contact_stage == Client.ContactStage.QUALIFIED
    assert not client_qualified.is_deleted


def test_direct_downgrade_explains_four_eyes(client_qualified, doc_type_contact, lead_user):
    """Direct downgrades with events explain approval:
    x_update_client__mutmut_55, x_update_client__mutmut_56.
    """
    Event.objects.create(
        facility=client_qualified.facility,
        client=client_qualified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
    )
    with pytest.raises(ValidationError) as error:
        main.update_client(client_qualified, lead_user, contact_stage=Client.ContactStage.IDENTIFIED)

    assert error.value.messages == [
        _("Bei vorhandenen Ereignissen ist ein Herabstufungsantrag mit Vier-Augen-Freigabe erforderlich.")
    ]
    client_qualified.refresh_from_db()
    assert client_qualified.contact_stage == Client.ContactStage.QUALIFIED
