"""Tests für Events — Event-Service + CRUD-Views (Create/Detail/Update + Atomicity) (Refs Welle 6 #929)."""

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
        # Der Inhalt wird dynamisch über /partials/cases/for-client/ nach Klientel-
        # Auswahl geladen — die URL muss im Rendering-Payload auftauchen.
        assert "/partials/cases/for-client/" in content

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

    def test_invalid_meta_post_does_not_leak_high_field_labels_to_assistant(self, client, assistant_user, facility):
        """Refs #774 — Sensitivity-Guard im invalid-meta-Branch.

        Vor dem Fix konnte ein Assistant durch invaliden POST mit
        ``document_type=<HIGH-id>`` die Feldlabels/Help-Texte des HIGH-
        DocumentTypes in der Re-Render-Antwort sichtbar machen, weil der
        Code ``DocumentType.objects.get(pk=...)`` aufrief, ohne
        ``user_can_see_document_type`` zu pruefen.

        Test:
        1. HIGH-DocumentType mit eindeutig benanntem Feld anlegen.
        2. Assistant POSTs mit fehlendem ``occurred_at`` (=> meta_form invalid)
           und ``document_type=<HIGH-id>``.
        3. Response darf den Feldnamen NICHT enthalten.
        """
        from core.models import DocumentType, DocumentTypeField, FieldTemplate

        unique_label = "Suizidrisiko-Klassifizierung-RF774"
        high_dt = DocumentType.objects.create(
            facility=facility,
            name="Krisen-Hochsensibel",
            sensitivity=DocumentType.Sensitivity.HIGH,
        )
        ft_secret = FieldTemplate.objects.create(
            facility=facility,
            name=unique_label,
            field_type=FieldTemplate.FieldType.TEXTAREA,
            sensitivity="high",
        )
        DocumentTypeField.objects.create(document_type=high_dt, field_template=ft_secret, sort_order=0)

        client.force_login(assistant_user)
        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(high_dt.pk),
                # occurred_at fehlt → meta_form ist invalid
            },
        )
        assert response.status_code == 200
        body = response.content.decode()
        assert unique_label not in body, (
            "Assistant darf bei invalidem POST keine HIGH-Feldlabels sehen — "
            "der Validierungsfehler-Pfad darf user_can_see_document_type nicht "
            "umgehen (Refs #774)."
        )

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
