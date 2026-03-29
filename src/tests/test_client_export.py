"""Tests for client data export (Art. 15/20 DSGVO)."""

from datetime import timedelta

import pytest
from django.test import Client as DjangoClient
from django.utils import timezone

from core.models import (
    AuditLog,
    Client,
    DocumentType,
    Event,
    Facility,
    Organization,
    Settings,
    User,
    WorkItem,
)
from core.models import Case as CaseModel
from core.services.client_export import export_client_data


@pytest.fixture
def facility(db):
    org = Organization.objects.create(name="ClientExport-Org")
    facility = Facility.objects.create(organization=org, name="ClientExport-Einrichtung")
    Settings.objects.create(facility=facility, facility_full_name="Anlaufstelle Teststadt")
    return facility


@pytest.fixture
def admin_user(facility):
    return User.objects.create_user(
        username="ce_admin",
        password="test1234",
        role=User.Role.ADMIN,
        facility=facility,
        is_staff=True,
    )


@pytest.fixture
def lead_user(facility):
    return User.objects.create_user(
        username="ce_lead",
        password="test1234",
        role=User.Role.LEAD,
        facility=facility,
        is_staff=True,
    )


@pytest.fixture
def staff_user(facility):
    return User.objects.create_user(
        username="ce_staff",
        password="test1234",
        role=User.Role.STAFF,
        facility=facility,
        is_staff=True,
    )


@pytest.fixture
def doc_type(facility):
    return DocumentType.objects.create(
        facility=facility,
        name="Kontakt",
        category=DocumentType.Category.CONTACT,
        system_type=DocumentType.SystemType.CONTACT,
    )


@pytest.fixture
def sample_client(facility, admin_user):
    return Client.objects.create(
        facility=facility,
        pseudonym="Export-Klientel",
        contact_stage=Client.ContactStage.IDENTIFIED,
        age_cluster=Client.AgeCluster.AGE_18_26,
        created_by=admin_user,
    )


@pytest.fixture
def sample_event(facility, admin_user, doc_type, sample_client):
    return Event.objects.create(
        facility=facility,
        client=sample_client,
        document_type=doc_type,
        occurred_at=timezone.now() - timedelta(days=1),
        data_json={"dauer": 15, "notiz": "Testnotiz"},
        created_by=admin_user,
    )


@pytest.fixture
def sample_case(facility, admin_user, sample_client):
    return CaseModel.objects.create(
        facility=facility,
        client=sample_client,
        title="Testfall",
        description="Beschreibung",
        created_by=admin_user,
    )


@pytest.fixture
def sample_workitem(facility, admin_user, sample_client):
    return WorkItem.objects.create(
        facility=facility,
        client=sample_client,
        created_by=admin_user,
        title="Test-Aufgabe",
        description="Test-Beschreibung",
    )


@pytest.mark.django_db
class TestClientExportService:
    """Service-level tests for export_client_data()."""

    def test_returns_correct_structure(self, sample_client, facility):
        data = export_client_data(sample_client, facility)
        assert "client" in data
        assert "events" in data
        assert "cases" in data
        assert "event_history" in data
        assert "deletion_requests" in data
        assert "work_items" in data
        assert "export_meta" in data

    def test_client_master_data(self, sample_client, facility):
        data = export_client_data(sample_client, facility)
        assert data["client"]["pseudonym"] == "Export-Klientel"
        assert data["client"]["contact_stage"] == "Identifiziert"
        assert data["client"]["age_cluster"] == "18–26"

    def test_events_included(self, sample_client, facility, sample_event):
        data = export_client_data(sample_client, facility)
        assert len(data["events"]) == 1
        assert data["events"][0]["document_type"] == "Kontakt"

    def test_cases_included(self, sample_client, facility, sample_case):
        data = export_client_data(sample_client, facility)
        assert len(data["cases"]) == 1
        assert data["cases"][0]["title"] == "Testfall"

    def test_workitems_included(self, sample_client, facility, sample_workitem):
        data = export_client_data(sample_client, facility)
        assert len(data["work_items"]) == 1
        assert data["work_items"][0]["title"] == "Test-Aufgabe"

    def test_deleted_events_excluded(self, sample_client, facility, doc_type, admin_user):
        Event.objects.create(
            facility=facility,
            client=sample_client,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            is_deleted=True,
            created_by=admin_user,
        )
        data = export_client_data(sample_client, facility)
        assert len(data["events"]) == 0

    def test_export_meta_contains_facility_name(self, sample_client, facility):
        data = export_client_data(sample_client, facility)
        assert data["export_meta"]["facility_name"] == "Anlaufstelle Teststadt"


@pytest.mark.django_db
class TestClientExportJSONView:
    """View-level tests for JSON export."""

    def test_admin_can_access(self, admin_user, sample_client, facility):
        client = DjangoClient()
        client.force_login(admin_user)
        response = client.get(f"/clients/{sample_client.pk}/export/json/")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/json; charset=utf-8"
        assert "attachment" in response["Content-Disposition"]

    def test_lead_can_access(self, lead_user, sample_client, facility):
        client = DjangoClient()
        client.force_login(lead_user)
        response = client.get(f"/clients/{sample_client.pk}/export/json/")
        assert response.status_code == 200

    def test_staff_gets_403(self, staff_user, sample_client, facility):
        client = DjangoClient()
        client.force_login(staff_user)
        response = client.get(f"/clients/{sample_client.pk}/export/json/")
        assert response.status_code == 403

    def test_unauthenticated_redirects(self, sample_client, facility):
        client = DjangoClient()
        response = client.get(f"/clients/{sample_client.pk}/export/json/")
        assert response.status_code == 302

    def test_creates_audit_log(self, admin_user, sample_client, facility):
        client = DjangoClient()
        client.force_login(admin_user)
        client.get(f"/clients/{sample_client.pk}/export/json/")
        assert AuditLog.objects.filter(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.EXPORT,
            target_type="Client-JSON",
        ).exists()


@pytest.mark.django_db
class TestClientExportPDFView:
    """View-level tests for PDF export."""

    def test_admin_can_download_pdf(self, admin_user, sample_client, facility):
        client = DjangoClient()
        client.force_login(admin_user)
        response = client.get(f"/clients/{sample_client.pk}/export/pdf/")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert response.content[:4] == b"%PDF"

    def test_staff_gets_403(self, staff_user, sample_client, facility):
        client = DjangoClient()
        client.force_login(staff_user)
        response = client.get(f"/clients/{sample_client.pk}/export/pdf/")
        assert response.status_code == 403

    def test_creates_audit_log(self, admin_user, sample_client, facility):
        client = DjangoClient()
        client.force_login(admin_user)
        client.get(f"/clients/{sample_client.pk}/export/pdf/")
        assert AuditLog.objects.filter(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.EXPORT,
            target_type="Client-PDF",
        ).exists()
