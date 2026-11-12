"""A two-person approval only covers the deadline it was given for.

If the event of an approved proposal is no longer due when the retention run
looks at it (new case activity, a reopened case or a longer retention period),
the run returns the proposal to review and discards both approvals with an
audit row. A later deletion needs a new two-person approval.
"""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.models import AuditLog, Case, Event, RetentionProposal
from core.retention.enforcement import _soft_delete_events
from core.services.compliance import detect_mass_delete
from core.services.retention import approve_proposal, create_proposal

pytestmark = pytest.mark.django_db

RESET_CATEGORY = "retention_proposal_approval_reset"


def _old():
    return timezone.now() - timedelta(days=45)


@pytest.fixture
def qualified_period(settings_obj):
    settings_obj.retention_qualified_days = 30
    settings_obj.save(update_fields=["retention_qualified_days"])
    return settings_obj


@pytest.fixture
def case(facility, client_qualified, staff_user):
    return Case.objects.create(facility=facility, client=client_qualified, title="Case", created_by=staff_user)


def _age_case(case):
    Case.objects.filter(pk=case.pk).update(created_at=_old(), last_activity_at=_old())


def _approved_events(facility, client, case, doc_type, lead_user, admin_user, count=1):
    events = [
        Event.objects.create(
            facility=facility,
            client=client,
            case=case,
            document_type=doc_type,
            occurred_at=_old(),
            data_json={"duration": 20},
        )
        for _ in range(count)
    ]
    _age_case(case)
    proposals = []
    for event in events:
        proposal = create_proposal(
            facility, "Event", event.pk, timezone.localdate() - timedelta(days=15), {}, "qualified"
        )[0]
        approve_proposal(proposal, lead_user)
        approve_proposal(proposal, admin_user)
        assert proposal.status == RetentionProposal.Status.APPROVED
        proposals.append(proposal)
    return events, proposals


@pytest.fixture
def approved(facility, client_qualified, case, doc_type_contact, lead_user, admin_user, qualified_period):
    events, proposals = _approved_events(facility, client_qualified, case, doc_type_contact, lead_user, admin_user)
    return events[0], proposals[0]


def _run(facility, *args):
    output = StringIO()
    call_command("enforce_retention", *args, facility=facility.name, stdout=output)
    return output.getvalue()


def _new_case_activity(facility, client, case, doc_type, settings_obj):
    Event.objects.create(
        facility=facility,
        client=client,
        case=case,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json={"duration": 5},
    )


def _longer_period(facility, client, case, doc_type, settings_obj):
    settings_obj.retention_qualified_days = 90
    settings_obj.save(update_fields=["retention_qualified_days"])


def _reset_rows(proposal):
    return AuditLog.objects.filter(
        facility=proposal.facility,
        action=AuditLog.Action.DELETE,
        target_id=str(proposal.target_id),
        detail__category=RESET_CATEGORY,
    )


def _assert_reset(proposal):
    proposal.refresh_from_db()
    assert proposal.status == RetentionProposal.Status.PENDING
    assert proposal.first_approved_by_id is None
    assert proposal.first_approved_at is None
    assert proposal.approved_by_id is None
    assert proposal.approved_at is None
    assert proposal.auto_approved_at is None
    row = _reset_rows(proposal).get()
    assert row.user is None
    assert row.detail["reason"] == "no_longer_due"


@pytest.mark.parametrize("postpone", [_new_case_activity, _longer_period], ids=["case_activity", "longer_period"])
def test_run_resets_approval_when_event_is_no_longer_due(
    postpone, approved, facility, client_qualified, case, doc_type_contact, qualified_period
):
    event, proposal = approved
    postpone(facility, client_qualified, case, doc_type_contact, qualified_period)
    _run(facility)
    assert not Event.objects.get(pk=event.pk).is_deleted
    _assert_reset(proposal)


def test_event_due_again_needs_a_new_two_person_approval(
    approved, facility, client_qualified, case, doc_type_contact, qualified_period, lead_user, admin_user
):
    event, proposal = approved
    _new_case_activity(facility, client_qualified, case, doc_type_contact, qualified_period)
    _run(facility)
    _assert_reset(proposal)

    # Time passes: the case is idle again and the event is due once more.
    _age_case(case)
    _run(facility)
    assert not Event.objects.get(pk=event.pk).is_deleted
    proposal.refresh_from_db()
    assert proposal.status == RetentionProposal.Status.PENDING

    approve_proposal(proposal, lead_user)
    _run(facility)
    assert not Event.objects.get(pk=event.pk).is_deleted

    approve_proposal(proposal, admin_user)
    _run(facility)
    assert Event.objects.get(pk=event.pk).is_deleted


def test_due_approved_event_is_still_deleted(approved, facility):
    event, proposal = approved
    _run(facility)
    assert Event.objects.get(pk=event.pk).is_deleted
    assert not _reset_rows(proposal).exists()


