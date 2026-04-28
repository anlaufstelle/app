"""Service for encrypted file storage."""

import logging
import uuid
from pathlib import Path

from django.conf import settings as django_settings

from core.models.attachment import EventAttachment
from core.services.encryption import decrypt_file_stream, encrypt_field, encrypt_file, safe_decrypt

logger = logging.getLogger(__name__)


def _facility_dir(facility):
    """Return the storage directory for a facility."""
    return Path(django_settings.MEDIA_ROOT) / str(facility.pk)


def store_encrypted_file(facility, uploaded_file, field_template, event, user):
    """Encrypt and store an uploaded file. Returns EventAttachment instance.

    1. Generate UUID filename
    2. Create facility subdirectory
    3. Encrypt file stream to disk
    4. Create EventAttachment record
    """
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
