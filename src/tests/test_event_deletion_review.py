"""Deletion review keeps missing targets neutral and supports decisions.

Refs #949, #2516.
"""

import uuid

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestDeletionRequestReviewClientNotFound:
    def test_missing_client_target_renders_neutral_review(self, client, lead_user, facility, staff_user):
        """A missing client leaves the request reviewable with no target link."""
        from core.models import DeletionRequest

        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=uuid.uuid4(),
            status=DeletionRequest.Status.PENDING,
            requested_by=staff_user,
            reason="Test",
        )
        client.force_login(lead_user)
        url = reverse("core:deletion_review", args=[dr.pk])
        response = client.get(url)
        assert response.status_code == 200
        assert "review-target-link" not in response.content.decode()


@pytest.mark.django_db
class TestDeletionRequestReviewEventNotFound:
    def test_missing_event_target_renders_neutral_review(self, client, lead_user, facility, staff_user):
        """A missing event leaves the request reviewable without metadata."""
        from core.models import DeletionRequest

        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=uuid.uuid4(),
            status=DeletionRequest.Status.PENDING,
            requested_by=staff_user,
            reason="Test",
        )
        client.force_login(lead_user)
        url = reverse("core:deletion_review", args=[dr.pk])
        response = client.get(url)
        assert response.status_code == 200
        assert "review-target-link" not in response.content.decode()


@pytest.mark.django_db
class TestDeletionRequestReviewEventApprove:
    def test_approve_event_deletion(self, client, lead_user, facility, staff_user, sample_event):
        """Line 107: POST action=approve mit Event-Target ruft ``approve_deletion`` auf."""
        from core.models import DeletionRequest

        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            status=DeletionRequest.Status.PENDING,
            requested_by=staff_user,
            reason="Test",
        )
        client.force_login(lead_user)
        url = reverse("core:deletion_review", args=[dr.pk])
        response = client.post(url, {"action": "approve"})
        assert response.status_code == 302
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED

    def test_reject_event_deletion(self, client, lead_user, facility, staff_user, sample_event):
        """Line 113: POST action=reject mit Event-Target ruft ``reject_deletion`` auf."""
        from core.models import DeletionRequest

        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            status=DeletionRequest.Status.PENDING,
            requested_by=staff_user,
            reason="Test",
        )
        client.force_login(lead_user)
        url = reverse("core:deletion_review", args=[dr.pk])
        response = client.post(url, {"action": "reject"})
        assert response.status_code == 302
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.REJECTED
