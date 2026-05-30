"""Coverage-Tests fuer ``core.views.event_deletion.DeletionRequestReviewView`` Branches.

Deckt die Branches:

* Client-Target mit ``Client.DoesNotExist`` -> 404 (Lines 66-70).
* Event-Target mit ``Event.DoesNotExist`` -> 404 (Lines 80-81).
* POST ``action=approve`` fuer Event-Target (Line 107).
* POST ``action=reject`` fuer Event-Target (Line 113).

Refs #949.
"""

import uuid

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestDeletionRequestReviewClientNotFound:
    def test_client_target_404_when_client_missing(self, client, lead_user, facility, staff_user):
        """Lines 66-70: Client-Target ohne existierenden Client -> Http404."""
        from core.models import DeletionRequest

        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=uuid.uuid4(),  # nicht existent
            status=DeletionRequest.Status.PENDING,
            requested_by=staff_user,
            reason="Test",
        )
        client.force_login(lead_user)
        url = reverse("core:deletion_review", args=[dr.pk])
        response = client.get(url)
        assert response.status_code == 404


@pytest.mark.django_db
class TestDeletionRequestReviewEventNotFound:
    def test_event_target_404_when_event_missing(self, client, lead_user, facility, staff_user):
        """Lines 80-81: Event-Target ohne existierendes Event -> Http404."""
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
        assert response.status_code == 404


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
