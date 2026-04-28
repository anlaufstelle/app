"""Tests for retention proposals, legal holds, dashboard views, and enforce_retention --propose."""

from datetime import date, timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, Event, LegalHold, RetentionProposal, Settings
from core.services.retention import (
    approve_proposal,
    cleanup_stale_proposals,
    create_legal_hold,
    create_proposal,
    dismiss_legal_hold,
    get_active_hold_target_ids,
    has_active_hold,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings_obj(facility):
    return Settings.objects.create(
        facility=facility,
        retention_anonymous_days=90,
        retention_identified_days=365,
        retention_qualified_days=3650,
    )


@pytest.fixture
def old_anonymous_event(facility, doc_type_contact, staff_user):
    """Anonymous event older than 90 days."""
    return Event.objects.create(
        facility=facility,
        document_type=doc_type_contact,
        occurred_at=timezone.now() - timedelta(days=100),
        data_json={"dauer": 5},
        is_anonymous=True,
        created_by=staff_user,
    )


@pytest.fixture
def proposal(facility, old_anonymous_event):
    return RetentionProposal.objects.create(
        facility=facility,
        target_type=RetentionProposal.TargetType.EVENT,
        target_id=old_anonymous_event.pk,
        deletion_due_at=date.today() + timedelta(days=10),
        status=RetentionProposal.Status.PENDING,
        details={"pseudonym": None, "document_type": "Kontakt"},
        retention_category="anonymous",
    )


# ---------------------------------------------------------------------------
# Model Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRetentionProposalModel:
    def test_unique_active_proposal(self, facility, old_anonymous_event):
        """Cannot create two active proposals for the same target."""
        RetentionProposal.objects.create(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=old_anonymous_event.pk,
            deletion_due_at=date.today(),
            status=RetentionProposal.Status.PENDING,
            retention_category="anonymous",
        )
        with pytest.raises(IntegrityError):
            RetentionProposal.objects.create(
                facility=facility,
                target_type=RetentionProposal.TargetType.EVENT,
                target_id=old_anonymous_event.pk,
                deletion_due_at=date.today(),
                status=RetentionProposal.Status.HELD,
                retention_category="anonymous",
            )

    def test_approved_allows_new_pending(self, facility, old_anonymous_event):
        """An approved proposal does not block a new pending one."""
        RetentionProposal.objects.create(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=old_anonymous_event.pk,
            deletion_due_at=date.today(),
            status=RetentionProposal.Status.APPROVED,
            retention_category="anonymous",
        )
        p2 = RetentionProposal.objects.create(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=old_anonymous_event.pk,
            deletion_due_at=date.today(),
            status=RetentionProposal.Status.PENDING,
            retention_category="anonymous",
        )
        assert p2.pk is not None


@pytest.mark.django_db
class TestLegalHoldModel:
    def test_is_active_true(self, facility, lead_user, old_anonymous_event):
        hold = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=old_anonymous_event.pk,
            reason="Test",
            created_by=lead_user,
        )
        assert hold.is_active is True

    def test_is_active_dismissed(self, facility, lead_user, old_anonymous_event):
        hold = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=old_anonymous_event.pk,
            reason="Test",
            created_by=lead_user,
            dismissed_at=timezone.now(),
            dismissed_by=lead_user,
        )
        assert hold.is_active is False

    def test_is_active_expired(self, facility, lead_user, old_anonymous_event):
        hold = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=old_anonymous_event.pk,
            reason="Test",
            expires_at=date.today() - timedelta(days=1),
            created_by=lead_user,
        )
        assert hold.is_active is False

    def test_is_active_future_expiry(self, facility, lead_user, old_anonymous_event):
        hold = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=old_anonymous_event.pk,
            reason="Test",
            expires_at=date.today() + timedelta(days=30),
            created_by=lead_user,
        )
        assert hold.is_active is True


