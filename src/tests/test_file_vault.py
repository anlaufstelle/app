"""Tests for file vault service + views."""

from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, DocumentType, DocumentTypeField, Event, FieldTemplate, Settings
from core.models.attachment import EventAttachment
from core.services.file_vault import (
    delete_attachment_file,
    delete_event_attachments,
    get_attachment_path,
    get_original_filename,
    store_encrypted_file,
)
from core.services.virus_scan import ScanResult, VirusScannerUnavailableError


@pytest.fixture
def _encryption_key(settings):
    settings.ENCRYPTION_KEY = Fernet.generate_key().decode("utf-8")
    settings.ENCRYPTION_KEYS = ""


@pytest.fixture
def doc_type_with_file(facility):
    dt = DocumentType.objects.create(
        facility=facility,
        name="DocWithFile",
        sensitivity=DocumentType.Sensitivity.NORMAL,
    )
    ft_text = FieldTemplate.objects.create(
        facility=facility,
        name="Notiz",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    ft_file = FieldTemplate.objects.create(
        facility=facility,
        name="Scan",
        field_type=FieldTemplate.FieldType.FILE,
    )
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_text, sort_order=0)
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_file, sort_order=1)
    return dt, ft_text, ft_file


@pytest.fixture
def event_with_file(facility, staff_user, doc_type_with_file, _encryption_key, settings):
    dt, ft_text, ft_file = doc_type_with_file
    event = Event.objects.create(
        facility=facility,
        document_type=dt,
        occurred_at=timezone.now(),
        data_json={"notiz": "Test"},
        created_by=staff_user,
    )
    uploaded = SimpleUploadedFile("testfile.pdf", b"PDF content here", content_type="application/pdf")
    settings.MEDIA_ROOT = str(settings.MEDIA_ROOT)  # ensure it's a string path
    attachment = store_encrypted_file(facility, uploaded, ft_file, event, staff_user)
    event.data_json["scan"] = {"__file__": True, "attachment_id": str(attachment.pk)}
    event.save(update_fields=["data_json"])
    return event, attachment


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestFileVaultService:
    def test_store_and_retrieve(self, facility, staff_user, doc_type_with_file):
        dt, _, ft_file = doc_type_with_file
        event = Event.objects.create(
            facility=facility,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        content = b"Hello encrypted file!"
        uploaded = SimpleUploadedFile("test.pdf", content, content_type="application/pdf")

        attachment = store_encrypted_file(facility, uploaded, ft_file, event, staff_user)

        assert attachment.pk is not None
        assert attachment.event == event
        assert attachment.field_template == ft_file
        assert attachment.file_size == len(content)
        assert attachment.mime_type == "application/pdf"
        assert attachment.storage_filename.endswith(".enc")

        # Physical file exists
        path = get_attachment_path(attachment)
        assert path.exists()

        # Original filename is encrypted and decryptable
        assert get_original_filename(attachment) == "test.pdf"

    def test_uuid_filename_no_leakage(self, facility, staff_user, doc_type_with_file):
        dt, _, ft_file = doc_type_with_file
        event = Event.objects.create(
            facility=facility,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        uploaded = SimpleUploadedFile("geheim_bescheid.pdf", b"secret", content_type="application/pdf")
        attachment = store_encrypted_file(facility, uploaded, ft_file, event, staff_user)

        # Storage filename must not contain original name
        assert "geheim" not in attachment.storage_filename
        assert "bescheid" not in attachment.storage_filename

    def test_facility_directory_isolation(self, organization, staff_user, doc_type_with_file):
        from core.models import Facility

        dt, _, ft_file = doc_type_with_file
        facility_a = dt.facility
        facility_b = Facility.objects.create(organization=organization, name="Other")

        event = Event.objects.create(
            facility=facility_a,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        uploaded = SimpleUploadedFile("test.pdf", b"data", content_type="application/pdf")
        attachment = store_encrypted_file(facility_a, uploaded, ft_file, event, staff_user)

        path = get_attachment_path(attachment)
        assert str(facility_a.pk) in str(path)
        assert str(facility_b.pk) not in str(path)

    def test_delete_attachment_file(self, event_with_file):
        _, attachment = event_with_file
        path = get_attachment_path(attachment)
        assert path.exists()

        delete_attachment_file(attachment)
        assert not path.exists()

    def test_delete_event_attachments(self, event_with_file):
        event, attachment = event_with_file
        path = get_attachment_path(attachment)
        assert path.exists()

        delete_event_attachments(event)
        assert not path.exists()
        assert EventAttachment.objects.filter(event=event).count() == 0

    def test_file_field_forces_encrypted(self, facility):
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="FileTest",
            field_type=FieldTemplate.FieldType.FILE,
            is_encrypted=False,  # should be overridden
        )
        ft.refresh_from_db()
        assert ft.is_encrypted is True


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestFileUploadView:
    def test_create_event_with_file(self, client, staff_user, facility, doc_type_with_file):
        dt, ft_text, ft_file = doc_type_with_file
        Settings.objects.get_or_create(facility=facility)

        client.force_login(staff_user)
        uploaded = SimpleUploadedFile("bescheid.pdf", b"PDF bytes", content_type="application/pdf")

        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(dt.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "notiz": "Test text",
                "scan": uploaded,
            },
        )
        assert response.status_code == 302  # redirect to detail

        event = Event.objects.filter(document_type=dt).latest("created_at")
        assert "scan" in event.data_json
        assert event.data_json["scan"]["__file__"] is True

        attachment = EventAttachment.objects.get(event=event)
        assert attachment.field_template == ft_file
        assert attachment.file_size == len(b"PDF bytes")

    def test_download_requires_auth(self, client, event_with_file):
        event, attachment = event_with_file
        url = reverse("core:attachment_download", kwargs={"pk": event.pk, "attachment_pk": attachment.pk})
        response = client.get(url)
        assert response.status_code == 302  # redirect to login

    def test_download_works_for_staff(self, client, staff_user, event_with_file):
        event, attachment = event_with_file
        client.force_login(staff_user)
        url = reverse("core:attachment_download", kwargs={"pk": event.pk, "attachment_pk": attachment.pk})
        response = client.get(url)
        assert response.status_code == 200
        # PDFs are now served inline by default (#508 — MIME-Whitelist)
        assert response["Content-Disposition"] == 'inline; filename="testfile.pdf"'

        # Verify content is decrypted correctly
        content = b"".join(response.streaming_content)
        assert content == b"PDF content here"

    def test_download_force_attachment_with_query_param(self, client, staff_user, event_with_file):
        """?download=1 forces Content-Disposition: attachment even for whitelisted MIME types."""
        event, attachment = event_with_file
        client.force_login(staff_user)
        url = reverse("core:attachment_download", kwargs={"pk": event.pk, "attachment_pk": attachment.pk})
        response = client.get(url + "?download=1")
        assert response.status_code == 200
        assert response["Content-Disposition"] == 'attachment; filename="testfile.pdf"'

    def test_download_image_inline_by_default(self, client, staff_user, facility, doc_type_with_file):
        """Images are served inline by default (#508)."""
        dt, _, ft_file = doc_type_with_file
        event = Event.objects.create(
            facility=facility,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        uploaded = SimpleUploadedFile("photo.png", b"PNG bytes here", content_type="image/png")
        attachment = store_encrypted_file(facility, uploaded, ft_file, event, staff_user)

        client.force_login(staff_user)
        url = reverse("core:attachment_download", kwargs={"pk": event.pk, "attachment_pk": attachment.pk})
        response = client.get(url)
        assert response.status_code == 200
        assert response["Content-Disposition"] == 'inline; filename="photo.png"'

    def test_download_zip_falls_back_to_attachment(self, client, staff_user, facility, doc_type_with_file):
        """Non-whitelisted MIME types are served as attachment (#508)."""
        dt, _, ft_file = doc_type_with_file
        event = Event.objects.create(
            facility=facility,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        uploaded = SimpleUploadedFile("archive.zip", b"ZIP bytes", content_type="application/zip")
        attachment = store_encrypted_file(facility, uploaded, ft_file, event, staff_user)

        client.force_login(staff_user)
        url = reverse("core:attachment_download", kwargs={"pk": event.pk, "attachment_pk": attachment.pk})
        response = client.get(url)
        assert response.status_code == 200
        assert response["Content-Disposition"] == 'attachment; filename="archive.zip"'

    def test_download_html_does_not_render_inline(self, client, staff_user, facility, doc_type_with_file):
        """text/html is NOT inline-renderable — XSS risk (#508)."""
        dt, _, ft_file = doc_type_with_file
        event = Event.objects.create(
            facility=facility,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        uploaded = SimpleUploadedFile("evil.html", b"<script>alert(1)</script>", content_type="text/html")
        attachment = store_encrypted_file(facility, uploaded, ft_file, event, staff_user)

        client.force_login(staff_user)
        url = reverse("core:attachment_download", kwargs={"pk": event.pk, "attachment_pk": attachment.pk})
        response = client.get(url)
        assert response.status_code == 200
        assert response["Content-Disposition"] == 'attachment; filename="evil.html"'

    def test_download_creates_audit_log(self, client, staff_user, event_with_file):
        from core.models import AuditLog

        event, attachment = event_with_file
        client.force_login(staff_user)
        url = reverse("core:attachment_download", kwargs={"pk": event.pk, "attachment_pk": attachment.pk})
        client.get(url)

        log = AuditLog.objects.filter(action=AuditLog.Action.DOWNLOAD).latest("timestamp")
        assert log.target_type == "EventAttachment"
        assert log.target_id == str(attachment.pk)

    def test_detail_view_shows_file_info(self, client, staff_user, event_with_file):
        event, attachment = event_with_file
        client.force_login(staff_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event.pk}))
        content = response.content.decode()
        assert "testfile.pdf" in content

    def test_attachment_list_view(self, client, staff_user, event_with_file, facility):
        client.force_login(staff_user)
        response = client.get(reverse("core:attachment_list"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "testfile.pdf" in content


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestAttachmentListSensitivityFiltering:
    """Verify that the attachment list filters by sensitivity BEFORE slicing."""

    def test_attachments_list_filters_before_slicing(self, client, staff_user, facility, settings):
        """Staff (max ELEVATED) must see NORMAL attachments even if many HIGH
        attachments exist — the sensitivity filter must run before the [:200]
        slice so that invisible HIGH records do not push visible NORMAL records
        out of the result window.
        """
        settings.ENCRYPTION_KEY = Fernet.generate_key().decode("utf-8")
        settings.ENCRYPTION_KEYS = ""

        ft_file = FieldTemplate.objects.create(
            facility=facility,
            name="Datei",
            field_type=FieldTemplate.FieldType.FILE,
        )

        # HIGH doc type — invisible to STAFF
        dt_high = DocumentType.objects.create(
            facility=facility,
            name="Geheim",
            sensitivity=DocumentType.Sensitivity.HIGH,
        )
        DocumentTypeField.objects.create(document_type=dt_high, field_template=ft_file, sort_order=0)

        # NORMAL doc type — visible to STAFF
        dt_normal = DocumentType.objects.create(
            facility=facility,
            name="Offen",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        DocumentTypeField.objects.create(document_type=dt_normal, field_template=ft_file, sort_order=0)

        # Create 5 HIGH events with attachments (newer, would fill the slice)
        for i in range(5):
            ev = Event.objects.create(
                facility=facility,
                document_type=dt_high,
                occurred_at=timezone.now(),
                data_json={},
                created_by=staff_user,
            )
            uploaded = SimpleUploadedFile(f"secret_{i}.pdf", b"high content", content_type="application/pdf")
            store_encrypted_file(facility, uploaded, ft_file, ev, staff_user)

        # Create 2 NORMAL events with attachments (older, would be pushed out)
        for i in range(2):
            ev = Event.objects.create(
                facility=facility,
                document_type=dt_normal,
                occurred_at=timezone.now(),
                data_json={},
                created_by=staff_user,
            )
            uploaded = SimpleUploadedFile(f"open_{i}.pdf", b"normal content", content_type="application/pdf")
            store_encrypted_file(facility, uploaded, ft_file, ev, staff_user)

        client.force_login(staff_user)
        response = client.get(reverse("core:attachment_list"))
        assert response.status_code == 200
        content = response.content.decode()

        # NORMAL attachments must be visible
        assert "open_0.pdf" in content
        assert "open_1.pdf" in content

        # HIGH attachments must NOT be visible to staff
        assert "secret_0.pdf" not in content

    def test_attachments_list_field_level_sensitivity_filtered(self, client, staff_user, facility, settings):
        """Attachments whose field_template has HIGH sensitivity are hidden
        from STAFF even when the doc_type is NORMAL.
        """
        settings.ENCRYPTION_KEY = Fernet.generate_key().decode("utf-8")
        settings.ENCRYPTION_KEYS = ""

        ft_normal = FieldTemplate.objects.create(
            facility=facility,
            name="NormalFile",
            field_type=FieldTemplate.FieldType.FILE,
        )
        ft_high = FieldTemplate.objects.create(
            facility=facility,
            name="HighFile",
            field_type=FieldTemplate.FieldType.FILE,
            sensitivity=DocumentType.Sensitivity.HIGH,
        )

        dt = DocumentType.objects.create(
            facility=facility,
            name="Mischtyp",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        DocumentTypeField.objects.create(document_type=dt, field_template=ft_normal, sort_order=0)
        DocumentTypeField.objects.create(document_type=dt, field_template=ft_high, sort_order=1)

        ev = Event.objects.create(
            facility=facility,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        uploaded_normal = SimpleUploadedFile("visible.pdf", b"ok", content_type="application/pdf")
        store_encrypted_file(facility, uploaded_normal, ft_normal, ev, staff_user)

        uploaded_high = SimpleUploadedFile("hidden.pdf", b"secret", content_type="application/pdf")
        store_encrypted_file(facility, uploaded_high, ft_high, ev, staff_user)

        client.force_login(staff_user)
        response = client.get(reverse("core:attachment_list"))
        assert response.status_code == 200
        content = response.content.decode()

        assert "visible.pdf" in content
        assert "hidden.pdf" not in content


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestFileValidation:
    def test_invalid_extension_rejected(self, client, staff_user, facility, doc_type_with_file):
        dt, _, _ = doc_type_with_file
        Settings.objects.get_or_create(
            facility=facility, defaults={"allowed_file_types": "pdf,jpg", "max_file_size_mb": 10}
        )

        client.force_login(staff_user)
        uploaded = SimpleUploadedFile("virus.exe", b"bad content", content_type="application/octet-stream")

        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(dt.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "notiz": "test",
                "scan": uploaded,
            },
        )
        # Should stay on the form (not redirect)
        assert response.status_code == 200
        assert EventAttachment.objects.count() == 0

    def test_oversized_file_rejected(self, client, staff_user, facility, doc_type_with_file):
        dt, _, _ = doc_type_with_file
        Settings.objects.get_or_create(facility=facility, defaults={"allowed_file_types": "pdf", "max_file_size_mb": 1})

        client.force_login(staff_user)
        # 2 MB file exceeds 1 MB limit
        uploaded = SimpleUploadedFile("big.pdf", b"X" * (2 * 1024 * 1024), content_type="application/pdf")

        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(dt.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                "notiz": "test",
                "scan": uploaded,
            },
        )
        assert response.status_code == 200
        assert EventAttachment.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestVirusScanIntegration:
    """``store_encrypted_file`` darf infizierte Dateien niemals verschlüsseln
    und muss jeden Fund als ``SECURITY_VIOLATION`` protokollieren (Issue #524)."""

    def _event(self, facility, staff_user, doc_type_with_file):
        dt, _, ft_file = doc_type_with_file
        event = Event.objects.create(
            facility=facility,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        return event, ft_file

    def test_infected_file_raises_validation_error(self, facility, staff_user, doc_type_with_file):
        event, ft_file = self._event(facility, staff_user, doc_type_with_file)
        uploaded = SimpleUploadedFile("malware.pdf", b"bad", content_type="application/pdf")

        with patch(
            "core.services.file_vault.scan_file",
            return_value=ScanResult(clean=False, infected=True, signature="Eicar-Test-Signature"),
        ):
            with pytest.raises(ValidationError) as exc_info:
                store_encrypted_file(facility, uploaded, ft_file, event, staff_user)

        assert "Eicar-Test-Signature" in str(exc_info.value)
        # Keine Attachment-Zeile, keine verschlüsselte Datei auf Platte.
        assert EventAttachment.objects.filter(event=event).count() == 0

    def test_infected_file_creates_security_violation_audit_log(self, facility, staff_user, doc_type_with_file):
        event, ft_file = self._event(facility, staff_user, doc_type_with_file)
        uploaded = SimpleUploadedFile("malware.pdf", b"bad", content_type="application/pdf")

        with patch(
            "core.services.file_vault.scan_file",
            return_value=ScanResult(clean=False, infected=True, signature="Eicar-Test-Signature"),
        ):
            with pytest.raises(ValidationError):
                store_encrypted_file(facility, uploaded, ft_file, event, staff_user)

        log = AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).latest("timestamp")
        assert log.user == staff_user
        assert log.facility == facility
        assert log.detail["reason"] == "virus_detected"
        assert log.detail["signature"] == "Eicar-Test-Signature"
        assert log.detail["filename"] == "malware.pdf"

    def test_scanner_unavailable_fails_closed(self, facility, staff_user, doc_type_with_file):
        event, ft_file = self._event(facility, staff_user, doc_type_with_file)
        uploaded = SimpleUploadedFile("ok.pdf", b"ok", content_type="application/pdf")

        with patch(
            "core.services.file_vault.scan_file",
            side_effect=VirusScannerUnavailableError("connection refused"),
        ):
            with pytest.raises(ValidationError):
                store_encrypted_file(facility, uploaded, ft_file, event, staff_user)

        assert EventAttachment.objects.filter(event=event).count() == 0
        log = AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).latest("timestamp")
        assert log.detail["reason"] == "virus_scanner_unavailable"

    def test_clean_file_is_stored(self, facility, staff_user, doc_type_with_file):
        event, ft_file = self._event(facility, staff_user, doc_type_with_file)
        uploaded = SimpleUploadedFile("ok.pdf", b"PDF bytes", content_type="application/pdf")

        with patch(
            "core.services.file_vault.scan_file",
            return_value=ScanResult(clean=True, infected=False),
        ):
            attachment = store_encrypted_file(facility, uploaded, ft_file, event, staff_user)

        assert attachment.pk is not None
        assert EventAttachment.objects.filter(event=event).count() == 1


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestEventAttachmentAtomicity:
    """Event-ORM und Dateioperationen müssen atomar sein (Refs #584).

    Scheitert `store_encrypted_file`, darf weder das Event noch der
    Attachment-Record in der DB verbleiben — und im Update-Pfad darf die
    alte Datei nicht gelöscht werden.
    """

    def test_create_rolls_back_event_when_file_store_fails(self, client, staff_user, facility, doc_type_with_file):
        """Virus/Upload-Fehler im Create-Pfad rollt das Event zurück."""
        dt, _, _ = doc_type_with_file
        Settings.objects.get_or_create(facility=facility)

        client.force_login(staff_user)
        uploaded = SimpleUploadedFile("infected.pdf", b"X", content_type="application/pdf")

        events_before = Event.objects.count()
        attachments_before = EventAttachment.objects.count()

        with patch(
            "core.views.events.store_encrypted_file",
            side_effect=ValidationError("virus"),
        ):
            response = client.post(
                reverse("core:event_create"),
                {
                    "document_type": str(dt.pk),
                    "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
                    "notiz": "x",
                    "scan": uploaded,
                },
            )

        # Formular rendert, Event- und Attachment-Zählung unverändert.
        assert response.status_code == 200
        assert Event.objects.count() == events_before
        assert EventAttachment.objects.count() == attachments_before

    def test_update_preserves_old_file_when_new_upload_fails(
        self, client, staff_user, event_with_file, doc_type_with_file
    ):
        """Scheitert die neue Datei im Update-Pfad, bleibt die alte vollständig
        erhalten (DB + Filesystem)."""
        event, old_attachment = event_with_file
        dt, _, ft_file = doc_type_with_file
        old_path = get_attachment_path(old_attachment)
        assert old_path.exists()

        client.force_login(staff_user)
        new_upload = SimpleUploadedFile("new.pdf", b"new bytes", content_type="application/pdf")

        with patch(
            "core.views.events.store_encrypted_file",
            side_effect=ValidationError("virus"),
        ):
            response = client.post(
                reverse("core:event_update", kwargs={"pk": event.pk}),
                {
                    "document_type": str(dt.pk),
                    "occurred_at": event.occurred_at.strftime("%Y-%m-%dT%H:%M"),
                    "notiz": event.data_json.get("notiz", ""),
                    "scan": new_upload,
                    "expected_updated_at": event.updated_at.isoformat(),
                },
            )

        # Alter Attachment-Record unverändert, alte Datei noch auf Platte.
        assert EventAttachment.objects.filter(pk=old_attachment.pk).exists()
        assert old_path.exists()
        # Kein neuer Attachment-Record (Rollback).
        assert EventAttachment.objects.filter(event=event).count() == 1
        # Response: Redirect oder Form-Rerender, nie 500 — das Formular muss
        # in jedem Fall gnadenvoll wieder ins UI kommen.
        assert response.status_code in (200, 302)
