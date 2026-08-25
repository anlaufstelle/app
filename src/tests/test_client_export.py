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
from core.services.client import client_activity_targets_q, client_and_event_targets_q, export_client_data
from core.services.client.export import ATTACHMENT_FIELD_PLACEHOLDER


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
        report_key="contact",
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
class TestClientTargetQueries:
    """Gemeinsame Art.-15-/Art.-17-Zielmengen (Refs #1833, #1751)."""

    def test_client_and_event_targets_match_only_the_given_person_and_events(
        self,
        facility,
        admin_user,
        doc_type,
        sample_client,
        sample_event,
    ):
        other_client = Client.objects.create(
            facility=facility,
            pseudonym="Fremd-Target",
            created_by=admin_user,
        )
        other_event = Event.objects.create(
            facility=facility,
            client=other_client,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=admin_user,
        )

        client_hold = LegalHold.objects.create(
            facility=facility,
            target_type="Client",
            target_id=sample_client.pk,
            reason="Eigene Person",
            created_by=admin_user,
        )
        event_hold = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=sample_event.pk,
            reason="Eigenes Ereignis",
            created_by=admin_user,
        )
        for target_type, target_id in (
            ("Client", other_client.pk),
            ("Event", other_event.pk),
            ("Case", sample_client.pk),
        ):
            LegalHold.objects.create(
                facility=facility,
                target_type=target_type,
                target_id=target_id,
                reason="Nicht Teil der Zielmenge",
                created_by=admin_user,
            )

        matches = LegalHold.objects.filter(client_and_event_targets_q(sample_client, [sample_event.pk])).values_list(
            "pk", flat=True
        )
        assert set(matches) == {client_hold.pk, event_hold.pk}

        matches_without_events = LegalHold.objects.filter(client_and_event_targets_q(sample_client, [])).values_list(
            "pk", flat=True
        )
        assert set(matches_without_events) == {client_hold.pk}

    def test_activity_targets_cover_all_four_own_target_types_only(
        self,
        facility,
        admin_user,
        doc_type,
        sample_client,
        sample_event,
        sample_case,
        sample_workitem,
    ):
        from django.contrib.contenttypes.models import ContentType

        other_client = Client.objects.create(
            facility=facility,
            pseudonym="Fremd-Activity",
            created_by=admin_user,
        )
        other_event = Event.objects.create(
            facility=facility,
            client=other_client,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=admin_user,
        )
        other_case = CaseModel.objects.create(
            facility=facility,
            client=other_client,
            title="Fremdfall",
            created_by=admin_user,
        )
        other_workitem = WorkItem.objects.create(
            facility=facility,
            client=other_client,
            title="Fremdaufgabe",
            created_by=admin_user,
        )

        def activity_for(target):
            return Activity.objects.create(
                facility=facility,
                actor=admin_user,
                verb=Activity.Verb.UPDATED,
                target=target,
                context={"schema": 1, "action": Activity.Action.REDACTED.value},
            )

        own = {activity_for(target).pk for target in (sample_client, sample_event, sample_workitem, sample_case)}
        for target in (other_client, other_event, other_workitem, other_case):
            activity_for(target)
        Activity.objects.create(
            facility=facility,
            actor=admin_user,
            verb=Activity.Verb.UPDATED,
            target_type=ContentType.objects.get_for_model(CaseModel),
            target_id=sample_client.pk,
            context={"schema": 1, "action": Activity.Action.REDACTED.value},
        )

        matches = Activity.objects.filter(client_activity_targets_q(sample_client, [sample_event.pk])).values_list(
            "pk", flat=True
        )
        assert set(matches) == own


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
        assert data["client"]["contact_stage"] == "Nur Pseudonym"
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
        sample_event.idempotency_key = "export-internal-key"
        sample_event.idempotency_payload_hash = "a" * 64
        sample_event.save(update_fields=["idempotency_key", "idempotency_payload_hash"])

        data = export_client_data(sample_client, facility, admin_user)
        event_entry = data["events"][0]
        assert event_entry["created_at"] is not None
        assert event_entry["updated_at"] is not None
        assert event_entry["is_deleted"] is False
        assert event_entry["is_anonymous"] is False
        assert "idempotency_key" not in event_entry
        assert "idempotency_payload_hash" not in event_entry
        assert "created_via" not in event_entry

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
            action=Activity.Action.CLIENT_CREATED,
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert len(data["activities"]) == 1
        entry = data["activities"][0]
        assert entry["summary"] == f"Person {sample_client.pseudonym} angelegt"
        assert entry["verb"] == "erstellt"
        assert entry["target"] == "Person"
        assert entry["actor"] == "ce_admin"

    def test_activities_cover_event_and_workitem_targets(
        self, sample_client, facility, sample_event, sample_workitem, admin_user
    ):
        from core.services.dashboard import log_activity

        log_activity(
            facility=facility,
            actor=admin_user,
            verb=Activity.Verb.CREATED,
            target=sample_event,
            action=Activity.Action.EVENT_CREATED,
        )
        log_activity(
            facility=facility,
            actor=admin_user,
            verb=Activity.Verb.COMPLETED,
            target=sample_workitem,
            action=Activity.Action.WORKITEM_COMPLETED,
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert {e["summary"] for e in data["activities"]} == {
            f"{sample_event.document_type.name} für {sample_client.pseudonym}",
            f"Aufgabe erledigt: {sample_workitem.title}",
        }

    def test_activities_of_other_client_excluded(self, sample_client, facility, admin_user):
        from core.services.dashboard import log_activity

        other = Client.objects.create(facility=facility, pseudonym="Fremd-A", created_by=admin_user)
        log_activity(
            facility=facility,
            actor=admin_user,
            verb=Activity.Verb.CREATED,
            target=other,
            action=Activity.Action.CLIENT_CREATED,
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
        """Im Historien-Snapshot bleibt es beim Platzhalter (Refs #1738).

        Anhang-Angaben liefert seit M24 ``_gather_attachments`` aus der
        lebenden Zeile (Feldname, Dateiname, Zeitstempel). Der Snapshot traegt
        dagegen nur rohe Attachment-IDs — als Zahlenkolonne waeren sie keine
        Auskunft, sondern nur ein Leck interner Kennungen.
        """
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
class TestClientExportDeletedCases:
    """Refs #1818 — Faelle im Papierkorb als eigener Auskunfts-Abschnitt.

    Dieselbe Begruendung wie bei den geloeschten Ereignissen (#1752): ein Fall
    im Papierkorb ist bis zur Tilgung weiterhin gespeicherter personenbezogener
    Datenbestand und gehoert in die Auskunft — aber getrennt vom Live-Bestand,
    damit die betroffene Person beides unterscheiden kann. Bis #1818 lieferte
    ``_gather_cases`` **eine** Liste ueber alle Faelle; die Papierkorb-Metadaten
    standen zwar je Fall drin, die Auskunft mischte die beiden Mengen aber in
    denselben Abschnitt.
    """

    @pytest.fixture
    def trashed_case(self, facility, sample_client, admin_user):
        case = CaseModel.objects.create(
            facility=facility,
            client=sample_client,
            title="Papierkorb-Fall",
            description="Papierkorb-Beschreibung",
            created_by=admin_user,
        )
        case.soft_delete(user=admin_user)
        return case

    def test_trashed_case_is_exported_in_its_own_section(self, sample_client, facility, trashed_case, admin_user):
        data = export_client_data(sample_client, facility, admin_user)
        assert len(data["deleted_cases"]) == 1
        entry = data["deleted_cases"][0]
        assert entry["title"] == "Papierkorb-Fall"
        assert entry["is_deleted"] is True
        assert entry["deleted_at"] is not None
        assert entry["deleted_by"] == "ce_admin"

    def test_trashed_case_stays_out_of_the_live_section(
        self, sample_client, facility, trashed_case, sample_case, admin_user
    ):
        data = export_client_data(sample_client, facility, admin_user)
        assert [c["is_deleted"] for c in data["cases"]] == [False]

    def test_live_case_reports_an_empty_deleted_section(self, sample_client, facility, sample_case, admin_user):
        data = export_client_data(sample_client, facility, admin_user)
        assert data["deleted_cases"] == []

    def test_case_entry_reports_the_anonymization_stamp(self, sample_client, facility, trashed_case, admin_user):
        """``anonymized_at`` trennt „im Papierkorb" von „endgueltig getilgt".

        Ohne den Stempel liest sich ein getilgter Fall in der Auskunft wie ein
        wiederherstellbarer — beides ``is_deleted=True``, und der Inhalt ist
        nur deshalb leer, weil ihn niemand gefuellt hat. Die Auskunft muss
        beantworten, ob die Daten noch existieren.
        """
        from core.services.case import anonymize_case

        data = export_client_data(sample_client, facility, admin_user)
        assert data["deleted_cases"][0]["anonymized_at"] is None

        anonymize_case(trashed_case, user=admin_user, trigger="manual")
        data = export_client_data(sample_client, facility, admin_user)
        assert data["deleted_cases"][0]["anonymized_at"] is not None

    def test_live_case_carries_the_stamp_key_as_none(self, sample_client, facility, sample_case, admin_user):
        data = export_client_data(sample_client, facility, admin_user)
        assert data["cases"][0]["anonymized_at"] is None


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
        assert entry["action"] == "Daten der Stufe „Mit Zusatzangaben“ eingesehen"
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

    def test_download_rows_neues_attachment_schema_kategorisiert_als_anhang(
        self, sample_client, facility, sample_event, admin_user
    ):
        """Seit M24 (ADR-039 §5) schreibt die Anhang-Domaene ``target_type="Attachment"``
        — die Kategorie-Zuordnung muss BEIDE Schemata kennen; ohne den neuen
        Praefix fiele jede neue Zeile auf „Sonstiges" (G2-Befund, Refs #1738)."""
        AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.DOWNLOAD,
            target_type="Attachment",
            target_id="11111111-1111-1111-1111-111111111111",
            detail={"event_id": str(sample_event.pk), "field": "datei"},
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert [e["category"] for e in data["audit_trail"]] == ["Anhang"]

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

    @pytest.mark.parametrize("owner_kind", ["client", "case", "event"])
    def test_download_rows_are_matched_via_owner_detail(
        self, sample_client, facility, sample_case, sample_event, admin_user, owner_kind
    ):
        """Neues Detail-Schema der Anhang-Domaene (ADR-039 §5, Refs #1738).

        Download-Zeilen zeigen auf den Anhang, nicht auf seinen Owner; die
        Zuordnung laeuft ueber ``detail``. Seit M24 steht dort ``owner_type``/
        ``owner_id`` statt ``event_id`` — und der Owner kann eine Person oder
        ein Fall sein. Ohne die Erweiterung fiele der Download einer
        personen-eigenen Datei komplett aus dem Zugriffsprotokoll der
        betroffenen Person heraus.
        """
        owner_pk = {
            "client": sample_client.pk,
            "case": sample_case.pk,
            "event": sample_event.pk,
        }[owner_kind]
        AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.DOWNLOAD,
            target_type="Attachment",
            target_id="22222222-2222-2222-2222-222222222222",
            detail={"owner_type": owner_kind, "owner_id": str(owner_pk), "field": "datei"},
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert [e["category"] for e in data["audit_trail"]] == ["Anhang"]
        assert data["audit_trail"][0]["action"] == "Download"

    def test_case_targeted_attachment_rows_are_listed(self, sample_client, facility, sample_case, admin_user):
        """Anhang-Zeilen zeigen per ``target_id`` auf den OWNER (ADR-039 §5, AP2).

        Bei fall-eigenen Uploads ist das die Fall-PK — und die matchte bisher
        keinen einzigen Zweig: ``case_id_strings`` wurde nur im
        DOWNLOAD/``owner_id``-Zweig verwendet. Ein ``SECURITY_VIOLATION`` an
        einem Fall-Upload (abgelehnte Endung, Virus-Fund) fiel damit komplett
        aus dem Zugriffsprotokoll der betroffenen Person heraus (Refs #1738).
        """
        AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.SECURITY_VIOLATION,
            target_type="Attachment",
            target_id=str(sample_case.pk),
            detail={"reason": "extension_not_allowed", "owner_type": "case", "owner_id": str(sample_case.pk)},
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert [e["category"] for e in data["audit_trail"]] == ["Anhang"]
        assert data["audit_trail"][0]["action"] == "Sicherheitsverletzung"

    def test_episode_rows_are_listed_as_episode_category(self, sample_client, facility, sample_case, admin_user):
        """Refs #1959: ``EPISODE_UPDATE`` zeigt auf die **Episoden**-PK.

        Ohne den Episoden-Zweig fiele jede Bearbeitung einer Episode still aus
        dem Zugriffsprotokoll der betroffenen Person — die Zeile traegt weder
        die Personen- noch die Fall-PK, sie matchte keinen einzigen Zweig.
        """
        episode = Episode.objects.create(
            case=sample_case,
            title="Export-Episode",
            started_at=timezone.now().date(),
            created_by=admin_user,
        )
        AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.EPISODE_UPDATE,
            target_type="Episode",
            target_id=str(episode.pk),
            detail={"case_id": str(sample_case.pk), "changed_fields": ["title"]},
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert [e["category"] for e in data["audit_trail"]] == ["Episode"]
        assert data["audit_trail"][0]["action"] == "Episode aktualisiert"

    def test_goal_rows_are_listed_as_goal_category(self, sample_client, facility, sample_case, admin_user):
        """Refs #1959: dasselbe fuer ``GOAL_UPDATE`` — ``target_id`` ist die Ziel-PK."""
        goal = OutcomeGoal.objects.create(case=sample_case, title="Export-Ziel", created_by=admin_user)
        AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.GOAL_UPDATE,
            target_type="OutcomeGoal",
            target_id=str(goal.pk),
            detail={"case_id": str(sample_case.pk), "changed_fields": ["description"]},
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert [e["category"] for e in data["audit_trail"]] == ["Wirkungsziel"]
        assert data["audit_trail"][0]["action"] == "Wirkungsziel aktualisiert"

    def test_episode_and_goal_rows_of_other_client_excluded(self, sample_client, facility, admin_user):
        """Gegenprobe: Episoden/Ziele fremder Faelle duerfen nicht mitmatchen."""
        other = Client.objects.create(facility=facility, pseudonym="Fremd-Episode", created_by=admin_user)
        other_case = CaseModel.objects.create(
            facility=facility,
            client=other,
            title="Fremdfall",
            created_by=admin_user,
        )
        other_episode = Episode.objects.create(
            case=other_case,
            title="Fremd-Episode",
            started_at=timezone.now().date(),
            created_by=admin_user,
        )
        other_goal = OutcomeGoal.objects.create(case=other_case, title="Fremd-Ziel", created_by=admin_user)
        for action, target_type, target_id in (
            (AuditLog.Action.EPISODE_UPDATE, "Episode", other_episode.pk),
            (AuditLog.Action.GOAL_UPDATE, "OutcomeGoal", other_goal.pk),
        ):
            AuditLog.objects.create(
                facility=facility,
                user=admin_user,
                action=action,
                target_type=target_type,
                target_id=str(target_id),
            )
        data = export_client_data(sample_client, facility, admin_user)
        assert data["audit_trail"] == []

    def test_case_event_rows_are_listed_as_event_category(
        self, sample_client, facility, sample_case, sample_event, admin_user
    ):
        """``CASE_EVENT_ASSIGNED``/``-REMOVED`` zeigen auf die Ereignis-PK und
        brauchen deshalb keinen eigenen Zweig (Ticket-Aussage am Code belegt)."""
        for action in (AuditLog.Action.CASE_EVENT_ASSIGNED, AuditLog.Action.CASE_EVENT_REMOVED):
            AuditLog.objects.create(
                facility=facility,
                user=admin_user,
                action=action,
                target_type="Event",
                target_id=str(sample_event.pk),
                detail={"case_id": str(sample_case.pk)},
            )
        data = export_client_data(sample_client, facility, admin_user)
        assert {e["category"] for e in data["audit_trail"]} == {"Ereignis"}
        assert len(data["audit_trail"]) == 2

    def test_case_targeted_rows_of_other_client_excluded(self, sample_client, facility, admin_user):
        """Gegenprobe zum Fall-Zweig: fremde Fall-IDs duerfen nicht mitmatchen."""
        other = Client.objects.create(facility=facility, pseudonym="Fremd-Fall", created_by=admin_user)
        other_case = CaseModel.objects.create(
            facility=facility,
            client=other,
            title="Fremdfall",
            created_by=admin_user,
        )
        AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.SECURITY_VIOLATION,
            target_type="Attachment",
            target_id=str(other_case.pk),
            detail={"reason": "extension_not_allowed", "owner_type": "case", "owner_id": str(other_case.pk)},
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert data["audit_trail"] == []

    def test_download_of_other_clients_owner_excluded(self, sample_client, facility, admin_user):
        """Gegenprobe zum Owner-Schema: fremde Owner-IDs bleiben draussen."""
        other = Client.objects.create(
            facility=facility,
            pseudonym="Fremd-Owner",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=admin_user,
        )
        AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.DOWNLOAD,
            target_type="Attachment",
            target_id="33333333-3333-3333-3333-333333333333",
            detail={"owner_type": "client", "owner_id": str(other.pk), "field": "datei"},
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert data["audit_trail"] == []

    def test_rectification_rows_are_listed_as_own_category(self, sample_client, facility, staff_user, admin_user):
        """Refs #1796: ``target_type="RectificationRequest"`` zeigt auf die ANTRAGS-PK.

        Die matchte bisher keinen einzigen Zweig — weder Personen-, Fall- noch
        Ereignis-PK —, also fiel der komplette Art.-16-Vorgang aus dem
        Zugriffsprotokoll der betroffenen Person heraus (Muster des Fall-Zweigs
        aus Refs #1738).
        """
        from core.services.client.rectification import request_rectification

        request_rectification(sample_client, staff_user, "pseudonym", "Neu-Pseudonym", "Schreibfehler")
        data = export_client_data(sample_client, facility, admin_user)
        assert [e["category"] for e in data["audit_trail"]] == ["Berichtigung"]
        assert data["audit_trail"][0]["action"] == "Berichtigung beantragt"

    def test_rectification_rows_of_other_client_excluded(self, sample_client, facility, staff_user, admin_user):
        """Gegenprobe: fremde Antrags-PKs duerfen nicht mitmatchen."""
        from core.services.client.rectification import request_rectification

        other = Client.objects.create(
            facility=facility,
            pseudonym="Fremd-Berichtigung-Audit",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=admin_user,
        )
        request_rectification(other, staff_user, "pseudonym", "Fremd-Wunsch", "Fremd-Grund")
        data = export_client_data(sample_client, facility, admin_user)
        assert data["audit_trail"] == []


@pytest.fixture
def file_field_template(facility):
    """FILE-Feldvorlage fuer Anhang-Tests (Refs #1738)."""
    from core.models import FieldTemplate

    return FieldTemplate.objects.create(
        facility=facility,
        name="Nachweis",
        field_type=FieldTemplate.FieldType.FILE,
    )


def _make_attachment(facility, owner, field_template, user, filename="Mietvertrag.pdf", **overrides):
    """Attachment-Zeile mit verschluesseltem Originalnamen (ohne Disk-Datei).

    Die Auskunft liefert ausschliesslich **Metadaten** (M4/#1060, ADR-039) —
    fuer alle Pruefungen hier ist keine ``.enc``-Datei noetig.
    """
    from core.models import Attachment
    from core.models.attachment import owner_field_name
    from core.services.file_vault import encrypt_field

    defaults = {
        "facility": facility,
        "field_template": field_template,
        "storage_filename": "0000-storage.enc",
        "original_filename_encrypted": encrypt_field(filename),
        "file_size": 2048,
        "mime_type": "application/octet-stream",
        "detected_mime": "application/pdf",
        "created_by": user,
        owner_field_name(owner): owner,
    }
    defaults.update(overrides)
    return Attachment.objects.create(**defaults)


@pytest.mark.django_db
class TestClientExportAttachments:
    """Art.-15-Auskunft ueber Dateianhaenge — alle drei Owner (Refs #1738, ADR-039).

    Produktentscheid M24: Attachment-**Metadaten** gehoeren in JSON und PDF,
    Binaerinhalte nicht (konsistent mit M4/#1060). Das schliesst zugleich die
    Bestandsluecke, dass der Export bis dahin ueberhaupt keine Anhang-Daten
    enthielt — auch nicht fuer Ereignisse.
    """

    EXPECTED_KEYS = {
        "field",
        "field_slug",
        "filename",
        "file_size",
        "mime_type",
        "uploaded_at",
        "uploaded_by",
        "is_current",
        "superseded_at",
        "removed_at",
    }

    def test_client_owned_attachment_is_a_top_level_category(
        self, sample_client, facility, file_field_template, admin_user
    ):
        _make_attachment(facility, sample_client, file_field_template, admin_user)
        data = export_client_data(sample_client, facility, admin_user)
        assert len(data["attachments"]) == 1
        entry = data["attachments"][0]
        assert set(entry) == self.EXPECTED_KEYS
        assert entry["field"] == "Nachweis"
        assert entry["field_slug"] == file_field_template.slug
        assert entry["filename"] == "Mietvertrag.pdf"
        assert entry["file_size"] == 2048
        assert entry["uploaded_by"] == admin_user.username
        assert entry["is_current"] is True
        assert entry["superseded_at"] is None
        assert entry["removed_at"] is None

    def test_top_level_category_is_empty_list_without_attachments(self, sample_client, facility, admin_user):
        data = export_client_data(sample_client, facility, admin_user)
        assert data["attachments"] == []

    def test_case_owned_attachment_is_nested_in_its_case(
        self, sample_client, facility, sample_case, file_field_template, admin_user
    ):
        _make_attachment(facility, sample_case, file_field_template, admin_user, filename="Fallakte.pdf")
        data = export_client_data(sample_client, facility, admin_user)
        assert [a["filename"] for a in data["cases"][0]["attachments"]] == ["Fallakte.pdf"]

    def test_event_owned_attachment_is_nested_in_its_event(
        self, sample_client, facility, sample_event, file_field_template, admin_user
    ):
        """Schliesst die Bestandsluecke: Ereignis-Anhaenge fehlten bisher komplett."""
        _make_attachment(facility, sample_event, file_field_template, admin_user, filename="Ereignis.pdf")
        data = export_client_data(sample_client, facility, admin_user)
        assert [a["filename"] for a in data["events"][0]["attachments"]] == ["Ereignis.pdf"]

    def test_nested_key_absent_when_owner_has_no_attachments(
        self, sample_client, facility, sample_case, sample_event, admin_user
    ):
        """``Key nur wenn nicht leer`` — das Bestandsformat bleibt bitgleich."""
        data = export_client_data(sample_client, facility, admin_user)
        assert "attachments" not in data["cases"][0]
        assert "attachments" not in data["events"][0]

    def test_mime_prefers_the_verified_type(self, sample_client, facility, file_field_template, admin_user):
        """``detected_mime or mime_type`` — der browser-gemeldete Typ ist nur Fallback."""
        _make_attachment(facility, sample_client, file_field_template, admin_user)
        _make_attachment(
            facility,
            sample_client,
            file_field_template,
            admin_user,
            filename="Ohne-Erkennung.bin",
            detected_mime="",
            mime_type="text/plain",
        )
        data = export_client_data(sample_client, facility, admin_user)
        by_name = {a["filename"]: a["mime_type"] for a in data["attachments"]}
        assert by_name["Mietvertrag.pdf"] == "application/pdf"
        assert by_name["Ohne-Erkennung.bin"] == "text/plain"

    def test_removed_attachment_is_listed_with_its_removal_date(
        self, sample_client, facility, file_field_template, admin_user
    ):
        """Soft-deletete Kettenglieder bleiben gespeicherte Daten der Person.

        Bis zum physischen Loeschen (Owner-Lebenszyklus) existiert die Datei
        weiter — die Auskunft weist sie deshalb aus und markiert sie ueber
        ``removed_at`` als entfernt, statt sie zu verschweigen.
        """
        removed_at = timezone.now() - timedelta(days=3)
        _make_attachment(
            facility,
            sample_client,
            file_field_template,
            admin_user,
            filename="Entfernt.pdf",
            deleted_at=removed_at,
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert [a["filename"] for a in data["attachments"]] == ["Entfernt.pdf"]
        assert data["attachments"][0]["removed_at"] == removed_at.isoformat()

    def test_superseded_version_is_listed_as_not_current(
        self, sample_client, facility, file_field_template, admin_user
    ):
        """Die Versionskette gehoert vollstaendig zur Auskunft."""
        superseded_at = timezone.now() - timedelta(days=1)
        current = _make_attachment(facility, sample_client, file_field_template, admin_user, filename="V2.pdf")
        _make_attachment(
            facility,
            sample_client,
            file_field_template,
            admin_user,
            filename="V1.pdf",
            entry_id=current.entry_id,
            is_current=False,
            superseded_by=current,
            superseded_at=superseded_at,
        )
        data = export_client_data(sample_client, facility, admin_user)
        old = next(a for a in data["attachments"] if a["filename"] == "V1.pdf")
        assert old["is_current"] is False
        assert old["superseded_at"] == superseded_at.isoformat()

    def test_no_storage_internals_leak_into_the_export(
        self, sample_client, facility, sample_event, file_field_template, admin_user
    ):
        """Keine Binaerinhalte, keine Speicherpfade, keine Marker (M4/#1060, ADR-039).

        Der Ereignis-Zweig ist der schaerfste Fall: Ereignisse tragen seit der
        E8-Aera (ADR-037 §9) FILE-Marker in ``data_json`` — der **gesamte
        Bestand** laeuft also durch :func:`_serialize_event`. Ohne den
        Platzhalter-Zweig landete das rohe ``__files__``-Dict samt
        Attachment-UUIDs in ``events[i]["data"][slug]`` (JSON) und in der
        PDF-Spalte „Daten" (G4-Blocker, Refs #1738).
        """
        import json

        attachment = _make_attachment(facility, sample_client, file_field_template, admin_user)
        event_attachment = _make_attachment(
            facility, sample_event, file_field_template, admin_user, filename="Ereignis.pdf"
        )
        sample_event.data_json = {
            **sample_event.data_json,
            file_field_template.slug: {
                "__files__": True,
                "entries": [{"id": str(event_attachment.pk), "sort": 0}],
            },
        }
        sample_event.save(update_fields=["data_json"])

        data = export_client_data(sample_client, facility, admin_user)
        payload = json.dumps(data, default=str, ensure_ascii=False)
        assert attachment.storage_filename not in payload
        assert "original_filename_encrypted" not in payload
        assert str(attachment.pk) not in payload
        assert str(event_attachment.pk) not in payload
        assert "__files__" not in payload
        assert "__file__" not in payload
        # Positiv-Kontrolle: das Feld faellt nicht weg, es wird nur lesbar.
        assert data["events"][0]["data"][file_field_template.slug] == ATTACHMENT_FIELD_PLACEHOLDER

    def test_attachments_are_fetched_per_owner_kind_not_per_owner(
        self, sample_client, facility, doc_type, file_field_template, admin_user
    ):
        """N+1-Pin: drei Anhang-Queries, unabhaengig von der Zahl der Owner (Refs #1738).

        Die Auskunft sammelte pro Fall und pro Ereignis eine eigene Query
        (``owner.attachments``). Bei einer Person mit langer Historie skaliert
        das linear — ausgerechnet auf dem Pfad, der ohnehin den gesamten
        Datenbestand einer Person zusammentraegt. Gebuendelt sind es genau drei:
        je eine pro Owner-Sorte.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        for i in range(3):
            case = CaseModel.objects.create(
                facility=facility,
                client=sample_client,
                title=f"Fall {i}",
                created_by=admin_user,
            )
            _make_attachment(facility, case, file_field_template, admin_user, filename=f"Fall-{i}.pdf")
            event = Event.objects.create(
                facility=facility,
                client=sample_client,
                document_type=doc_type,
                occurred_at=timezone.now() - timedelta(days=i + 1),
                data_json={},
                created_by=admin_user,
            )
            _make_attachment(facility, event, file_field_template, admin_user, filename=f"Ereignis-{i}.pdf")
        _make_attachment(facility, sample_client, file_field_template, admin_user)

        with CaptureQueriesContext(connection) as ctx:
            data = export_client_data(sample_client, facility, admin_user)

        attachment_queries = [q["sql"] for q in ctx.captured_queries if "core_attachment" in q["sql"]]
        assert len(attachment_queries) == 3, f"{len(attachment_queries)} Anhang-Queries:\n" + "\n".join(
            attachment_queries
        )
        # Positiv-Kontrolle: die Buendelung darf nichts verlieren.
        assert [a["filename"] for a in data["attachments"]] == ["Mietvertrag.pdf"]
        assert sorted(a["filename"] for case in data["cases"] for a in case["attachments"]) == [
            "Fall-0.pdf",
            "Fall-1.pdf",
            "Fall-2.pdf",
        ]
        assert sorted(a["filename"] for event in data["events"] for a in event["attachments"]) == [
            "Ereignis-0.pdf",
            "Ereignis-1.pdf",
            "Ereignis-2.pdf",
        ]

    def test_undecryptable_filename_is_named_as_encrypted_not_as_download(
        self, sample_client, facility, file_field_template, admin_user
    ):
        """Ein unlesbares Namens-Token darf keinen Dateinamen erfinden (Refs #1738).

        ``get_original_filename`` faellt auf ``"download"`` zurueck — sinnvoll
        fuer den Content-Disposition-Header, in einer Auskunft aber eine
        Falschaussage: von einer echten Datei namens „download" ist der
        Fallback nicht zu unterscheiden. Der Modul-Standard
        ``safe_decrypt(..., default="[verschlüsselt]")`` sagt statt dessen, dass
        der Name nicht entschluesselbar ist.
        """
        _make_attachment(
            facility,
            sample_client,
            file_field_template,
            admin_user,
            original_filename_encrypted={"__encrypted__": True, "value": "kaputtes-token"},
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert data["attachments"][0]["filename"] == "[verschlüsselt]"

    def test_attachments_of_another_client_are_excluded(self, sample_client, facility, file_field_template, admin_user):
        other = Client.objects.create(
            facility=facility,
            pseudonym="Fremd-Anhang",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=admin_user,
        )
        _make_attachment(facility, other, file_field_template, admin_user, filename="Fremd.pdf")
        data = export_client_data(sample_client, facility, admin_user)
        assert data["attachments"] == []

    def test_file_marker_field_renders_a_readable_placeholder(
        self, sample_client, facility, file_field_template, admin_user
    ):
        """Marker-Dicts duerfen nicht roh in den Zusatzfeldern erscheinen.

        FILE-Felder speichern in ``data_json`` nur einen Verweis
        (``__files__``-Marker). Ohne eigenen Zweig laendete das rohe Dict samt
        Attachment-UUIDs im Auskunftsdokument — technisch und unlesbar. Die
        eigentlichen Angaben stehen im Anhang-Abschnitt.
        """
        from core.models import DocumentTypeField

        dt = DocumentType.objects.create(
            facility=facility,
            name="Personenprofil-Datei",
            entity_type=DocumentType.EntityType.CLIENT,
        )
        DocumentTypeField.objects.create(document_type=dt, field_template=file_field_template, sort_order=0)
        sample_client.document_type = dt
        sample_client.data_json = {
            file_field_template.slug: {
                "__files__": True,
                "entries": [{"id": "44444444-4444-4444-4444-444444444444", "sort": 0}],
            }
        }
        sample_client.save(update_fields=["document_type", "data_json"])

        data = export_client_data(sample_client, facility, admin_user)
        entry = next(e for e in data["client"]["dynamic_data"] if e["slug"] == file_field_template.slug)
        assert "__files__" not in str(entry)
        assert "44444444" not in str(entry)
        assert "Dateianhang" in entry["value"]


def _select_field_on_client(facility, sample_client, name="Wohnsituation", slug_label=("wohnungslos", "Wohnungslos")):
    """SELECT-Zusatzfeld an einem Personen-Dokumentationstyp (Refs #1796).

    Liefert das ``FieldTemplate`` zurueck; ``sample_client`` traegt danach den
    Typ, damit ein ``data_json.<slug>``-Berichtigungsantrag zulaessig ist.
    """
    from core.models import DocumentTypeField, FieldTemplate

    slug, label = slug_label
    ft = FieldTemplate.objects.create(
        facility=facility,
        name=name,
        field_type=FieldTemplate.FieldType.SELECT,
        options_json=[{"slug": slug, "label": label}],
    )
    dt = DocumentType.objects.create(
        facility=facility,
        name="Personenprofil-Berichtigung",
        entity_type=DocumentType.EntityType.CLIENT,
    )
    DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
    sample_client.document_type = dt
    sample_client.save(update_fields=["document_type"])
    return ft


@pytest.mark.django_db
class TestClientExportRectificationRequests:
    """Refs #1796 — Art.-16-Berichtigungsantraege gehoeren in die Art.-15-Auskunft.

    Der Antrag (ADR-041, M4/#1060) ist gespeicherte Information ueber die
    betroffene Person: er benennt das strittige Feld, den alten und den
    gewuenschten Wert sowie die Begruendung. Ausgewiesen werden ALLE Status —
    ein abgelehnter Antrag ist fuer die betroffene Person die interessanteste
    Zeile, nicht die unwichtigste.
    """

    EXPECTED_KEYS = {
        "field_path",
        "field_label",
        "status",
        "old_value",
        "new_value",
        "reason",
        "review_note",
        "requested_by",
        "reviewed_by",
        "created_at",
        "reviewed_at",
    }

    def test_pending_request_is_exported_with_decrypted_values(self, sample_client, facility, staff_user, admin_user):
        from core.services.client.rectification import request_rectification

        request_rectification(sample_client, staff_user, "pseudonym", "Neu-Pseudonym", "Schreibfehler im Pseudonym")
        data = export_client_data(sample_client, facility, admin_user)
        assert len(data["rectification_requests"]) == 1
        entry = data["rectification_requests"][0]
        assert set(entry) == self.EXPECTED_KEYS
        assert entry["field_path"] == "pseudonym"
        assert entry["field_label"] == "Pseudonym"
        assert entry["status"] == "Ausstehend"
        assert entry["old_value"] == "Export-Klientel"
        assert entry["new_value"] == "Neu-Pseudonym"
        assert entry["reason"] == "Schreibfehler im Pseudonym"
        # NULL (nie geprueft) wird als ``None`` exportiert — die Auskunft
        # unterscheidet "kein Pruefvermerk vorhanden" von einem echten leeren
        # Freitext (Review-Befund #1796).
        assert entry["review_note"] is None
        assert entry["requested_by"] == "ce_staff"
        assert entry["reviewed_by"] is None
        assert entry["created_at"] is not None
        assert entry["reviewed_at"] is None

    def test_rejected_request_carries_status_reviewer_and_note(
        self, sample_client, facility, staff_user, lead_user, admin_user
    ):
        """Abgelehnte Antraege bleiben in der Auskunft — samt Pruefvermerk."""
        from core.services.client.rectification import reject_rectification, request_rectification

        rr = request_rectification(sample_client, staff_user, "notes", "Korrigierte Notiz", "Sachlich falsch")
        reject_rectification(rr, lead_user, "Nicht nachvollziehbar")
        data = export_client_data(sample_client, facility, admin_user)
        entry = data["rectification_requests"][0]
        assert entry["field_label"] == "Notizen"
        assert entry["status"] == "Abgelehnt"
        assert entry["review_note"] == "Nicht nachvollziehbar"
        assert entry["reviewed_by"] == "ce_lead"
        assert entry["reviewed_at"] is not None

    def test_age_cluster_values_are_resolved_to_labels(self, sample_client, facility, staff_user, admin_user):
        """Ein Code wie ``18_26`` waere fuer die betroffene Person keine Auskunft."""
        from core.services.client.rectification import request_rectification

        request_rectification(sample_client, staff_user, "age_cluster", Client.AgeCluster.AGE_27_PLUS, "Alter falsch")
        data = export_client_data(sample_client, facility, admin_user)
        entry = data["rectification_requests"][0]
        assert entry["field_label"] == "Altersgruppe"
        assert entry["old_value"] == "18–26"
        assert entry["new_value"] == "27+"

    def test_dynamic_field_uses_template_label_and_display_value(self, sample_client, facility, staff_user, admin_user):
        """``data_json.<slug>``: Label aus der Feldvorlage, Wert als Options-Label."""
        from core.services.client.rectification import request_rectification

        ft = _select_field_on_client(facility, sample_client)
        request_rectification(sample_client, staff_user, f"data_json.{ft.slug}", "wohnungslos", "Lage geaendert")
        data = export_client_data(sample_client, facility, admin_user)
        entry = data["rectification_requests"][0]
        assert entry["field_path"] == f"data_json.{ft.slug}"
        assert entry["field_label"] == "Wohnsituation"
        assert entry["new_value"] == "Wohnungslos"
        assert entry["old_value"] is None

    def test_orphan_dynamic_path_falls_back_to_the_raw_field_path(
        self, sample_client, facility, staff_user, admin_user
    ):
        """Ein spaeter entferntes Feld darf die Auskunft nicht reissen (Refs #1725-Muster)."""
        from core.models import RectificationRequest
        from core.services.client.rectification import _encode_value

        RectificationRequest.objects.create(
            facility=facility,
            target_client=sample_client,
            field_path="data_json.verwaist",
            old_value=_encode_value("alt"),
            new_value=_encode_value("neu"),
            requested_by=staff_user,
        )
        data = export_client_data(sample_client, facility, admin_user)
        entry = data["rectification_requests"][0]
        assert entry["field_label"] == "data_json.verwaist"
        assert entry["new_value"] == "neu"

    def test_request_of_another_client_is_excluded(self, sample_client, facility, staff_user, admin_user):
        from core.services.client.rectification import request_rectification

        other = Client.objects.create(
            facility=facility,
            pseudonym="Fremd-Berichtigung",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=admin_user,
        )
        request_rectification(other, staff_user, "pseudonym", "Fremd-Wunsch", "Fremd-Grund")
        data = export_client_data(sample_client, facility, admin_user)
        assert data["rectification_requests"] == []

    def test_request_of_another_facility_is_excluded(self, sample_client, facility, staff_user, admin_user):
        """Defense-in-Depth: ``for_facility`` zusaetzlich zu RLS (Muster Loeschantrag)."""
        from core.models import RectificationRequest
        from core.services.client.rectification import _encode_value

        other_org = Organization.objects.create(name="Fremd-Org-1796")
        other_facility = Facility.objects.create(organization=other_org, name="Fremd-Einrichtung-1796")
        RectificationRequest.objects.create(
            facility=other_facility,
            target_client=sample_client,
            field_path="pseudonym",
            old_value=_encode_value("alt"),
            new_value=_encode_value("Fremd-Facility-Wunsch"),
            requested_by=staff_user,
        )
        data = export_client_data(sample_client, facility, admin_user)
        assert data["rectification_requests"] == []

    def test_redacted_request_is_listed_without_values(self, sample_client, facility, staff_user, admin_user):
        """Nach der Anonymisierung (Refs #1792) sind alle vier Wertspalten NULL.

        Die Zeile bleibt als Vier-Augen-Nachweis stehen — die Auskunft muss sie
        ohne Absturz ausweisen, mit leeren Werten statt Klartext.
        """
        from core.models import RectificationRequest
        from core.services.client.rectification import request_rectification

        request_rectification(sample_client, staff_user, "pseudonym", "Neu-Pseudonym", "Schreibfehler")
        RectificationRequest.objects.filter(target_client=sample_client).update(
            old_value=None,
            new_value=None,
            reason=None,
            review_note=None,
        )
        data = export_client_data(sample_client, facility, admin_user)
        entry = data["rectification_requests"][0]
        assert entry["field_label"] == "Pseudonym"
        assert entry["status"] == "Ausstehend"
        # Alle vier Wertspalten sind ``None`` — nicht ``""``: die Auskunft
        # unterscheidet "redigiert" von "leer eingereicht" (Review-Befund
        # #1796; das PDF rendert ``None`` als "—", echte leere Werte leer).
        assert entry["old_value"] is None
        assert entry["new_value"] is None
        assert entry["reason"] is None
        assert entry["review_note"] is None

    def test_corrupt_token_yields_the_placeholder_instead_of_crashing(
        self, sample_client, facility, staff_user, admin_user
    ):
        """Muster ``test_history_decrypts_encrypted_values`` — Gegenprobe mit kaputtem Token."""
        from core.models import RectificationRequest

        RectificationRequest.objects.create(
            facility=facility,
            target_client=sample_client,
            field_path="pseudonym",
            old_value={"__encrypted__": True, "value": "kaputtes-token"},
            new_value={"__encrypted__": True, "value": "kaputtes-token"},
            reason={"__encrypted__": True, "value": "kaputtes-token"},
            requested_by=staff_user,
        )
        data = export_client_data(sample_client, facility, admin_user)
        entry = data["rectification_requests"][0]
        assert entry["old_value"] == "[verschlüsselt]"
        assert entry["new_value"] == "[verschlüsselt]"
        assert entry["reason"] == "[verschlüsselt]"

    def test_empty_list_without_requests(self, sample_client, facility, admin_user):
        data = export_client_data(sample_client, facility, admin_user)
        assert data["rectification_requests"] == []


@pytest.fixture
def fully_populated_client(facility, doc_type, sample_client, admin_user, confirmer_user, lead_user):
    """Eine Person mit je einem Datensatz aus jeder Auskunfts-Kategorie (Refs #1752)."""
    # Refs #1876 (ADR-045 §7): ein Merge-Tombstone — der abgeleitete PDF-Guard
    # prueft jede Kategorie durch Leeren, ein leeres ``merged_from`` waere
    # blind. Der Merge laeuft VOR den Mutationen unten: ``k_anonymized`` und
    # der PENDING-Loeschantrag wuerden ``_validate_merge`` sonst abweisen.
    from core.services.client import merge_clients

    merge_source = Client.objects.create(
        facility=facility,
        pseudonym="PDF-Duplikat",
        contact_stage=Client.ContactStage.IDENTIFIED,
        created_by=admin_user,
    )
    merge_clients(merge_source, sample_client, lead_user)

    sample_client.notes = "Notiz-Freitext-PDF"
    sample_client.k_anonymized = True
    sample_client.save(update_fields=["notes", "k_anonymized"])

    case = CaseModel.objects.create(
        facility=facility,
        client=sample_client,
        title="PDF-Fall",
        description="Fall-Beschreibung",
        created_by=admin_user,
    )
    # Refs #1818: ein Fall im Papierkorb — der abgeleitete PDF-Guard prueft
    # jede Kategorie durch Leeren, ein leeres ``deleted_cases`` waere blind.
    trashed_case = CaseModel.objects.create(
        facility=facility,
        client=sample_client,
        title="PDF-Papierkorb-Fall",
        description="Papierkorb-Beschreibung",
        created_by=admin_user,
    )
    trashed_case.soft_delete(user=admin_user)
    episode = Episode.objects.create(
        case=case,
        title="PDF-Episode",
        description="Episoden-Text",
        started_at=timezone.now().date(),
        created_by=admin_user,
    )
    goal = OutcomeGoal.objects.create(case=case, title="PDF-Ziel", description="Ziel-Text", created_by=admin_user)
    Milestone.objects.create(goal=goal, title="PDF-Meilenstein", sort_order=0)
    event = Event.objects.create(
        facility=facility,
        client=sample_client,
        document_type=doc_type,
        case=case,
        episode=episode,
        occurred_at=timezone.now(),
        data_json={"notiz": "Ereignis-Klartext"},
        created_by=admin_user,
    )
    deleted_event = Event.objects.create(
        facility=facility,
        client=sample_client,
        document_type=doc_type,
        occurred_at=timezone.now() - timedelta(days=2),
        data_json={},
        is_deleted=True,
        created_by=admin_user,
    )
    EventHistory.objects.create(
        event=event,
        changed_by=admin_user,
        action=EventHistory.Action.CREATE,
        data_after={"notiz": "Historien-Klartext"},
        field_metadata={"notiz": {"name": "Notiz", "sensitivity": "", "is_encrypted": False}},
    )
    DeletionRequest.objects.create(
        facility=facility,
        target_type=DeletionRequest.TargetType.CLIENT,
        target_id=sample_client.pk,
        reason="PDF-Loeschgrund",
        requested_by=admin_user,
        reviewed_by=confirmer_user,
    )
    # Refs #1796: Berichtigungsantrag (Art. 16, ADR-041) — eigene PDF-Nadel.
    from core.services.client.rectification import request_rectification

    request_rectification(sample_client, admin_user, "notes", "PDF-Wunschnotiz", "PDF-Berichtigungsgrund")
    LegalHold.objects.create(
        facility=facility,
        target_type="Event",
        target_id=deleted_event.pk,
        reason="PDF-Hold-Grund",
        created_by=admin_user,
    )
    RetentionProposal.objects.create(
        facility=facility,
        target_type=RetentionProposal.TargetType.EVENT,
        target_id=event.pk,
        deletion_due_at=timezone.now().date() + timedelta(days=5),
        retention_category="pdf-kategorie",
    )
    workitem = WorkItem.objects.create(
        facility=facility,
        client=sample_client,
        created_by=admin_user,
        assigned_to=lead_user,
        title="PDF-Aufgabe",
        description="Aufgaben-Text",
    )
    Activity.objects.create(
        facility=facility,
        actor=admin_user,
        verb=Activity.Verb.CREATED,
        target=workitem,
        context={"schema": 1, "action": Activity.Action.WORKITEM_CREATED},
    )
    RecentClientVisit.objects.create(user=admin_user, client=sample_client, facility=facility, is_favorite=True)
    AuditLog.objects.create(
        facility=facility,
        user=admin_user,
        action=AuditLog.Action.VIEW_QUALIFIED,
        target_type="Client",
        target_id=str(sample_client.pk),
    )
    # Refs #1738: je ein Anhang an Person, Fall und Ereignis — die drei Owner
    # haben im PDF drei verschiedene Fundstellen (eigener Abschnitt bzw. Spalte
    # in Faelle/Kontakte); der Paritaets-Guard deckt nur Top-Level-Keys ab.
    from core.models import FieldTemplate

    ft_pdf = FieldTemplate.objects.create(
        facility=facility,
        name="PDF-Anhangfeld",
        field_type=FieldTemplate.FieldType.FILE,
    )
    pdf_owners = (
        (sample_client, "PDF-Personenakte.pdf"),
        (case, "PDF-Fallakte.pdf"),
        (event, "PDF-Ereignisakte.pdf"),
    )
    for owner, filename in pdf_owners:
        _make_attachment(facility, owner, ft_pdf, admin_user, filename=filename)
    return sample_client


def _render_export_html(client_obj, facility, user):
    """PDF erzeugen und das HTML abfangen, das WeasyPrint zu sehen bekommt."""
    from unittest.mock import patch

    import weasyprint

    from core.services.client import export_client_data_pdf

    captured = {}
    real_html = weasyprint.HTML

    def capturing_html(*args, **kwargs):
        captured["string"] = kwargs.get("string", "")
        return real_html(*args, **kwargs)

    with patch("core.services.client.export.weasyprint.HTML", side_effect=capturing_html):
        pdf_bytes = export_client_data_pdf(client_obj, facility, user)
    assert pdf_bytes[:4] == b"%PDF"
    return captured["string"]


@pytest.mark.django_db
class TestClientExportPdfCoverage:
    """Refs #1752 — JSON und PDF decken denselben Umfang ab.

    Das PDF darf zusammenfassen, muss aber jede Kategorie enthalten.
    """

    # Top-Level-Keys aus ``export_client_data``, die im PDF-Template legitim
    # KEINEN eigenen Abschnitt haben (Refs #1765). Jeder Eintrag traegt eine
    # am Code verifizierbare Begruendung — siehe
    # ``test_every_category_has_its_own_pdf_section``.
    _PDF_SECTION_EXEMPT = {
        "export_meta": (
            "export_client_data_pdf() (core/services/client/export.py) "
            "entpackt export_meta NICHT als eigenen Abschnitt, sondern in "
            "facility_name/generated_at des Template-Kontexts — beide "
            "fliessen in Kopf- bzw. Fusszeile des PDFs, nicht in einen "
            "eigenen nummerierten Abschnitt."
        ),
    }

    @pytest.mark.parametrize(
        "needle",
        [
            "Notiz-Freitext-PDF",  # Client.notes
            "K-anonymisiert",  # Anonymisierungs-Metadaten
            "PDF-Fall",  # Fall
            "PDF-Papierkorb-Fall",  # Fall im Papierkorb (Refs #1818)
            "PDF-Episode",  # Episode
            "PDF-Ziel",  # Wirkungsziel
            "PDF-Meilenstein",  # Meilenstein
            "Ereignis-Klartext",  # Ereignis-Daten
            "Historien-Klartext",  # Historien-Inhalte
            "PDF-Loeschgrund",  # Loeschantrag mit Client-Target
            "PDF-Berichtigungsgrund",  # Berichtigungsantrag (Refs #1796)
            "PDF-Hold-Grund",  # Legal Hold
            "pdf-kategorie",  # Loeschvorschlag
            "PDF-Aufgabe",  # Aufgabe
            "Aufgabe: PDF-Aufgabe",  # Zeitstrom (zur Anzeigezeit gerendert)
            "Daten der Stufe „Mit Zusatzangaben“ eingesehen",  # Audit-Auszug
            "PDF-Duplikat",  # Zusammenfuehrungs-Herkunft (Refs #1876)
        ],
    )
    def test_pdf_contains_every_category(self, fully_populated_client, facility, admin_user, needle):
        html = _render_export_html(fully_populated_client, facility, admin_user)
        assert needle in html

    @pytest.mark.parametrize(
        "needle",
        [
            "PDF-Personenakte.pdf",  # personen-eigener Anhang (eigener Abschnitt)
            "PDF-Fallakte.pdf",  # fall-eigener Anhang (Spalte im Fall-Abschnitt)
            "PDF-Ereignisakte.pdf",  # ereignis-eigener Anhang (Spalte im Kontakt-Abschnitt)
        ],
    )
    def test_pdf_names_attachments_of_every_owner(self, fully_populated_client, facility, admin_user, needle):
        """Refs #1738: Der Paritaets-Guard unten deckt nur Top-Level-Keys ab.

        Die fall- und ereignis-eigenen Anhaenge haengen als verschachtelte
        Schluessel in ``cases``/``events`` — sie brauchen daher eigene Nadeln,
        sonst koennte ihr Template-Zweig fehlen, ohne dass ein Test bricht.
        """
        html = _render_export_html(fully_populated_client, facility, admin_user)
        assert needle in html

    def test_pdf_lists_deleted_events_section(self, fully_populated_client, facility, admin_user):
        html = _render_export_html(fully_populated_client, facility, admin_user)
        assert "Gelöschte Ereignisse" in html

    def test_pdf_lists_recent_visits_section(self, fully_populated_client, facility, admin_user):
        html = _render_export_html(fully_populated_client, facility, admin_user)
        assert "Merkliste" in html

    def test_pdf_audit_section_omits_employee_identity(self, fully_populated_client, facility, admin_user):
        """Der Audit-Abschnitt darf keine Beschaeftigten-Identitaet zeigen."""
        html = _render_export_html(fully_populated_client, facility, admin_user)
        # Letztes Vorkommen = die Abschnitts-Ueberschrift (das erste steht im
        # Hinweis-Kasten oben); danach folgen nur noch Tabelle und Footer.
        audit_section = html.rsplit("Zugriffsprotokoll", 1)[1]
        assert "Daten der Stufe „Mit Zusatzangaben“ eingesehen" in audit_section
        assert "ce_admin" not in audit_section

    def test_pdf_names_the_deliberate_exclusions(self, fully_populated_client, facility, admin_user):
        """Die Auskunft muss ihre bewussten Ausschluesse benennen statt Vollstaendigkeit zu behaupten.

        Anforderung geaendert mit M24 (Refs #1738): Anhang-**Metadaten** waren
        bis dahin selbst ein erklaerter Ausschluss und sind jetzt Teil der
        Auskunft. Uebrig bleiben zwei Ausschluesse — die Beschaeftigten-
        Identitaet im Zugriffsprotokoll (Art. 15 Abs. 4, Rechte Dritter) und
        die Binaerinhalte der Dateien (M4/#1060).
        """
        html = _render_export_html(fully_populated_client, facility, admin_user)
        notice = html.split("<h2>", 1)[0]
        assert "Beschäftigten" in notice
        assert "Inhalte der Dateien" in notice
        assert "Nicht enthalten sind: Angaben zu Dateianhängen" not in notice

    def test_every_category_has_its_own_pdf_section(self, fully_populated_client, facility, admin_user):
        """Abgeleiteter Guard (Refs #1765) — Vorbild ``TestKAnonBridgeRedactionParity``.

        Der obige parametrisierte Test prueft 14 fest verdrahtete Nadeln gegen
        das PDF; erweitert ``export_client_data`` (core/services/client/export.py)
        eine bestehende oder neue Kategorie, ohne dass jemand eine passende
        Nadel ergaenzt, bleibt dieser Test blind gruen (genau das Risiko aus
        #1765).

        Dieser Guard iteriert stattdessen ueber die tatsaechlichen Top-Level-
        Keys aus ``export_client_data(fully_populated_client, ...)`` — die
        Ist-Menge, kein Hardcode — und rendert das Template zweimal: einmal
        mit den vollen Daten, einmal mit GENAU EINER auf "leer" gesetzten
        Kategorie (``[]`` fuer Listen, ``{}`` fuer den ``client``-Block).
        Aendert sich das gerenderte HTML dabei nicht, hat die Kategorie keine
        eigene Repraesentation im Template — sie lief am PDF vorbei.
        """
        from django.template.loader import render_to_string

        data = export_client_data(fully_populated_client, facility, admin_user)
        context = {key: value for key, value in data.items() if key != "export_meta"}
        context["facility_name"] = data["export_meta"]["facility_name"]
        context["generated_at"] = timezone.now()
        baseline_html = render_to_string("core/export/client_data_pdf.html", context)

        for key, value in data.items():
            if key in self._PDF_SECTION_EXEMPT:
                continue
            emptied_context = dict(context)
            emptied_context[key] = [] if isinstance(value, list) else {}
            emptied_html = render_to_string("core/export/client_data_pdf.html", emptied_context)
            assert emptied_html != baseline_html, (
                f"Kategorie {key!r} veraendert das gerenderte PDF nicht, wenn sie geleert wird "
                "— sie hat vermutlich keinen eigenen Abschnitt im Template "
                "(core/export/client_data_pdf.html). Entweder das Template ergaenzen oder die "
                "Kategorie in _PDF_SECTION_EXEMPT mit nachpruefbarer Begruendung eintragen."
            )


@pytest.mark.django_db
class TestClientExportCompleteness:
    """Refs #1752 — Vollstaendigkeits-Pin ueber die Kategorienliste des Issues.

    Faellt eine Kategorie kuenftig aus ``export_client_data`` heraus, bricht
    dieser Test — unabhaengig davon, ob ein Detail-Test sie noch abdeckt.
    """

    # Kategorie -> Pfad im Export-Dict (Top-Level-Key bzw. client-Unterfeld).
    REQUIRED_LIST_CATEGORIES = [
        "events",
        "deleted_events",
        "cases",
        # Refs #1818: Faelle im Papierkorb als eigener Abschnitt (ADR-044).
        "deleted_cases",
        # Refs #1738 (M24): Anhang-Metadaten der Person als eigene Kategorie.
        "attachments",
        "episodes",
        "outcome_goals",
        "event_history",
        "deletion_requests",
        # Refs #1796: Art.-16-Berichtigungsantraege (ADR-041).
        "rectification_requests",
        "legal_holds",
        "retention_proposals",
        "work_items",
        "activities",
        "recent_visits",
        # Refs #1876 (ADR-045 §7): Zusammenfuehrungs-Herkunft des Ziels.
        "merged_from",
        "audit_trail",
    ]
    REQUIRED_CLIENT_FIELDS = [
        "notes",
        "is_deleted",
        "deleted_at",
        "deleted_by",
        "k_anonymized",
        "updated_at",
    ]

    def test_every_category_is_populated(self, fully_populated_client, facility, admin_user):
        data = export_client_data(fully_populated_client, facility, admin_user)
        empty = [key for key in self.REQUIRED_LIST_CATEGORIES if not data.get(key)]
        assert empty == [], f"Kategorien ohne Daten in der Auskunft: {empty}"

    def test_client_master_data_covers_required_fields(self, fully_populated_client, facility, admin_user):
        data = export_client_data(fully_populated_client, facility, admin_user)
        missing = [key for key in self.REQUIRED_CLIENT_FIELDS if key not in data["client"]]
        assert missing == [], f"Fehlende Stammdaten-Felder: {missing}"

    def test_history_entries_carry_contents(self, fully_populated_client, facility, admin_user):
        data = export_client_data(fully_populated_client, facility, admin_user)
        assert any(entry["data_after"] or entry["data_before"] for entry in data["event_history"])

    def test_json_view_serializes_every_category(self, fully_populated_client, facility, admin_user):
        """Die neuen Kategorien muessen den ``json.dumps``-Pfad des Views ueberstehen."""
        import json

        client = DjangoClient()
        client.force_login(admin_user)
        response = client.get(f"/clients/{fully_populated_client.pk}/export/json/")
        assert response.status_code == 200
        payload = json.loads(response.content.decode("utf-8"))
        empty = [key for key in self.REQUIRED_LIST_CATEGORIES if not payload.get(key)]
        assert empty == [], f"Kategorien ohne Daten im JSON-Download: {empty}"


@pytest.mark.django_db
class TestClientExportMergeProvenance:
    """Refs #1876 (ADR-045 §7) — die Auskunft weist Zusammenfuehrungen aus.

    Ziel-Export: ``merged_from`` als eigene Top-Level-Kategorie — je Tombstone
    Pseudonym, Zeitpunkt und Tilgungsstatus, gelesen aus der **lebenden**
    Tombstone-Zeile (Muster ``_gather_attachments``): nach der Redaktion steht
    dort der redigierte Marker, das Klartext-Pseudonym der Quelle lebt in der
    Auskunft also exakt so lange wie im Papierkorb selbst.

    Quell-Export: solange der Tombstone existiert, benennt ``client.merged_into``
    die Ziel-Akte — die Art.-15-Antwort „wohin sind die Daten gewandert".
    Der Zeitpunkt ist das vorhandene ``deleted_at`` (Soft-Delete und Merge
    laufen in derselben Transaktion, ADR-045 §2).
    """

    @pytest.fixture
    def merged_pair(self, facility, sample_client, admin_user, lead_user):
        from core.services.client import merge_clients

        source = Client.objects.create(
            facility=facility,
            pseudonym="Merge-Quelle",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=admin_user,
        )
        merge_clients(source, sample_client, lead_user)
        source.refresh_from_db()
        return source, sample_client

    def test_target_export_lists_the_merged_source(self, merged_pair, facility, admin_user):
        source, target = merged_pair
        data = export_client_data(target, facility, admin_user)
        assert data["merged_from"] == [
            {
                "pseudonym": "Merge-Quelle",
                "merged_at": source.deleted_at.isoformat(),
                "anonymized": False,
            }
        ]

    def test_merged_from_is_always_present(self, sample_client, facility, admin_user):
        data = export_client_data(sample_client, facility, admin_user)
        assert data["merged_from"] == []

    def test_redacted_tombstone_keeps_only_the_redacted_marker(self, merged_pair, facility, admin_user):
        """Live-Zeilen-Lektuere: die Redaktion des Tombstones erfasst die Auskunft mit."""
        from core.services.client import anonymize_client

        source, target = merged_pair
        anonymize_client(source)
        data = export_client_data(target, facility, admin_user)
        (entry,) = data["merged_from"]
        assert entry["anonymized"] is True
        assert "Merge-Quelle" not in entry["pseudonym"]

    def test_source_tombstone_export_names_the_target(self, merged_pair, facility, admin_user):
        source, target = merged_pair
        data = export_client_data(source, facility, admin_user)
        assert data["client"]["merged_into"] == target.pseudonym
        assert data["client"]["is_deleted"] is True

    def test_unmerged_client_has_no_merged_into_key(self, sample_client, facility, admin_user):
        """Bestandsformat bitgleich — der Schluessel existiert nur am Tombstone."""
        data = export_client_data(sample_client, facility, admin_user)
        assert "merged_into" not in data["client"]

    def test_pdf_names_the_target_on_the_tombstone_export(self, merged_pair, facility, admin_user):
        """Der ``merged_into``-Wert haengt im ``client``-Block und laeuft damit am
        Top-Level-Paritaets-Guard vorbei — eigene Nadel (Muster Anhang-Spalten)."""
        source, target = merged_pair
        html = _render_export_html(source, facility, admin_user)
        assert "Zusammengeführt in" in html
        assert target.pseudonym in html

    def test_merge_request_row_is_marked(self, facility, sample_client, admin_user, staff_user):
        """Ein Merge-Antrag darf in der Auskunft nicht als Loeschantrag durchgehen
        (dasselbe Prinzip wie in der Antragsliste: die Antragsart beim Namen nennen)."""
        from core.services.client import request_client_merge

        source = Client.objects.create(
            facility=facility,
            pseudonym="Merge-Antrag-Quelle",
            contact_stage=Client.ContactStage.QUALIFIED,
            created_by=admin_user,
        )
        request_client_merge(source, sample_client, staff_user, "Doppelt angelegt")
        data = export_client_data(source, facility, admin_user)
        (row,) = data["deletion_requests"]
        assert row["merge_request"] is True
        html = _render_export_html(source, facility, admin_user)
        assert "Zusammenführung" in html.rsplit("Löschanträge", 1)[1]

    def test_plain_deletion_request_row_keeps_its_format(self, facility, sample_client, admin_user):
        """Kein ``merge_request``-Schluessel an gewoehnlichen Loeschantraegen."""
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=sample_client.pk,
            reason="Nur Loeschung",
            requested_by=admin_user,
        )
        data = export_client_data(sample_client, facility, admin_user)
        (row,) = data["deletion_requests"]
        assert "merge_request" not in row


@pytest.mark.django_db
class TestAnonymizationRedactsOnlyOwnCaseRequests:
    """Die Fall-Antrags-Redaktion der Personen-Anonymisierung ist fall-gebunden.

    Refs #1818: ``_redact_case_deletion_requests`` (``core/services/client/main.py``)
    grenzt ueber ``target_id__in`` auf die Faelle der anonymisierten Person ein.
    Ohne diese Grenze ueberschriebe jede Personen-Anonymisierung die
    Begruendungen SAEMTLICHER Fall-Loeschantraege datenbankweit und
    facility-uebergreifend — einschliesslich PENDING-Vier-Augen-Antraege
    lebender Faelle fremder Personen; der Reviewer saehe "[Anonymisiert]"
    statt der Begruendung, die auch die Art.-15-Auskunft des fremden Falls
    traegt.
    """

    def test_foreign_case_request_survives_person_anonymization(self, sample_client, sample_case, facility, admin_user):
        from core.services.case import request_case_deletion
        from core.services.client import anonymize_client_now

        own_dr = request_case_deletion(sample_case, admin_user, "Eigener Antrag")

        fremd_org = Organization.objects.create(name="Fremd-Org")
        fremd_facility = Facility.objects.create(organization=fremd_org, name="Fremd-Einrichtung")
        fremd_user = User.objects.create_user(
            username="ce_fremd",
            password="test1234",
            role=User.Role.FACILITY_ADMIN,
            facility=fremd_facility,
            is_staff=True,
        )
        fremd_client = Client.objects.create(facility=fremd_facility, pseudonym="Fremd-Person", created_by=fremd_user)
        fremd_case = CaseModel.objects.create(
            facility=fremd_facility, client=fremd_client, title="Fremder Fall", created_by=fremd_user
        )
        fremd_dr = request_case_deletion(fremd_case, fremd_user, "Fremder Antrag")

        sample_client.soft_delete(user=admin_user)
        anonymize_client_now(sample_client, admin_user)

        own_dr.refresh_from_db()
        fremd_dr.refresh_from_db()
        # Positivkontrolle: der Antrag zum eigenen Fall ist redigiert ...
        assert own_dr.reason == "[Anonymisiert]"
        # ... der fremde PENDING-Antrag bleibt wortgleich stehen.
        assert fremd_dr.reason == "Fremder Antrag"
        assert fremd_dr.status == DeletionRequest.Status.PENDING
