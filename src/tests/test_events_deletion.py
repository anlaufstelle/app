"""Tests für Events — Event-Lösch-Workflows (Delete-View + 4-Augen Review) (Refs #929)."""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import DeletionRequest, Event


@pytest.mark.django_db
class TestEventDeleteView:
    def test_event_delete_confirm_renders(self, client, staff_user, sample_event):
        client.force_login(staff_user)
        response = client.get(reverse("core:event_delete", kwargs={"pk": sample_event.pk}))
        assert response.status_code == 200

    def test_event_delete_identified_direct(self, client, staff_user, sample_event):
        """Identified client → direkte Löschung."""
        client.force_login(staff_user)
        response = client.post(reverse("core:event_delete", kwargs={"pk": sample_event.pk}))
        assert response.status_code == 302
        sample_event.refresh_from_db()
        assert sample_event.is_deleted is True

    def test_event_delete_qualified_creates_request(
        self, client, staff_user, facility, doc_type_contact, client_qualified
    ):
        """Qualified client → Löschantrag."""
        event = Event.objects.create(
            facility=facility,
            client=client_qualified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 10},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_delete", kwargs={"pk": event.pk}),
            {"reason": "DSGVO-Anfrage"},
        )
        assert response.status_code == 302
        event.refresh_from_db()
        assert event.is_deleted is False  # Noch nicht gelöscht
        assert DeletionRequest.objects.filter(target_id=event.pk).exists()

    def test_event_delete_anonymous_direct(self, client, staff_user, facility, doc_type_contact):
        """Anonymes Event → direkte Löschung."""
        event = Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.post(reverse("core:event_delete", kwargs={"pk": event.pk}))
        assert response.status_code == 302
        event.refresh_from_db()
        assert event.is_deleted is True


@pytest.mark.django_db
class TestDeletionReview:
    def test_deletion_review_lead_can_access(
        self, client, lead_user, staff_user, facility, doc_type_contact, client_qualified
    ):
        event = Event.objects.create(
            facility=facility,
            client=client_qualified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Test",
            requested_by=staff_user,
        )
        client.force_login(lead_user)
        response = client.get(reverse("core:deletion_review", kwargs={"pk": dr.pk}))
        assert response.status_code == 200

    def test_deletion_review_approve(self, client, lead_user, staff_user, facility, doc_type_contact, client_qualified):
        event = Event.objects.create(
            facility=facility,
            client=client_qualified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Test",
            requested_by=staff_user,
        )
        client.force_login(lead_user)
        response = client.post(
            reverse("core:deletion_review", kwargs={"pk": dr.pk}),
            {"action": "approve"},
        )
        assert response.status_code == 302
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED
        event.refresh_from_db()
        assert event.is_deleted is True

    def test_deletion_review_reject(self, client, lead_user, staff_user, facility, doc_type_contact, client_qualified):
        event = Event.objects.create(
            facility=facility,
            client=client_qualified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Test",
            requested_by=staff_user,
        )
        client.force_login(lead_user)
        response = client.post(
            reverse("core:deletion_review", kwargs={"pk": dr.pk}),
            {"action": "reject"},
        )
        assert response.status_code == 302
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.REJECTED

    def test_deletion_review_same_user_cannot_approve(
        self, client, lead_user, facility, doc_type_contact, client_qualified
    ):
        """Reviewer ≠ Requester."""
        event = Event.objects.create(
            facility=facility,
            client=client_qualified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=lead_user,
        )
        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Test",
            requested_by=lead_user,
        )
        client.force_login(lead_user)
        response = client.post(
            reverse("core:deletion_review", kwargs={"pk": dr.pk}),
            {"action": "approve"},
        )
        assert response.status_code == 302
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING  # Nicht genehmigt

    def test_deletion_review_staff_forbidden(self, client, staff_user, facility, doc_type_contact, client_qualified):
        event = Event.objects.create(
            facility=facility,
            client=client_qualified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Test",
            requested_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:deletion_review", kwargs={"pk": dr.pk}))
        assert response.status_code == 403
