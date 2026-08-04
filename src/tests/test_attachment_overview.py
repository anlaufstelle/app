"""Zentrale Datei-Uebersicht — Befunde B1-B3 + Owner-Generik (M24/AP4, Refs #1738).

Die Uebersicht (``core:attachment_list``) war bis M24 fest an das Ereignis
gebunden (Basis-Queryset ``event__facility=…``). Beim Umbau auf die
owner-generische Pipeline sind dabei drei Bestandsbefunde aufgefallen, die
hier je mit eigenem Failing-Test fixiert werden:

* **B1** — soft-geloeschte Anhaenge (``deleted_at``) erschienen weiter,
* **B2** — das Dokumentationstyp-Dropdown leakte HIGH-Typnamen an die Assistenz,
* **B3** — ein Nicht-UUID in ``?client=`` erzeugte HTTP 500.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate
from core.services.file_vault import store_encrypted_file

pytestmark = pytest.mark.django_db

PDF_HEADER = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)

LIST_URL_NAME = "core:attachment_list"


def _file_template(facility, name, *, sensitivity=""):
    return FieldTemplate.objects.create(
        facility=facility,
        name=name,
        field_type=FieldTemplate.FieldType.FILE,
        sensitivity=sensitivity,
    )


def _doc_type(facility, name, entity_type, *, sensitivity=DocumentType.Sensitivity.NORMAL, file_field=None):
    dt = DocumentType.objects.create(
        facility=facility,
        name=name,
        category=DocumentType.Category.ADMIN,
        entity_type=entity_type,
        sensitivity=sensitivity,
    )
    if file_field is not None:
        DocumentTypeField.objects.create(document_type=dt, field_template=file_field, sort_order=0)
    return dt


def _attach(facility, owner, ft, user, filename):
    upload = SimpleUploadedFile(filename, PDF_HEADER + filename.encode(), content_type="application/pdf")
    attachment = store_encrypted_file(facility, upload, ft, owner, user)
    owner.data_json = dict(owner.data_json or {})
    owner.data_json[ft.slug] = {"__files__": True, "entries": [{"id": str(attachment.pk), "sort": 0}]}
    owner.save(update_fields=["data_json"])
    return attachment


@pytest.fixture
def event_attachment(facility, staff_user, client_identified):
    ft = _file_template(facility, "Ereignis-Datei")
    dt = _doc_type(facility, "Ereignistyp mit Datei", DocumentType.EntityType.EVENT, file_field=ft)
    event = Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=dt,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )
    return event, _attach(facility, event, ft, staff_user, "ereignis.pdf"), dt, ft


# ---------------------------------------------------------------------------
# B1 — soft-geloeschte Anhaenge
# ---------------------------------------------------------------------------


class TestSoftDeletedAttachmentsAreHidden:
    """B1 (Refs #1738): ``deleted_at`` blendet den Anhang ueberall aus — nur die
    Uebersicht hatte den Filter nicht.

    Der Download-View wies soft-geloeschte Anhaenge seit L5 (Refs #1375) mit 404
    ab; die Uebersicht listete sie weiter samt Dateiname und Download-Link. Wer
    einen Anhang entfernt hat, darf ihn dort nicht wiederfinden.
    """

    def test_soft_deleted_attachment_is_not_listed(self, client, staff_user, event_attachment):
        _event, attachment, _dt, _ft = event_attachment
        attachment.deleted_at = timezone.now()
        attachment.save(update_fields=["deleted_at"])
        client.force_login(staff_user)
        response = client.get(reverse(LIST_URL_NAME))
        assert response.status_code == 200
        assert "ereignis.pdf" not in response.content.decode()

    def test_active_attachment_is_still_listed(self, client, staff_user, event_attachment):
        client.force_login(staff_user)
        response = client.get(reverse(LIST_URL_NAME))
        assert "ereignis.pdf" in response.content.decode()
