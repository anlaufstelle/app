"""Tests für Events — Event-Service + CRUD-Views (Create/Detail/Update + Atomicity) (Refs #929)."""

from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, DeletionRequest, Event, EventHistory
from core.services.events import (
    approve_deletion,
    create_event,
    decrypt_event_text_data,
    merge_update_payload,
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
class TestEventCreateSerienerfassung:
    """Serienerfassung „Speichern & nächster Kontakt" (Refs #1349, Stufe 1)."""

    def test_save_and_new_redirects_to_create_with_document_type(self, client, staff_user, doc_type_contact):
        """_save_and_new ohne Person → 302 auf event_create?document_type=<pk>, anonym."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type_contact.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "dauer": "5",
                "notiz": "",
                "_save_and_new": "1",
            },
        )
        assert response.status_code == 302
        assert response.url.startswith(reverse("core:event_create"))
        assert f"document_type={doc_type_contact.pk}" in response.url
        # Redirect bewusst OHNE client → nächster Kontakt startet anonym.
        assert "client=" not in response.url
        event = Event.objects.filter(document_type=doc_type_contact, created_by=staff_user).first()
        assert event is not None
        assert event.is_anonymous is True

    def test_save_and_new_raw_json_replay_keeps_normal_redirect(self, client, staff_user, doc_type_contact):
        """Offline-Replay (Accept: application/json) darf sich NICHT ändern:
        trotz _save_and_new normales Verhalten (Redirect auf Detail, nicht Create)."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type_contact.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "dauer": "5",
                "notiz": "",
                "_save_and_new": "1",
            },
            HTTP_ACCEPT="application/json",
        )
        assert response.status_code == 302
        event = Event.objects.filter(document_type=doc_type_contact, created_by=staff_user).first()
        assert event is not None
        # Normaler Erfolgs-Redirect zeigt auf die Detail-Seite des Events.
        assert response.url == reverse("core:event_detail", kwargs={"pk": event.pk})

    def test_save_and_new_button_rendered(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create"))
        assert response.status_code == 200
        assert 'name="_save_and_new"' in response.content.decode()
        assert 'data-testid="event-submit-next"' in response.content.decode()

    def test_get_prefill_document_type_preselects_and_renders_fields(self, client, staff_user, doc_type_contact):
        """?document_type=<pk> wählt den Typ vor und rendert dessen dynamische Felder."""
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create") + f"?document_type={doc_type_contact.pk}")
        assert response.status_code == 200
        body = response.content.decode()
        # Dynamische Felder des Typs sind vorgerendert.
        assert "Dauer" in body
        assert "Notiz" in body
        # Erste sinnvolle Eingabe bekommt Fokus (autofocus-Attribut).
        assert "autofocus" in body

    def test_get_prefill_marks_form_server_prefilled(self, client, staff_user, doc_type_contact):
        """Serienerfassungs-Prefill markiert das Formular als server-prefilled,
        damit der autosave-Draft desselben Pfads es beim Laden nicht überschreibt (#625)."""
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create") + f"?document_type={doc_type_contact.pk}")
        assert response.status_code == 200
        assert "data-autosave-server-prefilled" in response.content.decode()

    def test_get_without_prefill_not_server_prefilled(self, client, staff_user):
        """Leeres Formular ohne Prefill trägt den Server-Prefill-Marker NICHT
        (autosave-Wiederherstellung bleibt für die normale Erfassung aktiv)."""
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create"))
        assert response.status_code == 200
        assert "data-autosave-server-prefilled" not in response.content.decode()

    def test_get_prefill_document_type_foreign_facility_404(self, client, staff_user, organization):
        """Fremd-Facility-DocumentType im Prefill → 404 (Facility-Guard)."""
        from core.models import DocumentType, Facility

        other_facility = Facility.objects.create(organization=organization, name="Fremd")
        foreign_dt = DocumentType.objects.create(facility=other_facility, name="Fremd-Kontakt")
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create") + f"?document_type={foreign_dt.pk}")
        assert response.status_code == 404

    def test_get_prefill_high_type_no_leak_for_assistant(self, client, assistant_user, facility):
        """Assistant darf per ?document_type=<HIGH-pk> keine HIGH-Feldlabels sehen (IDOR)."""
        from core.models import DocumentType, DocumentTypeField, FieldTemplate

        unique_label = "Suizidrisiko-Klassifizierung-RF1349"
        high_dt = DocumentType.objects.create(
            facility=facility,
            name="Krisen-Hochsensibel-1349",
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
        response = client.get(reverse("core:event_create") + f"?document_type={high_dt.pk}")
        assert response.status_code == 200
        assert unique_label not in response.content.decode()


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


@pytest.fixture
def doc_type_with_date(facility):
    """DocumentType mit DATE- und TIME-Feld — Repro fuer Refs #1073."""
    from core.models import DocumentType, DocumentTypeField, FieldTemplate

    doc_type = DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.CONTACT,
        name="Arztbesuch",
    )
    ft_datum = FieldTemplate.objects.create(
        facility=facility,
        name="Datum",
        field_type=FieldTemplate.FieldType.DATE,
    )
    ft_uhrzeit = FieldTemplate.objects.create(
        facility=facility,
        name="Uhrzeit",
        field_type=FieldTemplate.FieldType.TIME,
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_datum, sort_order=0)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_uhrzeit, sort_order=1)
    return doc_type


