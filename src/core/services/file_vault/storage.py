"""Hot-Path-Storage fuer encrypted File-Vault-Attachments.

Enthaelt die produktiv aufgerufenen Funktionen rund um Upload, Replace,
Soft-Delete und Read-Pfade. Validation-Policy haengt an
:mod:`core.services.file_vault.policy`; physischer Cleanup (Orphans,
Event-Loeschung) liegt in :mod:`core.services.file_vault.cleanup`.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from django.conf import settings as django_settings
from django.utils import timezone

from core.models.attachment import EventAttachment
from core.services.file_vault.encryption import decrypt_file_stream, encrypt_field, encrypt_file, safe_decrypt
from core.services.file_vault.policy import (
    enforce_allowed_file_types,
    enforce_archive_limits,
    enforce_image_limits,
    enforce_magic_bytes,
    enforce_upload_size,
    run_virus_scan,
)

logger = logging.getLogger(__name__)


def _facility_dir(facility):
    """Return the storage directory for a facility."""
    return Path(django_settings.MEDIA_ROOT) / str(facility.pk)


def store_encrypted_file(
    facility,
    uploaded_file,
    field_template,
    event,
    user,
    supersedes=None,
    sort_order=None,
):
    """Encrypt and store an uploaded file. Returns EventAttachment instance.

    Modi:
    * ``supersedes=None`` → **add**: Neue Versionskette mit frischer ``entry_id``.
    * ``supersedes=<attachment>`` → **replace**: Übernimmt ``entry_id`` und
      ``sort_order`` des Vorgängers, markiert diesen als ersetzt
      (``is_current=False``, ``superseded_by=<new>``, ``superseded_at``).

    1. Enforce ``Settings.allowed_file_types`` extension whitelist (Refs #610).
    2. Scan file with ClamAV (if CLAMAV_ENABLED) BEFORE encryption — infected
       uploads are rejected with ``ValidationError`` and logged as
       ``SECURITY_VIOLATION``. Scanner errors are treated as fail-closed when
       scanning is enabled (Issue #524).
    3. Verify magic-bytes MIME matches the declared ``content_type`` — rejects
       payload-smuggling attempts (Refs #610).
    4. Generate UUID filename
    5. Create facility subdirectory
    6. Encrypt file stream to disk
    7. Create EventAttachment record

    Disk file stays until the event itself is deleted or anonymized
    (Refs #587/622 — Versionshistorie + Stufe-B Multi-Entry).
    """
    enforce_allowed_file_types(facility, uploaded_file, event, user)
    # #1268: harte Service-Groessenobergrenze VOR der Voll-Pufferung im Virenscan
    # (Memory-DoS-Schutz). #1363: erzwingt zugleich das per-Facility-Limit als SSOT
    # (min(Facility-Limit, globaler Cap)) fuer ALLE Aufrufer, auch den Replace-Pfad,
    # der am DynamicEventDataForm vorbeilaeuft.
    enforce_upload_size(facility, uploaded_file, event, user)
    run_virus_scan(facility, uploaded_file, event, user)
    # #1274: den verifizierten (libmagic) MIME festhalten, damit er beim Download
    # massgeblich ist statt des browser-gemeldeten content_type.
    detected_mime = enforce_magic_bytes(facility, uploaded_file, event, user)
    # #1268: Decompression-Bomb-Schutz fuer Bild-Uploads (Pixel-Obergrenze).
    enforce_image_limits(facility, uploaded_file, event, user)
    # #1310 (S4): Archiv-Expansions-Guard (Zip-Bomb) fuer ZIP/OOXML-Container.
    enforce_archive_limits(facility, uploaded_file, event, user)

    storage_name = f"{uuid.uuid4()}.enc"
    output_path = _facility_dir(facility) / storage_name

    # A4.5 (Refs #1016): Chunks an die Storage-ID binden (v2-Format) — schützt
    # Bestandsdateien gegen Reorder/Truncation/Cross-File-Splicing auf der Disk.
    encrypt_file(uploaded_file, output_path, file_id=storage_name)

    try:
        if supersedes is not None:
            # Replace-Modus: entry_id + sort_order vom Vorgänger übernehmen.
            entry_id = supersedes.entry_id
            effective_sort = supersedes.sort_order if sort_order is None else sort_order
        else:
            # Add-Modus: frische entry_id, sort_order wie übergeben (oder 0).
            entry_id = uuid.uuid4()
            effective_sort = sort_order if sort_order is not None else 0

        new_attachment = EventAttachment.objects.create(
            event=event,
            field_template=field_template,
            storage_filename=storage_name,
            original_filename_encrypted=encrypt_field(uploaded_file.name),
            file_size=uploaded_file.size,
            mime_type=uploaded_file.content_type or "application/octet-stream",
            detected_mime=detected_mime or "",
            created_by=user,
            is_current=True,
            entry_id=entry_id,
            sort_order=effective_sort,
        )

        if supersedes is not None:
            supersedes.is_current = False
            supersedes.superseded_by = new_attachment
            supersedes.superseded_at = timezone.now()
            supersedes.save(update_fields=["is_current", "superseded_by", "superseded_at"])
    except Exception:
        # Synchroner Fehler in DB-Operationen — geschriebene Datei sofort
        # entfernen, damit der Disk nicht waechst (#662).
        output_path.unlink(missing_ok=True)
        raise

    return new_attachment


def soft_delete_attachment_chain(event, entry_id, user):
    """Markiere alle Attachments einer Versionskette (entry_id) als soft-deleted.

    Für UI-Lösch-Aktionen (Stufe B, Refs #622). Physischer Disk-Cleanup
    erfolgt weiterhin erst im Event-Delete/Anonymize. Bereits soft-deleted
    eingetragene Entries werden nicht erneut angefasst.

    Returns: Anzahl der neu soft-deleted Attachments in der Kette.
    """
    qs = event.attachments.filter(entry_id=entry_id, deleted_at__isnull=True)
    now = timezone.now()
    updated = qs.update(deleted_at=now)
    return updated


def get_current_entries_for_field(event, field_template):
    """Liefert die aktuellen, nicht soft-deleted Einträge für ein FILE-Feld.

    Gibt Heads der Versionsketten zurück (is_current=True, deleted_at IS NULL),
    sortiert nach ``sort_order`` und ``created_at``. Der Aufrufer kann daraus
    ``entry_id``, ``pk``, ``original_filename`` etc. ableiten.
    """
    return list(
        event.attachments.filter(
            field_template=field_template,
            is_current=True,
            deleted_at__isnull=True,
        ).order_by("sort_order", "created_at")
    )


def get_attachment_path(attachment):
    """Return the full filesystem path of an encrypted attachment."""
    return _facility_dir(attachment.event.facility) / attachment.storage_filename


def get_decrypted_file_stream(attachment):
    """Return a generator of decrypted chunks for streaming download."""
    # A4.5: v2-Bindung gegen die Storage-ID verifizieren (Legacy-v1 ignoriert sie).
    return decrypt_file_stream(get_attachment_path(attachment), file_id=attachment.storage_filename)


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
