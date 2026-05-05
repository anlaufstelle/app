"""Dedicated tests for the four-eyes deletion request workflow."""

from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, DeletionRequest, Event, EventHistory
from core.services.event import (
    approve_deletion,
    reject_deletion,
    request_deletion,
    soft_delete_event,
)

# ---------------------------------------------------------------------------
# Fixtures local to this module
# ---------------------------------------------------------------------------


@pytest.fixture
def event_qualified(facility, client_qualified, doc_type_contact, staff_user):
    """Event linked to a QUALIFIED client (four-eyes required)."""
    return Event.objects.create(
        facility=facility,
        client=client_qualified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={"dauer": 20, "notiz": "Qualifiziert"},
        created_by=staff_user,
    )


@pytest.fixture
def event_identified(facility, client_identified, doc_type_contact, staff_user):
    """Event linked to an IDENTIFIED client (direct deletion)."""
    return Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={"dauer": 10, "notiz": "Identifiziert"},
        created_by=staff_user,
    )


@pytest.fixture
def event_anonymous(facility, doc_type_contact, staff_user):
    """Anonymous event (direct deletion)."""
    return Event.objects.create(
        facility=facility,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={},
        is_anonymous=True,
        created_by=staff_user,
    )


@pytest.fixture
def pending_request(event_qualified, staff_user):
    """A pending DeletionRequest for a qualified event."""
    return request_deletion(event_qualified, staff_user, "DSGVO-Anfrage")


# ---------------------------------------------------------------------------
# 1. Service layer tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRequestDeletion:
    """Tests for request_deletion() service function."""

    def test_creates_deletion_request(self, event_qualified, staff_user):
        dr = request_deletion(event_qualified, staff_user, "DSGVO-Anfrage")
        assert dr.pk is not None
        assert dr.status == DeletionRequest.Status.PENDING
        assert dr.reason == "DSGVO-Anfrage"
        assert dr.requested_by == staff_user
        assert dr.target_type == "Event"
        assert dr.target_id == event_qualified.pk
        assert dr.facility == event_qualified.facility

    def test_event_not_deleted_after_request(self, event_qualified, staff_user):
        """Creating a request must not alter the event itself."""
        request_deletion(event_qualified, staff_user, "Grund")
        event_qualified.refresh_from_db()
        assert event_qualified.is_deleted is False
        assert event_qualified.data_json == {"dauer": 20, "notiz": "Qualifiziert"}

    def test_idempotent_when_pending_request_exists(self, event_qualified, staff_user, lead_user):
        """Issue #530: a second request_deletion() for the same event while a
        PENDING request exists must return the existing record, not create a
        duplicate. Different requesters get the same record."""
        dr1 = request_deletion(event_qualified, staff_user, "Grund 1")
        dr2 = request_deletion(event_qualified, lead_user, "Grund 2")
        assert dr1.pk == dr2.pk
        assert DeletionRequest.objects.filter(target_id=event_qualified.pk).count() == 1
        # The existing record's reason and requester are preserved
        dr1.refresh_from_db()
        assert dr1.reason == "Grund 1"
        assert dr1.requested_by == staff_user

    def test_new_request_allowed_after_previous_was_rejected(self, event_qualified, staff_user, lead_user):
        """When the previous request was REJECTED, a fresh PENDING one is allowed."""
        dr1 = request_deletion(event_qualified, staff_user, "Grund 1")
        reject_deletion(dr1, lead_user)

        dr2 = request_deletion(event_qualified, staff_user, "Grund 2")
        assert dr2.pk != dr1.pk
        assert DeletionRequest.objects.filter(target_id=event_qualified.pk).count() == 2

    def test_db_constraint_prevents_duplicate_pending_requests(self, event_qualified, staff_user):
        """The DB-level UniqueConstraint(condition=Q(status='pending')) is the
        ultimate guard — even a raw .objects.create() that bypasses the
        service must be rejected."""
        from django.db import IntegrityError, transaction

        request_deletion(event_qualified, staff_user, "Grund 1")
        with pytest.raises(IntegrityError), transaction.atomic():
            DeletionRequest.objects.create(
                facility=event_qualified.facility,
                target_type="Event",
                target_id=event_qualified.pk,
                reason="Spoofed",
                requested_by=staff_user,
            )

    def test_reviewed_by_initially_null(self, event_qualified, staff_user):
        dr = request_deletion(event_qualified, staff_user, "Grund")
        assert dr.reviewed_by is None
        assert dr.reviewed_at is None


