"""Service for encrypted file storage."""

import logging
import uuid
from pathlib import Path

from django.conf import settings as django_settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models.attachment import EventAttachment
from core.models.audit import AuditLog
from core.models.settings import Settings
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
        _log_security_violation(
            facility,
            user,
            event,
            reason="virus_scanner_unavailable",
            filename=uploaded_file.name,
            extra={"error": str(exc)},
        )
        raise ValidationError(_("Datei-Upload abgelehnt: Virenscanner ist nicht erreichbar.")) from exc

    if result.infected:
        _log_security_violation(
            facility,
            user,
            event,
            reason="virus_detected",
            filename=uploaded_file.name,
            extra={"signature": result.signature or ""},
        )
        raise ValidationError(
            _("Datei wurde von Virenscanner abgewiesen: %(signature)s") % {"signature": result.signature or "unknown"}
        )


def _log_security_violation(facility, user, event, *, reason, filename, extra=None):
    """Create an ``AuditLog`` entry for a file-upload security violation.

    Central helper reused by virus-scan, MIME-mismatch and whitelist-breach
    paths so the AuditLog payload stays consistent (Refs #610).
    """
    detail = {"reason": reason, "filename": filename}
    if extra:
        detail.update(extra)
    AuditLog.objects.create(
        facility=facility,
        user=user,
        action=AuditLog.Action.SECURITY_VIOLATION,
        target_type="EventAttachment",
        target_id=str(event.pk) if getattr(event, "pk", None) else "",
        detail=detail,
    )


def _enforce_allowed_file_types(facility, uploaded_file, event, user):
    """Reject uploads whose extension is not in ``Settings.allowed_file_types``.

    The form layer already performs this check for UX, but the service layer
    is the final authority — direct/programmatic callers bypass the form, so
    we re-check here and log every violation as ``SECURITY_VIOLATION``
    (Refs #610).
    """
    try:
        facility_settings = Settings.objects.get(facility=facility)
    except Settings.DoesNotExist:
        return  # No settings yet → no whitelist to enforce.

    allowed = {
        ext.strip().lower().lstrip(".")
        for ext in (facility_settings.allowed_file_types or "").split(",")
        if ext.strip()
    }
    if not allowed:
        return

    name = uploaded_file.name or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in allowed:
        return

    _log_security_violation(
        facility,
        user,
        event,
        reason="extension_not_allowed",
        filename=name,
        extra={"extension": ext, "allowed": sorted(allowed)},
    )
    raise ValidationError(
        _("Dateityp .%(ext)s ist nicht erlaubt. Erlaubt: %(allowed)s")
        % {"ext": ext or "?", "allowed": ", ".join(sorted(allowed))}
    )


def _enforce_magic_bytes(facility, uploaded_file, event, user):
    """Verify the file's true MIME type (sniffed via libmagic) matches the
    browser-declared ``content_type``.

    Rejects files whose byte-content disagrees with the declared MIME — a
    common payload-smuggling pattern (e.g. a PE executable sent as
    ``application/pdf``). Every mismatch is logged as ``SECURITY_VIOLATION``
    (Refs #610).
    """
    # Lazy-Import: ``python-magic`` braucht die System-Bibliothek ``libmagic1``.
    # Im Docker-Image (Prod) ist sie installiert; lokale Unit-Tests ohne
    # libmagic sollen trotzdem collectable bleiben und gezielt skippen.
    import magic  # noqa: PLC0415 — intentional lazy import, see docstring

    uploaded_file.seek(0)
    buffer = uploaded_file.read(2048)
    uploaded_file.seek(0)

    detected_mime = magic.from_buffer(buffer, mime=True) or "application/octet-stream"
    declared_mime = uploaded_file.content_type or "application/octet-stream"

    # libmagic returns "application/octet-stream" when it cannot confidently
    # identify the content (common for small archives, ZIP-like containers, and
    # formats whose magic.mgc definitions are terse). Treat it as "unknown",
    # not as a mismatch — the extension whitelist and ClamAV remain in force.
    # Only reject when libmagic POSITIVELY identifies a type that contradicts
    # the declared one (e.g. PE executable declared as PDF).
    if detected_mime in (declared_mime, "application/octet-stream"):
        return

    _log_security_violation(
        facility,
        user,
        event,
        reason="mime_mismatch",
        filename=uploaded_file.name,
        extra={"declared": declared_mime, "detected": detected_mime},
    )
    raise ValidationError(
        _("Datei-Inhalt stimmt nicht mit dem angegebenen Typ überein (erkannt: %(detected)s, erwartet: %(declared)s).")
        % {"detected": detected_mime, "declared": declared_mime}
    )


def store_encrypted_file(facility, uploaded_file, field_template, event, user, supersedes=None):
    """Encrypt and store an uploaded file. Returns EventAttachment instance.

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

    If ``supersedes`` is given, the referenced prior attachment is not deleted:
    it gets ``is_current=False``, ``superseded_by`` pointing at the new record,
    and ``superseded_at`` set. Disk file stays until the event itself is deleted
    or anonymized (Refs #587, Stufe A — Versionshistorie).
    """
    _enforce_allowed_file_types(facility, uploaded_file, event, user)
    _run_virus_scan(facility, uploaded_file, event, user)
    _enforce_magic_bytes(facility, uploaded_file, event, user)

    storage_name = f"{uuid.uuid4()}.enc"
    output_path = _facility_dir(facility) / storage_name

    encrypt_file(uploaded_file, output_path)

    new_attachment = EventAttachment.objects.create(
        event=event,
        field_template=field_template,
        storage_filename=storage_name,
        original_filename_encrypted=encrypt_field(uploaded_file.name),
        file_size=uploaded_file.size,
        mime_type=uploaded_file.content_type or "application/octet-stream",
        created_by=user,
        is_current=True,
    )

    if supersedes is not None:
        supersedes.is_current = False
        supersedes.superseded_by = new_attachment
        supersedes.superseded_at = timezone.now()
        supersedes.save(update_fields=["is_current", "superseded_by", "superseded_at"])

    return new_attachment


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