# ---------------------------------------------------------------------------
# Service Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateProposal:
    def test_creates_new_proposal(self, facility, old_anonymous_event):
        p, created = create_proposal(
            facility=facility,
            target_type="Event",
            target_id=old_anonymous_event.pk,
            deletion_due_at=date.today(),
            details={"test": True},
            category="anonymous",
        )
        assert created is True
        assert p.status == RetentionProposal.Status.PENDING

    def test_idempotent(self, facility, old_anonymous_event):
        p1, c1 = create_proposal(
            facility=facility,
            target_type="Event",
            target_id=old_anonymous_event.pk,
            deletion_due_at=date.today(),
            details={},
            category="anonymous",
        )
        p2, c2 = create_proposal(
            facility=facility,
            target_type="Event",
            target_id=old_anonymous_event.pk,
            deletion_due_at=date.today(),
            details={},
            category="anonymous",
        )
        assert c1 is True
        assert c2 is False
        assert p1.pk == p2.pk

    def test_idempotent_with_deferred_proposal(self, facility, old_anonymous_event):
        """DEFERRED ist laut Model-Constraint aktiv. create_proposal muss den
        bestehenden DEFERRED-Proposal erkennen, nicht in IntegrityError laufen
        oder einen zweiten anlegen (Refs #585)."""
        p1, _ = create_proposal(
            facility=facility,
            target_type="Event",
            target_id=old_anonymous_event.pk,
            deletion_due_at=date.today(),
            details={},
            category="anonymous",
        )
        p1.status = RetentionProposal.Status.DEFERRED
        p1.save(update_fields=["status"])

        p2, created = create_proposal(
            facility=facility,
            target_type="Event",
            target_id=old_anonymous_event.pk,
            deletion_due_at=date.today(),
            details={},
            category="anonymous",
        )
        assert created is False
        assert p2.pk == p1.pk
        assert p2.status == RetentionProposal.Status.DEFERRED


@pytest.mark.django_db
class TestApproveProposal:
    def test_sets_status_approved(self, proposal, lead_user):
        approve_proposal(proposal, lead_user)
        proposal.refresh_from_db()
        assert proposal.status == RetentionProposal.Status.APPROVED

    def test_creates_audit_log(self, proposal, lead_user):
        approve_proposal(proposal, lead_user)
        log = AuditLog.objects.filter(
            action=AuditLog.Action.DELETE,
            target_id=str(proposal.target_id),
        ).first()
        assert log is not None
        assert log.detail["category"] == "retention_proposal_approved"
        assert log.user == lead_user


@pytest.mark.django_db
class TestCreateLegalHold:
    def test_creates_hold_and_sets_proposal_held(self, proposal, lead_user):
        hold = create_legal_hold(proposal, lead_user, "Gerichtsverfahren")
        assert hold.pk is not None
        assert hold.reason == "Gerichtsverfahren"
        assert hold.is_active is True
        proposal.refresh_from_db()
        assert proposal.status == RetentionProposal.Status.HELD

    def test_creates_audit_log(self, proposal, lead_user):
        create_legal_hold(proposal, lead_user, "Test")
        log = AuditLog.objects.filter(
            action=AuditLog.Action.LEGAL_HOLD,
            target_id=str(proposal.target_id),
        ).first()
        assert log is not None
        assert log.detail["category"] == "legal_hold_created"

    def test_with_expires_at(self, proposal, lead_user):
        expires = date.today() + timedelta(days=90)
        hold = create_legal_hold(proposal, lead_user, "Test", expires_at=expires)
        assert hold.expires_at == expires


@pytest.mark.django_db
class TestDismissLegalHold:
    def test_dismisses_hold(self, proposal, lead_user):
        hold = create_legal_hold(proposal, lead_user, "Grund")
        dismiss_legal_hold(hold, lead_user)
        hold.refresh_from_db()
        assert hold.dismissed_at is not None
        assert hold.dismissed_by == lead_user
        assert hold.is_active is False

    def test_reverts_proposal_to_pending(self, proposal, lead_user):
        hold = create_legal_hold(proposal, lead_user, "Grund")
        dismiss_legal_hold(hold, lead_user)
        proposal.refresh_from_db()
        assert proposal.status == RetentionProposal.Status.PENDING

    def test_creates_audit_log(self, proposal, lead_user):
        hold = create_legal_hold(proposal, lead_user, "Grund")
        dismiss_legal_hold(hold, lead_user)
        logs = AuditLog.objects.filter(
            action=AuditLog.Action.LEGAL_HOLD,
            detail__category="legal_hold_dismissed",
        )
        assert logs.count() == 1


