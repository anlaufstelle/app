"""Tests für Event-CRUD."""

from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, DeletionRequest, Event, EventHistory
from core.services.event import (
    approve_deletion,
    create_event,
    reject_deletion,
    request_deletion,
    soft_delete_event,
    update_event,
)


@pytest.mark.django_db
class TestEventService:
    def test_create_event_creates_history(self, facility, staff_user, doc_type_contact):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
        )
        assert event.pk is not None
        assert EventHistory.objects.filter(event=event, action=EventHistory.Action.CREATE).exists()

    def test_event_history_stores_field_metadata(self, facility, staff_user, doc_type_contact):
        """EventHistory.field_metadata must capture slug -> name/sensitivity/is_encrypted."""
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15, "notiz": "Test"},
        )
        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.CREATE).first()
        assert entry.field_metadata
        assert "dauer" in entry.field_metadata
        assert "notiz" in entry.field_metadata
        for slug in ("dauer", "notiz"):
            meta = entry.field_metadata[slug]
            assert "name" in meta
            assert "sensitivity" in meta
            assert "is_encrypted" in meta

    def test_update_event_stores_field_metadata(self, facility, staff_user, doc_type_contact):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
        )
        update_event(event, staff_user, {"dauer": 30})
        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.UPDATE).first()
        assert entry.field_metadata
        assert "dauer" in entry.field_metadata

    def test_soft_delete_stores_field_metadata(self, facility, staff_user, doc_type_contact):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
        )
        soft_delete_event(event, staff_user)
        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.DELETE).first()
        assert entry.field_metadata
        assert "dauer" in entry.field_metadata

    def test_update_event_creates_history(self, facility, staff_user, doc_type_contact):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
        )
        update_event(event, staff_user, {"dauer": 30})
        history = EventHistory.objects.filter(event=event, action=EventHistory.Action.UPDATE).first()
        assert history is not None
        assert history.data_before == {"dauer": 15}
        assert history.data_after == {"dauer": 30}

    def test_soft_delete_event(self, facility, staff_user, doc_type_contact):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
        )
        soft_delete_event(event, staff_user)
        event.refresh_from_db()
        assert event.is_deleted is True
        history = EventHistory.objects.filter(event=event, action=EventHistory.Action.DELETE).first()
        assert history is not None
        assert history.data_before == {"_redacted": True, "fields": ["dauer"]}
        assert AuditLog.objects.filter(action=AuditLog.Action.DELETE, target_type="Event").exists()

    def test_request_deletion_creates_request(self, sample_event, staff_user):
        dr = request_deletion(sample_event, staff_user, "DSGVO-Löschung")
        assert dr.status == DeletionRequest.Status.PENDING
        assert dr.reason == "DSGVO-Löschung"

    def test_approve_deletion(self, sample_event, staff_user, lead_user):
        dr = request_deletion(sample_event, staff_user, "DSGVO")
        approve_deletion(dr, lead_user)
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED
        assert dr.reviewed_by == lead_user
        sample_event.refresh_from_db()
        assert sample_event.is_deleted is True

    def test_reject_deletion(self, sample_event, staff_user, lead_user):
        dr = request_deletion(sample_event, staff_user, "DSGVO")
        reject_deletion(dr, lead_user)
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.REJECTED
        assert dr.reviewed_by == lead_user


@pytest.mark.django_db
class TestEventServiceAtomicity:
    def test_approve_deletion_rolls_back_on_failure(self, sample_event, staff_user, lead_user):
        """If deletion_request.save() fails, soft_delete must also be rolled back."""
        dr = request_deletion(sample_event, staff_user, "DSGVO")

        with patch.object(DeletionRequest, "save", side_effect=RuntimeError("DB error")):
            with pytest.raises(RuntimeError, match="DB error"):
                approve_deletion(dr, lead_user)

        # Event must NOT be soft-deleted because the transaction was rolled back.
        sample_event.refresh_from_db()
        assert sample_event.is_deleted is False

        # No EventHistory DELETE or AuditLog should have been created.
        assert not EventHistory.objects.filter(event=sample_event, action=EventHistory.Action.DELETE).exists()
        assert not AuditLog.objects.filter(target_type="Event", target_id=str(sample_event.pk)).exists()

        # DeletionRequest should still be PENDING.
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING

    def test_soft_delete_rolls_back_on_audit_failure(self, sample_event, staff_user):
        """If AuditLog creation fails, the soft-delete and history must be rolled back."""
        with patch.object(AuditLog.objects, "create", side_effect=RuntimeError("Audit error")):
            with pytest.raises(RuntimeError, match="Audit error"):
                soft_delete_event(sample_event, staff_user)

        sample_event.refresh_from_db()
        assert sample_event.is_deleted is False
        assert not EventHistory.objects.filter(event=sample_event, action=EventHistory.Action.DELETE).exists()


