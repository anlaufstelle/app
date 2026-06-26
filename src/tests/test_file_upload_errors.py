"""Upload-Error-Cases: Oversize, Empty, Filename-Injection, Unicode-Filename.

Refs #927, Master #922.

Die Service-Schicht ``store_encrypted_file`` ist die letzte Security-Bastion;
zusätzlich greift im UI-Pfad die Größen-Validierung der ``DynamicEventDataForm``.
Diese Tests dokumentieren:

- **Oversize:** Form lehnt Dateien > ``Settings.max_file_size_mb`` ab.
- **Empty:** Service akzeptiert 0-Byte-Dateien aktuell — explizit dokumentiert
  als Akzept-Verhalten (Magic-Byte-Check würde greifen, aber nur wenn
  ``content_type`` deklariert ist; 0 Bytes ohne Magic-Detection passieren
  die Validierung).
- **Filename-Injection:** Storage-Pfad ist intrinsisch sicher (UUID.enc),
  unabhängig vom Original-Filename — Path-Traversal ist konstruktiv
  ausgeschlossen.
- **Unicode-Filename:** Original-Filename wird verschlüsselt persistiert,
  Storage-Filename ist UUID — Unicode/Emoji/RTL-Marker im Filename brechen
  nichts.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

# libmagic-Setup analog zu ``test_file_vault_magic_bytes.py`` — ohne libmagic1
# kann der Service den Upload nicht validieren; dann sind die Tests sinnlos.
try:
    import magic

    magic.from_buffer(b"%PDF-1.4\n", mime=True)
except Exception as _libmagic_exc:  # noqa: BLE001
    pytest.skip(
        f"libmagic nicht lauffähig ({_libmagic_exc}) — Tests erfordern libmagic1.",
        allow_module_level=True,
    )

from cryptography.fernet import Fernet  # noqa: E402

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate, Settings  # noqa: E402
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
class TestFilenameSafety:
    """Storage-Pfad ist UUID.enc — Filename hat keinen Einfluss auf den Pfad."""

    def test_path_traversal_filename_does_not_escape(
        self, facility_with_settings, staff_user, doc_type_with_file, event
    ):
        """Klassischer ../../etc/passwd-Angriff: Storage-Filename ist trotzdem UUID.enc.

        Da die Extension-Whitelist nur ``.pdf`` etc. erlaubt, würde der naive
        Angriff bereits an der Extension scheitern. Wir fixen die Extension auf
        ``.pdf`` und prüfen, dass der reale Storage-Name UUID-basiert ist.
        """
        _, ft_file = doc_type_with_file
        evil_name = "../../../../etc/passwd.pdf"
        uploaded = SimpleUploadedFile(evil_name, VALID_PDF_BYTES, content_type="application/pdf")

        attachment = store_encrypted_file(facility_with_settings, uploaded, ft_file, event, staff_user)

        assert attachment.storage_filename.endswith(".enc")
        assert ".." not in attachment.storage_filename
        assert "/" not in attachment.storage_filename
        assert "passwd" not in attachment.storage_filename
        # 36 UUID-Zeichen + ".enc"
        assert len(attachment.storage_filename) == 40

    def test_null_byte_in_filename_does_not_break_storage(
        self, facility_with_settings, staff_user, doc_type_with_file, event
    ):
        """Filename mit Null-Byte: Service speichert trotzdem unter UUID.enc.

        Der Null-Byte in ``original_filename`` wird verschlüsselt persistiert
        und betrifft den Disk-Pfad nicht. Wir prüfen, dass der Upload entweder
        durchgeht oder mit einer klaren ValidationError abgelehnt wird —
        nicht aber zu einem korrupten Disk-Pfad führt.
        """
        _, ft_file = doc_type_with_file
        # Extension muss erlaubt sein, sonst greift die Whitelist als erstes.
        evil_name = "clean\x00.pdf"
        uploaded = SimpleUploadedFile(evil_name, VALID_PDF_BYTES, content_type="application/pdf")
        try:
            attachment = store_encrypted_file(facility_with_settings, uploaded, ft_file, event, staff_user)
        except (ValidationError, ValueError):
            # Akzeptable Defense: explizite Ablehnung. Kein korrupter Pfad.
            return
        # Akzeptabel: Service akzeptiert, Storage-Pfad ist UUID, Null-Byte nicht im Pfad.
        assert "\x00" not in attachment.storage_filename
        assert attachment.storage_filename.endswith(".enc")

    def test_unicode_filename_accepted(self, facility_with_settings, staff_user, doc_type_with_file, event):
        """Filename mit Emoji + Umlaut: Upload geht durch, Storage ist UUID.enc."""
        _, ft_file = doc_type_with_file
        name = "Bericht_📝_Müller_2026.pdf"
        uploaded = SimpleUploadedFile(name, VALID_PDF_BYTES, content_type="application/pdf")

        attachment = store_encrypted_file(facility_with_settings, uploaded, ft_file, event, staff_user)

        assert attachment.pk is not None
        # Original-Filename ist verschlüsselt persistiert; nicht im Klartext lesbar.
        assert name not in (attachment.original_filename_encrypted or "")

    def test_rtl_override_in_filename_accepted(self, facility_with_settings, staff_user, doc_type_with_file, event):
        """Filename mit RTL-Override (U+202E) — heute akzeptiert.

        RTL-Override ist ein bekannter Spoofing-Vektor (z.B. ``Bericht‮gpj.pdf``
        rendert als ``Berichtdfp.jpg``). Aktuelles Verhalten: Upload geht durch.
        Test schützt vor unbeabsichtigter Änderung.
        """
        _, ft_file = doc_type_with_file
        spoofed = "Bericht‮gpj.pdf"
        uploaded = SimpleUploadedFile(spoofed, VALID_PDF_BYTES, content_type="application/pdf")

        attachment = store_encrypted_file(facility_with_settings, uploaded, ft_file, event, staff_user)

        assert attachment.pk is not None
        assert attachment.storage_filename.endswith(".enc")


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestEmptyFile:
    """Verhalten bei 0-Byte-Uploads.

    Eine leere Datei hat keine Magic-Bytes — libmagic gibt typischerweise
    ``application/x-empty`` zurück. Das Service-Layer-Verhalten ist heute
    dokumentationswürdig: der Magic-Byte-Check lehnt ab, weil
    detected_mime != declared_mime.
    """

    def test_empty_file_rejected_by_magic_byte_mismatch(
        self, facility_with_settings, staff_user, doc_type_with_file, event
    ):
        _, ft_file = doc_type_with_file
        uploaded = SimpleUploadedFile("leer.pdf", b"", content_type="application/pdf")

        with pytest.raises(ValidationError):
            store_encrypted_file(facility_with_settings, uploaded, ft_file, event, staff_user)

        assert EventAttachment.objects.filter(event=event).count() == 0


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestServiceSizeCap:
    """Refs #1268 (T3): harte Service-Layer-Größenobergrenze.

    Der Service-Layer ist die letzte Bastion; ``FILE_VAULT_MAX_UPLOAD_BYTES``
    ist eine absolute Obergrenze, die VOR der Voll-Pufferung (Virenscan/Encrypt)
    greift — auch für programmatische Caller, die das Formular umgehen.
    """

    def test_service_rejects_file_over_hard_cap(
        self, facility_with_settings, staff_user, doc_type_with_file, event, settings
    ):
        settings.FILE_VAULT_MAX_UPLOAD_BYTES = 1024  # 1 KiB
        _, ft_file = doc_type_with_file
        oversize = SimpleUploadedFile("big.pdf", VALID_PDF_BYTES + b"a" * 2048, content_type="application/pdf")
        with pytest.raises(ValidationError):
            store_encrypted_file(facility_with_settings, oversize, ft_file, event, staff_user)
        assert EventAttachment.objects.filter(event=event).count() == 0

    def test_service_accepts_file_within_cap(
        self, facility_with_settings, staff_user, doc_type_with_file, event, settings
    ):
        settings.FILE_VAULT_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
        _, ft_file = doc_type_with_file
        uploaded = SimpleUploadedFile("ok.pdf", VALID_PDF_BYTES, content_type="application/pdf")
        attachment = store_encrypted_file(facility_with_settings, uploaded, ft_file, event, staff_user)
        assert attachment.pk is not None


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestImageDecompressionBomb:
    """Refs #1268 (T3): Pixel-Obergrenze gegen Bild-Decompression-Bomben.

    Eine klein komprimierte Datei, die zu Milliarden Pixeln dekodiert, würde
    den RAM erschöpfen; der Header-Pixel-Check lehnt sie ab, bevor dekodiert
    wird.
    """

    def test_oversized_pixel_image_rejected(
        self, facility_with_settings, staff_user, doc_type_with_file, event, settings
    ):
        import io

        from PIL import Image

        settings.FILE_VAULT_MAX_IMAGE_PIXELS = 50  # winziges Limit
        _, ft_file = doc_type_with_file
        buf = io.BytesIO()
        Image.new("RGB", (10, 10)).save(buf, format="PNG")  # 100 Pixel > 50
        uploaded = SimpleUploadedFile("bomb.png", buf.getvalue(), content_type="image/png")
        with pytest.raises(ValidationError):
            store_encrypted_file(facility_with_settings, uploaded, ft_file, event, staff_user)
        assert EventAttachment.objects.filter(event=event).count() == 0

    def test_small_image_within_cap_accepted(
        self, facility_with_settings, staff_user, doc_type_with_file, event, settings
    ):
        import io

        from PIL import Image

        settings.FILE_VAULT_MAX_IMAGE_PIXELS = 1000
        _, ft_file = doc_type_with_file
        buf = io.BytesIO()
        Image.new("RGB", (10, 10)).save(buf, format="PNG")  # 100 Pixel < 1000
        uploaded = SimpleUploadedFile("ok.png", buf.getvalue(), content_type="image/png")
        attachment = store_encrypted_file(facility_with_settings, uploaded, ft_file, event, staff_user)
        assert attachment.pk is not None


class TestMultipleFileCountCap:
    """Refs #1268 (T3): ``MultipleFileField`` cappt die Datei-Anzahl pro Feld/POST."""

    def test_too_many_files_rejected(self, settings):
        from django import forms

        from core.forms.events import MultipleFileField

        settings.FILE_VAULT_MAX_UPLOAD_FILES = 2
        field = MultipleFileField(required=False)
        files = [SimpleUploadedFile(f"f{i}.pdf", VALID_PDF_BYTES, content_type="application/pdf") for i in range(3)]
        with pytest.raises(forms.ValidationError):
            field.clean(files)

    def test_within_cap_accepted(self, settings):
        from core.forms.events import MultipleFileField

        settings.FILE_VAULT_MAX_UPLOAD_FILES = 2
        field = MultipleFileField(required=False)
        files = [SimpleUploadedFile(f"f{i}.pdf", VALID_PDF_BYTES, content_type="application/pdf") for i in range(2)]
        cleaned = field.clean(files)
        assert len(cleaned) == 2


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestOversizeViaForm:
    """Größenlimit greift im Form-Layer (per-Facility ``max_file_size_mb``) UND
    seit Refs #1268 zusätzlich als harte Service-Obergrenze
    (``FILE_VAULT_MAX_UPLOAD_BYTES``, siehe :class:`TestServiceSizeCap`).

    Dieser Test deckt weiterhin den UI-Pfad ab: ``DynamicEventDataForm`` lehnt
    eine Datei über der per-Facility-Größe ab.
    """

    def test_oversize_via_form_rejected(self, facility_with_settings, staff_user, doc_type_with_file):
        """Form mit Datei > max_file_size_mb → ValidationError im File-Feld."""
        from core.forms.events import DynamicEventDataForm

        dt, ft_file = doc_type_with_file
        # Settings auf 1 MB drücken; Datei = 2 MB.
        Settings.objects.update_or_create(
            facility=facility_with_settings,
            defaults={"allowed_file_types": "pdf", "max_file_size_mb": 1},
        )
        huge = SimpleUploadedFile(
            "zu_gross.pdf", VALID_PDF_BYTES + b"a" * (2 * 1024 * 1024), content_type="application/pdf"
        )
        form = DynamicEventDataForm(
            document_type=dt,
            facility=facility_with_settings,
            data={},
            files={ft_file.slug: huge},
        )
        assert not form.is_valid(), f"Form unerwartet gültig: {form.errors}"
        # Größen-Fehler muss im Field-Error stecken.
        all_errors = " ".join(str(e) for errs in form.errors.values() for e in errs)
        assert "groß" in all_errors.lower() or "max" in all_errors.lower() or "mb" in all_errors.lower(), (
            f"Erwarteter Größen-Hinweis in Fehlermeldung, got: {form.errors}"
        )
