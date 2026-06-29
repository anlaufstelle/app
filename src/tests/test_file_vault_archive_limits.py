"""Tests für den Archiv-Expansions-Guard (Zip-Bomb) im File-Vault (Refs #1310 S4).

``enforce_archive_limits`` liest die Summe der UNkomprimierten Eintrags-Größen und
das Expansions-Verhältnis (unkomprimiert/komprimiert) aus dem Zip-Directory —
ohne die Einträge zu entpacken — und lehnt Decompression-Bomben ab, bevor ein
nachgelagerter Konsument sie auspackt.

Der direkte Guard-Test braucht kein libmagic. Der End-to-End-Test durch
``store_encrypted_file`` schon (Magic-Bytes-Schritt) und wird sonst geskippt.
"""

from __future__ import annotations

import io
import struct
import zipfile

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from core.models import AuditLog, DocumentType, DocumentTypeField, Event, FieldTemplate, Settings
from core.models.attachment import EventAttachment
from core.services.file_vault import store_encrypted_file
from core.services.file_vault.policy import enforce_archive_limits

# libmagic wird nur fuer den End-to-End-Pfad (store_encrypted_file) gebraucht.
try:
    import magic

    magic.from_buffer(b"%PDF-1.4\n", mime=True)
    _HAS_LIBMAGIC = True
except Exception:  # noqa: BLE001 — libmagic-Shared-Library fehlt
    _HAS_LIBMAGIC = False