@pytest.mark.django_db
class TestEventDateFieldSerialization:
    """Datumsfelder in dynamischen Formularen duerfen keinen 500 ausloesen (Refs #1073).

    ``DynamicEventDataForm`` liefert fuer DATE/TIME-Felder ``datetime.date``/
    ``datetime.time``-Objekte in ``cleaned_data``. ``Event.data_json`` ist ein
    JSONField ohne Custom-Encoder — die Objekte muessen vor dem Save als
    ISO-Strings normalisiert werden (Wire-Format, das auch ``bans.py`` und
    der Seed verwenden).
    """

    def test_create_event_serializes_date_to_iso_string(self, facility, staff_user, doc_type_with_date):
        from datetime import date, time

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_with_date,
            occurred_at=timezone.now(),
            data_json={"datum": date(2026, 7, 1), "uhrzeit": time(14, 30)},
        )
        event.refresh_from_db()
        assert event.data_json["datum"] == "2026-07-01"
        assert event.data_json["uhrzeit"] == "14:30:00"
        history = EventHistory.objects.get(event=event, action=EventHistory.Action.CREATE)
        assert history.data_after["datum"] == "2026-07-01"

    def test_update_event_serializes_date_to_iso_string(self, facility, staff_user, doc_type_with_date):
        from datetime import date

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_with_date,
            occurred_at=timezone.now(),
            data_json={},
        )
        update_event(event, staff_user, {"datum": date(2026, 8, 15)})
        event.refresh_from_db()
        assert event.data_json["datum"] == "2026-08-15"

    def test_event_create_view_with_date_succeeds(self, client, staff_user, doc_type_with_date, client_identified):
        """Akzeptanzkriterium #1073: Kontakt mit Datum speichern — kein 500."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type_with_date.pk),
                "client": str(client_identified.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "datum": "2026-07-01",
                "uhrzeit": "14:30",
            },
        )
        assert response.status_code == 302
        event = Event.objects.get(document_type=doc_type_with_date)
        assert event.data_json["datum"] == "2026-07-01"
        assert event.data_json["uhrzeit"] == "14:30:00"

    def test_event_create_view_invalid_date_shows_form_error(
        self, client, staff_user, doc_type_with_date, client_identified
    ):
        """Akzeptanzkriterium #1073: ungueltiges Datum → Formularfehler, kein 500."""
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type_with_date.pk),
                "client": str(client_identified.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "datum": "kein-datum",
            },
        )
        assert response.status_code == 200
        assert not Event.objects.filter(document_type=doc_type_with_date).exists()

    def test_event_update_view_with_date_succeeds(self, client, staff_user, facility, doc_type_with_date):
        """Akzeptanzkriterium #1073: Bearbeiten mit Datum — kein 500."""
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_with_date,
            occurred_at=timezone.now(),
            data_json={"datum": "2026-07-01"},
        )
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_update", kwargs={"pk": event.pk}),
            {"datum": "2026-09-30", "uhrzeit": "08:15"},
        )
        assert response.status_code == 302
        event.refresh_from_db()
        assert event.data_json["datum"] == "2026-09-30"