@pytest.mark.django_db
class TestEventCreateView:
    def test_event_create_form_renders(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create"))
        assert response.status_code == 200

    def test_event_create_with_client_preselect(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create") + f"?client={client_identified.pk}")
        assert response.status_code == 200

    def test_event_create_success(self, client, staff_user, doc_type_contact, client_identified):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type_contact.pk),
                "client": str(client_identified.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "dauer": "15",
                "notiz": "Testnotiz",
            },
        )
        assert response.status_code == 302
        assert Event.objects.filter(document_type=doc_type_contact, created_by=staff_user).exists()

    def test_event_create_anonymous(self, client, staff_user, doc_type_contact):
        """Without client selection, event is automatically anonymous."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type_contact.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "dauer": "5",
                "notiz": "",
            },
        )
        assert response.status_code == 302
        event = Event.objects.filter(is_anonymous=True).first()
        assert event is not None
        assert event.client is None

    def test_event_create_assistant_allowed(self, client, assistant_user):
        client.force_login(assistant_user)
        response = client.get(reverse("core:event_create"))
        assert response.status_code == 200

    def test_event_create_form_shows_case_dropdown(self, client, staff_user, case_open):
        """Form enthält das Case-Select + lädt Fälle pro Klientel per Fetch (Refs #620)."""
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create"))
        assert response.status_code == 200
        content = response.content.decode()
        assert 'name="case"' in content
        # Der Inhalt wird dynamisch über /api/cases/for-client/ nach Klientel-
        # Auswahl geladen — die URL muss im Rendering-Payload auftauchen.
        assert "/api/cases/for-client/" in content

    def test_event_create_assigns_case(self, client, staff_user, doc_type_contact, client_identified, case_open):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type_contact.pk),
                "client": str(client_identified.pk),
                "case": str(case_open.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "dauer": "15",
                "notiz": "Fallbezug",
            },
        )
        assert response.status_code == 302
        event = Event.objects.filter(document_type=doc_type_contact, created_by=staff_user).first()
        assert event is not None
        assert event.case_id == case_open.pk

    def test_event_create_rejects_case_of_other_client(self, client, staff_user, facility, doc_type_contact, case_open):
        """Case is bound to client_identified; picking a different client must fail."""
        from core.models import Client as ClientModel

        other = ClientModel.objects.create(
            facility=facility,
            pseudonym="Orca",
            contact_stage=ClientModel.ContactStage.IDENTIFIED,
        )
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type_contact.pk),
                "client": str(other.pk),
                "case": str(case_open.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "dauer": "15",
                "notiz": "Mismatch",
            },
        )
        assert response.status_code == 200
        assert not Event.objects.filter(created_by=staff_user).exists()


@pytest.mark.django_db
class TestEventDetailView:
    def test_event_detail_renders(self, client, staff_user, sample_event):
        client.force_login(staff_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": sample_event.pk}))
        assert response.status_code == 200
        assert "Kontakt" in response.content.decode()

    def test_event_detail_facility_scoping(self, client, staff_user, facility, organization, doc_type_contact):
        from core.models import Facility

        other_facility = Facility.objects.create(organization=organization, name="Andere")
        other_doc = doc_type_contact.__class__.objects.create(facility=other_facility, name="Kontakt")
        event = Event.objects.create(
            facility=other_facility,
            document_type=other_doc,
            occurred_at=timezone.now(),
            data_json={},
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event.pk}))
        assert response.status_code == 404


@pytest.mark.django_db
class TestEventUpdateView:
    def test_event_update_form_renders(self, client, staff_user, sample_event):
        client.force_login(staff_user)
        response = client.get(reverse("core:event_update", kwargs={"pk": sample_event.pk}))
        assert response.status_code == 200

    def test_event_update_creates_history(self, client, staff_user, sample_event):
        client.force_login(staff_user)
        client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}),
            {"dauer": "30", "notiz": "Aktualisiert"},
        )
        assert EventHistory.objects.filter(
            event=sample_event,
            action=EventHistory.Action.UPDATE,
        ).exists()


@pytest.mark.django_db
class TestEventDeleteView:
    def test_event_delete_confirm_renders(self, client, staff_user, sample_event):
        client.force_login(staff_user)
        response = client.get(reverse("core:event_delete", kwargs={"pk": sample_event.pk}))
        assert response.status_code == 200

    def test_event_delete_identified_direct(self, client, staff_user, sample_event):
        """Identified client → direkte Löschung."""
        client.force_login(staff_user)
        response = client.post(reverse("core:event_delete", kwargs={"pk": sample_event.pk}))
        assert response.status_code == 302
        sample_event.refresh_from_db()
        assert sample_event.is_deleted is True

    def test_event_delete_qualified_creates_request(
        self, client, staff_user, facility, doc_type_contact, client_qualified
    ):
        """Qualified client → Löschantrag."""
        event = Event.objects.create(
            facility=facility,
            client=client_qualified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 10},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_delete", kwargs={"pk": event.pk}),
            {"reason": "DSGVO-Anfrage"},
        )
        assert response.status_code == 302
        event.refresh_from_db()
        assert event.is_deleted is False  # Noch nicht gelöscht
        assert DeletionRequest.objects.filter(target_id=event.pk).exists()

    def test_event_delete_anonymous_direct(self, client, staff_user, facility, doc_type_contact):
        """Anonymes Event → direkte Löschung."""
        event = Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.post(reverse("core:event_delete", kwargs={"pk": event.pk}))
        assert response.status_code == 302
        event.refresh_from_db()
        assert event.is_deleted is True


