"""Fail-closed-Tests fuer File-Vault Default-Whitelist (Refs #771).

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
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

# libmagic ist Pflicht fuer ``store_encrypted_file`` (Magic-Bytes-Pruefung).
try:
    import magic

    magic.from_buffer(b"%PDF-1.4\n", mime=True)
except Exception as _libmagic_exc:  # noqa: BLE001
    pytest.skip(
        f"libmagic nicht lauffaehig ({_libmagic_exc}) — libmagic erfordert libmagic1.",
        allow_module_level=True,
    )


from core.constants import DEFAULT_ALLOWED_FILE_TYPES  # noqa: E402
from core.models import AuditLog, Settings  # noqa: E402
from core.models.attachment import Attachment  # noqa: E402
from core.services.audit.hash import hmac_hash_value  # noqa: E402
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

        assert Attachment.objects.filter(event=event).count() == 0
        log = AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).latest("timestamp")
        assert log.detail["reason"] == "extension_not_allowed"
        assert "filename" not in log.detail  # Refs #1751: write-time minimiert
        assert log.detail["filename_hash"] == hmac_hash_value("trojaner.exe")
        assert log.detail["filename_ext"] == "exe"

    def test_empty_allowed_file_types_rejects_exe(self, facility, staff_user, doc_type_with_file, event):
        """Case 2: Settings.allowed_file_types == '' → Default-Whitelist greift."""
        Settings.objects.update_or_create(
            facility=facility,
            defaults={"allowed_file_types": "", "max_file_size_mb": 10},
        )
        _, ft_file = doc_type_with_file

        with pytest.raises(ValidationError):
            store_encrypted_file(facility, self._exe_upload(), ft_file, event, staff_user)

        assert Attachment.objects.filter(event=event).count() == 0

    def test_whitespace_only_allowed_file_types_rejects_exe(self, facility, staff_user, doc_type_with_file, event):
        """Case 3: Settings.allowed_file_types == ' , , ' → Default-Whitelist greift."""
        Settings.objects.update_or_create(
            facility=facility,
            defaults={"allowed_file_types": " , , ", "max_file_size_mb": 10},
        )
        _, ft_file = doc_type_with_file

        with pytest.raises(ValidationError):
            store_encrypted_file(facility, self._exe_upload(), ft_file, event, staff_user)

        assert Attachment.objects.filter(event=event).count() == 0

    def test_no_settings_row_accepts_pdf_via_default(self, facility, staff_user, doc_type_with_file, event):
        """Default-Whitelist erlaubt legitime Uploads (pdf), damit der Operator
        nicht erst Settings konfigurieren muss, bevor das Hochladen funktioniert."""
        Settings.objects.filter(facility=facility).delete()
        _, ft_file = doc_type_with_file

        attachment = store_encrypted_file(facility, self._pdf_upload(), ft_file, event, staff_user)
        assert attachment.pk is not None
