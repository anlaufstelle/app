"""Tests for client data export (Art. 15/20 DSGVO)."""

from datetime import timedelta

import pytest
from django.test import Client as DjangoClient
from django.utils import timezone

from core.models import (
    AuditLog,
    Client,
    DeletionRequest,
    DocumentType,
    Episode,
    Event,
    Facility,
    Organization,
    Settings,
    User,
    WorkItem,
)
from core.models import Case as CaseModel
from core.services.client import export_client_data


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
        role=User.Role.FACILITY_ADMIN,
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

    def test_returns_correct_structure(self, sample_client, facility, admin_user):
        data = export_client_data(sample_client, facility, admin_user)
        assert "client" in data
        assert "events" in data
        assert "cases" in data
        assert "event_history" in data
        assert "deletion_requests" in data
        assert "work_items" in data
        assert "export_meta" in data

    def test_client_master_data(self, sample_client, facility, admin_user):
        data = export_client_data(sample_client, facility, admin_user)
        assert data["client"]["pseudonym"] == "Export-Klientel"
        assert data["client"]["contact_stage"] == "Identifiziert"
        assert data["client"]["age_cluster"] == "18–26"

    def test_events_included(self, sample_client, facility, sample_event, admin_user):
        data = export_client_data(sample_client, facility, admin_user)
        assert len(data["events"]) == 1
        assert data["events"][0]["document_type"] == "Kontakt"

    def test_cases_included(self, sample_client, facility, sample_case, admin_user):
        data = export_client_data(sample_client, facility, admin_user)
        assert len(data["cases"]) == 1
        assert data["cases"][0]["title"] == "Testfall"

    def test_workitems_included(self, sample_client, facility, sample_workitem, admin_user):
        data = export_client_data(sample_client, facility, admin_user)
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
        data = export_client_data(sample_client, facility, admin_user)
        assert len(data["events"]) == 0

    def test_export_meta_contains_facility_name(self, sample_client, facility, admin_user):
        data = export_client_data(sample_client, facility, admin_user)
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


@pytest.fixture
def confirmer_user(facility):
    """Zweite berechtigte Person — DeletionRequest verlangt Antragsteller != Prüfer."""
    return User.objects.create_user(
        username="ce_confirm",
        password="test1234",
        role=User.Role.LEAD,
        facility=facility,
        is_staff=True,
    )


@pytest.mark.django_db
class TestClientExportCoreCategories:
    """Refs #1752 — Kern-Kategorien der Art.-15-Auskunft.

    Produktentscheid 2026-08-03 (#1755):
    ``Client.notes``, Papierkorb-/Anonymisierungs-Metadaten und die im Issue
    gelisteten Kleinfelder gehoeren in die Auskunft.
    """

    def test_client_notes_included(self, sample_client, facility, admin_user):
        sample_client.notes = "Freitext-Vermerk der Fachkraft"
        sample_client.save(update_fields=["notes"])
        data = export_client_data(sample_client, facility, admin_user)
        assert data["client"]["notes"] == "Freitext-Vermerk der Fachkraft"

    def test_client_trash_and_anonymization_metadata(self, sample_client, facility, admin_user):
        sample_client.k_anonymized = True
        sample_client.save(update_fields=["k_anonymized"])
        sample_client.soft_delete(user=admin_user)
        data = export_client_data(sample_client, facility, admin_user)
        client_data = data["client"]
        assert client_data["is_deleted"] is True
        assert client_data["deleted_at"] is not None
        assert client_data["deleted_by"] == "ce_admin"
        assert client_data["k_anonymized"] is True
        assert client_data["updated_at"] is not None

    def test_client_trash_metadata_defaults_for_live_record(self, sample_client, facility, admin_user):
        data = export_client_data(sample_client, facility, admin_user)
        assert data["client"]["is_deleted"] is False
        assert data["client"]["deleted_at"] is None
        assert data["client"]["deleted_by"] is None
        assert data["client"]["k_anonymized"] is False

    def test_case_small_fields(self, sample_client, facility, sample_case, admin_user):
        data = export_client_data(sample_client, facility, admin_user)
        case_entry = data["cases"][0]
        assert case_entry["updated_at"] is not None
        assert case_entry["is_deleted"] is False
        assert case_entry["deleted_at"] is None
        assert case_entry["deleted_by"] is None

    def test_event_small_fields(self, sample_client, facility, sample_event, admin_user):
        data = export_client_data(sample_client, facility, admin_user)
        event_entry = data["events"][0]
        assert event_entry["created_at"] is not None
        assert event_entry["updated_at"] is not None
        assert event_entry["is_deleted"] is False
        assert event_entry["is_anonymous"] is False

    def test_event_case_and_episode_reference(self, sample_client, facility, sample_case, doc_type, admin_user):
        episode = Episode.objects.create(
            case=sample_case,
            title="Erste Phase",
            started_at=timezone.now().date(),
            created_by=admin_user,
        )
        Event.objects.create(
            facility=facility,
            client=sample_client,
            document_type=doc_type,
            case=sample_case,
            episode=episode,
            occurred_at=timezone.now(),
            data_json={},
            created_by=admin_user,
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert data["events"][0]["case"] == "Testfall"
        assert data["events"][0]["episode"] == "Erste Phase"

    def test_workitem_small_fields(self, sample_client, facility, sample_workitem, admin_user, lead_user):
        sample_workitem.assigned_to = lead_user
        sample_workitem.save(update_fields=["assigned_to"])
        data = export_client_data(sample_client, facility, admin_user)
        item = data["work_items"][0]
        assert item["created_by"] == "ce_admin"
        assert item["assigned_to"] == "ce_lead"
        assert item["item_type"] == "Aufgabe"
        assert item["updated_at"] is not None
        assert item["is_deleted"] is False

    def test_client_target_deletion_request_included(self, sample_client, facility, admin_user, confirmer_user):
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=sample_client.pk,
            reason="Betroffene hat Loeschung verlangt",
            requested_by=admin_user,
            reviewed_by=confirmer_user,
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert len(data["deletion_requests"]) == 1
        entry = data["deletion_requests"][0]
        assert entry["target_type"] == "Person"
        assert entry["reason"] == "Betroffene hat Loeschung verlangt"

    def test_event_target_deletion_request_still_included(
        self, sample_client, facility, sample_event, admin_user, confirmer_user
    ):
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="Ereignis fehlerhaft",
            requested_by=admin_user,
            reviewed_by=confirmer_user,
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert [e["target_type"] for e in data["deletion_requests"]] == ["Event"]

    def test_deletion_requests_of_other_client_excluded(self, sample_client, facility, admin_user, confirmer_user):
        other = Client.objects.create(
            facility=facility,
            pseudonym="Fremd-Klientel",
            created_by=admin_user,
        )
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=other.pk,
            reason="Fremder Antrag",
            requested_by=admin_user,
            reviewed_by=confirmer_user,
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert data["deletion_requests"] == []