@pytest.mark.django_db
class TestDeletionReview:
    def test_deletion_review_lead_can_access(
        self, client, lead_user, staff_user, facility, doc_type_contact, client_qualified
    ):
        event = Event.objects.create(
            facility=facility,
            client=client_qualified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Test",
            requested_by=staff_user,
        )
        client.force_login(lead_user)
        response = client.get(reverse("core:deletion_review", kwargs={"pk": dr.pk}))
        assert response.status_code == 200

    def test_deletion_review_approve(self, client, lead_user, staff_user, facility, doc_type_contact, client_qualified):
        event = Event.objects.create(
            facility=facility,
            client=client_qualified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Test",
            requested_by=staff_user,
        )
        client.force_login(lead_user)
        response = client.post(
            reverse("core:deletion_review", kwargs={"pk": dr.pk}),
            {"action": "approve"},
        )
        assert response.status_code == 302
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED
        event.refresh_from_db()
        assert event.is_deleted is True

    def test_deletion_review_reject(self, client, lead_user, staff_user, facility, doc_type_contact, client_qualified):
        event = Event.objects.create(
            facility=facility,
            client=client_qualified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Test",
            requested_by=staff_user,
        )
        client.force_login(lead_user)
        response = client.post(
            reverse("core:deletion_review", kwargs={"pk": dr.pk}),
            {"action": "reject"},
        )
        assert response.status_code == 302
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.REJECTED

    def test_deletion_review_same_user_cannot_approve(
        self, client, lead_user, facility, doc_type_contact, client_qualified
    ):
        """Reviewer ≠ Requester."""
        event = Event.objects.create(
            facility=facility,
            client=client_qualified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=lead_user,
        )
        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Test",
            requested_by=lead_user,
        )
        client.force_login(lead_user)
        response = client.post(
            reverse("core:deletion_review", kwargs={"pk": dr.pk}),
            {"action": "approve"},
        )
        assert response.status_code == 302
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING  # Nicht genehmigt

    def test_deletion_review_staff_forbidden(self, client, staff_user, facility, doc_type_contact, client_qualified):
        event = Event.objects.create(
            facility=facility,
            client=client_qualified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Test",
            requested_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:deletion_review", kwargs={"pk": dr.pk}))
        assert response.status_code == 403


@pytest.mark.django_db
class TestEventCreateDefaultDocType:
    """Tests fuer Standard-Dokumentationstyp aus Einstellungen (#156)."""

    def test_default_document_type_preselected(self, client, staff_user, facility, doc_type_contact):
        """Wenn ein Standard-Dokumentationstyp gesetzt ist, wird er vorausgewaehlt."""
        from core.models import Settings

        Settings.objects.create(facility=facility, default_document_type=doc_type_contact)
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create"))
        assert response.status_code == 200
        content = response.content.decode()
        # Der Default-Typ sollte selected sein
        assert f'value="{doc_type_contact.pk}"' in content
        # selected-Attribut in derselben <option>
        import re

        assert re.search(rf'value="{doc_type_contact.pk}"[^>]*selected', content)
        # Dynamische Felder sollten vorgerendert sein
        assert "Dauer" in content

    def test_no_default_document_type(self, client, staff_user, facility):
        """Ohne Standard-Dokumentationstyp wird kein Typ vorausgewaehlt."""
        from core.models import Settings

        Settings.objects.create(facility=facility)
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "selected" not in content or 'value="" selected' not in content

    def test_no_settings_object(self, client, staff_user, facility):
        """Ohne Settings-Objekt soll die Seite trotzdem fehlerfrei laden."""
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create"))
        assert response.status_code == 200

    def test_inactive_default_document_type_ignored(self, client, staff_user, facility):
        """Inaktiver Standard-Dokumentationstyp wird nicht vorausgewaehlt."""
        from core.models import DocumentType, Settings

        inactive_dt = DocumentType.objects.create(
            facility=facility,
            name="Inaktiv",
            category=DocumentType.Category.CONTACT,
            is_active=False,
        )
        Settings.objects.create(facility=facility, default_document_type=inactive_dt)
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create"))
        assert response.status_code == 200
        content = response.content.decode()
        # Inaktiver Typ sollte nicht selected sein
        assert f'value="{inactive_dt.pk}" selected' not in content


@pytest.mark.django_db
class TestEventFieldsPartial:
    def test_event_fields_partial(self, client, staff_user, doc_type_contact):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:event_fields_partial"),
            {"document_type": str(doc_type_contact.pk)},
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "Dauer" in content
        assert "Notiz" in content

    def test_event_fields_partial_empty(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:event_fields_partial"))
        assert response.status_code == 200


@pytest.mark.django_db
class TestClientAutocomplete:
    def test_autocomplete_returns_json(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.get(reverse("core:client_autocomplete"), {"q": "ID"})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["pseudonym"] == "Test-ID-01"

    def test_autocomplete_empty_query_returns_clients(self, client, staff_user, client_identified):
        """Empty query returns active clients (sorted by recency)."""
        client.force_login(staff_user)
        response = client.get(reverse("core:client_autocomplete"), {"q": ""})
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        pseudonyms = [d["pseudonym"] for d in data]
        assert "Test-ID-01" in pseudonyms

    def test_autocomplete_sorted_by_recency(self, client, staff_user, facility, doc_type_contact):
        """Clients with more recent events appear first."""
        from core.models import Client

        old_client = Client.objects.create(facility=facility, pseudonym="Sort-Old", created_by=staff_user)
        new_client = Client.objects.create(facility=facility, pseudonym="Sort-New", created_by=staff_user)
        # Create events at different times
        create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now() - timezone.timedelta(days=10),
            data_json={"Dauer": 30},
            client=old_client,
        )
        create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now() - timezone.timedelta(days=1),
            data_json={"Dauer": 30},
            client=new_client,
        )

        client.force_login(staff_user)
        response = client.get(reverse("core:client_autocomplete"), {"q": "Sort"})
        data = response.json()
        assert len(data) == 2
        assert data[0]["pseudonym"] == "Sort-New"
        assert data[1]["pseudonym"] == "Sort-Old"

    def test_autocomplete_clients_without_events_at_bottom(self, client, staff_user, facility, doc_type_contact):
        """Clients without events appear after clients with events."""
        from core.models import Client

        with_event = Client.objects.create(facility=facility, pseudonym="Rank-A", created_by=staff_user)
        Client.objects.create(facility=facility, pseudonym="Rank-B", created_by=staff_user)
        create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"Dauer": 30},
            client=with_event,
        )

        client.force_login(staff_user)
        response = client.get(reverse("core:client_autocomplete"), {"q": "Rank"})
        data = response.json()
        assert len(data) == 2
        assert data[0]["pseudonym"] == "Rank-A"
        assert data[1]["pseudonym"] == "Rank-B"

    def test_autocomplete_single_char_query(self, client, staff_user, client_identified):
        """Single character query now returns results."""
        client.force_login(staff_user)
        response = client.get(reverse("core:client_autocomplete"), {"q": "T"})
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1


