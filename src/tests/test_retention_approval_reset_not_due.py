"""A two-person approval only covers the deadline it was given for.

If the event of an approved proposal is no longer due when the retention run
looks at it (new case activity or a longer retention period),
the run returns the proposal to review and discards both approvals with an
audit row. A later deletion needs a new two-person approval.
"""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.models import AuditLog, Case, Event, RetentionProposal, Settings
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


@pytest.fixture
def first_approved(facility, client_qualified, case, doc_type_contact, lead_user, qualified_period):
    event = Event.objects.create(
        facility=facility,
        client=client_qualified,
        case=case,
        document_type=doc_type_contact,
        occurred_at=_old(),
        data_json={"duration": 20},
    )
    _age_case(case)
    proposal = create_proposal(facility, "Event", event.pk, timezone.localdate() - timedelta(days=15), {}, "qualified")[
        0
    ]
    approve_proposal(proposal, lead_user)
    assert proposal.status == RetentionProposal.Status.PENDING
    assert proposal.first_approved_by_id == lead_user.pk
    return event, proposal


def test_lone_first_approval_does_not_survive_a_moved_deadline(
    first_approved, facility, client_qualified, case, doc_type_contact, qualified_period, lead_user, admin_user
):
    """A first approval from the old deadline plus one new approval must not delete."""
    event, proposal = first_approved
    _new_case_activity(facility, client_qualified, case, doc_type_contact, qualified_period)
    _run(facility)
    assert not Event.objects.get(pk=event.pk).is_deleted
    _assert_reset(proposal)

    # Due again; one new approval is only a first approval.
    _age_case(case)
    approve_proposal(proposal, admin_user)
    proposal.refresh_from_db()
    assert proposal.status == RetentionProposal.Status.PENDING
    _run(facility)
    assert not Event.objects.get(pk=event.pk).is_deleted

    approve_proposal(proposal, lead_user)
    _run(facility)
    assert Event.objects.get(pk=event.pk).is_deleted


def test_first_approval_reset_rows_do_not_raise_mass_delete(
    facility, client_qualified, case, doc_type_contact, lead_user, qualified_period, settings
):
    settings.BREACH_DELETE_THRESHOLD = 2
    settings.BREACH_DELETE_THRESHOLD_LONG = 2
    proposals = []
    for _ in range(3):
        event = Event.objects.create(
            facility=facility,
            client=client_qualified,
            case=case,
            document_type=doc_type_contact,
            occurred_at=_old(),
            data_json={"duration": 20},
        )
        proposal = create_proposal(facility, "Event", event.pk, timezone.localdate(), {}, "qualified")[0]
        proposals.append(proposal)
    _age_case(case)
    for proposal in proposals:
        approve_proposal(proposal, lead_user)
    _new_case_activity(facility, client_qualified, case, doc_type_contact, qualified_period)
    _run(facility)
    for proposal in proposals:
        _assert_reset(proposal)
    assert detect_mass_delete(facility) == []


def test_legal_hold_discards_both_approvals_with_audit(approved, facility, lead_user):
    from core.retention.legal_holds import create_hold_for_target

    event, proposal = approved
    create_hold_for_target(facility, target_type="Event", target_id=event.pk, user=lead_user, reason="Review")
    proposal.refresh_from_db()
    assert proposal.status == RetentionProposal.Status.PENDING
    assert proposal.first_approved_by_id is None
    assert proposal.first_approved_at is None
    assert proposal.approved_by_id is None
    assert proposal.approved_at is None
    assert proposal.auto_approved_at is None
    row = AuditLog.objects.get(
        facility=facility,
        target_id=str(event.pk),
        detail__category="retention_proposal_first_approval_cleared",
    )
    assert row.user == lead_user
    assert row.detail["reason"] == "legal_hold"


def test_reset_moves_the_deletion_date_to_the_current_deadline(
    approved, facility, client_qualified, case, doc_type_contact, qualified_period, lead_user, admin_user
):
    """After a reset the dashboard shows the new deadline, not the old overdue date."""
    from core.retention.proposals import annotate_urgency

    event, proposal = approved
    _new_case_activity(facility, client_qualified, case, doc_type_contact, qualified_period)
    _run(facility)
    _assert_reset(proposal)
    case.refresh_from_db()
    new_due = timezone.localdate(case.last_activity_at + timedelta(days=qualified_period.retention_qualified_days))
    assert proposal.deletion_due_at == new_due
    assert annotate_urgency(proposal).urgency != "red"

    # Once that date has passed the event is due again and needs two new approvals.
    later = timezone.now() + timedelta(days=qualified_period.retention_qualified_days + 1)
    with patch("django.utils.timezone.now", return_value=later):
        assert annotate_urgency(proposal).urgency == "red"
        _run(facility)
        assert not Event.objects.get(pk=event.pk).is_deleted
        approve_proposal(proposal, lead_user)
        _run(facility)
        assert not Event.objects.get(pk=event.pk).is_deleted
        approve_proposal(proposal, admin_user)
        _run(facility)
    assert Event.objects.get(pk=event.pk).is_deleted


