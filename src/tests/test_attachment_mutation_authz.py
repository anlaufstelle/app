"""R11 (Refs #1476/#1666): Sensitivity-Authz-Gate auf Anhang-Mutation.

``apply_attachment_changes`` verarbeitet REMOVE/REPLACE/ADD für FILE-Felder
direkt aus ``request.POST``/``request.FILES`` und iterierte bisher **alle**
FILE-Felder — ohne die Feld-Sensitivity gegen die Rolle zu prüfen. Text-Felder
oberhalb der Rollen-Sicht werden im Update-Pfad per ``remove_restricted_fields``
aus dem Form entfernt; die Anhang-Mutation umging dieses Gate.

Folge: Ein User, der das Event sieht (Doc-Type-Gate bestanden), aber ein
FILE-Feld wegen eines feldbezogenen Sensitivity-Overrides (höher als der
Doc-Type) NICHT sehen darf, konnte dessen Anhänge löschen/ersetzen/ergänzen.

Setup: Doc-Type NORMAL (für STAFF sichtbar), FILE-Feld mit
``sensitivity="high"`` → effektiv HIGH → STAFF (max ELEVATED) darf es NICHT
sehen, LEAD (max HIGH) schon.
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate
from core.services.events import apply_attachment_changes
from core.services.file_vault import store_encrypted_file

PDF_HEADER = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _upload(marker=b"A"):
    return SimpleUploadedFile(f"file_{marker.decode()}.pdf", PDF_HEADER + marker, content_type="application/pdf")


@pytest.fixture
def normal_dt_with_high_file(facility):
    """Doc-Type NORMAL + FILE-Feld mit feldbezogener HIGH-Sensitivity."""
    dt = DocumentType.objects.create(
        facility=facility,
        name="DocFileHigh",
        category=DocumentType.Category.NOTE,
        sensitivity=DocumentType.Sensitivity.NORMAL,
    )
    ft = FieldTemplate.objects.create(
        facility=facility,
        name="Anhang (vertraulich)",
        field_type=FieldTemplate.FieldType.FILE,
        sensitivity="high",
    )
    DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
    return dt, ft


def _event_with_one_attachment(facility, author, dt, ft):
    event = Event.objects.create(facility=facility, document_type=dt, occurred_at=timezone.now(), created_by=author)
    att = store_encrypted_file(facility, _upload(b"1"), ft, event, author, sort_order=0)
    event.data_json = {ft.slug: {"__files__": True, "entries": [{"id": str(att.pk), "sort": 0}]}}
    event.save(update_fields=["data_json"])
    return event, att


@pytest.mark.django_db
class TestAttachmentMutationSensitivityGate:
    def test_understaffed_user_cannot_remove_restricted_file_attachment(
        self, facility, lead_user, staff_user, normal_dt_with_high_file
    ):
        """STAFF darf ein HIGH-Feld nicht sehen → REMOVE ist ein No-Op:
        Attachment bleibt current, Marker unverändert."""
        dt, ft = normal_dt_with_high_file
        event, att = _event_with_one_attachment(facility, lead_user, dt, ft)

        apply_attachment_changes(
            event=event,
            user=staff_user,
            request_post={f"{ft.slug}__remove": str(att.entry_id)},
            request_files={},
            file_fields={},
            document_type=dt,
        )

        att.refresh_from_db()
        assert att.is_current is True
        assert att.deleted_at is None
        assert event.data_json[ft.slug]["entries"] == [{"id": str(att.pk), "sort": 0}]

    def test_understaffed_user_cannot_add_to_restricted_file_field(
        self, facility, lead_user, staff_user, normal_dt_with_high_file
    ):
        """STAFF darf dem HIGH-Feld auch keinen neuen Anhang hinzufügen."""
        dt, ft = normal_dt_with_high_file
        event, att = _event_with_one_attachment(facility, lead_user, dt, ft)

        apply_attachment_changes(
            event=event,
            user=staff_user,
            request_post={},
            request_files={},
            file_fields={ft.slug: [_upload(b"2")]},
            document_type=dt,
        )

        assert event.attachments.filter(is_current=True).count() == 1
        assert event.data_json[ft.slug]["entries"] == [{"id": str(att.pk), "sort": 0}]

    def test_authorized_lead_can_still_remove(self, facility, lead_user, normal_dt_with_high_file):
        """LEAD (max HIGH) darf das Feld sehen → REMOVE greift wie bisher."""
        dt, ft = normal_dt_with_high_file
        event, att = _event_with_one_attachment(facility, lead_user, dt, ft)

        apply_attachment_changes(
            event=event,
            user=lead_user,
            request_post={f"{ft.slug}__remove": str(att.entry_id)},
            request_files={},
            file_fields={},
            document_type=dt,
        )

        att.refresh_from_db()
        assert att.deleted_at is not None
        assert ft.slug not in event.data_json  # leerer Marker entfernt