@pytest.mark.django_db
class TestMinContactStageGate:
    """Tests for min_contact_stage enforcement in event creation."""

    def test_create_event_rejected_when_stage_too_low(self, facility, staff_user, client_identified):
        """Identified client cannot create event requiring qualified stage."""
        from core.models import DocumentType

        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Nur Qualifiziert",
            category=DocumentType.Category.SERVICE,
            min_contact_stage="qualified",
        )
        with pytest.raises(Exception, match="Kontaktstufe"):
            create_event(
                facility=facility,
                user=staff_user,
                document_type=doc_type,
                occurred_at=timezone.now(),
                data_json={"test": "data"},
                client=client_identified,
            )

    def test_create_event_allowed_when_stage_sufficient(self, facility, staff_user, client_qualified):
        """Qualified client can create event requiring qualified stage."""
        from core.models import DocumentType

        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Nur Qualifiziert",
            category=DocumentType.Category.SERVICE,
            min_contact_stage="qualified",
        )
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"test": "data"},
            client=client_qualified,
        )
        assert event.pk is not None

    def test_create_event_rejected_when_anonymous_and_min_stage(self, facility, staff_user):
        """Anonymous events are rejected when doc_type has min_contact_stage."""
        from core.models import DocumentType

        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Nur Qualifiziert",
            category=DocumentType.Category.SERVICE,
            min_contact_stage="qualified",
        )
        with pytest.raises(Exception, match="Anonyme Kontakte"):
            create_event(
                facility=facility,
                user=staff_user,
                document_type=doc_type,
                occurred_at=timezone.now(),
                data_json={},
                is_anonymous=True,
            )

    def test_create_event_anonymous_allowed_without_min_stage(self, facility, staff_user):
        """Anonymous events are allowed when doc_type has no min_contact_stage."""
        from core.models import DocumentType

        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Frei",
            category=DocumentType.Category.NOTE,
        )
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            is_anonymous=True,
        )
        assert event.pk is not None
        assert event.is_anonymous is True

    def test_create_event_no_gate_when_no_min_stage(self, facility, staff_user, client_identified):
        """No gate when document_type has no min_contact_stage."""
        from core.models import DocumentType

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=DocumentType.objects.create(
                facility=facility,
                name="Frei",
                category=DocumentType.Category.NOTE,
            ),
            occurred_at=timezone.now(),
            data_json={},
            client=client_identified,
        )
        assert event.pk is not None

    def test_form_accepts_low_stage_defers_to_service(self, facility, staff_user, client_identified):
        """EventMetaForm.clean() no longer checks contact stage (deferred to service)."""
        from core.forms.events import EventMetaForm
        from core.models import DocumentType

        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Nur Qualifiziert",
            category=DocumentType.Category.SERVICE,
            min_contact_stage="qualified",
            is_active=True,
        )
        form = EventMetaForm(
            data={
                "document_type": str(doc_type.pk),
                "client": str(client_identified.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
            },
            facility=facility,
        )
        assert form.is_valid()

    def test_create_event_auto_anonymous_when_no_client(self, facility, staff_user):
        """Event without client is auto-normalized to anonymous."""
        from core.models import DocumentType

        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Frei",
            category=DocumentType.Category.NOTE,
        )
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            client=None,
            is_anonymous=False,
        )
        assert event.pk is not None
        assert event.is_anonymous is True
        assert event.client is None

    def test_create_event_no_client_with_min_stage_rejected(self, facility, staff_user):
        """Event without client and min_contact_stage still raises ValidationError."""
        from core.models import DocumentType

        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Nur Qualifiziert",
            category=DocumentType.Category.SERVICE,
            min_contact_stage="qualified",
        )
        with pytest.raises(Exception, match="Klientel ausgewählt werden"):
            create_event(
                facility=facility,
                user=staff_user,
                document_type=doc_type,
                occurred_at=timezone.now(),
                data_json={},
                client=None,
                is_anonymous=False,
            )

    def test_form_valid_without_client_and_min_stage_doctype(self, facility, staff_user):
        """EventMetaForm is valid without client — anonymous check deferred to service."""
        from core.forms.events import EventMetaForm
        from core.models import DocumentType

        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Nur Qualifiziert",
            category=DocumentType.Category.SERVICE,
            min_contact_stage="qualified",
            is_active=True,
        )
        form = EventMetaForm(
            data={
                "document_type": str(doc_type.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
            },
            facility=facility,
        )
        assert form.is_valid()


@pytest.mark.django_db
class TestDocumentTypeRoleFilter:
    """Restriktive DocumentTypes dürfen nur Rollen mit ausreichender
    Sensitivity-Berechtigung angeboten werden — sowohl im Form-Queryset als
    auch im HTMX-Field-Partial und im Service-Layer.
    """

    def test_assistant_does_not_see_elevated_doctype_in_form_queryset(self, facility, assistant_user, doc_type_crisis):
        from core.forms.events import EventMetaForm

        form = EventMetaForm(facility=facility, user=assistant_user)
        ids = list(form.fields["document_type"].queryset.values_list("pk", flat=True))
        assert doc_type_crisis.pk not in ids

    def test_staff_sees_elevated_doctype_in_form_queryset(self, facility, staff_user, doc_type_crisis):
        from core.forms.events import EventMetaForm

        form = EventMetaForm(facility=facility, user=staff_user)
        ids = list(form.fields["document_type"].queryset.values_list("pk", flat=True))
        assert doc_type_crisis.pk in ids

    def test_lead_sees_elevated_doctype_in_form_queryset(self, facility, lead_user, doc_type_crisis):
        from core.forms.events import EventMetaForm

        form = EventMetaForm(facility=facility, user=lead_user)
        ids = list(form.fields["document_type"].queryset.values_list("pk", flat=True))
        assert doc_type_crisis.pk in ids

    def test_event_create_get_hides_restricted_doctype_for_assistant(
        self, client, assistant_user, doc_type_crisis, doc_type_contact
    ):
        client.force_login(assistant_user)
        response = client.get(reverse("core:event_create"))
        assert response.status_code == 200
        content = response.content.decode()
        assert doc_type_crisis.name not in content
        assert doc_type_contact.name in content

    def test_event_fields_partial_blocks_restricted_doctype_for_assistant(
        self, client, assistant_user, doc_type_crisis
    ):
        client.force_login(assistant_user)
        response = client.get(
            reverse("core:event_fields_partial"),
            {"document_type": str(doc_type_crisis.pk)},
        )
        assert response.status_code == 403

    def test_event_fields_partial_returns_form_for_staff(self, client, staff_user, doc_type_crisis):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:event_fields_partial"),
            {"document_type": str(doc_type_crisis.pk)},
        )
        assert response.status_code == 200

    def test_create_event_service_rejects_restricted_doctype_for_assistant(
        self, facility, assistant_user, doc_type_crisis, client_identified
    ):
        from django.core.exceptions import PermissionDenied

        with pytest.raises(PermissionDenied):
            create_event(
                facility=facility,
                user=assistant_user,
                document_type=doc_type_crisis,
                occurred_at=timezone.now(),
                data_json={},
                client=client_identified,
            )

    def test_create_event_service_allows_restricted_doctype_for_staff(
        self, facility, staff_user, doc_type_crisis, client_identified
    ):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={},
            client=client_identified,
        )
        assert event.pk is not None

    def test_event_create_post_rejects_restricted_doctype_for_assistant(
        self, client, assistant_user, doc_type_crisis, client_identified
    ):
        """A spoofed POST with a restricted DocumentType id must be rejected
        even though the form queryset hides it from the dropdown."""
        client.force_login(assistant_user)
        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type_crisis.pk),
                "client": str(client_identified.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
            },
        )
        # Either 403 (rejected at form/service) or form re-render with error
        assert response.status_code in (200, 403)
        assert not Event.objects.filter(document_type=doc_type_crisis, created_by=assistant_user).exists()