def test_reset_restarts_the_deferral_count(
    approved, facility, client_qualified, case, doc_type_contact, qualified_period, lead_user
):
    """After a reset a single deferral must not trigger the automatic approval."""
    from core.retention.proposals import defer_proposal, reactivate_deferred_proposals

    event, proposal = approved
    qualified_period.retention_auto_approve_after_defer = True
    qualified_period.retention_max_defer_count = 2
    qualified_period.save(update_fields=["retention_auto_approve_after_defer", "retention_max_defer_count"])
    RetentionProposal.objects.filter(pk=proposal.pk).update(defer_count=2)
    _new_case_activity(facility, client_qualified, case, doc_type_contact, qualified_period)
    _run(facility)
    _assert_reset(proposal)
    assert proposal.defer_count == 0

    defer_proposal(proposal, lead_user, days=1)
    RetentionProposal.objects.filter(pk=proposal.pk).update(deferred_until=timezone.localdate() - timedelta(days=1))
    reactivate_deferred_proposals(facility)
    proposal.refresh_from_db()
    assert proposal.defer_count == 1
    assert proposal.status == RetentionProposal.Status.PENDING
    assert proposal.auto_approved_at is None


def test_binding_change_in_the_reset_sweep_skips_only_that_proposal(
    facility, client_qualified, case, doc_type_contact, lead_user, admin_user, qualified_period, monkeypatch, caplog
):
    """A concurrent merge on one target must not stop the rest of the facility run."""
    from django.core.exceptions import ValidationError

    from core.retention import proposals as proposal_service

    events, proposals = _approved_events(
        facility, client_qualified, case, doc_type_contact, lead_user, admin_user, count=2
    )
    blocked, other = proposals
    _new_case_activity(facility, client_qualified, case, doc_type_contact, qualified_period)
    real_lock = proposal_service._lock_target_rows_for_approval

    def merge_in_progress(proposal):
        if proposal.target_id == blocked.target_id:
            raise ValidationError(proposal_service.TARGET_BINDING_CHANGED_MESSAGE)
        return real_lock(proposal)

    with monkeypatch.context() as merging:
        merging.setattr(proposal_service, "_lock_target_rows_for_approval", merge_in_progress)
        output = _run(facility)
    assert "failed_facilities=0" in output
    _assert_reset(other)
    blocked.refresh_from_db()
    assert blocked.status == RetentionProposal.Status.APPROVED
    assert client_qualified.pseudonym not in caplog.text

    # The next run checks the skipped proposal again.
    _run(facility)
    _assert_reset(blocked)
    assert not any(Event.objects.get(pk=event.pk).is_deleted for event in events)


def _change_period_before_the_locked_decision(monkeypatch, facility, user, days):
    """Change the period through the settings service after the run selected its events.

    The selection and the ``settings_obj`` handed to the deletion loop still
    carry the old period; the change is committed before the per-event
    transaction takes its locks.
    """
    from core.retention import enforcement
    from core.services.system.settings import update_settings

    original = enforcement._soft_delete_events
    changed = []

    def change_then_delete(*args, **kwargs):
        if not changed:
            current = Settings.objects.get(facility=facility)
            update_settings(current, user, retention_qualified_days=days)
            changed.append(days)
        return original(*args, **kwargs)

    monkeypatch.setattr(enforcement, "_soft_delete_events", change_then_delete)
    return changed


def test_period_extended_during_the_run_governs_the_locked_decision(approved, facility, admin_user, monkeypatch):
    event, proposal = approved
    changed = _change_period_before_the_locked_decision(monkeypatch, facility, admin_user, 90)
    _run(facility)
    assert changed == [90]
    assert not Event.objects.get(pk=event.pk).is_deleted
    _assert_reset(proposal)
    # The new date follows the same, current period: anchor 45 days ago + 90 days.
    assert proposal.deletion_due_at == timezone.localdate(_old() + timedelta(days=90))


def test_period_shortened_during_the_run_deletes_only_what_it_covers(approved, facility, admin_user, monkeypatch):
    event, proposal = approved
    changed = _change_period_before_the_locked_decision(monkeypatch, facility, admin_user, 20)
    _run(facility)
    assert changed == [20]
    assert Event.objects.get(pk=event.pk).is_deleted
    assert not _reset_rows(proposal).exists()


def test_reset_sweep_reads_the_current_period(approved, facility, qualified_period):
    from core.retention.proposals import reset_approvals_with_moved_deadline

    event, proposal = approved
    Settings.objects.filter(pk=qualified_period.pk).update(retention_qualified_days=90)
    stale = qualified_period  # still holds 30 days in memory
    assert reset_approvals_with_moved_deadline(facility, stale, timezone.now()) == 1
    assert not Event.objects.get(pk=event.pk).is_deleted
    _assert_reset(proposal)


def test_hold_on_a_final_approval_clears_it_for_good(approved, facility, lead_user, admin_user):
    from core.retention.legal_holds import create_legal_hold, dismiss_legal_hold

    event, proposal = approved
    # The earlier approvals were given before the current deadline.
    RetentionProposal.objects.filter(pk=proposal.pk).update(
        first_approved_at=timezone.now() - timedelta(days=21),
        approved_at=timezone.now() - timedelta(days=20),
    )
    proposal.refresh_from_db()
    hold = create_legal_hold(proposal, lead_user, "Court order")
    proposal.refresh_from_db()
    assert proposal.status == RetentionProposal.Status.HELD
    assert (proposal.approved_by_id, proposal.approved_at, proposal.auto_approved_at) == (None, None, None)
    assert (proposal.first_approved_by_id, proposal.first_approved_at) == (None, None)
    dismiss_legal_hold(hold, lead_user)
    proposal.refresh_from_db()
    assert proposal.status == RetentionProposal.Status.PENDING
    # A new first approval given while the event is due survives the next run.
    approve_proposal(proposal, lead_user)
    _run(facility)
    proposal.refresh_from_db()
    assert proposal.first_approved_by_id == lead_user.pk
    assert not _reset_rows(proposal).exists()
    assert not Event.objects.get(pk=event.pk).is_deleted
