"""Retention and manual case erasure respect current processing protection."""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.utils import timezone

from core.models import Case, Client
from core.retention.legal_holds import create_hold_for_target
from core.services.case.deletion import (
    anonymize_case_now,
    anonymize_eligible_soft_deleted_cases,
    approve_case_deletion,
    request_case_deletion,
)
from core.services.client.restriction import restrict_processing
from core.services.events.crud import create_event

pytestmark = pytest.mark.django_db


@pytest.fixture
def expired_case(facility, client_qualified, staff_user, admin_user, doc_type_contact):
    with patch("django.utils.timezone.now", return_value=timezone.now() - timedelta(days=40)):
        case = Case.objects.create(
            facility=facility,
            client=client_qualified,
            title="Preserve this case",
            description="Evidence",
            created_by=staff_user,
        )
        event = create_event(
            facility,
            staff_user,
            doc_type_contact,
            timezone.now(),
            {"dauer": 20},
            client=client_qualified,
            case=case,
        )
        request = request_case_deletion(case, staff_user, "Remove duplicate case")
        approve_case_deletion(request, admin_user)
    case.refresh_from_db()
    assert case.is_deleted and case.anonymized_at is None
    return case, event


@pytest.mark.parametrize("protection", ["restriction", "Case", "Client", "Event"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_case_cleanup_preserves_and_counts_protected_targets(
    facility, settings_obj, expired_case, admin_user, protection, dry_run
):
    case, event = expired_case
    if protection == "restriction":
        restrict_processing(case.client, admin_user, reason=Client.RestrictionReason.LEGAL_CLAIMS)
    else:
        target = {"Case": case, "Client": case.client, "Event": event}[protection]
        create_hold_for_target(
            facility,
            target_type=protection,
            target_id=target.pk,
            user=admin_user,
            reason="Preserve evidence",
        )
    output = StringIO()
    call_command("enforce_retention", dry_run=dry_run, stdout=output)
    case.refresh_from_db()
    event.refresh_from_db()
    assert case.anonymized_at is None
    assert case.title == "Preserve this case" and case.description == "Evidence"
    assert event.case_id == case.pk
    assert "Deferred 1 event/person/case target(s)" in output.getvalue()


def test_case_cleanup_rechecks_restriction_after_selection(
    facility, settings_obj, expired_case, admin_user, monkeypatch
):
    from core.services.case import deletion

    case, event = expired_case
    original_lock = deletion._lock_case_chain

    def restrict_then_lock(facility_id, case_pk):
        restrict_processing(case.client, admin_user, reason=Client.RestrictionReason.LEGAL_CLAIMS)
        return original_lock(facility_id, case_pk)

    monkeypatch.setattr(deletion, "_lock_case_chain", restrict_then_lock)
    deferred = set()
    count = anonymize_eligible_soft_deleted_cases(facility, settings_obj, deferred_ids=deferred)
    case.refresh_from_db()
    assert count == 0 and deferred == {case.pk}
    assert case.anonymized_at is None and case.description == "Evidence"


def test_case_cleanup_still_erases_unprotected_case(facility, settings_obj, expired_case):
    case, event = expired_case
    deferred = set()
    assert anonymize_eligible_soft_deleted_cases(facility, settings_obj, deferred_ids=deferred) == 1
    case.refresh_from_db()
    event.refresh_from_db()
    assert case.anonymized_at is not None and case.description == ""
    assert event.case_id is None
    assert deferred == set()


def test_manual_case_erasure_rechecks_current_restriction(expired_case, admin_user):
    case, event = expired_case
    restrict_processing(case.client, admin_user, reason=Client.RestrictionReason.LEGAL_CLAIMS)
    with pytest.raises(ValidationError, match="Art. 18"):
        anonymize_case_now(case, admin_user)
    case.refresh_from_db()
    assert case.anonymized_at is None and case.description == "Evidence"
