"""Retention deadlines and approval through public services (Refs #2524)."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from core.models import Activity, Case, Client, Event, RetentionProposal, WorkItem
from core.retention.anonymization import anonymize_clients
from core.retention.enforcement import collect_doomed_events, process_facility_retention
from core.retention.proposals import (
    approve_proposal,
    create_proposal,
    create_proposals_for_facility,
    defer_proposal,
    reject_proposal,
)
from tests._retention_approval_helpers import prime_first_approval

pytestmark = pytest.mark.django_db


@pytest.fixture
def clock():
    return timezone.now()


@pytest.fixture
def periods(settings_obj):
    settings_obj.retention_anonymous_days = 10
    settings_obj.retention_identified_days = 20
    settings_obj.retention_qualified_days = 30
    settings_obj.save()
    return settings_obj


@pytest.fixture
def make_event(facility, doc_type_contact, client_qualified, clock):
    def create(*, case_status=None, age=45, client=client_qualified, anonymous=False):
        old = clock - timedelta(days=age)
        with patch("django.utils.timezone.now", return_value=old):
            case = None
            if case_status:
                case = Case.objects.create(
                    facility=facility,
                    client=client,
                    title="Retention case",
                    status=case_status,
                    closed_at=old if case_status == Case.Status.CLOSED else None,
                )
            return Event.objects.create(
                facility=facility,
                client=client,
                case=case,
                document_type=doc_type_contact,
                occurred_at=old,
                is_anonymous=anonymous,
                data_json={"duration": 20},
            )

    return create


@pytest.mark.parametrize("case_status", [None, Case.Status.OPEN, Case.Status.CLOSED])
def test_standard_run_proposes_qualified_then_deletes_after_approval(
    make_event, facility, periods, clock, lead_user, case_status
):
    event = make_event(case_status=case_status)
    assert process_facility_retention(facility, periods, clock, dry_run=False)["count"] == 0
    event.refresh_from_db()
    assert not event.is_deleted
    assert event.data_json
    proposal = RetentionProposal.objects.get(facility=facility, target_id=event.pk)
    assert proposal.status == RetentionProposal.Status.PENDING
    prime_first_approval(proposal)
    approve_proposal(proposal, lead_user)
    assert process_facility_retention(facility, periods, clock, dry_run=False)["count"] == 1
    event.refresh_from_db()
    assert event.is_deleted
    assert event.data_json == {}


def test_type_deadline_cannot_bypass_qualified_approval(make_event, facility, periods, clock, doc_type_contact):
    doc_type_contact.retention_days = 5
    doc_type_contact.save()
    event = make_event(age=10)
    process_facility_retention(facility, periods, clock, dry_run=False)
    event.refresh_from_db()
    assert not event.is_deleted
    proposal = RetentionProposal.objects.get(facility=facility, target_id=event.pk)
    assert proposal.deletion_due_at == timezone.localdate(event.occurred_at + timedelta(days=5))


@pytest.mark.parametrize("kind", ["anonymous", "identified", "qualified", "document_type"])
def test_proposal_stores_actual_deadline(
    make_event, facility, periods, clock, client_identified, doc_type_contact, kind
):
    client = client_identified if kind in {"identified", "document_type"} else None
    if kind == "qualified":
        event = make_event(case_status=Case.Status.CLOSED)
        days = 30
    else:
        if kind == "document_type":
            doc_type_contact.retention_days = 5
            doc_type_contact.save()
        event = make_event(client=client, anonymous=kind == "anonymous")
        days = {"anonymous": 10, "identified": 20, "document_type": 5}[kind]
    assert create_proposals_for_facility(facility, periods, clock)["count"] == 1
    proposal = RetentionProposal.objects.get(facility=facility, target_id=event.pk)
    assert proposal.deletion_due_at == timezone.localdate(event.occurred_at + timedelta(days=days))
    assert proposal.retention_category == kind


def test_rejected_proposal_returns_after_365_days(make_event, facility, periods, clock, lead_user):
    event = make_event(case_status=Case.Status.CLOSED)
    create_proposals_for_facility(facility, periods, clock)
    proposal = RetentionProposal.objects.get(facility=facility, target_id=event.pk)
    with patch("django.utils.timezone.now", return_value=clock):
        reject_proposal(proposal, lead_user)
    for days in (0, 364):
        later = clock + timedelta(days=days)
        with patch("django.utils.timezone.now", return_value=later):
            process_facility_retention(facility, periods, later, dry_run=False)
        event.refresh_from_db()
        assert not event.is_deleted
        assert RetentionProposal.objects.filter(facility=facility, target_id=event.pk).count() == 1
    later = clock + timedelta(days=365)
    with patch("django.utils.timezone.now", return_value=later):
        process_facility_retention(facility, periods, later, dry_run=False)
    assert (
        RetentionProposal.objects.filter(
            facility=facility, target_id=event.pk, status=RetentionProposal.Status.PENDING
        ).count()
        == 1
    )
    event.refresh_from_db()
    assert not event.is_deleted


def test_deferred_proposal_protects_event(make_event, facility, periods, clock, lead_user):
    event = make_event(case_status=Case.Status.CLOSED)
    create_proposals_for_facility(facility, periods, clock)
    proposal = RetentionProposal.objects.get(facility=facility, target_id=event.pk)
    defer_proposal(proposal, lead_user, days=30)
    process_facility_retention(facility, periods, clock, dry_run=False)
    event.refresh_from_db()
    proposal.refresh_from_db()
    assert not event.is_deleted
    assert proposal.status == RetentionProposal.Status.DEFERRED


def test_approved_proposal_is_not_proposed_again(make_event, facility, periods, clock, lead_user):
    event = make_event(case_status=Case.Status.CLOSED)
    create_proposals_for_facility(facility, periods, clock)
    proposal = RetentionProposal.objects.get(facility=facility, target_id=event.pk)
    prime_first_approval(proposal)
    approve_proposal(proposal, lead_user)
    assert create_proposals_for_facility(facility, periods, clock)["count"] == 0
    assert RetentionProposal.objects.filter(facility=facility, target_id=event.pk).count() == 1


def test_approval_requires_confirmation_permission(make_event, facility, periods, clock, lead_user):
    event = make_event(case_status=Case.Status.CLOSED)
    create_proposals_for_facility(facility, periods, clock)
    proposal = RetentionProposal.objects.get(facility=facility, target_id=event.pk)
    lead_user.can_confirm_deletion = False
    lead_user.save()
    with pytest.raises(ValidationError):
        approve_proposal(proposal, lead_user)
    proposal.refresh_from_db()
    assert proposal.status == RetentionProposal.Status.PENDING


def test_proposer_cannot_approve_own_proposal(make_event, facility, periods, clock, lead_user):
    event = make_event(case_status=Case.Status.CLOSED)
    proposal, _ = create_proposal(facility, "Event", event.pk, clock.date(), {}, "qualified", user=lead_user)
    with pytest.raises(ValidationError):
        approve_proposal(proposal, lead_user)
    proposal.refresh_from_db()
    assert proposal.status == RetentionProposal.Status.PENDING


@pytest.mark.parametrize("stage", [Client.ContactStage.IDENTIFIED, Client.ContactStage.QUALIFIED])
def test_inactive_eventless_client_requires_identified_stage(facility, periods, clock, stage):
    client = Client.objects.create(facility=facility, pseudonym="Inactive", contact_stage=stage)
    Client.objects.filter(facility=facility, pk=client.pk).update(updated_at=clock - timedelta(days=21))
    expected_count = int(stage == Client.ContactStage.IDENTIFIED)
    with patch("django.utils.timezone.now", return_value=clock):
        assert anonymize_clients(facility, dry_run=False)["count"] == expected_count
    client.refresh_from_db()
    assert (client.anonymized_at is not None) == bool(expected_count)


@pytest.mark.parametrize("source", ["case", "event", "task", "activity"])
def test_business_activity_restarts_open_case_deadline(make_event, facility, periods, clock, source):
    event = make_event(case_status=Case.Status.OPEN)
    assert collect_doomed_events(facility, periods, clock).filter(pk=event.pk).exists()
    with patch("django.utils.timezone.now", return_value=clock):
        if source == "case":
            event.case.title = "Changed case"
            event.case.save(update_fields=["title", "updated_at"])
        elif source == "event":
            event.data_json = {"duration": 30}
            event.save(update_fields=["data_json", "updated_at"])
        elif source == "task":
            WorkItem.objects.create(facility=facility, client=event.client, title="New task")
        else:
            Activity.objects.create(
                facility=facility,
                target_type=ContentType.objects.get_for_model(Case),
                target_id=event.case_id,
                verb=Activity.Verb.UPDATED,
                occurred_at=clock,
            )
    assert not collect_doomed_events(facility, periods, clock).filter(pk=event.pk).exists()


def test_closed_case_without_closing_timestamp_has_deadline(make_event, facility, periods, clock):
    event = make_event(case_status=Case.Status.CLOSED)
    Case.objects.filter(facility=facility, pk=event.case_id).update(closed_at=None)
    assert create_proposals_for_facility(facility, periods, clock)["count"] == 1


def test_dormant_open_case_notice(client, lead_user, make_event, periods, clock):
    event = make_event(case_status=Case.Status.OPEN, age=400)
    client.force_login(lead_user)
    response = client.get(reverse("core:retention_dashboard"))
    assert response.status_code == 200
    assert "Fall seit 400 Tagen ohne Aktivität" in response.content.decode()
    assert str(event.case_id) in response.content.decode()


def test_document_type_form_explains_maximum(facility, doc_type_contact):
    from core.forms.config import DocumentTypeForm

    form = DocumentTypeForm(facility=facility, instance=doc_type_contact)
    assert str(form.fields["retention_days"].label) == "Höchstfrist (verkürzt nur)"
    assert "verkürzt" in str(form.fields["retention_days"].help_text)


@pytest.mark.parametrize("enabled", [False, True])
def test_auto_approval_is_opt_in(make_event, facility, periods, clock, lead_user, enabled):
    event = make_event(case_status=Case.Status.CLOSED)
    create_proposals_for_facility(facility, periods, clock)
    proposal = RetentionProposal.objects.get(facility=facility, target_id=event.pk)
    with patch("django.utils.timezone.now", return_value=clock - timedelta(days=2)):
        defer_proposal(proposal, lead_user, days=1)
    periods.retention_max_defer_count = 1
    periods.retention_auto_approve_after_defer = enabled
    periods.save()
    with patch("django.utils.timezone.now", return_value=clock):
        call_command("enforce_retention", facility=facility.name, verbosity=0)
    event.refresh_from_db()
    assert event.is_deleted is enabled


@pytest.mark.parametrize("kind,days", [("anonymous", 10), ("identified", 20), ("qualified", 30), ("document_type", 5)])
@pytest.mark.parametrize("overdue", [False, True])
def test_strict_deadline_boundary_and_path_parity(
    make_event, facility, periods, clock, client_identified, doc_type_contact, kind, days, overdue
):
    if kind == "document_type":
        doc_type_contact.retention_days = days
        doc_type_contact.save()
    if kind == "qualified":
        event = make_event(age=days)
    else:
        event = make_event(
            age=days, client=None if kind == "anonymous" else client_identified, anonymous=kind == "anonymous"
        )
    run_at = clock + timedelta(seconds=1 if overdue else 0)
    assert collect_doomed_events(facility, periods, run_at).filter(pk=event.pk).exists() is overdue
    assert create_proposals_for_facility(facility, periods, run_at)["count"] == int(overdue)
    assert process_facility_retention(facility, periods, run_at, dry_run=True)["count"] == int(overdue)
    process_facility_retention(facility, periods, run_at, dry_run=False)
    event.refresh_from_db()
    assert event.is_deleted is (overdue and kind != "qualified")


def test_clientless_event_has_anonymous_fallback(make_event, facility, periods, clock):
    event = make_event(client=None, anonymous=False)
    assert collect_doomed_events(facility, periods, clock).filter(pk=event.pk).exists()
    assert create_proposals_for_facility(facility, periods, clock)["count"] == 1
    process_facility_retention(facility, periods, clock, dry_run=False)
    event.refresh_from_db()
    assert event.is_deleted


def test_rotation_does_not_restart_case_deadline(make_event, facility, periods, clock):
    from core.services.file_vault import rotation

    event = make_event(case_status=Case.Status.OPEN)
    with patch.object(rotation, "_reencrypt_data_json", return_value=({"rotated": "ciphertext"}, True)):
        rotation.rotate_all(dry_run=False)
    event.case.refresh_from_db()
    assert event.case.updated_at >= clock
    assert create_proposals_for_facility(facility, periods, clock)["count"] == 1


def test_inactive_client_boundary_is_strict(facility, periods, clock):
    client = Client.objects.create(facility=facility, pseudonym="Boundary")
    Client.objects.filter(facility=facility, pk=client.pk).update(updated_at=clock - timedelta(days=20))
    with patch("django.utils.timezone.now", return_value=clock):
        assert anonymize_clients(facility, dry_run=False)["count"] == 0
    with patch("django.utils.timezone.now", return_value=clock + timedelta(seconds=1)):
        assert anonymize_clients(facility, dry_run=False)["count"] == 1


@pytest.mark.parametrize("target", ["Client", "Case"])
def test_proposal_and_preview_share_parent_hold_exclusions(make_event, facility, periods, clock, lead_user, target):
    from core.retention.legal_holds import create_hold_for_target

    event = make_event(case_status=Case.Status.CLOSED)
    create_hold_for_target(
        facility,
        target_type=target,
        target_id=event.client_id if target == "Client" else event.case_id,
        reason="Proceedings",
        user=lead_user,
    )
    assert not collect_doomed_events(facility, periods, clock).exists()
    assert create_proposals_for_facility(facility, periods, clock)["count"] == 0


def test_configured_rejection_wait_starts_at_rejection(make_event, facility, periods, clock, lead_user):
    event = make_event(case_status=Case.Status.CLOSED)
    create_proposals_for_facility(facility, periods, clock)
    proposal = RetentionProposal.objects.get(facility=facility, target_id=event.pk)
    rejected_at = clock + timedelta(days=10)
    with patch("django.utils.timezone.now", return_value=rejected_at):
        reject_proposal(proposal, lead_user)
    periods.retention_rejected_retry_days = 2
    assert create_proposals_for_facility(facility, periods, rejected_at + timedelta(days=1))["count"] == 0
    assert create_proposals_for_facility(facility, periods, rejected_at + timedelta(days=2))["count"] == 1


def test_dormancy_threshold_is_configurable(client, lead_user, make_event, periods):
    make_event(case_status=Case.Status.OPEN, age=40)
    periods.retention_open_case_inactive_days = 30
    periods.save()
    client.force_login(lead_user)
    response = client.get(reverse("core:retention_dashboard"))
    assert "Fall seit 40 Tagen ohne Aktivität" in response.content.decode()


def test_long_type_period_cannot_extend_stage_period(
    make_event, facility, periods, clock, doc_type_contact, client_identified
):
    doc_type_contact.retention_days = 200
    doc_type_contact.save()
    event = make_event(client=client_identified, age=21)
    create_proposals_for_facility(facility, periods, clock)
    proposal = RetentionProposal.objects.get(facility=facility, target_id=event.pk)
    assert proposal.deletion_due_at == timezone.localdate(event.occurred_at + timedelta(days=20))
    process_facility_retention(facility, periods, clock, dry_run=False)
    event.refresh_from_db()
    assert event.is_deleted


def test_generation_rechecks_business_deadline_under_target_lock(make_event, facility, periods, clock, staff_user):
    from core.retention import proposals
    from core.services.case.cases import update_case

    event = make_event(case_status=Case.Status.OPEN)
    lock_targets = proposals._lock_target_rows_for_approval

    def change_before_lock(proposal):
        update_case(event.case, staff_user, title="Recently active")
        return lock_targets(proposal)

    with patch.object(proposals, "_lock_target_rows_for_approval", side_effect=change_before_lock):
        assert create_proposals_for_facility(facility, periods, clock)["count"] == 0
    assert not RetentionProposal.objects.filter(facility=facility, target_id=event.pk).exists()


def test_case_clock_ignores_timestamp_only_save(make_event, facility, periods, clock):
    event = make_event(case_status=Case.Status.OPEN)
    event.case.save(update_fields=["updated_at"])
    assert create_proposals_for_facility(facility, periods, clock)["count"] == 1


def test_feed_activity_anchor_survives_feed_pruning(make_event, facility, periods, clock):
    event = make_event(case_status=Case.Status.OPEN)
    activity = Activity.objects.create(
        facility=facility,
        target_type=ContentType.objects.get_for_model(Case),
        target_id=event.case_id,
        verb=Activity.Verb.UPDATED,
        occurred_at=clock,
    )
    activity.delete()
    assert not collect_doomed_events(facility, periods, clock).filter(pk=event.pk).exists()
    later = clock + timedelta(days=31)
    assert collect_doomed_events(facility, periods, later).filter(pk=event.pk).exists()


def test_foreign_proposer_is_refused(make_event, facility, clock, second_facility_user):
    event = make_event()
    with pytest.raises(ValidationError):
        create_proposal(facility, "Event", event.pk, clock.date(), {}, "qualified", user=second_facility_user)
    assert not RetentionProposal.objects.filter(facility=facility, target_id=event.pk).exists()


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_decision_returns_persisted_metadata(make_event, facility, periods, clock, lead_user, decision):
    event = make_event(case_status=Case.Status.CLOSED)
    create_proposals_for_facility(facility, periods, clock)
    proposal = RetentionProposal.objects.get(facility=facility, target_id=event.pk)
    if decision == "approve":
        prime_first_approval(proposal)
    with patch("django.utils.timezone.now", return_value=clock):
        result = (
            approve_proposal(proposal, lead_user) if decision == "approve" else reject_proposal(proposal, lead_user)
        )
    if decision == "approve":
        assert result.approved_by_id == lead_user.pk
        assert result.approved_at == clock
    else:
        assert result.rejected_at == clock


@pytest.mark.parametrize("days", [0, -5])
def test_invalid_idle_period_cannot_anonymize_recent_client(facility, periods, clock, days):
    client = Client.objects.create(facility=facility, pseudonym="Recent")
    Client.objects.filter(facility=facility, pk=client.pk).update(updated_at=clock - timedelta(seconds=1))
    periods.retention_identified_days = days
    with patch("django.utils.timezone.now", return_value=clock):
        assert anonymize_clients(facility, dry_run=False)["count"] == 0
    client.refresh_from_db()
    assert client.anonymized_at is None