@pytest.mark.django_db
class TestApproveDeletion:
    """Tests for approve_deletion() service function."""

    def test_approves_and_soft_deletes(self, pending_request, lead_user, event_qualified):
        approve_deletion(pending_request, lead_user)
        pending_request.refresh_from_db()
        event_qualified.refresh_from_db()

        assert pending_request.status == DeletionRequest.Status.APPROVED
        assert pending_request.reviewed_by == lead_user
        assert pending_request.reviewed_at is not None
        assert event_qualified.is_deleted is True
        assert event_qualified.data_json == {}

    def test_creates_event_history_delete(self, pending_request, lead_user, event_qualified):
        approve_deletion(pending_request, lead_user)
        history = EventHistory.objects.filter(
            event=event_qualified,
            action=EventHistory.Action.DELETE,
        ).first()
        assert history is not None
        assert history.changed_by == lead_user

    def test_creates_audit_log(self, pending_request, lead_user, event_qualified):
        approve_deletion(pending_request, lead_user)
        audit = AuditLog.objects.filter(
            action=AuditLog.Action.DELETE,
            target_type="Event",
            target_id=str(event_qualified.pk),
        ).first()
        assert audit is not None
        assert audit.user == lead_user

    def test_atomicity_rolls_back_on_failure(self, pending_request, lead_user, event_qualified):
        """If DeletionRequest.save() fails, the entire transaction rolls back."""
        with patch.object(DeletionRequest, "save", side_effect=RuntimeError("DB error")):
            with pytest.raises(RuntimeError, match="DB error"):
                approve_deletion(pending_request, lead_user)

        event_qualified.refresh_from_db()
        assert event_qualified.is_deleted is False
        pending_request.refresh_from_db()
        assert pending_request.status == DeletionRequest.Status.PENDING

    def test_admin_can_approve(self, pending_request, admin_user, event_qualified):
        """Admin users can approve deletion requests."""
        approve_deletion(pending_request, admin_user)
        pending_request.refresh_from_db()
        assert pending_request.status == DeletionRequest.Status.APPROVED


@pytest.mark.django_db
class TestRejectDeletion:
    """Tests for reject_deletion() service function."""

    def test_rejects_request(self, pending_request, lead_user, event_qualified):
        reject_deletion(pending_request, lead_user)
        pending_request.refresh_from_db()
        event_qualified.refresh_from_db()

        assert pending_request.status == DeletionRequest.Status.REJECTED
        assert pending_request.reviewed_by == lead_user
        assert pending_request.reviewed_at is not None
        # Event must remain untouched
        assert event_qualified.is_deleted is False
        assert event_qualified.data_json == {"dauer": 20, "notiz": "Qualifiziert"}

    def test_no_event_history_on_reject(self, pending_request, lead_user, event_qualified):
        """Rejecting must not create a DELETE history entry."""
        reject_deletion(pending_request, lead_user)
        assert not EventHistory.objects.filter(
            event=event_qualified,
            action=EventHistory.Action.DELETE,
        ).exists()

    def test_no_audit_log_on_reject(self, pending_request, lead_user, event_qualified):
        """Rejecting must not create an AuditLog DELETE entry."""
        reject_deletion(pending_request, lead_user)
        assert not AuditLog.objects.filter(
            action=AuditLog.Action.DELETE,
            target_type="Event",
            target_id=str(event_qualified.pk),
        ).exists()


@pytest.mark.django_db
class TestFourEyesConstraint:
    """The reviewer must differ from the requester (DB constraint)."""

    def test_db_constraint_prevents_self_review(self, event_qualified, lead_user):
        """DB CHECK constraint prevents requester == reviewer."""
        from django.db import IntegrityError

        dr = request_deletion(event_qualified, lead_user, "Selbst-Test")
        dr.status = DeletionRequest.Status.APPROVED
        dr.reviewed_by = lead_user
        dr.reviewed_at = timezone.now()
        with pytest.raises(IntegrityError):
            dr.save()