# ===================================================================
# Refs #1160 R1b: decrypt_event_text_data + merge_update_payload
# ===================================================================


@pytest.mark.django_db
class TestDecryptEventTextData:
    """``decrypt_event_text_data`` — gemeinsamer Helfer fuer EventUpdateView.get/post.

    Grenzfaelle: leeres/None-data_json, File-Marker (legacy ``__file__`` +
    Stufe-B ``__files__``) werden uebersprungen, uebrige Werte entschluesselt.
    """

    def _event(self, facility, staff_user, doc_type_contact, data_json):
        return Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json=data_json,
            created_by=staff_user,
        )

    def test_empty_data_json_returns_empty_dict(self, facility, staff_user, doc_type_contact):
        event = self._event(facility, staff_user, doc_type_contact, {})
        assert decrypt_event_text_data(event) == {}

    def test_none_data_json_returns_empty_dict(self, facility, staff_user, doc_type_contact):
        event = self._event(facility, staff_user, doc_type_contact, {})
        event.data_json = None
        assert decrypt_event_text_data(event) == {}

    def test_plain_values_passed_through(self, facility, staff_user, doc_type_contact):
        event = self._event(facility, staff_user, doc_type_contact, {"dauer": 15, "notiz": "Hallo"})
        assert decrypt_event_text_data(event) == {"dauer": 15, "notiz": "Hallo"}

    def test_legacy_file_marker_skipped(self, facility, staff_user, doc_type_contact):
        """Legacy ``__file__``-Marker darf NICHT als Form-Initial auftauchen."""
        event = self._event(
            facility,
            staff_user,
            doc_type_contact,
            {"notiz": "x", "anhang": {"__file__": True, "attachment_id": "abc", "name": "a.pdf"}},
        )
        result = decrypt_event_text_data(event)
        assert "anhang" not in result
        assert result == {"notiz": "x"}

    def test_stage_b_files_marker_skipped(self, facility, staff_user, doc_type_contact):
        """Stufe-B ``__files__``-Marker wird ebenfalls uebersprungen."""
        event = self._event(
            facility,
            staff_user,
            doc_type_contact,
            {"notiz": "x", "anhang": {"__files__": True, "entries": [{"id": "abc", "sort": 0}]}},
        )
        result = decrypt_event_text_data(event)
        assert "anhang" not in result
        assert result == {"notiz": "x"}

    def test_non_marker_dict_is_decrypted_not_skipped(self, facility, staff_user, doc_type_contact):
        """Ein Dict ohne ``__file__``/``__files__`` ist KEIN File-Marker und
        geht durch ``safe_decrypt`` — ohne Encryption-Marker bleibt es gleich."""
        payload = {"some": "dict"}
        event = self._event(facility, staff_user, doc_type_contact, {"notiz": payload})
        assert decrypt_event_text_data(event) == {"notiz": payload}