def test_dry_run_keeps_the_approval(approved, facility, client_qualified, case, doc_type_contact, qualified_period):
    event, proposal = approved
    _new_case_activity(facility, client_qualified, case, doc_type_contact, qualified_period)
    _run(facility, "--dry-run")
    proposal.refresh_from_db()
    assert proposal.status == RetentionProposal.Status.APPROVED
    assert not _reset_rows(proposal).exists()


def test_stale_selection_resets_approval_under_lock(
    approved, facility, client_qualified, case, doc_type_contact, qualified_period
):
    """The event was selected as due, but the case changed before its row lock."""
    event, proposal = approved
    stale_selection = Event.objects.filter(pk=event.pk)
    _new_case_activity(facility, client_qualified, case, doc_type_contact, qualified_period)
    count = len(
        _soft_delete_events(
            stale_selection,
            facility,
            "qualified",
            qualified_period.retention_qualified_days,
            settings_obj=qualified_period,
            recheck_expiry=True,
        )[1]
    )
    assert count == 0
    assert not Event.objects.get(pk=event.pk).is_deleted
    _assert_reset(proposal)


def test_reset_rows_do_not_raise_mass_delete(
    facility, client_qualified, case, doc_type_contact, lead_user, admin_user, qualified_period, settings
):
    settings.BREACH_DELETE_THRESHOLD = 2
    settings.BREACH_DELETE_THRESHOLD_LONG = 2
    _, proposals = _approved_events(facility, client_qualified, case, doc_type_contact, lead_user, admin_user, count=3)
    _new_case_activity(facility, client_qualified, case, doc_type_contact, qualified_period)
    _run(facility)
    for proposal in proposals:
        _assert_reset(proposal)
    assert detect_mass_delete(facility) == []


def test_activity_between_run_start_and_selection_cannot_reuse_the_approval(
    approved, facility, client_qualified, case, doc_type_contact, qualified_period, monkeypatch
):
    """New case activity lands after the run started but before the event selection.

    The event drops out of the selection, so the deletion loop never locks it.
    The proposal must still go back to review in this run, and the next daily
    run must not delete the event on the old approval.
    """
    from core.retention import proposals as proposal_service

    event, proposal = approved
    qualified_period.retention_qualified_days = 1
    qualified_period.save(update_fields=["retention_qualified_days"])
    first_run_at = timezone.now()
    activity_at = first_run_at + timedelta(seconds=1)
    next_run_at = activity_at + timedelta(days=1, seconds=1)
    real_create = proposal_service.create_proposals_for_facility
    calls = []

    def create_then_activity(*args, **kwargs):
        result = real_create(*args, **kwargs)
        calls.append(kwargs.get("qualified_only"))
        # The second call sits in the qualified step, right before its selection.
        if len(calls) == 2:
            with patch("django.utils.timezone.now", return_value=activity_at):
                _new_case_activity(facility, client_qualified, case, doc_type_contact, qualified_period)
        return result

    with monkeypatch.context() as scheduled:
        scheduled.setattr(proposal_service, "create_proposals_for_facility", create_then_activity)
        with patch("django.utils.timezone.now", return_value=first_run_at):
            _run(facility)
    assert len(calls) == 2
    assert not Event.objects.get(pk=event.pk).is_deleted
    _assert_reset(proposal)

    # The next daily run is slightly later; there was no new human approval.
    with patch("django.utils.timezone.now", return_value=next_run_at):
        _run(facility)
    assert not Event.objects.get(pk=event.pk).is_deleted
    proposal.refresh_from_db()
    assert proposal.status == RetentionProposal.Status.PENDING


def test_deadline_moved_and_passed_between_two_runs_needs_a_new_approval(
    approved, facility, client_qualified, case, doc_type_contact, qualified_period
):
    """The deadline moves after approval and passes again before any run looks.

    No run ever sees the event as not due; the approval still predates the
    current deadline and must not carry the deletion.
    """
    event, proposal = approved
    qualified_period.retention_qualified_days = 1
    qualified_period.save(update_fields=["retention_qualified_days"])
    activity_at = timezone.now() + timedelta(seconds=1)
    with patch("django.utils.timezone.now", return_value=activity_at):
        _new_case_activity(facility, client_qualified, case, doc_type_contact, qualified_period)

    with patch("django.utils.timezone.now", return_value=activity_at + timedelta(days=1, seconds=1)):
        _run(facility)
    assert not Event.objects.get(pk=event.pk).is_deleted
    proposal.refresh_from_db()
    assert proposal.status == RetentionProposal.Status.PENDING
    assert proposal.first_approved_by_id is None
    assert proposal.approved_by_id is None
    row = _reset_rows(proposal).get()
    assert row.detail["reason"] == "approved_before_current_deadline"
