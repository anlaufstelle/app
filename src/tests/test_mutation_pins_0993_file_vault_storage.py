"""Pins fuer die Vorsperre beim Soft-Delete einer Kette (``core.services.file_vault.storage``).

Quelle: Mutations-Delta des Checkpoints release-0.99.2, 4 Mutanten in
``soft_delete_attachment_chain``. Eine Gruppe: die Vorsperre ueber
``lock_attachment_rows_in_facility_order`` nimmt genau die eigene Kette
(``[entry_id]``, nicht ``None`` = alle Owner-Zeilen) und die Owner-Stufe
(``lock_owner_rows=True``). Der Aufruf wird mitgeschnitten und exakt verglichen.
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import FieldTemplate
from core.services.attachments import crud
from core.services.file_vault import commit_staged_upload, prepare_encrypted_upload, soft_delete_attachment_chain

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _attach(owner, ft, user):
    upload = SimpleUploadedFile("beleg.pdf", PDF, content_type="application/pdf")
    staged = prepare_encrypted_upload(owner.facility, upload, user, owner=owner)
    return commit_staged_upload(owner, ft, staged, user)


class TestSoftDeleteAttachmentChain:
    def test_vorsperre_eigene_kette_mit_owner_stufe(self, sample_event, staff_user, monkeypatch):
        """x_soft_delete_attachment_chain__mutmut_3 (``None`` = alle
        Owner-Zeilen), _4 (``lock_owner_rows=None``), _7
        (``lock_owner_rows=False``), _8 (``lock_owner_rows`` fehlt)."""
        aufrufe = []
        original = crud.lock_attachment_rows_in_facility_order

        def _spy(owner, entry_ids, **kwargs):
            aufrufe.append((owner, None if entry_ids is None else list(entry_ids), kwargs))
            return original(owner, entry_ids, **kwargs)

        monkeypatch.setattr(crud, "lock_attachment_rows_in_facility_order", _spy)
        ft = FieldTemplate.objects.create(
            facility=sample_event.facility, name="Soft-Delete", field_type=FieldTemplate.FieldType.FILE
        )
        eigene = _attach(sample_event, ft, staff_user)
        _attach(sample_event, ft, staff_user)
        assert soft_delete_attachment_chain(sample_event, eigene.entry_id, staff_user) == 1
        assert aufrufe == [(sample_event, [eigene.entry_id], {"lock_owner_rows": True})]
