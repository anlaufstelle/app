"""Tests für Magic-Bytes-Validierung und ``allowed_file_types``-Whitelist im File-Vault (Refs #610).

Die Service-Schicht ``store_encrypted_file`` muss die letzte Security-Bastion sein:

1. Extension-Whitelist aus ``Settings.allowed_file_types`` wird strikt erzwungen.
2. Der tatsächliche MIME-Typ (libmagic-Sniffing) muss zum browser-deklarierten
   ``content_type`` passen — Payload-Smuggling (z.B. PE-Executable als PDF)
   wird abgewiesen.
3. Jeder Verstoß wird als ``AuditLog.Action.SECURITY_VIOLATION`` protokolliert.

Wenn ``python-magic`` oder die System-Bibliothek ``libmagic1`` fehlen, werden
die Tests geskippt — der Production-Code läuft erst im neu gebauten
Docker-Image (siehe Dockerfile).
"""

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

# libmagic muss als Shared-Library vorhanden sein — sonst überspringen wir die
# Tests statt sie auf Host-Systemen ohne libmagic1 scheitern zu lassen.
try:
    import magic

    magic.from_buffer(b"%PDF-1.4\n", mime=True)
except Exception as _libmagic_exc:  # noqa: BLE001 — libmagic-Shared-Library fehlt
    pytest.skip(
        f"libmagic nicht lauffähig ({_libmagic_exc}) — Tests erfordern libmagic1.",
        allow_module_level=True,
    )


from core.models import AuditLog, DocumentType, DocumentTypeField, Event, FieldTemplate, Settings  # noqa: E402
from core.models.attachment import EventAttachment  # noqa: E402
from core.services.file_vault import store_encrypted_file  # noqa: E402

# Minimaler, aber gültiger PDF-Header — libmagic erkennt das als application/pdf.
VALID_PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)

# 1x1 transparentes PNG — echte Magic Bytes.
VALID_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
    b"\x89\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)

# Windows-PE-Header — libmagic erkennt das als application/x-dosexec.
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
def facility_with_settings(facility):
    Settings.objects.update_or_create(
        facility=facility,
        defaults={"allowed_file_types": "pdf,jpg,jpeg,png", "max_file_size_mb": 10},
    )
    return facility


@pytest.fixture
def event(facility_with_settings, staff_user, doc_type_with_file):
    dt, _ = doc_type_with_file
    return Event.objects.create(
        facility=facility_with_settings,
        document_type=dt,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestMagicBytesValidation:
    """Magic-Bytes-Sniffing muss gefälschte MIME-Typen zuverlässig erkennen."""

    def test_real_pdf_accepted(self, facility_with_settings, staff_user, doc_type_with_file, event):
        """Echte PDF-Bytes mit deklariertem PDF-Content-Type → erfolgreich gespeichert."""
        _, ft_file = doc_type_with_file
        uploaded = SimpleUploadedFile("bescheid.pdf", VALID_PDF_BYTES, content_type="application/pdf")

        attachment = store_encrypted_file(facility_with_settings, uploaded, ft_file, event, staff_user)

        assert attachment.pk is not None
        assert EventAttachment.objects.filter(event=event).count() == 1
        assert AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).count() == 0

    def test_png_masquerading_as_pdf_rejected(self, facility_with_settings, staff_user, doc_type_with_file, event):
        """PNG-Bytes mit PDF-Content-Type → ValidationError + SECURITY_VIOLATION."""
        _, ft_file = doc_type_with_file
        # Erweiterung .pdf, damit wir die Whitelist passieren und wirklich den Magic-Byte-Check treffen.
        uploaded = SimpleUploadedFile("tricky.pdf", VALID_PNG_BYTES, content_type="application/pdf")

        with pytest.raises(ValidationError):
            store_encrypted_file(facility_with_settings, uploaded, ft_file, event, staff_user)

        assert EventAttachment.objects.filter(event=event).count() == 0
        log = AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).latest("timestamp")
        assert log.detail["reason"] == "mime_mismatch"
        assert log.detail["declared"] == "application/pdf"
        assert log.detail["detected"].startswith("image/png")
        assert log.detail["filename"] == "tricky.pdf"

    def test_pe_executable_rejected(self, facility_with_settings, staff_user, doc_type_with_file, event):
        """PE-Executable (MZ-Header) mit PDF-Content-Type → ValidationError + SECURITY_VIOLATION."""
        _, ft_file = doc_type_with_file
        uploaded = SimpleUploadedFile("virus.pdf", PE_EXECUTABLE_BYTES, content_type="application/pdf")

        with pytest.raises(ValidationError):
            store_encrypted_file(facility_with_settings, uploaded, ft_file, event, staff_user)

        assert EventAttachment.objects.filter(event=event).count() == 0
        log = AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).latest("timestamp")
        assert log.detail["reason"] == "mime_mismatch"
        assert log.detail["declared"] == "application/pdf"
        # libmagic erkennt PE-Binaries als "application/x-dosexec" oder Varianten.
        assert "dos" in log.detail["detected"].lower() or "exec" in log.detail["detected"].lower()


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestAllowedFileTypesWhitelist:
    """Die ``allowed_file_types``-Whitelist muss auf Service-Ebene greifen."""

    def test_extension_outside_whitelist_rejected(self, facility, staff_user, doc_type_with_file):
        """SVG darf nicht durchkommen, wenn die Whitelist nur pdf/jpg/png kennt."""
        # Facility mit restriktiver Whitelist.
        Settings.objects.update_or_create(
            facility=facility,
            defaults={"allowed_file_types": "pdf,jpg,png", "max_file_size_mb": 10},
        )
        dt, ft_file = doc_type_with_file
        event = Event.objects.create(
            facility=facility,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        uploaded = SimpleUploadedFile(
            "evil.svg",
            b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
            content_type="image/svg+xml",
        )

        with pytest.raises(ValidationError):
            store_encrypted_file(facility, uploaded, ft_file, event, staff_user)

        assert EventAttachment.objects.filter(event=event).count() == 0
        log = AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).latest("timestamp")
        assert log.detail["reason"] == "extension_not_allowed"
        assert log.detail["extension"] == "svg"
        assert log.detail["filename"] == "evil.svg"

    def test_whitelist_applied_before_magic_check(self, facility, staff_user, doc_type_with_file):
        """Die Whitelist-Prüfung läuft VOR dem Magic-Byte-Check — so landet
        ein fremder Dateityp nicht im langsameren libmagic-Pfad, und der
        AuditLog ist eindeutig (``extension_not_allowed`` statt
        ``mime_mismatch``)."""
        Settings.objects.update_or_create(
            facility=facility,
            defaults={"allowed_file_types": "pdf", "max_file_size_mb": 10},
        )
        dt, ft_file = doc_type_with_file
        event = Event.objects.create(
            facility=facility,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        # PNG mit passenden PNG-Magic-Bytes, aber .png-Extension ist hier nicht whitelistet.
        uploaded = SimpleUploadedFile("photo.png", VALID_PNG_BYTES, content_type="image/png")

        with pytest.raises(ValidationError):
            store_encrypted_file(facility, uploaded, ft_file, event, staff_user)

        log = AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).latest("timestamp")
        assert log.detail["reason"] == "extension_not_allowed"
