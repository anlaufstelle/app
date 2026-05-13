"""RF-T05: Fail-closed-Tests fuer File-Vault Default-Whitelist (Refs #771).

Bevor #771 geschlossen
wurde, lieferte ``_enforce_allowed_file_types`` ``return`` (also fail-open),
wenn die ``Settings``-Row der Facility fehlte oder ``allowed_file_types`` leer
war — jede Datei wurde akzeptiert.

Diese Tests verankern die fail-closed-Garantie: ohne valide Whitelist greift
``DEFAULT_ALLOWED_FILE_TYPES``, sodass z.B. ``.exe`` weiterhin abgelehnt wird.
``pdf`` (Teil der Default-Whitelist) bleibt akzeptiert, damit legitime
Uploads ohne Operator-Konfiguration nicht broken sind.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

# libmagic ist Pflicht fuer ``store_encrypted_file`` (Magic-Bytes-Pruefung).
try:
    import magic

    magic.from_buffer(b"%PDF-1.4\n", mime=True)
except Exception as _libmagic_exc:  # noqa: BLE001
    pytest.skip(
        f"libmagic nicht lauffaehig ({_libmagic_exc}) — RF-T05 erfordert libmagic1.",
        allow_module_level=True,
    )


from core.constants import DEFAULT_ALLOWED_FILE_TYPES  # noqa: E402
from core.models import (  # noqa: E402
    AuditLog,
    DocumentType,
    DocumentTypeField,
    Event,
    FieldTemplate,
    Settings,
)
from core.models.attachment import EventAttachment  # noqa: E402
from core.services.file_vault import store_encrypted_file  # noqa: E402

VALID_PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)
PE_EXECUTABLE_BYTES = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00" + b"\x00" * 128


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
    ft_file = FieldTemplate.objects.create(
        facility=facility,
        name="Scan",
        field_type=FieldTemplate.FieldType.FILE,
    )
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_file, sort_order=0)
    return dt, ft_file


@pytest.fixture
def event(facility, staff_user, doc_type_with_file):
    dt, _ = doc_type_with_file
    return Event.objects.create(
        facility=facility,
        document_type=dt,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestFileVaultFailClosed:
    """Drei Cases — fehlende Settings-Row, leere Whitelist, Whitespace-only —
    duerfen das Whitelist-Tor nicht oeffnen.
    """

    def _exe_upload(self):
        return SimpleUploadedFile(
            "trojaner.exe",
            PE_EXECUTABLE_BYTES,
            content_type="application/octet-stream",
        )

    def _pdf_upload(self):
        return SimpleUploadedFile(
            "bescheid.pdf",
            VALID_PDF_BYTES,
            content_type="application/pdf",
        )

    def test_default_whitelist_seeded(self):
        """Sanity: die Default-Whitelist enthaelt die produktiv genutzten Typen."""
        for ext in ("pdf", "jpg", "jpeg", "png", "docx"):
            assert ext in DEFAULT_ALLOWED_FILE_TYPES

    def test_no_settings_row_rejects_exe(self, facility, staff_user, doc_type_with_file, event):
        """Case 1: Keine Settings-Row → Default-Whitelist greift, .exe abgelehnt."""
        Settings.objects.filter(facility=facility).delete()
        _, ft_file = doc_type_with_file

        with pytest.raises(ValidationError):
            store_encrypted_file(facility, self._exe_upload(), ft_file, event, staff_user)

        assert EventAttachment.objects.filter(event=event).count() == 0
        log = AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).latest("timestamp")
        assert log.detail["reason"] == "extension_not_allowed"
        assert log.detail["filename"] == "trojaner.exe"

    def test_empty_allowed_file_types_rejects_exe(self, facility, staff_user, doc_type_with_file, event):
        """Case 2: Settings.allowed_file_types == '' → Default-Whitelist greift."""
        Settings.objects.update_or_create(
            facility=facility,
            defaults={"allowed_file_types": "", "max_file_size_mb": 10},
        )
        _, ft_file = doc_type_with_file

        with pytest.raises(ValidationError):
            store_encrypted_file(facility, self._exe_upload(), ft_file, event, staff_user)

        assert EventAttachment.objects.filter(event=event).count() == 0

    def test_whitespace_only_allowed_file_types_rejects_exe(self, facility, staff_user, doc_type_with_file, event):
        """Case 3: Settings.allowed_file_types == ' , , ' → Default-Whitelist greift."""
        Settings.objects.update_or_create(
            facility=facility,
            defaults={"allowed_file_types": " , , ", "max_file_size_mb": 10},
        )
        _, ft_file = doc_type_with_file

        with pytest.raises(ValidationError):
            store_encrypted_file(facility, self._exe_upload(), ft_file, event, staff_user)

        assert EventAttachment.objects.filter(event=event).count() == 0

    def test_no_settings_row_accepts_pdf_via_default(self, facility, staff_user, doc_type_with_file, event):
        """Default-Whitelist erlaubt legitime Uploads (pdf), damit der Operator
        nicht erst Settings konfigurieren muss, bevor das Hochladen funktioniert."""
        Settings.objects.filter(facility=facility).delete()
        _, ft_file = doc_type_with_file

        attachment = store_encrypted_file(facility, self._pdf_upload(), ft_file, event, staff_user)
        assert attachment.pk is not None
