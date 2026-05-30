"""Tests für Events — Event-Attachment-Atomicity + Versionshistorie (Refs Welle 6 #929)."""

from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Event, EventHistory
from core.services.event import (
    soft_delete_event,
)


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

        # ``store_encrypted_file`` wird jetzt aus ``core.services.event.
        # attach_files_to_new_event`` heraus gerufen — den Lazy-Import-Alias
        # in dem Service-Modul patchen, damit der Mock greift.
        with (
            patch(
                "core.services.file_vault.store_encrypted_file",
                side_effect=RuntimeError("Simulierter Fernet-Fail"),
            ),
            pytest.raises(RuntimeError, match="Simulierter Fernet-Fail"),
        ):
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

        soft_delete_event(event, staff_user)
        assert EventAttachment.objects.filter(event=event).count() == 0
