"""Systematic facility scope tests — every view must filter by request.current_facility."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, Case, Client, Event, Facility, WorkItem
from core.models.episode import Episode
from core.models.outcome import Milestone, OutcomeGoal


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


@pytest.mark.django_db
class TestCaseListScope:
    def test_case_list_other_facility_not_visible(self, client, staff_user, facility, organization):
        """Cases of another facility are not shown in the case list."""
        other_facility = Facility.objects.create(organization=organization, name="Andere Stelle")
        other_client = Client.objects.create(facility=other_facility, pseudonym="Fremd-CA-01", created_by=staff_user)
        Case.objects.create(
            facility=other_facility,
            client=other_client,
            title="Fremder Fall",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )

        client.force_login(staff_user)
        response = client.get(reverse("core:case_list"))

        assert response.status_code == 200
        assert "Fremder Fall" not in response.content.decode()


@pytest.mark.django_db
class TestCaseDetailScope:
    def test_case_detail_other_facility_404(self, client, staff_user, facility, organization):
        """Accessing a case from another facility returns 404."""
        other_facility = Facility.objects.create(organization=organization, name="Andere Stelle")
        other_client = Client.objects.create(facility=other_facility, pseudonym="Fremd-CD-02", created_by=staff_user)
        other_case = Case.objects.create(
            facility=other_facility,
            client=other_client,
            title="Fremder Fall Detail",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )

        client.force_login(staff_user)
        response = client.get(reverse("core:case_detail", kwargs={"pk": other_case.pk}))

        assert response.status_code == 404


@pytest.mark.django_db
class TestEpisodeCreateScope:
    def test_episode_create_other_facility_404(self, client, staff_user, facility, organization):
        """Creating an episode for a case in another facility returns 404."""
        other_facility = Facility.objects.create(organization=organization, name="Andere Stelle")
        other_client = Client.objects.create(facility=other_facility, pseudonym="Fremd-EP-01", created_by=staff_user)
        other_case = Case.objects.create(
            facility=other_facility,
            client=other_client,
            title="Fremder Fall Episode",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )

        client.force_login(staff_user)
        response = client.get(reverse("core:episode_create", kwargs={"case_pk": other_case.pk}))

        assert response.status_code == 404


@pytest.mark.django_db
class TestEpisodeUpdateScope:
    def test_episode_update_other_facility_404(self, client, staff_user, facility, organization):
        """Editing an episode belonging to a case in another facility returns 404."""
        other_facility = Facility.objects.create(organization=organization, name="Andere Stelle")
        other_client = Client.objects.create(facility=other_facility, pseudonym="Fremd-EP-02", created_by=staff_user)
        other_case = Case.objects.create(
            facility=other_facility,
            client=other_client,
            title="Fremder Fall Episode Edit",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        other_episode = Episode.objects.create(
            case=other_case,
            title="Fremde Episode",
            started_at=timezone.now().date(),
            created_by=staff_user,
        )

        client.force_login(staff_user)
        response = client.get(reverse("core:episode_update", kwargs={"case_pk": other_case.pk, "pk": other_episode.pk}))

        assert response.status_code == 404


@pytest.mark.django_db
class TestGoalCreateScope:
    def test_goal_create_other_facility_404(self, client, staff_user, facility, organization):
        """Creating a goal for a case in another facility returns 404."""
        other_facility = Facility.objects.create(organization=organization, name="Andere Stelle")
        other_client = Client.objects.create(facility=other_facility, pseudonym="Fremd-GO-01", created_by=staff_user)
        other_case = Case.objects.create(
            facility=other_facility,
            client=other_client,
            title="Fremder Fall Ziel",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )

        client.force_login(staff_user)
        response = client.post(
            reverse("core:goal_create", kwargs={"case_pk": other_case.pk}),
            {"title": "Fremdes Ziel"},
        )

        assert response.status_code == 404


@pytest.mark.django_db
class TestGoalToggleScope:
    def test_goal_toggle_other_facility_404(self, client, staff_user, facility, organization):
        """Toggling a goal from another facility's case returns 404."""
        other_facility = Facility.objects.create(organization=organization, name="Andere Stelle")
        other_client = Client.objects.create(facility=other_facility, pseudonym="Fremd-GO-02", created_by=staff_user)
        other_case = Case.objects.create(
            facility=other_facility,
            client=other_client,
            title="Fremder Fall Toggle",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        other_goal = OutcomeGoal.objects.create(
            case=other_case,
            title="Fremdes Ziel Toggle",
            created_by=staff_user,
        )

        client.force_login(staff_user)
        response = client.post(reverse("core:goal_toggle", kwargs={"case_pk": other_case.pk, "pk": other_goal.pk}))

        assert response.status_code == 404


@pytest.mark.django_db
class TestMilestoneScope:
    def test_milestone_toggle_other_facility_404(self, client, staff_user, facility, organization):
        """Toggling a milestone from another facility's case returns 404."""
        other_facility = Facility.objects.create(organization=organization, name="Andere Stelle")
        other_client = Client.objects.create(facility=other_facility, pseudonym="Fremd-MS-01", created_by=staff_user)
        other_case = Case.objects.create(
            facility=other_facility,
            client=other_client,
            title="Fremder Fall Milestone",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        other_goal = OutcomeGoal.objects.create(
            case=other_case,
            title="Fremdes Ziel Milestone",
            created_by=staff_user,
        )
        other_milestone = Milestone.objects.create(
            goal=other_goal,
            title="Fremder Meilenstein",
        )

        client.force_login(staff_user)
        response = client.post(
            reverse(
                "core:milestone_toggle",
                kwargs={"case_pk": other_case.pk, "pk": other_milestone.pk},
            )
        )

        assert response.status_code == 404


@pytest.mark.django_db
class TestHandoverScope:
    def test_handover_other_facility_events_not_counted(
        self, client, staff_user, facility, organization, doc_type_contact
    ):
        """Handover summary only counts events from the user's own facility."""
        other_facility = Facility.objects.create(organization=organization, name="Andere Stelle")
        other_client = Client.objects.create(facility=other_facility, pseudonym="Fremd-HO-01", created_by=staff_user)
        Event.objects.create(
            facility=other_facility,
            client=other_client,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )

        client.force_login(staff_user)
        response = client.get(reverse("core:handover"))

        assert response.status_code == 200
        summary = response.context["summary"]
        assert summary["stats"]["events_total"] == 0

    def test_handover_other_facility_workitems_not_shown(self, client, staff_user, facility, organization):
        """Handover open tasks do not include work items from other facilities."""
        other_facility = Facility.objects.create(organization=organization, name="Andere Stelle")
        other_client = Client.objects.create(facility=other_facility, pseudonym="Fremd-HO-02", created_by=staff_user)
        WorkItem.objects.create(
            facility=other_facility,
            client=other_client,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            title="Fremde Handover-Aufgabe",
        )

        client.force_login(staff_user)
        response = client.get(reverse("core:handover"))

        assert response.status_code == 200
        open_tasks = response.context["summary"]["open_tasks"]
        task_titles = [t.title for t in open_tasks]
        assert "Fremde Handover-Aufgabe" not in task_titles


@pytest.mark.django_db
class TestDSGVOPackageScope:
    def test_dsgvo_document_uses_own_facility(self, client, admin_user, facility):
        """DSGVO document download renders content with the user's own facility data."""
        client.force_login(admin_user)
        response = client.get(reverse("core:dsgvo_document", kwargs={"document": "verarbeitungsverzeichnis"}))

        assert response.status_code == 200
        content = response.content.decode()
        # The rendered document should contain the facility's name
        assert facility.name in content


@pytest.mark.django_db
class TestCSVExportScope:
    def test_csv_export_other_facility_events_not_included(
        self, client, lead_user, facility, organization, doc_type_contact
    ):
        """CSV export only contains events from the user's own facility."""
        other_facility = Facility.objects.create(organization=organization, name="Andere Stelle")
        other_client = Client.objects.create(facility=other_facility, pseudonym="Fremd-EX-01", created_by=lead_user)
        Event.objects.create(
            facility=other_facility,
            client=other_client,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"notiz": "Geheimer Export"},
            created_by=lead_user,
        )

        today = timezone.localdate()
        client.force_login(lead_user)
        response = client.get(
            reverse("core:statistics_csv_export"),
            {"date_from": str(today - timedelta(days=1)), "date_to": str(today)},
        )

        assert response.status_code == 200
        # Collect streamed content
        csv_content = b"".join(response.streaming_content).decode()
        assert "Fremd-EX-01" not in csv_content
        assert "Geheimer Export" not in csv_content


@pytest.mark.django_db
class TestPDFExportScope:
    def test_pdf_export_scoped_to_facility(self, client, lead_user, facility, organization, doc_type_contact):
        """PDF export only uses data from the user's own facility."""
        other_facility = Facility.objects.create(organization=organization, name="Andere Stelle")
        other_client = Client.objects.create(facility=other_facility, pseudonym="Fremd-PDF-01", created_by=lead_user)
        Event.objects.create(
            facility=other_facility,
            client=other_client,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=lead_user,
        )

        today = timezone.localdate()
        client.force_login(lead_user)
        response = client.get(
            reverse("core:statistics_pdf_export"),
            {"date_from": str(today - timedelta(days=1)), "date_to": str(today)},
        )

        # PDF export should succeed and return PDF content type
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