@pytest.mark.django_db
class TestHasActiveHold:
    def test_no_hold(self, facility, old_anonymous_event):
        assert has_active_hold(facility, "Event", old_anonymous_event.pk) is False

    def test_active_hold(self, facility, lead_user, old_anonymous_event):
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=old_anonymous_event.pk,
            reason="Test",
            created_by=lead_user,
        )
        assert has_active_hold(facility, "Event", old_anonymous_event.pk) is True

    def test_dismissed_hold(self, facility, lead_user, old_anonymous_event):
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=old_anonymous_event.pk,
            reason="Test",
            created_by=lead_user,
            dismissed_at=timezone.now(),
        )
        assert has_active_hold(facility, "Event", old_anonymous_event.pk) is False


@pytest.mark.django_db
class TestGetActiveHoldTargetIds:
    def test_returns_held_ids(self, facility, lead_user, old_anonymous_event):
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=old_anonymous_event.pk,
            reason="Test",
            created_by=lead_user,
        )
        ids = get_active_hold_target_ids(facility, "Event")
        assert old_anonymous_event.pk in ids

    def test_excludes_expired(self, facility, lead_user, old_anonymous_event):
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=old_anonymous_event.pk,
            reason="Test",
            expires_at=date.today() - timedelta(days=1),
            created_by=lead_user,
        )
        ids = get_active_hold_target_ids(facility, "Event")
        assert old_anonymous_event.pk not in ids


@pytest.mark.django_db
class TestCleanupStaleProposals:
    def test_removes_proposals_for_deleted_events(self, facility, proposal, old_anonymous_event):
        old_anonymous_event.is_deleted = True
        old_anonymous_event.save()
        removed = cleanup_stale_proposals(facility)
        assert removed == 1
        assert not RetentionProposal.objects.filter(pk=proposal.pk).exists()

    def test_keeps_active_proposals(self, facility, proposal):
        removed = cleanup_stale_proposals(facility)
        assert removed == 0
        assert RetentionProposal.objects.filter(pk=proposal.pk).exists()


# ---------------------------------------------------------------------------
# Command Tests (--propose + LegalHold filter)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEnforceRetentionPropose:
    def test_propose_creates_proposals(self, facility, settings_obj, old_anonymous_event):
        out = StringIO()
        call_command("enforce_retention", "--propose", stdout=out)
        assert RetentionProposal.objects.filter(facility=facility).count() >= 1
        p = RetentionProposal.objects.get(target_id=old_anonymous_event.pk)
        assert p.retention_category == "anonymous"
        assert p.status == RetentionProposal.Status.PENDING

    def test_propose_does_not_delete_events(self, facility, settings_obj, old_anonymous_event):
        call_command("enforce_retention", "--propose", stdout=StringIO())
        old_anonymous_event.refresh_from_db()
        assert old_anonymous_event.is_deleted is False

    def test_propose_idempotent(self, facility, settings_obj, old_anonymous_event):
        call_command("enforce_retention", "--propose", stdout=StringIO())
        call_command("enforce_retention", "--propose", stdout=StringIO())
        assert (
            RetentionProposal.objects.filter(
                facility=facility,
                target_id=old_anonymous_event.pk,
            ).count()
            == 1
        )

    def test_dry_run_and_propose_exclusive(self, facility, settings_obj):
        err = StringIO()
        call_command("enforce_retention", "--dry-run", "--propose", stderr=err)
        assert "mutually exclusive" in err.getvalue()


@pytest.mark.django_db
class TestEnforceRetentionLegalHoldFilter:
    def test_hold_prevents_deletion(self, facility, settings_obj, old_anonymous_event, lead_user):
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=old_anonymous_event.pk,
            reason="Court order",
            created_by=lead_user,
        )
        call_command("enforce_retention", stdout=StringIO())
        old_anonymous_event.refresh_from_db()
        assert old_anonymous_event.is_deleted is False

    def test_no_hold_allows_deletion(self, facility, settings_obj, old_anonymous_event):
        call_command("enforce_retention", stdout=StringIO())
        old_anonymous_event.refresh_from_db()
        assert old_anonymous_event.is_deleted is True


