"""Tests fuer die Benutzerprofilseite."""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import DocumentType, Event, WorkItem


@pytest.fixture
def profile_url():
    return reverse("core:account_profile")


class TestAccountProfileView:
    """Profilseite ist erreichbar und zeigt korrekte Daten."""

    def test_profile_accessible_for_staff(self, client, staff_user, profile_url):
        client.force_login(staff_user)
        response = client.get(profile_url)
        assert response.status_code == 200

    def test_profile_accessible_for_assistant(self, client, assistant_user, profile_url):
        client.force_login(assistant_user)
        response = client.get(profile_url)
        assert response.status_code == 200

    def test_profile_accessible_for_admin(self, client, admin_user, profile_url):
        client.force_login(admin_user)
        response = client.get(profile_url)
        assert response.status_code == 200

    def test_profile_redirects_anonymous(self, client, profile_url):
        response = client.get(profile_url)
        assert response.status_code == 302

    def test_profile_shows_username(self, client, staff_user, profile_url):
        client.force_login(staff_user)
        response = client.get(profile_url)
        assert staff_user.username.encode() in response.content

    def test_profile_shows_role(self, client, staff_user, profile_url):
        client.force_login(staff_user)
        response = client.get(profile_url)
        assert staff_user.get_role_display().encode() in response.content

    def test_profile_shows_facility(self, client, staff_user, profile_url):
        client.force_login(staff_user)
        response = client.get(profile_url)
        assert str(staff_user.facility).encode() in response.content

    def test_context_contains_recent_events(self, client, staff_user, sample_event, profile_url):
        client.force_login(staff_user)
        response = client.get(profile_url)
        assert "recent_events" in response.context
        assert sample_event in response.context["recent_events"]

    def test_context_contains_open_workitems(self, client, staff_user, sample_workitem, profile_url):
        # Assign the workitem to the user
        sample_workitem.assigned_to = staff_user
        sample_workitem.save()
        client.force_login(staff_user)
        response = client.get(profile_url)
        assert "open_workitems" in response.context
        assert sample_workitem in response.context["open_workitems"]

    def test_context_contains_done_workitems(self, client, staff_user, sample_workitem, profile_url):
        sample_workitem.assigned_to = staff_user
        sample_workitem.status = WorkItem.Status.DONE
        sample_workitem.completed_at = timezone.now()
        sample_workitem.save()
        client.force_login(staff_user)
        response = client.get(profile_url)
        assert "done_workitems" in response.context
        assert sample_workitem in response.context["done_workitems"]

    def test_events_limited_to_own(
        self, client, staff_user, facility, doc_type_contact, client_identified, profile_url
    ):
        """Nur eigene Events werden angezeigt."""
        from core.models import User

        other_user = User.objects.create_user(username="other", role=User.Role.STAFF, facility=facility)
        other_event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=other_user,
        )
        own_event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.get(profile_url)
        events = response.context["recent_events"]
        assert own_event in events
        assert other_event not in events

    def test_profile_hides_own_high_events_for_staff(
        self, client, staff_user, facility, client_identified, profile_url
    ):
        """Staff darf eigene HIGH-Events auf der Profilseite nicht sehen."""
        doc_type_high = DocumentType.objects.create(
            facility=facility,
            category=DocumentType.Category.SERVICE,
            sensitivity=DocumentType.Sensitivity.HIGH,
            name="Hochsensibel",
        )
        high_event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_high,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.get(profile_url)
        assert high_event not in response.context["recent_events"]
        assert response.context["stats"]["events_today"] == 0
