"""Service for encrypted file storage."""

import logging
import uuid
from pathlib import Path

from django.conf import settings as django_settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.models.attachment import EventAttachment
from core.models.audit import AuditLog
from core.services.encryption import decrypt_file_stream, encrypt_field, encrypt_file, safe_decrypt
from core.services.virus_scan import VirusScannerUnavailableError, scan_file

logger = logging.getLogger(__name__)


def _facility_dir(facility):
    """Return the storage directory for a facility."""
    return Path(django_settings.MEDIA_ROOT) / str(facility.pk)


def _run_virus_scan(facility, uploaded_file, event, user):
    """Scan ``uploaded_file`` via ClamAV. Raises ``ValidationError`` on hit or
    when the scanner is unreachable while ``CLAMAV_ENABLED`` is true.

    Creates an ``AuditLog`` entry with ``Action.SECURITY_VIOLATION`` for every
    detected infection and also for fail-closed scanner outages — both are
    security-relevant events that operators need to see.
    """
    try:
        result = scan_file(uploaded_file)
    except VirusScannerUnavailableError as exc:
        logger.error(
            "Virenscanner nicht erreichbar — Upload wird abgewiesen (fail-closed): %s",
            exc,
        )
        AuditLog.objects.create(
            facility=facility,
            user=user,
            action=AuditLog.Action.SECURITY_VIOLATION,
            target_type="EventAttachment",
            target_id=str(event.pk) if getattr(event, "pk", None) else "",
            detail={
                "reason": "virus_scanner_unavailable",
                "filename": uploaded_file.name,
                "error": str(exc),
            },
        )
        raise ValidationError(_("Datei-Upload abgelehnt: Virenscanner ist nicht erreichbar.")) from exc

    if result.infected:
        AuditLog.objects.create(
            facility=facility,
            user=user,
            action=AuditLog.Action.SECURITY_VIOLATION,
            target_type="EventAttachment",
            target_id=str(event.pk) if getattr(event, "pk", None) else "",
            detail={
                "reason": "virus_detected",
                "filename": uploaded_file.name,
                "signature": result.signature or "",
            },
        )
        raise ValidationError(
            _("Datei wurde von Virenscanner abgewiesen: %(signature)s") % {"signature": result.signature or "unknown"}
        )


def store_encrypted_file(facility, uploaded_file, field_template, event, user):
    """Encrypt and store an uploaded file. Returns EventAttachment instance.

    1. Scan file with ClamAV (if CLAMAV_ENABLED) BEFORE encryption — infected
       uploads are rejected with ``ValidationError`` and logged as
       ``SECURITY_VIOLATION``. Scanner errors are treated as fail-closed when
       scanning is enabled (Issue #524).
    2. Generate UUID filename
    3. Create facility subdirectory
    4. Encrypt file stream to disk
    5. Create EventAttachment record
    """
    _run_virus_scan(facility, uploaded_file, event, user)

    storage_name = f"{uuid.uuid4()}.enc"
    output_path = _facility_dir(facility) / storage_name

    encrypt_file(uploaded_file, output_path)

    return EventAttachment.objects.create(
        event=event,
        field_template=field_template,
        storage_filename=storage_name,
        original_filename_encrypted=encrypt_field(uploaded_file.name),
        file_size=uploaded_file.size,
        mime_type=uploaded_file.content_type or "application/octet-stream",
        created_by=user,
    )


def get_attachment_path(attachment):
    """Return the full filesystem path of an encrypted attachment."""
    return _facility_dir(attachment.event.facility) / attachment.storage_filename


def get_decrypted_file_stream(attachment):
    """Return a generator of decrypted chunks for streaming download."""
    return decrypt_file_stream(get_attachment_path(attachment))


def get_original_filename(attachment):
    """Decrypt and return the original filename."""
    return safe_decrypt(attachment.original_filename_encrypted, default="download")


def delete_attachment_file(attachment):
    """Delete the physical encrypted file from disk."""
    path = get_attachment_path(attachment)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Failed to delete attachment file: %s", path)


def delete_event_attachments(event):
    """Delete all attachments for an event (files + DB records)."""
    for attachment in event.attachments.all():
        delete_attachment_file(attachment)
    event.attachments.all().delete()