@pytest.mark.django_db
class TestMergeUpdatePayload:
    """``merge_update_payload`` — Restricted-Felder + FILE-Marker re-injizieren.

    Grenzfaelle: Restricted-Key mit/ohne Original-Wert, FILE-Marker beider
    Formate bleibt erhalten, Nicht-FILE-Felder werden nicht angefasst.
    """

    @pytest.fixture
    def doc_type_with_file(self, facility):
        from core.models import DocumentType, DocumentTypeField, FieldTemplate

        dt = DocumentType.objects.create(facility=facility, name="Mit Datei", category=DocumentType.Category.NOTE)
        ft_file = FieldTemplate.objects.create(
            facility=facility, name="Anhang", field_type=FieldTemplate.FieldType.FILE
        )
        ft_text = FieldTemplate.objects.create(
            facility=facility, name="Notiz", field_type=FieldTemplate.FieldType.TEXTAREA
        )
        DocumentTypeField.objects.create(document_type=dt, field_template=ft_file, sort_order=0)
        DocumentTypeField.objects.create(document_type=dt, field_template=ft_text, sort_order=1)
        return dt, ft_file, ft_text

    def _event(self, facility, staff_user, doc_type, data_json):
        return Event.objects.create(
            facility=facility,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json=data_json,
            created_by=staff_user,
        )

    def test_restricted_key_reinjected_with_original_value(self, facility, staff_user, doc_type_with_file):
        dt, ft_file, ft_text = doc_type_with_file
        event = self._event(facility, staff_user, dt, {ft_text.slug: "ORIGINAL"})
        merged = {}
        result = merge_update_payload(event, merged, [ft_text.slug], dt)
        assert result is merged  # in-place
        assert merged[ft_text.slug] == "ORIGINAL"

    def test_restricted_key_absent_in_event_is_not_added(self, facility, staff_user, doc_type_with_file):
        """Restricted-Key, der gar nicht in ``event.data_json`` steht, wird
        nicht erfunden (Mutation am ``if key in event_data``)."""
        dt, ft_file, ft_text = doc_type_with_file
        event = self._event(facility, staff_user, dt, {})
        merged = {}
        merge_update_payload(event, merged, [ft_text.slug], dt)
        assert ft_text.slug not in merged

    def test_legacy_file_marker_preserved(self, facility, staff_user, doc_type_with_file):
        dt, ft_file, ft_text = doc_type_with_file
        marker = {"__file__": True, "attachment_id": "abc", "name": "a.pdf"}
        event = self._event(facility, staff_user, dt, {ft_file.slug: marker})
        merged = {}
        merge_update_payload(event, merged, [], dt)
        assert merged[ft_file.slug] == marker

    def test_stage_b_files_marker_preserved(self, facility, staff_user, doc_type_with_file):
        dt, ft_file, ft_text = doc_type_with_file
        marker = {"__files__": True, "entries": [{"id": "abc", "sort": 0}]}
        event = self._event(facility, staff_user, dt, {ft_file.slug: marker})
        merged = {}
        merge_update_payload(event, merged, [], dt)
        assert merged[ft_file.slug] == marker

    def test_file_field_without_marker_not_injected(self, facility, staff_user, doc_type_with_file):
        """FILE-Feld ohne bestehenden Marker bleibt unangetastet im merged."""
        dt, ft_file, ft_text = doc_type_with_file
        event = self._event(facility, staff_user, dt, {})
        merged = {"some_other": "value"}
        merge_update_payload(event, merged, [], dt)
        assert ft_file.slug not in merged
        assert merged == {"some_other": "value"}

    def test_existing_merged_form_value_not_overwritten_for_text(self, facility, staff_user, doc_type_with_file):
        """Nicht-restricted, Nicht-FILE-Felder im merged bleiben unveraendert —
        merge fasst nur restricted + FILE an."""
        dt, ft_file, ft_text = doc_type_with_file
        event = self._event(facility, staff_user, dt, {ft_text.slug: "DB-WERT"})
        merged = {ft_text.slug: "FORM-WERT"}
        merge_update_payload(event, merged, [], dt)
        # ft_text ist nicht restricted und kein FILE-Feld → Form-Wert gewinnt.
        assert merged[ft_text.slug] == "FORM-WERT"


# ===================================================================
# Refs #1160 R1a: create_event-Validierungs-Helfer (Verhalten via public API)
# ===================================================================