# ---------------------------------------------------------------------------
# View Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRetentionDashboardView:
    def test_lead_can_access(self, client, lead_user, facility, proposal):
        client.force_login(lead_user)
        resp = client.get(reverse("core:retention_dashboard"))
        assert resp.status_code == 200

    def test_staff_cannot_access(self, client, staff_user, facility, proposal):
        client.force_login(staff_user)
        resp = client.get(reverse("core:retention_dashboard"))
        assert resp.status_code == 403

    def test_assistant_cannot_access(self, client, assistant_user, facility, proposal):
        client.force_login(assistant_user)
        resp = client.get(reverse("core:retention_dashboard"))
        assert resp.status_code == 403

    def test_admin_can_access(self, client, admin_user, facility, proposal):
        client.force_login(admin_user)
        resp = client.get(reverse("core:retention_dashboard"))
        assert resp.status_code == 200

    def test_summary_cards_are_anchor_links(self, client, lead_user, facility, proposal):
        """Issue #516: summary cards must be clickable anchor links pointing
        to the proposals section."""
        client.force_login(lead_user)
        resp = client.get(reverse("core:retention_dashboard"))
        content = resp.content.decode()
        # The proposals container has the anchor target id
        assert 'id="retention-proposals"' in content
        # All three summary cards link to it
        assert 'href="#retention-proposals"' in content
        assert 'id="summary-pending"' in content
        assert 'id="summary-held"' in content
        assert 'id="summary-approved"' in content

    def test_category_sections_have_ids(self, client, lead_user, facility, proposal):
        """Each category section gets an id so it can be linked to from elsewhere."""
        client.force_login(lead_user)
        resp = client.get(reverse("core:retention_dashboard"))
        content = resp.content.decode()
        # The proposal fixture lives under one of the stage-based categories
        assert "retention-category-" in content


@pytest.mark.django_db
class TestRetentionApproveView:
    def test_approve_changes_status(self, client, lead_user, proposal):
        client.force_login(lead_user)
        resp = client.post(reverse("core:retention_approve", kwargs={"pk": proposal.pk}))
        assert resp.status_code == 200
        proposal.refresh_from_db()
        assert proposal.status == RetentionProposal.Status.APPROVED

    def test_staff_cannot_approve(self, client, staff_user, proposal):
        client.force_login(staff_user)
        resp = client.post(reverse("core:retention_approve", kwargs={"pk": proposal.pk}))
        assert resp.status_code == 403

    def test_creates_audit_log(self, client, lead_user, proposal):
        client.force_login(lead_user)
        client.post(reverse("core:retention_approve", kwargs={"pk": proposal.pk}))
        log = AuditLog.objects.filter(
            action=AuditLog.Action.DELETE,
            detail__category="retention_proposal_approved",
        ).first()
        assert log is not None


@pytest.mark.django_db
class TestRetentionHoldView:
    def test_creates_hold(self, client, lead_user, proposal):
        client.force_login(lead_user)
        resp = client.post(
            reverse("core:retention_hold", kwargs={"pk": proposal.pk}),
            {"reason": "Gerichtsverfahren"},
        )
        assert resp.status_code == 200
        proposal.refresh_from_db()
        assert proposal.status == RetentionProposal.Status.HELD
        assert LegalHold.objects.filter(target_id=proposal.target_id).count() == 1

    def test_requires_reason(self, client, lead_user, proposal):
        client.force_login(lead_user)
        resp = client.post(
            reverse("core:retention_hold", kwargs={"pk": proposal.pk}),
            {"reason": ""},
        )
        assert resp.status_code == 400

    def test_with_expires_at(self, client, lead_user, proposal):
        client.force_login(lead_user)
        expires = (date.today() + timedelta(days=90)).isoformat()
        resp = client.post(
            reverse("core:retention_hold", kwargs={"pk": proposal.pk}),
            {"reason": "Test", "expires_at": expires},
        )
        assert resp.status_code == 200
        hold = LegalHold.objects.filter(target_id=proposal.target_id).first()
        assert hold.expires_at == date.today() + timedelta(days=90)