@pytest.mark.django_db
class TestSoftDeleteEvent:
    """Tests for soft_delete_event() (direct path, no four-eyes)."""

    def test_redacts_data_json(self, event_identified, staff_user):
        soft_delete_event(event_identified, staff_user)
        event_identified.refresh_from_db()
        assert event_identified.is_deleted is True
        assert event_identified.data_json == {}

    def test_history_records_field_names_only(self, event_identified, staff_user):
        soft_delete_event(event_identified, staff_user)
        history = EventHistory.objects.filter(
            event=event_identified,
            action=EventHistory.Action.DELETE,
        ).first()
        assert history is not None
        assert history.data_before["_redacted"] is True
        assert set(history.data_before["fields"]) == {"dauer", "notiz"}

    def test_audit_log_created(self, event_identified, staff_user):
        soft_delete_event(event_identified, staff_user)
        assert AuditLog.objects.filter(
            action=AuditLog.Action.DELETE,
            target_type="Event",
            target_id=str(event_identified.pk),
        ).exists()

    def test_atomicity_on_audit_failure(self, event_identified, staff_user):
        with patch.object(AuditLog.objects, "create", side_effect=RuntimeError("Audit error")):
            with pytest.raises(RuntimeError, match="Audit error"):
                soft_delete_event(event_identified, staff_user)

        event_identified.refresh_from_db()
        assert event_identified.is_deleted is False
        assert not EventHistory.objects.filter(
            event=event_identified,
            action=EventHistory.Action.DELETE,
        ).exists()


