"""Mutation-Pins v0.96.0 — Einrichtungsbindung der Vault-Choke-Point-Zeilen.

``test_attachment_audit_choke_points.py`` holt die ATTACHMENT_UPLOAD- und
ATTACHMENT_DELETE-Zeile ueber ``action``/``target_id`` und prueft danach
``detail``, ``target_type`` und ``user`` — nie die ``facility``. Eine Zeile
ohne Einrichtung faellt aus dem ``FacilityScopedManager`` und damit aus jeder
Einrichtungs-Sicht heraus und haengt in der NULL-Facility-HMAC-Kette statt in
der der Einrichtung (Refs #1070): der Nachweis, dass eine Datei ins Haus kam
bzw. das Haus verliess, fehlt der Einrichtung dann genau dort, wo er gebraucht
wird.
"""

from __future__ import annotations

import pytest

from core.models import AuditLog, FieldTemplate
from core.services.file_vault.storage import StagedUpload, commit_staged_upload, soft_delete_attachment_chain

pytestmark = pytest.mark.django_db


@pytest.fixture
def file_template(facility):
    return FieldTemplate.objects.create(
        facility=facility,
        name="Pin-Anhang",
        field_type=FieldTemplate.FieldType.FILE,
    )


def _staged(tmp_path, *, name="Bericht-Klartext-Person.pdf") -> StagedUpload:
    output_path = tmp_path / "staged.enc"
    output_path.write_bytes(b"encrypted")
    return StagedUpload(
        storage_name="staged.enc",
        output_path=output_path,
        detected_mime="application/pdf",
        file_size=9,
        original_name=name,
        content_type="application/pdf",
    )


@pytest.mark.usefixtures("_encryption_key")
class TestVaultAuditFacilityBinding:
    def test_upload_row_belongs_to_the_owner_facility(self, tmp_path, sample_event, file_template, staff_user):
        """Pins x_audit_attachment_upload__mutmut_5: die Upload-Zeile traegt die
        Einrichtung des Owners, nicht ``None``."""
        commit_staged_upload(sample_event, file_template, _staged(tmp_path), staff_user)

        entry = AuditLog.objects.get(action=AuditLog.Action.ATTACHMENT_UPLOAD)
        assert entry.facility_id == sample_event.facility_id
        assert entry.facility_id is not None

    def test_delete_row_belongs_to_the_owner_facility(self, tmp_path, sample_event, file_template, staff_user):
        """Pins x_audit_attachment_delete__mutmut_5: die Soft-Delete-Zeile traegt
        die Einrichtung des Owners, nicht ``None``."""
        attachment = commit_staged_upload(sample_event, file_template, _staged(tmp_path), staff_user)

        assert soft_delete_attachment_chain(sample_event, attachment.entry_id, staff_user) == 1

        entry = AuditLog.objects.get(action=AuditLog.Action.ATTACHMENT_DELETE)
        assert entry.facility_id == sample_event.facility_id
        assert entry.facility_id is not None