@pytest.mark.django_db
class TestCreateEventValidationHelpers:
    """Die aus ``create_event`` extrahierten Validierungs-Helfer
    (``_validate_case_assignment``, ``_validate_contact_stage``) muessen
    wirkungs-identisch bleiben — geprueft ueber die public ``create_event``-API.
    """

    @pytest.fixture
    def doc_type_min_stage(self, facility):
        from core.models import Client, DocumentType

        return DocumentType.objects.create(
            facility=facility,
            category=DocumentType.Category.SERVICE,
            name="Mindeststufe",
            min_contact_stage=Client.ContactStage.QUALIFIED,
        )

    def test_case_from_other_facility_rejected(self, facility, second_facility, staff_user, doc_type_contact):
        from core.models import Case, Client

        other_client = Client.objects.create(
            facility=second_facility,
            pseudonym="Fremd-01",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=staff_user,
        )
        other_case = Case.objects.create(
            facility=second_facility,
            client=other_client,
            title="Fremd",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        with pytest.raises(ValidationError, match="selben Einrichtung"):
            create_event(
                facility=facility,
                user=staff_user,
                document_type=doc_type_contact,
                occurred_at=timezone.now(),
                data_json={"dauer": 5},
                case=other_case,
            )

    def test_case_person_mismatch_rejected(self, facility, staff_user, doc_type_contact, case_open):
        from core.models import Client

        other = Client.objects.create(
            facility=facility,
            pseudonym="Andere-01",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=staff_user,
        )
        with pytest.raises(ValidationError, match="passt nicht zur Person"):
            create_event(
                facility=facility,
                user=staff_user,
                document_type=doc_type_contact,
                occurred_at=timezone.now(),
                data_json={"dauer": 5},
                client=other,
                case=case_open,
            )

    def test_anonymous_event_rejected_for_client_case(self, facility, staff_user, doc_type_contact, case_open):
        with pytest.raises(ValidationError, match="Anonyme Ereignisse"):
            create_event(
                facility=facility,
                user=staff_user,
                document_type=doc_type_contact,
                occurred_at=timezone.now(),
                data_json={"dauer": 5},
                client=None,
                is_anonymous=True,
                case=case_open,
            )

    def test_min_stage_anonymous_rejected(self, facility, staff_user, doc_type_min_stage):
        with pytest.raises(ValidationError, match="Anonyme Kontakte"):
            create_event(
                facility=facility,
                user=staff_user,
                document_type=doc_type_min_stage,
                occurred_at=timezone.now(),
                data_json={},
                is_anonymous=True,
            )

    def test_min_stage_requires_client(self, facility, staff_user, doc_type_min_stage):
        with pytest.raises(ValidationError, match="muss eine Person"):
            create_event(
                facility=facility,
                user=staff_user,
                document_type=doc_type_min_stage,
                occurred_at=timezone.now(),
                data_json={},
                client=None,
                is_anonymous=False,
            )

    def test_min_stage_client_too_low_rejected(self, facility, staff_user, doc_type_min_stage, client_identified):
        # client_identified ist IDENTIFIED, doc verlangt QUALIFIED → zu niedrig.
        with pytest.raises(ValidationError, match="mindestens die Kontaktstufe"):
            create_event(
                facility=facility,
                user=staff_user,
                document_type=doc_type_min_stage,
                occurred_at=timezone.now(),
                data_json={},
                client=client_identified,
            )

    def test_min_stage_client_high_enough_succeeds(self, facility, staff_user, doc_type_min_stage):
        from core.models import Client

        qualified = Client.objects.create(
            facility=facility,
            pseudonym="Quali-01",
            contact_stage=Client.ContactStage.QUALIFIED,
            created_by=staff_user,
        )
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_min_stage,
            occurred_at=timezone.now(),
            data_json={},
            client=qualified,
        )
        assert event.pk is not None

    def test_auto_anonymous_when_no_client_and_no_min_stage(self, facility, staff_user, doc_type_contact):
        """Ohne Client + ohne min_contact_stage wird is_anonymous=True gesetzt —
        und der Contact-Stage-Check ueberspringt (kein min_contact_stage)."""
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 1},
            client=None,
            is_anonymous=False,
        )
        assert event.is_anonymous is True