# ---------------------------------------------------------------------------
# 2. View layer tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeletionRequestListView:
    """Tests for DeletionRequestListView (Lead/Admin only)."""

    def test_lead_can_access(self, client, lead_user):
        client.force_login(lead_user)
        response = client.get(reverse("core:deletion_request_list"))
        assert response.status_code == 200

    def test_admin_can_access(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:deletion_request_list"))
        assert response.status_code == 200

    def test_staff_forbidden(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:deletion_request_list"))
        assert response.status_code == 403

    def test_assistant_forbidden(self, client, assistant_user):
        client.force_login(assistant_user)
        response = client.get(reverse("core:deletion_request_list"))
        assert response.status_code == 403

    def test_unauthenticated_redirects(self, client):
        response = client.get(reverse("core:deletion_request_list"))
        assert response.status_code == 302  # redirect to login

    def test_shows_pending_requests(self, client, lead_user, pending_request):
        client.force_login(lead_user)
        response = client.get(reverse("core:deletion_request_list"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "DSGVO-Anfrage" in content or pending_request.reason in content


@pytest.mark.django_db
class TestDeletionRequestReviewView:
    """Tests for DeletionRequestReviewView."""

    def test_review_page_renders(self, client, lead_user, pending_request):
        client.force_login(lead_user)
        response = client.get(reverse("core:deletion_review", kwargs={"pk": pending_request.pk}))
        assert response.status_code == 200

    def test_approve_flow(self, client, lead_user, pending_request, event_qualified):
        client.force_login(lead_user)
        response = client.post(
            reverse("core:deletion_review", kwargs={"pk": pending_request.pk}),
            {"action": "approve"},
        )
        assert response.status_code == 302
        pending_request.refresh_from_db()
        event_qualified.refresh_from_db()
        assert pending_request.status == DeletionRequest.Status.APPROVED
        assert event_qualified.is_deleted is True

    def test_reject_flow(self, client, lead_user, pending_request, event_qualified):
        client.force_login(lead_user)
        response = client.post(
            reverse("core:deletion_review", kwargs={"pk": pending_request.pk}),
            {"action": "reject"},
        )
        assert response.status_code == 302
        pending_request.refresh_from_db()
        event_qualified.refresh_from_db()
        assert pending_request.status == DeletionRequest.Status.REJECTED
        assert event_qualified.is_deleted is False

    def test_same_user_cannot_approve_own_request(
        self, client, admin_user, facility, client_qualified, doc_type_contact
    ):
        """If requester == reviewer, the view must block approval."""
        from core.models import DeletionRequest as DR

        # Use a fresh event so the new unique_pending_deletion_request constraint
        # (#530) doesn't reject the setup as a duplicate of the pending_request
        # fixture.
        event = Event.objects.create(
            facility=facility,
            client=client_qualified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 5, "notiz": "Self review event"},
            created_by=admin_user,
        )
        dr = DR.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Self-review test",
            requested_by=admin_user,
        )
        client.force_login(admin_user)
        response = client.post(
            reverse("core:deletion_review", kwargs={"pk": dr.pk}),
            {"action": "approve"},
        )
        assert response.status_code == 302
        dr.refresh_from_db()
        # Must still be PENDING because the view blocks self-approval
        assert dr.status == DeletionRequest.Status.PENDING

    def test_staff_cannot_access_review(self, client, staff_user, pending_request):
        client.force_login(staff_user)
        response = client.get(reverse("core:deletion_review", kwargs={"pk": pending_request.pk}))
        assert response.status_code == 403

    def test_assistant_cannot_access_review(self, client, assistant_user, pending_request):
        client.force_login(assistant_user)
        response = client.get(reverse("core:deletion_review", kwargs={"pk": pending_request.pk}))
        assert response.status_code == 403

    def test_admin_can_approve(self, client, admin_user, pending_request, event_qualified):
        """Admin users can review and approve."""
        client.force_login(admin_user)
        response = client.post(
            reverse("core:deletion_review", kwargs={"pk": pending_request.pk}),
            {"action": "approve"},
        )
        assert response.status_code == 302
        pending_request.refresh_from_db()
        assert pending_request.status == DeletionRequest.Status.APPROVED


@pytest.mark.django_db
class TestEventDeleteViewRouting:
    """Tests for EventDeleteView four-eyes routing logic."""

    def test_anonymous_event_direct_deletion(self, client, staff_user, event_anonymous):
        client.force_login(staff_user)
        response = client.post(reverse("core:event_delete", kwargs={"pk": event_anonymous.pk}))
        assert response.status_code == 302
        event_anonymous.refresh_from_db()
        assert event_anonymous.is_deleted is True
        assert not DeletionRequest.objects.filter(target_id=event_anonymous.pk).exists()

    def test_identified_event_direct_deletion(self, client, staff_user, event_identified):
        client.force_login(staff_user)
        response = client.post(reverse("core:event_delete", kwargs={"pk": event_identified.pk}))
        assert response.status_code == 302
        event_identified.refresh_from_db()
        assert event_identified.is_deleted is True
        assert not DeletionRequest.objects.filter(target_id=event_identified.pk).exists()

    def test_qualified_event_creates_deletion_request(self, client, staff_user, event_qualified):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_delete", kwargs={"pk": event_qualified.pk}),
            {"reason": "Klient wuenscht Loeschung"},
        )
        assert response.status_code == 302
        event_qualified.refresh_from_db()
        assert event_qualified.is_deleted is False
        dr = DeletionRequest.objects.filter(target_id=event_qualified.pk).first()
        assert dr is not None
        assert dr.status == DeletionRequest.Status.PENDING
        assert dr.requested_by == staff_user

    def test_delete_confirm_page_renders(self, client, staff_user, event_qualified):
        client.force_login(staff_user)
        response = client.get(reverse("core:event_delete", kwargs={"pk": event_qualified.pk}))
        assert response.status_code == 200

    def test_lead_can_delete_any_event(self, client, lead_user, event_identified):
        """Lead users can delete events they did not create."""
        client.force_login(lead_user)
        response = client.post(reverse("core:event_delete", kwargs={"pk": event_identified.pk}))
        assert response.status_code == 302
        event_identified.refresh_from_db()
        assert event_identified.is_deleted is True

    def test_staff_cannot_delete_others_event(self, client, lead_user, facility, doc_type_contact, client_identified):
        """Staff can only delete their own events, not others'."""
        from core.models import User

        other_staff = User.objects.create_user(
            username="otherstaff",
            role=User.Role.STAFF,
            facility=facility,
            is_staff=True,
        )
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=lead_user,
        )
        client.force_login(other_staff)
        response = client.post(reverse("core:event_delete", kwargs={"pk": event.pk}))
        assert response.status_code == 403

    def test_assistant_cannot_delete(self, client, assistant_user, event_identified):
        """Assistants cannot access the delete view (StaffRequiredMixin)."""
        client.force_login(assistant_user)
        response = client.post(reverse("core:event_delete", kwargs={"pk": event_identified.pk}))
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# 3. Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeletionEdgeCases:
    """Edge cases for the deletion request workflow."""

    def test_double_approval_returns_404(self, client, lead_user, pending_request, event_qualified):
        """Approving an already-approved request should 404 (view queries PENDING only)."""
        client.force_login(lead_user)
        # First approval
        client.post(
            reverse("core:deletion_review", kwargs={"pk": pending_request.pk}),
            {"action": "approve"},
        )
        pending_request.refresh_from_db()
        assert pending_request.status == DeletionRequest.Status.APPROVED
        # Second attempt
        response = client.post(
            reverse("core:deletion_review", kwargs={"pk": pending_request.pk}),
            {"action": "approve"},
        )
        assert response.status_code == 404

    def test_approve_already_rejected_returns_404(self, client, lead_user, pending_request, event_qualified):
        """Trying to approve a rejected request should 404."""
        client.force_login(lead_user)
        # Reject first
        client.post(
            reverse("core:deletion_review", kwargs={"pk": pending_request.pk}),
            {"action": "reject"},
        )
        pending_request.refresh_from_db()
        assert pending_request.status == DeletionRequest.Status.REJECTED
        # Try to approve
        response = client.post(
            reverse("core:deletion_review", kwargs={"pk": pending_request.pk}),
            {"action": "approve"},
        )
        assert response.status_code == 404

    def test_reject_already_approved_returns_404(self, client, lead_user, pending_request, event_qualified):
        """Trying to reject an approved request should 404."""
        client.force_login(lead_user)
        # Approve first
        client.post(
            reverse("core:deletion_review", kwargs={"pk": pending_request.pk}),
            {"action": "approve"},
        )
        # Try to reject
        response = client.post(
            reverse("core:deletion_review", kwargs={"pk": pending_request.pk}),
            {"action": "reject"},
        )
        assert response.status_code == 404

    def test_delete_already_soft_deleted_event_returns_404(self, client, staff_user, event_identified):
        """Attempting to delete an already soft-deleted event returns 404."""
        # Soft-delete directly
        soft_delete_event(event_identified, staff_user)
        event_identified.refresh_from_db()
        assert event_identified.is_deleted is True
        # Try via view
        client.force_login(staff_user)
        response = client.post(reverse("core:event_delete", kwargs={"pk": event_identified.pk}))
        assert response.status_code == 404

    def test_review_with_deleted_target_event(self, client, lead_user, facility, staff_user, event_qualified):
        """Reviewing a request whose target event was already deleted raises 404."""
        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event_qualified.pk,
            reason="Race condition test",
            requested_by=staff_user,
        )
        # Manually soft-delete the event outside the normal flow
        event_qualified.is_deleted = True
        event_qualified.save()

        client.force_login(lead_user)
        # GET should still work (view fetches event including deleted ones)
        response = client.get(reverse("core:deletion_review", kwargs={"pk": dr.pk}))
        assert response.status_code == 200

        # POST approve should also work (approve_deletion fetches by pk)
        response = client.post(
            reverse("core:deletion_review", kwargs={"pk": dr.pk}),
            {"action": "approve"},
        )
        assert response.status_code == 302
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED

    def test_event_without_client_direct_deletion(self, client, staff_user, facility, doc_type_contact):
        """Event with no client (not anonymous, just no client) gets direct deletion."""
        event = Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.post(reverse("core:event_delete", kwargs={"pk": event.pk}))
        assert response.status_code == 302
        event.refresh_from_db()
        assert event.is_deleted is True

    def test_facility_scoping_prevents_cross_facility_review(
        self, client, facility, second_facility, second_facility_user, staff_user, event_qualified
    ):
        """A DeletionRequest from facility A is not accessible to users in facility B."""
        from core.models import User

        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event_qualified.pk,
            reason="Cross-facility test",
            requested_by=staff_user,
        )
        # Create a lead in the second facility
        other_lead = User.objects.create_user(
            username="otherlead",
            role=User.Role.LEAD,
            facility=second_facility,
            is_staff=True,
        )
        client.force_login(other_lead)
        response = client.get(reverse("core:deletion_review", kwargs={"pk": dr.pk}))
        assert response.status_code == 404