@pytest.mark.django_db
class TestRetentionDismissHoldView:
    def test_dismisses_hold(self, client, lead_user, proposal):
        hold = create_legal_hold(proposal, lead_user, "Grund")
        client.force_login(lead_user)
        resp = client.post(reverse("core:retention_dismiss_hold", kwargs={"pk": hold.pk}))
        assert resp.status_code == 200
        hold.refresh_from_db()
        assert hold.dismissed_at is not None
        proposal.refresh_from_db()
        assert proposal.status == RetentionProposal.Status.PENDING


@pytest.fixture
def two_pending_proposals(facility, doc_type_contact, staff_user):
    events = []
    proposals = []
    for i in range(2):
        ev = Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now() - timedelta(days=200 + i),
            data_json={"dauer": 5},
            is_anonymous=True,
            created_by=staff_user,
        )
        events.append(ev)
        proposals.append(
            RetentionProposal.objects.create(
                facility=facility,
                target_type=RetentionProposal.TargetType.EVENT,
                target_id=ev.pk,
                deletion_due_at=date.today() + timedelta(days=5 + i),
                status=RetentionProposal.Status.PENDING,
                details={"pseudonym": None, "document_type": "Kontakt"},
                retention_category="anonymous",
            )
        )
    return proposals


@pytest.mark.django_db
class TestRetentionBulkViews:
    def test_bulk_approve_sets_all_selected_to_approved(self, client, lead_user, two_pending_proposals):
        client.force_login(lead_user)
        resp = client.post(
            reverse("core:retention_bulk_approve"),
            {"proposal_ids": [str(p.pk) for p in two_pending_proposals]},
            headers={"HX-Request": "true"},
        )
        # HTMX path returns redirect with HX-Redirect header
        assert resp.status_code == 302
        assert "HX-Redirect" in resp.headers
        for p in two_pending_proposals:
            p.refresh_from_db()
            assert p.status == RetentionProposal.Status.APPROVED

    def test_bulk_defer_respects_days_param(self, client, lead_user, two_pending_proposals):
        client.force_login(lead_user)
        resp = client.post(
            reverse("core:retention_bulk_defer"),
            {"proposal_ids": [str(p.pk) for p in two_pending_proposals], "days": "14"},
        )
        assert resp.status_code == 302
        target = date.today() + timedelta(days=14)
        for p in two_pending_proposals:
            p.refresh_from_db()
            assert p.status == RetentionProposal.Status.DEFERRED
            assert p.deferred_until == target

    def test_bulk_reject_sets_all_to_rejected(self, client, lead_user, two_pending_proposals):
        client.force_login(lead_user)
        resp = client.post(
            reverse("core:retention_bulk_reject"),
            {"proposal_ids": [str(p.pk) for p in two_pending_proposals]},
        )
        assert resp.status_code == 302
        for p in two_pending_proposals:
            p.refresh_from_db()
            assert p.status == RetentionProposal.Status.REJECTED

    def test_bulk_without_ids_returns_400(self, client, lead_user):
        client.force_login(lead_user)
        resp = client.post(reverse("core:retention_bulk_approve"), {})
        assert resp.status_code == 400

    def test_bulk_forbids_staff(self, client, staff_user, two_pending_proposals):
        client.force_login(staff_user)
        resp = client.post(
            reverse("core:retention_bulk_approve"),
            {"proposal_ids": [str(two_pending_proposals[0].pk)]},
        )
        # LeadOrAdminRequiredMixin denies staff-only users
        assert resp.status_code in (302, 403)
        two_pending_proposals[0].refresh_from_db()
        assert two_pending_proposals[0].status == RetentionProposal.Status.PENDING

    def test_bulk_scopes_by_facility(self, client, lead_user, two_pending_proposals, organization):
        """Proposal IDs from another facility must not be touched."""
        from core.models import Facility

        other_facility = Facility.objects.create(organization=organization, name="Andere")
        other = RetentionProposal.objects.create(
            facility=other_facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=two_pending_proposals[0].target_id,
            deletion_due_at=date.today() + timedelta(days=5),
            status=RetentionProposal.Status.PENDING,
            details={},
            retention_category="anonymous",
        )
        client.force_login(lead_user)
        client.post(
            reverse("core:retention_bulk_approve"),
            {"proposal_ids": [str(other.pk)]},
        )
        other.refresh_from_db()
        assert other.status == RetentionProposal.Status.PENDING