@pytest.mark.django_db
class TestClientAutocompleteMinStageFilter:
    """ClientAutocomplete must filter results by an optional min_stage query
    parameter so the dropdown does not offer clients below the chosen
    DocumentType's required contact stage. (Issue #507)
    """

    def test_autocomplete_filters_clients_below_min_stage(
        self, client, staff_user, client_identified, client_qualified
    ):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:client_autocomplete"),
            {"q": "Test", "min_stage": "qualified"},
        )
        assert response.status_code == 200
        data = response.json()
        pseudonyms = [d["pseudonym"] for d in data]
        assert "Test-QU-01" in pseudonyms
        assert "Test-ID-01" not in pseudonyms

    def test_autocomplete_without_min_stage_returns_all(self, client, staff_user, client_identified, client_qualified):
        client.force_login(staff_user)
        response = client.get(reverse("core:client_autocomplete"), {"q": "Test"})
        assert response.status_code == 200
        data = response.json()
        pseudonyms = [d["pseudonym"] for d in data]
        assert "Test-QU-01" in pseudonyms
        assert "Test-ID-01" in pseudonyms

    def test_autocomplete_unknown_min_stage_returns_all(self, client, staff_user, client_identified, client_qualified):
        """An unknown stage value falls back to no filter (defensive)."""
        client.force_login(staff_user)
        response = client.get(
            reverse("core:client_autocomplete"),
            {"q": "Test", "min_stage": "bogus"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 2


@pytest.mark.django_db
class TestEventAttachmentAtomicity:
    """Event + Attachment müssen atomar angelegt werden (Refs #584, Refs #591 WP2).

    Scheitert der Attachment-Teil (Virus-Scan, Fernet, Disk, DB-Save), muss die
    Event-Row zurückgerollt werden — sonst verweist die DB auf einen Anhang,
    der nie persistiert wurde. Der View-Layer umschließt ``create_event()`` +
    ``store_encrypted_file()`` bewusst mit ``transaction.atomic()``.
    """

    @pytest.fixture
    def doc_type_with_file(self, facility):
        """DocumentType mit einem File-Feld."""
        from core.models import DocumentType, DocumentTypeField, FieldTemplate

        dt = DocumentType.objects.create(
            facility=facility,
            name="Doc mit Anhang",
            category=DocumentType.Category.NOTE,
        )
        ft_file = FieldTemplate.objects.create(
            facility=facility,
            name="Anhang",
            field_type=FieldTemplate.FieldType.FILE,
        )
        DocumentTypeField.objects.create(document_type=dt, field_template=ft_file, sort_order=0)
        return dt

    def test_attachment_store_failure_rolls_back_event_creation(self, client, staff_user, facility, doc_type_with_file):
        """Wenn ``store_encrypted_file`` fehlschlägt, darf kein Event bestehen bleiben.

        Der View legt das Event zuerst per ``create_event()`` an und ruft erst
        danach ``store_encrypted_file()``. Beide Aufrufe laufen innerhalb eines
        gemeinsamen ``transaction.atomic()``-Blocks — ein Fehler im zweiten
        Schritt muss den ersten rückgängig machen.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile

        client.force_login(staff_user)
        events_before = Event.objects.count()
        history_before = EventHistory.objects.count()

        # Echter PDF-Header, weil store_encrypted_file seit #610 Magic-Bytes prüft.
        pdf_bytes = (
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
            b"xref\n0 3\n0000000000 65535 f\n"
            b"trailer<</Size 3/Root 1 0 R>>\n"
            b"startxref\n9\n%%EOF\n"
        )
        uploaded = SimpleUploadedFile("test.pdf", pdf_bytes, content_type="application/pdf")

        # Die Referenz, die der View tatsächlich aufruft, liegt in
        # ``core.views.events.store_encrypted_file`` (Import-Alias, siehe
        # :file:`src/core/views/events.py`). Dort patchen — nicht im Service-Modul.
        with patch(
            "core.views.events.store_encrypted_file",
            side_effect=RuntimeError("Simulierter Fernet-Fail"),
        ):
            with pytest.raises(RuntimeError, match="Simulierter Fernet-Fail"):
                client.post(
                    reverse("core:event_create"),
                    {
                        "document_type": str(doc_type_with_file.pk),
                        "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                        "anhang": uploaded,
                    },
                )

        # Transaktion rollt zurück → kein neues Event, keine EventHistory.
        assert Event.objects.count() == events_before
        assert EventHistory.objects.count() == history_before

    def test_attachment_save_failure_rolls_back_event_creation(self, client, staff_user, facility, doc_type_with_file):
        """Alternative: Patch direkt auf ``EventAttachment.save`` — der Save
        läuft innerhalb von ``store_encrypted_file``. Erfordert allerdings,
        dass ``encrypt_file`` und Virus-Scan vorher laufen — im Testumfeld
        ist CLAMAV_ENABLED=False, aber der Encryption-Key muss gesetzt sein.

        Hinweis: Benötigt seit #610 die libmagic-Bibliothek, weil
        ``store_encrypted_file`` vor ``EventAttachment.save`` eine Magic-Bytes-
        Prüfung ausführt.
        """
        # Skip, wenn libmagic nicht lauffähig (z.B. Host ohne libmagic1).
        try:
            import magic

            magic.from_buffer(b"%PDF-1.4\n", mime=True)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"libmagic nicht lauffähig: {exc}")

        from core.models.attachment import EventAttachment

        client.force_login(staff_user)
        events_before = Event.objects.count()

        from django.core.files.uploadedfile import SimpleUploadedFile

        # Echter PDF-Header, weil store_encrypted_file seit #610 Magic-Bytes prüft.
        pdf_bytes = (
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
            b"xref\n0 3\n0000000000 65535 f\n"
            b"trailer<</Size 3/Root 1 0 R>>\n"
            b"startxref\n9\n%%EOF\n"
        )
        uploaded = SimpleUploadedFile("test.pdf", pdf_bytes, content_type="application/pdf")

        with patch.object(EventAttachment, "save", side_effect=RuntimeError("DB-Save-Fehler")):
            with pytest.raises(RuntimeError, match="DB-Save-Fehler"):
                client.post(
                    reverse("core:event_create"),
                    {
                        "document_type": str(doc_type_with_file.pk),
                        "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                        "anhang": uploaded,
                    },
                )

        # Rollback-Garantie: weder Event noch Attachment in der DB.
        assert Event.objects.count() == events_before
        assert EventAttachment.objects.count() == 0


@pytest.mark.django_db
class TestEventAttachmentVersioning:
    """Attachment-Versionierung beim Ersetzen (Refs #587, Stufe A).

    Upload einer neuen Datei in ein Feld mit bestehender Datei darf die
    Vorversion NICHT physisch löschen. Stattdessen: alte Version bleibt
    erhalten, wird als `is_current=False` markiert und zeigt via
    `superseded_by` auf den Nachfolger.
    """

    @pytest.fixture
    def doc_type_with_file(self, facility):
        from core.models import DocumentType, DocumentTypeField, FieldTemplate

        dt = DocumentType.objects.create(
            facility=facility,
            name="Doc mit Anhang",
            category=DocumentType.Category.NOTE,
        )
        ft_file = FieldTemplate.objects.create(
            facility=facility,
            name="Anhang",
            field_type=FieldTemplate.FieldType.FILE,
        )
        DocumentTypeField.objects.create(document_type=dt, field_template=ft_file, sort_order=0)
        return dt, ft_file

    @staticmethod
    def _pdf_bytes(marker=b"A"):
        return (
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
            b"xref\n0 3\n0000000000 65535 f\n"
            b"trailer<</Size 3/Root 1 0 R>>\n"
            b"startxref\n9\n%%EOF\n" + marker
        )

    def test_replace_supersedes_old_attachment(self, client, staff_user, facility, doc_type_with_file):
        """Replace-Modus über `__replace__<entry_id>` (Stufe B, Refs #622).

        Stufe A setzte einen erneuten Upload in dasselbe Feld automatisch
        als Replace. Stufe B macht aus einem erneuten Upload per Default
        einen Add; Replace ist jetzt explizit über die per-Entry-Replace-
        Inputs.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile

        from core.models.attachment import EventAttachment

        doc_type, _ft = doc_type_with_file
        client.force_login(staff_user)

        first_file = SimpleUploadedFile("original.pdf", self._pdf_bytes(b"v1"), content_type="application/pdf")
        resp = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "anhang": first_file,
            },
        )
        assert resp.status_code == 302
        event = Event.objects.filter(document_type=doc_type).first()
        assert event is not None
        original_attachment = event.attachments.get()
        assert original_attachment.is_current is True
        assert original_attachment.superseded_by is None

        # Replace per dedicated __replace__<entry_id> POST key.
        replacement = SimpleUploadedFile("neu.pdf", self._pdf_bytes(b"v2"), content_type="application/pdf")
        resp = client.post(
            reverse("core:event_update", kwargs={"pk": event.pk}),
            {
                "document_type": str(doc_type.pk),
                "occurred_at": event.occurred_at.strftime("%Y-%m-%dT%H:%M"),
                f"anhang__replace__{original_attachment.entry_id}": replacement,
            },
        )
        assert resp.status_code == 302

        attachments = list(EventAttachment.objects.filter(event=event).order_by("created_at"))
        assert len(attachments) == 2
        old, new = attachments
        old.refresh_from_db()
        new.refresh_from_db()
        assert old.pk == original_attachment.pk
        assert old.is_current is False
        assert old.superseded_by_id == new.pk
        assert old.superseded_at is not None
        assert new.is_current is True
        assert new.superseded_by is None
        # Entry-ID bleibt beim Replace stabil (Stufe B, Refs #622).
        assert new.entry_id == original_attachment.entry_id

    def test_event_data_json_points_at_current_version(self, client, staff_user, facility, doc_type_with_file):
        from django.core.files.uploadedfile import SimpleUploadedFile

        doc_type, _ft = doc_type_with_file
        client.force_login(staff_user)

        first = SimpleUploadedFile("a.pdf", self._pdf_bytes(b"v1"), content_type="application/pdf")
        client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "anhang": first,
            },
        )
        event = Event.objects.get(document_type=doc_type)
        original_entry_id = event.attachments.get().entry_id

        second = SimpleUploadedFile("b.pdf", self._pdf_bytes(b"v2"), content_type="application/pdf")
        client.post(
            reverse("core:event_update", kwargs={"pk": event.pk}),
            {
                "document_type": str(doc_type.pk),
                "occurred_at": event.occurred_at.strftime("%Y-%m-%dT%H:%M"),
                f"anhang__replace__{original_entry_id}": second,
            },
        )
        event.refresh_from_db()
        # Neues Format: data_json[slug] = {"__files__": True, "entries": [...]}.
        marker = event.data_json["anhang"]
        assert marker.get("__files__") is True
        entries = marker["entries"]
        assert len(entries) == 1
        current_id = entries[0]["id"]
        current = event.attachments.get(pk=current_id)
        assert current.is_current is True

    def test_detail_view_exposes_prior_versions(self, client, staff_user, facility, doc_type_with_file):
        from django.core.files.uploadedfile import SimpleUploadedFile

        doc_type, _ft = doc_type_with_file
        client.force_login(staff_user)

        client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "anhang": SimpleUploadedFile("a.pdf", self._pdf_bytes(b"v1"), content_type="application/pdf"),
            },
        )
        event = Event.objects.get(document_type=doc_type)
        entry_id = event.attachments.get().entry_id
        client.post(
            reverse("core:event_update", kwargs={"pk": event.pk}),
            {
                "document_type": str(doc_type.pk),
                "occurred_at": event.occurred_at.strftime("%Y-%m-%dT%H:%M"),
                f"anhang__replace__{entry_id}": SimpleUploadedFile(
                    "b.pdf", self._pdf_bytes(b"v2"), content_type="application/pdf"
                ),
            },
        )

        response = client.get(reverse("core:event_detail", kwargs={"pk": event.pk}))
        assert response.status_code == 200
        content = response.content.decode()
        assert "attachment-prior-versions" in content
        assert "Vorversion" in content

    def test_soft_delete_removes_all_versions(self, client, staff_user, facility, doc_type_with_file):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from core.models.attachment import EventAttachment

        doc_type, _ft = doc_type_with_file
        client.force_login(staff_user)

        client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "anhang": SimpleUploadedFile("a.pdf", self._pdf_bytes(b"v1"), content_type="application/pdf"),
            },
        )
        event = Event.objects.get(document_type=doc_type)
        entry_id = event.attachments.get().entry_id
        client.post(
            reverse("core:event_update", kwargs={"pk": event.pk}),
            {
                "document_type": str(doc_type.pk),
                "occurred_at": event.occurred_at.strftime("%Y-%m-%dT%H:%M"),
                f"anhang__replace__{entry_id}": SimpleUploadedFile(
                    "b.pdf", self._pdf_bytes(b"v2"), content_type="application/pdf"
                ),
            },
        )
        assert EventAttachment.objects.filter(event=event).count() == 2

        from core.services.event import soft_delete_event

        soft_delete_event(event, staff_user)
        assert EventAttachment.objects.filter(event=event).count() == 0
