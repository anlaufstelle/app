"""Pins for public case deletion services.

Source: mutation delta of the release-0.99.7 checkpoint, 12 mutants.
Groups: target selection (1), actor domain errors (3), batch protection (4).
Eight mutants are pinned; four are equivalent. A foreign actor still gets
the same domain error without changing the case: x_soft_delete_case__mutmut_9
only adds a read scoped to the actor's facility before rejection.
The three repeated restriction-lock mutations are equivalent: the same
person and protection gate are already held by the enclosing transaction.
"""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import AuditLog, Case, Client, LegalHold
from core.services.case import deletion

pytestmark = pytest.mark.django_db


def test_trash_only_the_requested_case(case_open, facility, client_identified, staff_user):
    """Trash and audit the requested case even when another comes first.

    Kills x__move_case_to_trash__mutmut_5 through soft_delete_case.
    """
    other = Case.objects.create(facility=facility, client=client_identified, title="Unrelated case")
    Case.objects.filter(pk=other.pk).update(created_at=case_open.created_at + timedelta(days=1))
    assert Case.objects.for_facility(facility).first().pk == other.pk

    result = deletion.soft_delete_case(case_open, staff_user)

    assert result is case_open
    case_open.refresh_from_db()
    other.refresh_from_db()
    assert case_open.is_deleted
    assert case_open.deleted_at is not None
    assert not other.is_deleted
    assert other.deleted_at is None
    assert set(
        AuditLog.objects.filter(facility=facility, action=AuditLog.Action.CASE_SOFT_DELETED).values_list(
            "target_id", flat=True
        )
    ) == {str(case_open.pk)}


def test_foreign_actor_is_rejected_without_deleting_case(case_open, second_facility_user):
    """Reject a foreign actor with the facility error and preserve the case."""
    with pytest.raises(ValidationError) as error:
        deletion.soft_delete_case(case_open, second_facility_user)
    assert error.value.messages == [deletion.CASE_OTHER_FACILITY_MESSAGE]
    case_open.refresh_from_db()
    assert not case_open.is_deleted


@pytest.mark.parametrize("dry_run", [True, False], ids=["preview", "erase"])
def test_unrelated_restriction_does_not_defer_an_eligible_case(case_trashed, facility, settings_obj, dry_run):
    """Only the case's own person can defer erasure.

    Kills x_anonymize_eligible_soft_deleted_cases__mutmut_62.
    """
    Client.objects.create(facility=facility, pseudonym="Unrelated restricted person", processing_restricted=True)
    Case.objects.filter(pk=case_trashed.pk).update(
        deleted_at=timezone.now() - timedelta(days=settings_obj.client_trash_days + 1)
    )
    deferred = set()

    count = deletion.anonymize_eligible_soft_deleted_cases(
        facility, settings_obj, dry_run=dry_run, deferred_ids=deferred
    )

    assert count == 1
    assert deferred == set()
    case_trashed.refresh_from_db()
    assert (case_trashed.anonymized_at is not None) is (not dry_run)


@pytest.mark.parametrize("dry_run", [True, False], ids=["preview", "erase"])
def test_initially_protected_cases_add_scalar_ids(
    case_trashed, facility, settings_obj, lead_user, client_identified, dry_run
):
    """Initially held and restricted cases add UUIDs, preserving supplied IDs.

    Kills x_anonymize_eligible_soft_deleted_cases__mutmut_25,
    x_anonymize_eligible_soft_deleted_cases__mutmut_27 and
    x_anonymize_eligible_soft_deleted_cases__mutmut_34.
    """
    Client.objects.filter(pk=client_identified.pk).update(processing_restricted=True)
    other_client = Client.objects.create(facility=facility, pseudonym="Held case person")
    held = Case.objects.create(facility=facility, client=other_client, title="Held case", is_deleted=True)
    Case.objects.filter(pk__in=[case_trashed.pk, held.pk]).update(
        deleted_at=timezone.now() - timedelta(days=settings_obj.client_trash_days + 1)
    )
    LegalHold.objects.create(
        facility=facility, target_type="Case", target_id=held.pk, reason="Preserve evidence", created_by=lead_user
    )
    existing_id = uuid4()
    deferred = {existing_id}

    assert (
        deletion.anonymize_eligible_soft_deleted_cases(facility, settings_obj, dry_run=dry_run, deferred_ids=deferred)
        == 0
    )
    assert deferred == {existing_id, case_trashed.pk, held.pk}
    for case in (case_trashed, held):
        case.refresh_from_db()
        assert case.anonymized_at is None


@pytest.mark.parametrize("actor", [None, AnonymousUser()], ids=["missing", "anonymous"])
@pytest.mark.parametrize(
    "operation",
    [deletion.anonymize_case_now, deletion.create_or_get_case_deletion_request, deletion.restore_case],
    ids=["anonymize", "request", "restore"],
)
def test_missing_actor_facility_has_the_domain_error(case_trashed, actor, operation):
    """Actors without facility_id receive the domain-specific facility error.

    Kills x_anonymize_case_now__mutmut_6,
    x_create_or_get_case_deletion_request__mutmut_6 and x_restore_case__mutmut_6.
    """
    with pytest.raises(ValidationError) as error:
        if operation is deletion.create_or_get_case_deletion_request:
            operation(case_trashed, actor, "Requested erasure")
        else:
            operation(case_trashed, actor)
    assert error.value.messages == [deletion.CASE_OTHER_FACILITY_MESSAGE]
