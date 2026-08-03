"""Tests for client data export (Art. 15/20 DSGVO)."""

from datetime import timedelta

import pytest
from django.test import Client as DjangoClient
from django.utils import timezone

from core.models import (
    Activity,
    AuditLog,
    Client,
    DeletionRequest,
    DocumentType,
    Episode,
    Event,
    EventHistory,
    Facility,
    LegalHold,
    Milestone,
    Organization,
    OutcomeGoal,
    RecentClientVisit,
    RetentionProposal,
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

    def test_deleted_events_are_not_in_the_live_events_section(self, sample_client, facility, doc_type, admin_user):
        """Geloeschte Ereignisse bleiben aus ``events`` heraus — aber nicht aus der Auskunft.

        Anforderung geaendert per Produktentscheid 2026-08-03 (Refs #1755/#1752):
        sie erscheinen jetzt unter ``deleted_events``. Siehe
        ``TestClientExportDeletedEvents``.
        """
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
        assert len(data["deleted_events"]) == 1

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


@pytest.mark.django_db
class TestClientExportCrossTableCategories:
    """Refs #1752 — Quertabellen, die bislang komplett fehlten.

    Episoden, Wirkungsziele/Meilensteine, Aktivitaeten, Personen-Besuche
    sowie die Art.-18-relevanten Aufbewahrungs-Objekte (Legal Hold,
    Loeschvorschlag).
    """

    def test_episodes_included(self, sample_client, facility, sample_case, admin_user):
        Episode.objects.create(
            case=sample_case,
            title="Clearing",
            description="Erstklaerung",
            started_at=timezone.now().date() - timedelta(days=10),
            created_by=admin_user,
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert len(data["episodes"]) == 1
        episode = data["episodes"][0]
        assert episode["title"] == "Clearing"
        assert episode["description"] == "Erstklaerung"
        assert episode["case"] == "Testfall"
        assert episode["created_by"] == "ce_admin"
        assert episode["ended_at"] is None
        assert episode["is_deleted"] is False

    def test_episodes_of_other_client_excluded(self, sample_client, facility, admin_user):
        other = Client.objects.create(facility=facility, pseudonym="Fremd-E", created_by=admin_user)
        other_case = CaseModel.objects.create(facility=facility, client=other, title="Fremdfall", created_by=admin_user)
        Episode.objects.create(
            case=other_case,
            title="Fremd-Episode",
            started_at=timezone.now().date(),
            created_by=admin_user,
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert data["episodes"] == []

    def test_outcome_goals_with_milestones_included(self, sample_client, facility, sample_case, admin_user):
        goal = OutcomeGoal.objects.create(
            case=sample_case,
            title="Eigene Wohnung",
            description="Wohnraum sichern",
            created_by=admin_user,
        )
        Milestone.objects.create(goal=goal, title="Antrag gestellt", sort_order=1)
        Milestone.objects.create(goal=goal, title="Termin Amt", sort_order=0, is_completed=True)
        data = export_client_data(sample_client, facility, admin_user)
        assert len(data["outcome_goals"]) == 1
        entry = data["outcome_goals"][0]
        assert entry["title"] == "Eigene Wohnung"
        assert entry["description"] == "Wohnraum sichern"
        assert entry["case"] == "Testfall"
        assert entry["is_achieved"] is False
        assert [m["title"] for m in entry["milestones"]] == ["Termin Amt", "Antrag gestellt"]
        assert entry["milestones"][0]["is_completed"] is True

    def test_activities_included(self, sample_client, facility, admin_user):
        from core.services.dashboard import log_activity

        log_activity(
            facility=facility,
            actor=admin_user,
            verb=Activity.Verb.CREATED,
            target=sample_client,
            summary="Person angelegt",
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert len(data["activities"]) == 1
        entry = data["activities"][0]
        assert entry["summary"] == "Person angelegt"
        assert entry["verb"] == "erstellt"
        assert entry["target"] == "Person"
        assert entry["actor"] == "ce_admin"

    def test_activities_cover_event_and_workitem_targets(
        self, sample_client, facility, sample_event, sample_workitem, admin_user
    ):
        from core.services.dashboard import log_activity

        log_activity(
            facility=facility, actor=admin_user, verb=Activity.Verb.CREATED, target=sample_event, summary="Kontakt"
        )
        log_activity(
            facility=facility, actor=admin_user, verb=Activity.Verb.COMPLETED, target=sample_workitem, summary="Aufgabe"
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert {e["summary"] for e in data["activities"]} == {"Kontakt", "Aufgabe"}

    def test_activities_of_other_client_excluded(self, sample_client, facility, admin_user):
        from core.services.dashboard import log_activity

        other = Client.objects.create(facility=facility, pseudonym="Fremd-A", created_by=admin_user)
        log_activity(
            facility=facility, actor=admin_user, verb=Activity.Verb.CREATED, target=other, summary="Fremd-Aktivitaet"
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert data["activities"] == []

    def test_recent_visits_included_without_employee_identity(self, sample_client, facility, admin_user):
        RecentClientVisit.objects.create(user=admin_user, client=sample_client, facility=facility, is_favorite=True)
        data = export_client_data(sample_client, facility, admin_user)
        assert len(data["recent_visits"]) == 1
        entry = data["recent_visits"][0]
        assert entry["is_favorite"] is True
        assert entry["visited_at"] is not None
        # Beschaeftigten-Identitaet bleibt draussen (Zugriffs-Protokoll).
        assert "user" not in entry
        assert "ce_admin" not in str(entry)

    def test_legal_hold_on_client_included(self, sample_client, facility, admin_user):
        LegalHold.objects.create(
            facility=facility,
            target_type="Client",
            target_id=sample_client.pk,
            reason="Laufendes Verfahren",
            created_by=admin_user,
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert len(data["legal_holds"]) == 1
        entry = data["legal_holds"][0]
        assert entry["target_type"] == "Client"
        assert entry["reason"] == "Laufendes Verfahren"
        assert entry["is_active"] is True
        assert entry["created_by"] == "ce_admin"

    def test_legal_hold_on_event_included(self, sample_client, facility, sample_event, admin_user):
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=sample_event.pk,
            reason="Pruefverfahren",
            created_by=admin_user,
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert [e["reason"] for e in data["legal_holds"]] == ["Pruefverfahren"]

    def test_legal_hold_of_other_target_excluded(self, sample_client, facility, admin_user):
        other = Client.objects.create(facility=facility, pseudonym="Fremd-L", created_by=admin_user)
        LegalHold.objects.create(
            facility=facility,
            target_type="Client",
            target_id=other.pk,
            reason="Fremd-Hold",
            created_by=admin_user,
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert data["legal_holds"] == []

    def test_retention_proposal_included(self, sample_client, facility, sample_event, admin_user):
        RetentionProposal.objects.create(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=sample_event.pk,
            deletion_due_at=timezone.now().date() + timedelta(days=30),
            retention_category="identified",
            details={"document_type": "Kontakt"},
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert len(data["retention_proposals"]) == 1
        entry = data["retention_proposals"][0]
        assert entry["status"] == "Ausstehend"
        assert entry["retention_category"] == "identified"
        assert entry["deletion_due_at"] is not None
        assert entry["details"] == {"document_type": "Kontakt"}

    def test_retention_proposal_of_other_event_excluded(self, sample_client, facility, doc_type, admin_user):
        other = Client.objects.create(facility=facility, pseudonym="Fremd-R", created_by=admin_user)
        other_event = Event.objects.create(
            facility=facility,
            client=other,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=admin_user,
        )
        RetentionProposal.objects.create(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=other_event.pk,
            deletion_due_at=timezone.now().date(),
            retention_category="identified",
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert data["retention_proposals"] == []


@pytest.mark.django_db
class TestClientExportHistoryContents:
    """Refs #1752 — Aenderungshistorie mit Inhalten statt nur Metadaten."""

    @staticmethod
    def _metadata(**slugs):
        return {
            slug: {"name": name, "sensitivity": "", "is_encrypted": encrypted}
            for slug, (name, encrypted) in slugs.items()
        }

    def test_history_carries_create_values_with_labels(self, sample_client, facility, sample_event, admin_user):
        EventHistory.objects.create(
            event=sample_event,
            changed_by=admin_user,
            action=EventHistory.Action.CREATE,
            data_after={"dauer": 15, "notiz": "Erstkontakt"},
            field_metadata=self._metadata(dauer=("Dauer", False), notiz=("Notiz", False)),
        )
        data = export_client_data(sample_client, facility, admin_user)
        entry = data["event_history"][0]
        assert entry["action"] == "Erstellt"
        assert entry["redacted"] is False
        by_label = {field["label"]: field["value"] for field in entry["data_after"]}
        assert by_label == {"Dauer": 15, "Notiz": "Erstkontakt"}
        assert entry["data_before"] == []

    def test_history_carries_update_before_and_after(self, sample_client, facility, sample_event, admin_user):
        EventHistory.objects.create(
            event=sample_event,
            changed_by=admin_user,
            action=EventHistory.Action.UPDATE,
            data_before={"notiz": "alt"},
            data_after={"notiz": "neu"},
            field_metadata=self._metadata(notiz=("Notiz", False)),
        )
        data = export_client_data(sample_client, facility, admin_user)
        entry = data["event_history"][0]
        assert [f["value"] for f in entry["data_before"]] == ["alt"]
        assert [f["value"] for f in entry["data_after"]] == ["neu"]

    def test_history_identifies_its_event(self, sample_client, facility, sample_event, admin_user):
        EventHistory.objects.create(
            event=sample_event,
            changed_by=admin_user,
            action=EventHistory.Action.CREATE,
            data_after={},
            field_metadata={},
        )
        data = export_client_data(sample_client, facility, admin_user)
        entry = data["event_history"][0]
        assert entry["event"]["document_type"] == "Kontakt"
        assert entry["event"]["is_deleted"] is False
        assert entry["event"]["occurred_at"] is not None

    def test_history_decrypts_encrypted_values(self, sample_client, facility, sample_event, admin_user):
        from cryptography.fernet import Fernet
        from django.test import override_settings

        from core.services.file_vault import encrypt_field

        key = Fernet.generate_key().decode()
        with override_settings(ENCRYPTION_KEY=key):
            EventHistory.objects.create(
                event=sample_event,
                changed_by=admin_user,
                action=EventHistory.Action.CREATE,
                data_after={"diagnose": encrypt_field("Klartext-Befund")},
                field_metadata=self._metadata(diagnose=("Diagnose", True)),
            )
            data = export_client_data(sample_client, facility, admin_user)
        entry = data["event_history"][0]
        assert [f["value"] for f in entry["data_after"]] == ["Klartext-Befund"]

    def test_history_falls_back_to_slug_without_metadata(self, sample_client, facility, sample_event, admin_user):
        EventHistory.objects.create(
            event=sample_event,
            changed_by=admin_user,
            action=EventHistory.Action.CREATE,
            data_after={"unbekannt": "Wert"},
            field_metadata={},
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert data["event_history"][0]["data_after"] == [{"slug": "unbekannt", "label": "unbekannt", "value": "Wert"}]

    def test_history_masks_attachment_markers(self, sample_client, facility, sample_event, admin_user):
        """Anhang-Metadaten bleiben bis #1738 ausgeschlossen — nur der Platzhalter."""
        EventHistory.objects.create(
            event=sample_event,
            changed_by=admin_user,
            action=EventHistory.Action.CREATE,
            data_after={"datei": {"__files__": True, "entries": [{"id": "abc", "sort": 0}]}},
            field_metadata=self._metadata(datei=("Datei", False)),
        )
        data = export_client_data(sample_client, facility, admin_user)
        value = data["event_history"][0]["data_after"][0]["value"]
        assert value == "[Anhang]"
        assert "abc" not in str(data["event_history"][0])

    def test_history_of_redacted_delete_lists_fields_only(self, sample_client, facility, sample_event, admin_user):
        EventHistory.objects.create(
            event=sample_event,
            changed_by=admin_user,
            action=EventHistory.Action.DELETE,
            data_before={"_redacted": True, "fields": ["notiz"]},
            field_metadata=self._metadata(notiz=("Notiz", False)),
        )
        data = export_client_data(sample_client, facility, admin_user)
        entry = data["event_history"][0]
        assert entry["redacted"] is True
        assert entry["data_before"] == [{"slug": "notiz", "label": "Notiz", "value": None}]


@pytest.mark.django_db
class TestClientExportDeletedEvents:
    """Refs #1752 — geloeschte Ereignisse gehoeren bis zum Hard-Purge in die Auskunft.

    Anforderung geaendert per Produktentscheid 2026-08-03
    (#1755); vorher pinnte
    ``test_deleted_events_excluded`` deren vollstaendige Abwesenheit.
    """

    @pytest.fixture
    def deleted_event(self, facility, doc_type, sample_client, admin_user):
        return Event.objects.create(
            facility=facility,
            client=sample_client,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            is_deleted=True,
            created_by=admin_user,
        )

    def test_deleted_events_exported_in_own_section(self, sample_client, facility, deleted_event, admin_user):
        data = export_client_data(sample_client, facility, admin_user)
        assert len(data["deleted_events"]) == 1
        entry = data["deleted_events"][0]
        assert entry["document_type"] == "Kontakt"
        assert entry["is_deleted"] is True

    def test_deleted_events_stay_out_of_live_section(
        self, sample_client, facility, deleted_event, sample_event, admin_user
    ):
        data = export_client_data(sample_client, facility, admin_user)
        assert [e["is_deleted"] for e in data["events"]] == [False]

    def test_history_of_deleted_event_included(self, sample_client, facility, deleted_event, admin_user):
        EventHistory.objects.create(
            event=deleted_event,
            changed_by=admin_user,
            action=EventHistory.Action.DELETE,
            data_before={"_redacted": True, "fields": ["notiz"]},
            field_metadata={"notiz": {"name": "Notiz", "sensitivity": "", "is_encrypted": False}},
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert len(data["event_history"]) == 1
        assert data["event_history"][0]["event"]["is_deleted"] is True

    def test_deletion_request_of_deleted_event_included(
        self, sample_client, facility, deleted_event, admin_user, confirmer_user
    ):
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=deleted_event.pk,
            reason="Auf Wunsch geloescht",
            requested_by=admin_user,
            reviewed_by=confirmer_user,
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert [e["reason"] for e in data["deletion_requests"]] == ["Auf Wunsch geloescht"]

    def test_deleted_events_respect_sensitivity_filter(self, sample_client, facility, staff_user, admin_user):
        high_type = DocumentType.objects.create(
            facility=facility,
            name="Krise",
            category=DocumentType.Category.CONTACT,
            system_type=DocumentType.SystemType.CRISIS,
            sensitivity=DocumentType.Sensitivity.HIGH,
        )
        Event.objects.create(
            facility=facility,
            client=sample_client,
            document_type=high_type,
            occurred_at=timezone.now(),
            data_json={},
            is_deleted=True,
            created_by=admin_user,
        )
        data = export_client_data(sample_client, facility, staff_user)
        assert data["deleted_events"] == []


@pytest.mark.django_db
class TestClientExportAuditTrail:
    """Refs #1752 — Audit-Auszug ueber die Person, ohne Beschaeftigten-Identitaet.

    Produktentscheid 2026-08-03
    (#1755): Aktion,
    Zeitpunkt und Kategorie gehoeren in die Auskunft; Actor-Username/-Name
    nicht (Beschaeftigtendaten-Konflikt).
    """

    def test_view_qualified_is_listed_as_person_category(self, sample_client, facility, admin_user):
        AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.VIEW_QUALIFIED,
            target_type="Client",
            target_id=str(sample_client.pk),
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert len(data["audit_trail"]) == 1
        entry = data["audit_trail"][0]
        assert entry["action"] == "Qualifizierte Daten eingesehen"
        assert entry["category"] == "Person"
        assert entry["timestamp"] is not None

    def test_export_rows_are_listed(self, sample_client, facility, admin_user):
        AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.EXPORT,
            target_type="Client-JSON",
            target_id=str(sample_client.pk),
            detail={"format": "JSON", "pseudonym": sample_client.pseudonym},
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert [e["action"] for e in data["audit_trail"]] == ["Export"]
        assert data["audit_trail"][0]["category"] == "Person"

    def test_client_actions_are_listed(self, sample_client, facility, admin_user):
        AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.CLIENT_UPDATE,
            target_type="Client",
            target_id=str(sample_client.pk),
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert [e["action"] for e in data["audit_trail"]] == ["Person aktualisiert"]

    def test_event_rows_are_listed_as_event_category(self, sample_client, facility, sample_event, admin_user):
        AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.EVENT_CREATE,
            target_type="Event",
            target_id=str(sample_event.pk),
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert [e["category"] for e in data["audit_trail"]] == ["Ereignis"]

    def test_download_rows_are_matched_via_event_detail(self, sample_client, facility, sample_event, admin_user):
        AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.DOWNLOAD,
            target_type="EventAttachment",
            target_id="11111111-1111-1111-1111-111111111111",
            detail={"event_id": str(sample_event.pk), "field": "datei"},
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert [e["category"] for e in data["audit_trail"]] == ["Anhang"]
        assert data["audit_trail"][0]["action"] == "Download"

    def test_entries_carry_no_employee_identity(self, sample_client, facility, admin_user):
        AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.VIEW_QUALIFIED,
            target_type="Client",
            target_id=str(sample_client.pk),
            detail={"pseudonym": sample_client.pseudonym},
            ip_address="203.0.113.9",
        )
        data = export_client_data(sample_client, facility, admin_user)
        entry = data["audit_trail"][0]
        assert set(entry.keys()) == {"action", "category", "target_type", "timestamp"}
        rendered = str(entry)
        assert "ce_admin" not in rendered
        assert "203.0.113.9" not in rendered

    def test_audit_rows_of_other_client_excluded(self, sample_client, facility, admin_user):
        other = Client.objects.create(facility=facility, pseudonym="Fremd-Audit", created_by=admin_user)
        AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.VIEW_QUALIFIED,
            target_type="Client",
            target_id=str(other.pk),
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert data["audit_trail"] == []

    def test_download_of_other_clients_event_excluded(self, sample_client, facility, doc_type, admin_user):
        other = Client.objects.create(facility=facility, pseudonym="Fremd-DL", created_by=admin_user)
        other_event = Event.objects.create(
            facility=facility,
            client=other,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=admin_user,
        )
        AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.DOWNLOAD,
            target_type="EventAttachment",
            target_id="22222222-2222-2222-2222-222222222222",
            detail={"event_id": str(other_event.pk)},
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert data["audit_trail"] == []

    def test_audit_of_deleted_event_included(self, sample_client, facility, doc_type, admin_user):
        deleted = Event.objects.create(
            facility=facility,
            client=sample_client,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            is_deleted=True,
            created_by=admin_user,
        )
        AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.DELETE,
            target_type="Event",
            target_id=str(deleted.pk),
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert [e["action"] for e in data["audit_trail"]] == ["Löschung"]