# OOXML-MIME fuer .docx (Browser-deklarierter Content-Type eines echten DOCX).
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _zip_upload(name, payload, *, content_type="application/zip", compression=zipfile.ZIP_DEFLATED):
    """Baue ein gueltiges In-Memory-ZIP mit einem einzigen Eintrag ``payload``."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        zf.writestr("payload.bin", payload)
    return SimpleUploadedFile(name, buf.getvalue(), content_type=content_type)


def _zip_with_entries(name, count, *, content_type="application/zip"):
    """Baue ein gueltiges In-Memory-ZIP mit ``count`` trivialen Eintraegen."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(count):
            zf.writestr(f"e{i}.txt", b"x")
    return SimpleUploadedFile(name, buf.getvalue(), content_type=content_type)


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
        defaults={"allowed_file_types": "pdf,zip,docx", "max_file_size_mb": 50},
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
class TestArchiveExpansionGuard:
    """``enforce_archive_limits`` lehnt Zip-Bomben ab und lässt normale Archive durch."""

    def test_ratio_bomb_rejected(self, facility_with_settings, staff_user, event, settings):
        """Hohes Expansions-Verhältnis (2 MB Nullen → wenige KB) → archive_bomb."""
        settings.FILE_VAULT_MAX_ARCHIVE_RATIO = 10  # 2 MB Nullen liegen weit darüber
        upload = _zip_upload("bomb.zip", b"\x00" * (2 * 1024 * 1024))

        with pytest.raises(ValidationError):
            enforce_archive_limits(facility_with_settings, upload, event, staff_user)

        # Stream zurückgespult — die nachgelagerte Verschlüsselung kann noch lesen.
        assert upload.tell() == 0
        log = AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).latest("timestamp")
        assert log.detail["reason"] == "archive_bomb"
        assert log.detail["filename"] == "bomb.zip"

    def test_size_bomb_rejected(self, facility_with_settings, staff_user, event, settings):
        """Unkomprimierte Gesamtgröße über dem Byte-Cap → archive_bomb (Ratio isoliert)."""
        settings.FILE_VAULT_MAX_ARCHIVE_BYTES = 1024 * 1024  # 1 MB
        settings.FILE_VAULT_MAX_ARCHIVE_RATIO = 10**9  # Ratio nie auslösen → Bytes isolieren
        upload = _zip_upload("big.zip", b"\x00" * (2 * 1024 * 1024))

        with pytest.raises(ValidationError):
            enforce_archive_limits(facility_with_settings, upload, event, staff_user)

        log = AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).latest("timestamp")
        assert log.detail["reason"] == "archive_bomb"

    def test_normal_small_zip_accepted(self, facility_with_settings, staff_user, event):
        """Ein normales kleines ZIP (mäßig komprimierbar) darf NICHT auslösen."""
        upload = _zip_upload("normal.zip", bytes(range(256)) * 8)

        enforce_archive_limits(facility_with_settings, upload, event, staff_user)

        assert upload.tell() == 0
        assert AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).count() == 0

    def test_normal_small_docx_accepted(self, facility_with_settings, staff_user, event):
        """OOXML ist ein ZIP-Container → derselbe Guard greift, darf bei normalen
        Dateien aber nicht auslösen."""
        upload = _zip_upload("brief.docx", b"<?xml version='1.0'?><doc/>", content_type=DOCX_MIME)

        enforce_archive_limits(facility_with_settings, upload, event, staff_user)

        assert AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).count() == 0

    def test_corrupt_zip_skipped(self, facility_with_settings, staff_user, event):
        """Kaputtes ZIP trotz .zip-Endung → BadZipFile → Guard überspringt
        (Magic/Extension/Virus entscheiden), keine Ablehnung hier."""
        upload = SimpleUploadedFile("broken.zip", b"PK\x03\x04 kein echtes zip", content_type="application/zip")

        enforce_archive_limits(facility_with_settings, upload, event, staff_user)

        assert upload.tell() == 0
        assert AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).count() == 0

    def test_non_archive_extension_skipped(self, facility_with_settings, staff_user, event):
        """Nicht-Archiv-Endung (.pdf) → Guard greift gar nicht."""
        upload = SimpleUploadedFile("scan.pdf", b"%PDF-1.4\n%%EOF\n", content_type="application/pdf")

        enforce_archive_limits(facility_with_settings, upload, event, staff_user)

        assert AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).count() == 0

    def test_too_many_entries_rejected(self, facility_with_settings, staff_user, event, settings):
        """Eine ZIP mit absurd vielen Einträgen → archive_too_many_entries.

        Das ist der eigentliche S4-DoS-Vektor: ein strukturell gültiges ≤50-MB-
        ZIP64 mit ~1 Mio Mini-Einträgen ließe ``ZipFile()``/``infolist()`` pro
        Eintrag ein ``ZipInfo`` allokieren (hunderte MB transient → OOM auf RAM-
        limitierten Containern). Der billige EOCD-Vorabcheck lehnt das ab.
        Refs #1310 (S4).
        """
        settings.FILE_VAULT_MAX_ARCHIVE_ENTRIES = 5
        upload = _zip_with_entries("many.zip", 6)

        with pytest.raises(ValidationError):
            enforce_archive_limits(facility_with_settings, upload, event, staff_user)

        # Stream zurückgespult — die nachgelagerte Verschlüsselung kann noch lesen.
        assert upload.tell() == 0
        log = AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).latest("timestamp")
        assert log.detail["reason"] == "archive_too_many_entries"
        assert log.detail["entries"] == 6
        assert log.detail["max_entries"] == 5

    def test_entry_count_precheck_runs_before_zipfile(
        self, facility_with_settings, staff_user, event, settings, monkeypatch
    ):
        """Der Eintrags-Check lehnt ab, OHNE ``zipfile.ZipFile()`` aufzurufen —
        sonst würde ``infolist()`` pro Eintrag ein ``ZipInfo`` materialisieren
        (genau der Memory-DoS, den der Check verhindert). Refs #1310 (S4)."""
        settings.FILE_VAULT_MAX_ARCHIVE_ENTRIES = 5
        upload = _zip_with_entries("many.zip", 6)

        def _must_not_open(*args, **kwargs):  # pragma: no cover - darf nie aufgerufen werden
            raise AssertionError("zipfile.ZipFile vor dem EOCD-Vorabcheck aufgerufen")

        monkeypatch.setattr(zipfile, "ZipFile", _must_not_open)

        with pytest.raises(ValidationError):
            enforce_archive_limits(facility_with_settings, upload, event, staff_user)

        log = AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).latest("timestamp")
        assert log.detail["reason"] == "archive_too_many_entries"

    def test_few_entries_under_cap_accepted(self, facility_with_settings, staff_user, event, settings):
        """Unter dem Eintrags-Cap greift der Vorabcheck nicht — normale Archive
        laufen weiter durch den Größen-/Ratio-Pfad (kein False Positive)."""
        settings.FILE_VAULT_MAX_ARCHIVE_ENTRIES = 5
        upload = _zip_with_entries("few.zip", 2)

        enforce_archive_limits(facility_with_settings, upload, event, staff_user)

        assert upload.tell() == 0
        assert AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).count() == 0

    @pytest.mark.parametrize(
        "exc",
        [OSError("disk weg"), EOFError(), ValueError("kaputt"), struct.error("unpack")],
    )
    def test_malformed_zip_non_badzipfile_skipped(self, facility_with_settings, staff_user, event, monkeypatch, exc):
        """Nicht nur ``BadZipFile``: auch andere Parser-Fehler (OSError/EOFError/
        ValueError/struct.error) auf pathologischen ZIPs werden graceful
        übersprungen statt als unbehandelter 500 durchzuschlagen. Refs #1310 (S4)."""
        upload = _zip_upload("weird.zip", b"egal")  # gültiges EOCD, 1 Eintrag → Vorabcheck passt

        def _raise(*args, **kwargs):
            raise exc

        monkeypatch.setattr(zipfile, "ZipFile", _raise)

        enforce_archive_limits(facility_with_settings, upload, event, staff_user)

        assert upload.tell() == 0
        assert AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).count() == 0


@pytest.mark.skipif(not _HAS_LIBMAGIC, reason="libmagic erforderlich für store_encrypted_file (Magic-Bytes)")
@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestArchiveGuardWiring:
    """Der Guard ist in ``store_encrypted_file`` verdrahtet und greift VOR der
    Verschlüsselung (kein EventAttachment, keine Datei auf der Disk)."""

    def test_zip_bomb_rejected_end_to_end(self, facility_with_settings, staff_user, doc_type_with_file, settings):
        settings.FILE_VAULT_MAX_ARCHIVE_RATIO = 10
        _, ft_file = doc_type_with_file
        dt = DocumentType.objects.get(facility=facility_with_settings)
        event = Event.objects.create(
            facility=facility_with_settings,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        upload = _zip_upload("bomb.zip", b"\x00" * (2 * 1024 * 1024))

        with pytest.raises(ValidationError):
            store_encrypted_file(facility_with_settings, upload, ft_file, event, staff_user)

        assert EventAttachment.objects.filter(event=event).count() == 0
        log = AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).latest("timestamp")
        assert log.detail["reason"] == "archive_bomb"
