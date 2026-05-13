"""Systematische Negativ-Tests für Berechtigungen."""

import uuid

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestUnauthenticatedRedirects:
    """Unauthenticated requests to protected views must redirect to login."""

    def test_client_list_redirects_to_login(self, client):
        response = client.get(reverse("core:client_list"))
        assert response.status_code == 302
        assert "/login/" in response.url

    def test_statistics_redirects_to_login(self, client):
        response = client.get(reverse("core:statistics"))
        assert response.status_code == 302
        assert "/login/" in response.url

    def test_audit_log_redirects_to_login(self, client):
        response = client.get(reverse("core:audit_log"))
        assert response.status_code == 302
        assert "/login/" in response.url


@pytest.mark.django_db
class TestAssistantPermissions:
    """Assistant role has access to AssistantOrAboveRequiredMixin views but not StaffRequiredMixin views."""

    def test_assistant_can_access_client_list(self, client, assistant_user):
        client.force_login(assistant_user)
        response = client.get(reverse("core:client_list"))
        assert response.status_code == 200

    def test_assistant_can_create_events(self, client, assistant_user):
        client.force_login(assistant_user)
        response = client.get(reverse("core:event_create"))
        assert response.status_code == 200

    def test_assistant_can_access_aktivitaetslog(self, client, assistant_user):
        client.force_login(assistant_user)
        response = client.get(reverse("core:zeitstrom"))
        assert response.status_code == 200

    def test_assistant_can_access_workitem_inbox(self, client, assistant_user):
        client.force_login(assistant_user)
        response = client.get(reverse("core:workitem_inbox"))
        assert response.status_code == 200

    def test_assistant_can_access_search(self, client, assistant_user):
        client.force_login(assistant_user)
        response = client.get(reverse("core:search"))
        assert response.status_code == 200

    def test_assistant_cannot_create_client(self, client, assistant_user):
        client.force_login(assistant_user)
        response = client.get(reverse("core:client_create"))
        assert response.status_code == 403

    def test_assistant_cannot_create_workitem(self, client, assistant_user):
        client.force_login(assistant_user)
        response = client.get(reverse("core:workitem_create"))
        assert response.status_code == 403

    def test_assistant_client_detail_hides_qualified_details(self, client, assistant_user, client_qualified):
        client.force_login(assistant_user)
        response = client.get(reverse("core:client_detail", kwargs={"pk": client_qualified.pk}))
        assert response.status_code == 200
        assert response.context["hide_qualified_details"] is True

    def test_staff_client_detail_shows_qualified_details(self, client, staff_user, client_qualified):
        client.force_login(staff_user)
        response = client.get(reverse("core:client_detail", kwargs={"pk": client_qualified.pk}))
        assert response.status_code == 200
        assert response.context["hide_qualified_details"] is False

    def test_assistant_cannot_edit_others_event(self, client, assistant_user, sample_event):
        """Assistant kann fremde Events nicht bearbeiten."""
        client.force_login(assistant_user)
        response = client.get(reverse("core:event_update", kwargs={"pk": sample_event.pk}))
        assert response.status_code == 403

    def test_assistant_can_edit_own_event(self, client, assistant_user, facility, doc_type_contact):
        """Assistant kann eigene Events bearbeiten."""
        from django.utils import timezone

        from core.models import Event

        own_event = Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=assistant_user,
        )
        client.force_login(assistant_user)
        response = client.get(reverse("core:event_update", kwargs={"pk": own_event.pk}))
        assert response.status_code == 200

    def test_assistant_zeitstrom_hides_task_and_client_links(self, client, assistant_user):
        """Assistenz sieht auf dem Zeitstrom nur 'Neuer Kontakt', nicht 'Neue Aufgabe'/'Neues Klientel'."""
        client.force_login(assistant_user)
        response = client.get(reverse("core:zeitstrom"))
        content = response.content.decode()
        assert reverse("core:event_create") in content
        assert reverse("core:workitem_create") not in content
        assert reverse("core:client_create") not in content

    def test_staff_zeitstrom_shows_all_quick_actions(self, client, staff_user):
        """Fachkraft sieht auf dem Zeitstrom alle drei Quick-Action-Links."""
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        content = response.content.decode()
        assert reverse("core:event_create") in content
        assert reverse("core:workitem_create") in content
        assert reverse("core:client_create") in content


@pytest.mark.django_db
class TestStaffEventDeletion:
    """Staff darf nur eigene Events löschen; Lead/Admin dürfen fremde Events löschen."""

    def test_staff_cannot_delete_others_event(self, client, staff_user, sample_event, facility, doc_type_contact):
        """Staff kann fremde Events nicht löschen."""

        from core.models import User

        other_staff = User.objects.create_user(
            username="otherstaff",
            role=User.Role.STAFF,
            facility=facility,
            is_staff=True,
        )
        other_staff.set_password("testpass123")
        other_staff.save()

        client.force_login(other_staff)
        response = client.get(reverse("core:event_delete", kwargs={"pk": sample_event.pk}))
        assert response.status_code == 403

    def test_staff_can_delete_own_event(self, client, staff_user, sample_event):
        """Staff kann eigene Events löschen (sample_event wurde von staff_user erstellt)."""
        client.force_login(staff_user)
        response = client.get(reverse("core:event_delete", kwargs={"pk": sample_event.pk}))
        assert response.status_code == 200

    def test_lead_can_delete_others_event(self, client, lead_user, sample_event):
        """Lead kann fremde Events löschen."""
        client.force_login(lead_user)
        response = client.get(reverse("core:event_delete", kwargs={"pk": sample_event.pk}))
        assert response.status_code == 200


@pytest.mark.django_db
class TestStaffPermissions:
    """Staff role is denied access to LeadOrAdminRequiredMixin and FacilityAdminRequiredMixin views."""

    def test_staff_cannot_access_statistics(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:statistics"))
        assert response.status_code == 403

    def test_staff_cannot_access_audit_log(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:audit_log"))
        assert response.status_code == 403

    def test_staff_cannot_access_deletion_request_list(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:deletion_request_list"))
        assert response.status_code == 403

    def test_staff_cannot_review_deletion(self, client, staff_user):
        client.force_login(staff_user)
        pk = uuid.uuid4()
        response = client.get(reverse("core:deletion_review", kwargs={"pk": pk}))
        assert response.status_code == 403


@pytest.mark.django_db
class TestLeadPermissions:
    """Lead role is denied access to FacilityAdminRequiredMixin views."""

    def test_lead_cannot_access_audit_log(self, client, lead_user):
        client.force_login(lead_user)
        response = client.get(reverse("core:audit_log"))
        assert response.status_code == 403


@pytest.mark.django_db
class TestNonAdminAuditLogDenied:
    """Both Staff and Lead are denied access to audit_log (Admin only)."""

    def test_staff_cannot_access_audit_log(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:audit_log"))
        assert response.status_code == 403

    def test_lead_cannot_access_audit_log(self, client, lead_user):
        client.force_login(lead_user)
        response = client.get(reverse("core:audit_log"))
        assert response.status_code == 403
