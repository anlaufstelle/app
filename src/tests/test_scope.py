"""Systematic facility scope tests — every view must filter by request.current_facility."""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, Client, Event, Facility, WorkItem


@pytest.mark.django_db
class TestClientListScope:
    def test_client_list_other_facility_not_visible(self, client, staff_user, facility, organization):
        """Clients of another facility are not shown in the client list."""
        other_facility = Facility.objects.create(organization=organization, name="Andere Stelle")
        Client.objects.create(facility=other_facility, pseudonym="Fremd-KL-01", created_by=staff_user)

        client.force_login(staff_user)
        response = client.get(reverse("core:client_list"))

        assert response.status_code == 200
        assert "Fremd-KL-01" not in response.content.decode()


@pytest.mark.django_db
class TestClientDetailScope:
    def test_client_detail_other_facility_404(self, client, staff_user, facility, organization):
        """Accessing a client from another facility returns 404."""
        other_facility = Facility.objects.create(organization=organization, name="Andere Stelle")
        other_client = Client.objects.create(
            facility=other_facility,
            pseudonym="Fremd-CD-01",
            created_by=staff_user,
        )

        client.force_login(staff_user)
        response = client.get(reverse("core:client_detail", kwargs={"pk": other_client.pk}))

        assert response.status_code == 404


@pytest.mark.django_db
class TestEventDetailScope:
    def test_event_detail_other_facility_404(self, client, staff_user, facility, organization, doc_type_contact):
        """Accessing an event from another facility returns 404."""
        other_facility = Facility.objects.create(organization=organization, name="Andere Stelle")
        other_client = Client.objects.create(
            facility=other_facility,
            pseudonym="Fremd-EV-01",
            created_by=staff_user,
        )
        other_event = Event.objects.create(
            facility=other_facility,
            client=other_client,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )

        client.force_login(staff_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": other_event.pk}))

        assert response.status_code == 404


@pytest.mark.django_db
class TestWorkItemInboxScope:
    def test_workitem_inbox_other_facility_not_visible(
        self, client, staff_user, facility, organization, client_identified
    ):
        """WorkItems of another facility are not shown in the inbox."""
        other_facility = Facility.objects.create(organization=organization, name="Andere Stelle")
        other_client = Client.objects.create(
            facility=other_facility,
            pseudonym="Fremd-WI-01",
            created_by=staff_user,
        )
        WorkItem.objects.create(
            facility=other_facility,
            client=other_client,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            title="Fremde Aufgabe",
        )

        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_inbox"))

        assert response.status_code == 200
        assert "Fremde Aufgabe" not in response.content.decode()


@pytest.mark.django_db
class TestStatisticsScope:
    def test_statistics_scoped_to_facility(self, client, lead_user, facility, organization, doc_type_contact):
        """Statistics only show own facility data; other-facility events don't inflate counts."""
        other_facility = Facility.objects.create(organization=organization, name="Andere Stelle")
        other_client = Client.objects.create(
            facility=other_facility,
            pseudonym="Fremd-ST-01",
            created_by=lead_user,
        )
        Event.objects.create(
            facility=other_facility,
            client=other_client,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=lead_user,
        )

        client.force_login(lead_user)
        response = client.get(reverse("core:statistics"))

        assert response.status_code == 200
        # The statistics context total_contacts should not include the other facility's event
        stats = response.context["stats"]
        assert stats["total_contacts"] == 0


@pytest.mark.django_db
class TestAuditLogScope:
    def test_audit_log_scoped_to_facility(self, client, admin_user, facility, organization):
        """AuditLog only shows own facility entries; other facility entries are invisible."""
        other_facility = Facility.objects.create(organization=organization, name="Andere Stelle")

        AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.LOGIN,
        )
        AuditLog.objects.create(
            facility=other_facility,
            user=admin_user,
            action=AuditLog.Action.EXPORT,
            detail={"text": "Fremd-Export"},
        )

        client.force_login(admin_user)
        response = client.get(reverse("core:audit_log"))

        assert response.status_code == 200
        page_obj = response.context["page_obj"]
        returned_ids = {str(entry.id) for entry in page_obj}
        other_entries = AuditLog.objects.filter(facility=other_facility)
        for entry in other_entries:
            assert str(entry.id) not in returned_ids


@pytest.mark.django_db
class TestSearchScope:
    def test_search_scoped_to_facility(self, client, staff_user, facility, organization):
        """Search only finds own facility data; other-facility clients are invisible."""
        other_facility = Facility.objects.create(organization=organization, name="Andere Stelle")
        Client.objects.create(
            facility=other_facility,
            pseudonym="Fremd-SR-01",
            created_by=staff_user,
        )

        client.force_login(staff_user)
        response = client.get(reverse("core:search"), {"q": "Fremd-SR-01"})

        assert response.status_code == 200
        # Context must contain no clients from other facility
        assert list(response.context["clients"]) == []
        assert response.context["has_results"] is False
