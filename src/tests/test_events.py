"""Tests für Event-CRUD (C.5, C.6, C.7)."""

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
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type_contact.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "is_anonymous": "on",
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

    def test_form_accepts_anonymous_with_min_stage_defers_to_service(self, facility, staff_user):
        """EventMetaForm.clean() no longer checks anonymous+min_stage (deferred to service)."""
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
                "is_anonymous": True,
            },
            facility=facility,
        )
        assert form.is_valid()
