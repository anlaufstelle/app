"""Pins from the targeted mutation delta DEAD, batch case.

Source: delta DEAD survivors of ``core.services.case.cases`` (edit form state
token) and ``core.services.case.deletion`` (trash-expiry sweep, deletion
request, rejection and restore refusals). Every refusal is asserted with its
complete German message under an explicit locale.
"""

from datetime import timedelta
from uuid import UUID

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone, translation

from core.models import Case, DeletionRequest, LegalHold, User
from core.models.client import Client
from core.services.case.cases import case_form_state_token
from core.services.case.deletion import (
    anonymize_eligible_soft_deleted_cases,
    create_or_get_case_deletion_request,
    reject_case_deletion,
    restore_case,
)
from tests._row_lock_helpers import mitschnitt as capture_locks

pytestmark = pytest.mark.django_db


def _trashed_case(facility, client, user, title, days_ago):
    case = Case.objects.create(facility=facility, client=client, title=title, created_by=user)
    Case.objects.filter(pk=case.pk).update(is_deleted=True, deleted_at=timezone.now() - timedelta(days=days_ago))
    case.refresh_from_db()
    return case


def test_form_state_token_is_a_stable_salted_digest(settings):
    """The hidden edit-form fingerprint keeps its exact digest for a fixed secret and case.

    core.services.case.cases.x__case_form_state_token_from_values__mutmut_10,
    core.services.case.cases.x__case_form_state_token_from_values__mutmut_16,
    core.services.case.cases.x__case_form_state_token_from_values__mutmut_17
    """
    settings.SECRET_KEY = "pin-secret-key"
    settings.SECRET_KEY_FALLBACKS = []
    case = Case(
        pk=UUID("6f1f3c1e-7d2b-4a55-9c0e-1a2b3c4d5e6f"),
        title="Pin case",
        lead_user_id=None,
        document_type_id=None,
        data_json={"k": "v"},
    )

    assert case_form_state_token(case) == "7714dc5735dd305eece4c59298d51c43584a697ed544bd6f0180de8da6fc405a"


def test_preview_and_deferral_ignore_cases_inside_the_trash_period(
    facility, settings_obj, client_identified, staff_user, lead_user
):
    """Cases trashed within the period are neither counted nor reported as deferred.

    core.services.case.deletion.x_anonymize_eligible_soft_deleted_cases__mutmut_4
    """
    days = settings_obj.client_trash_days
    fresh = _trashed_case(facility, client_identified, staff_user, "Fresh case", 1)
    fresh_held = _trashed_case(facility, client_identified, staff_user, "Fresh held case", 1)
    expired = _trashed_case(facility, client_identified, staff_user, "Expired case", days + 5)
    expired_held = _trashed_case(facility, client_identified, staff_user, "Expired held case", days + 5)
    for held in (fresh_held, expired_held):
        LegalHold.objects.create(
            facility=facility, target_type="Case", target_id=held.pk, reason="Pin hold", created_by=lead_user
        )
    deferred = set()

    count = anonymize_eligible_soft_deleted_cases(facility, settings_obj, dry_run=True, deferred_ids=deferred)

    assert count == 1
    assert deferred == {expired_held.pk}
    assert Case.objects.filter(pk__in=[fresh.pk, fresh_held.pk, expired.pk]).count() == 3


def test_sweep_skips_a_case_that_left_the_trash_before_its_row_lock(
    facility, settings_obj, client_identified, staff_user
):
    """A case whose trash flag is cleared after selection is never erased.

    core.services.case.deletion.x_anonymize_eligible_soft_deleted_cases__mutmut_63
    """
    case = _trashed_case(facility, client_identified, staff_user, "Rescued case", settings_obj.client_trash_days + 5)

    with capture_locks() as captured:
        captured.nach_sperre(Client._meta.db_table, lambda: Case.objects.filter(pk=case.pk).update(is_deleted=False))
        count = anonymize_eligible_soft_deleted_cases(facility, settings_obj)

    assert captured.offene_schranken() == []
    case.refresh_from_db()
    assert count == 0
    assert case.anonymized_at is None
    assert case.title == "Rescued case"


def test_deletion_request_refusals_name_the_reason(facility, client_identified, staff_user):
    """Requesting deletion of a trashed or erased case is refused with a specific message.

    core.services.case.deletion.x_create_or_get_case_deletion_request__mutmut_26,
    core.services.case.deletion.x_create_or_get_case_deletion_request__mutmut_32,
    core.services.case.deletion.x_create_or_get_case_deletion_request__mutmut_33
    """
    trashed = _trashed_case(facility, client_identified, staff_user, "Trashed case", 1)
    erased = Case.objects.create(facility=facility, client=client_identified, title="Erased case")
    Case.objects.filter(pk=erased.pk).update(anonymized_at=timezone.now())

    with translation.override("de"):
        with pytest.raises(ValidationError) as in_trash:
            create_or_get_case_deletion_request(trashed, staff_user, "Reason")
        with pytest.raises(ValidationError) as anonymized:
            create_or_get_case_deletion_request(erased, staff_user, "Reason")

    assert in_trash.value.messages == ["Der Fall liegt bereits im Papierkorb."]
    assert anonymized.value.messages == ["Der Fall wurde bereits endgültig anonymisiert."]


def test_rejection_refusals_name_the_reason(facility, second_facility, client_identified, staff_user, lead_user):
    """A foreign reviewer and a repeated rejection are refused with specific messages.

    core.services.case.deletion.x_reject_case_deletion__mutmut_18,
    core.services.case.deletion.x_reject_case_deletion__mutmut_29,
    core.services.case.deletion.x_reject_case_deletion__mutmut_30
    """
    case = Case.objects.create(facility=facility, client=client_identified, title="Open case", created_by=staff_user)
    request, _created = create_or_get_case_deletion_request(case, staff_user, "Reason")
    foreign_reviewer = User.objects.create_user(
        username="foreign-pin-reviewer",
        role=User.Role.LEAD,
        facility=second_facility,
        can_confirm_deletion=True,
    )

    with translation.override("de"):
        with pytest.raises(ValidationError) as foreign:
            reject_case_deletion(request, foreign_reviewer)
        reject_case_deletion(request, lead_user)
        with pytest.raises(ValidationError) as repeated:
            reject_case_deletion(request, lead_user)

    assert foreign.value.messages == ["Antrag gehört zu einer anderen Einrichtung."]
    assert repeated.value.messages == ["Löschantrag wurde bereits bearbeitet (nicht mehr ausstehend)."]
    request.refresh_from_db()
    assert request.status == DeletionRequest.Status.REJECTED


def test_restore_refuses_an_erased_case_with_its_reason(facility, client_identified, staff_user, lead_user):
    """An erased case in the trash cannot come back; the lead is told why.

    core.services.case.deletion.x_restore_case__mutmut_33,
    core.services.case.deletion.x_restore_case__mutmut_34
    """
    case = _trashed_case(facility, client_identified, staff_user, "Erased case", 400)
    Case.objects.filter(pk=case.pk).update(anonymized_at=timezone.now())

    with translation.override("de"), pytest.raises(ValidationError) as excinfo:
        restore_case(case, lead_user)

    assert excinfo.value.messages == ["Der Fall wurde endgültig anonymisiert und kann nicht wiederhergestellt werden."]
    case.refresh_from_db()
    assert case.is_deleted is True
