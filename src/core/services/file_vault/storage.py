"""Hot-Path-Storage fuer encrypted File-Vault-Attachments.

Enthaelt die produktiv aufgerufenen Funktionen rund um Upload, Replace,
Soft-Delete und Read-Pfade. Validation-Policy haengt an
:mod:`core.services.file_vault.policy`; physischer Cleanup (Orphans,
Event-Loeschung) liegt in :mod:`core.services.file_vault.cleanup`.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
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


@dataclass
class StagedUpload:
    """Ergebnis von :func:`prepare_encrypted_upload` — eine bereits verschluesselt
    auf Disk liegende, aber noch NICHT als ``EventAttachment`` persistierte Datei.

    Traegt alle Werte, die :func:`commit_staged_upload` fuer den reinen DB-Write
    braucht, sodass zwischen Scan/Encrypt (teuer, synchron) und dem DB-Insert
    keine erneute Datei-Beruehrung noetig ist (Refs #1345).
    """

    storage_name: str
    output_path: Path
    detected_mime: str
    file_size: int
    original_name: str
    content_type: str


def prepare_encrypted_upload(facility, uploaded_file, user, *, event=None):
    """Validieren, scannen und verschluesseln — OHNE DB-Write (Refs #1345).

    Fuehrt die volle Pre-Encrypt-Policy-Pipeline plus ClamAV-Scan und
    Fernet-Verschluesselung auf Disk aus und liefert einen :class:`StagedUpload`.
    Bewusst OHNE Aufrufer-Transaktion gedacht: der teure, synchrone Teil (ClamAV
    bis ``CLAMAV_TIMEOUT`` pro Datei + Verschluesselung) wird so VOR den
    ``transaction.atomic()``-Block des Aufrufers gezogen. Dadurch wird der
    per-Facility ``pg_advisory_xact_lock`` des AuditLog-Inserts (Hashkette) NICHT
    ueber den Scan gehalten — andere audit-schreibende Aktionen derselben
    Facility blockieren nicht mehr (Schreibserialisierung je Facility, #1345).

    Reihenfolge der Checks bleibt EXAKT wie bisher (#1268: Groesse VOR
    Voll-Pufferung im Virenscan — Memory-DoS-Schutz; #1274: verifizierter
    libmagic-MIME festgehalten). ``event`` ist optional und geht nur in die
    ``SECURITY_VIOLATION``-Audit-Attribution ein (``None`` bei Anlage eines noch
    nicht existierenden Events). Bei jedem Fehler wird eine bereits geschriebene
    ``.enc``-Datei entfernt und die Exception re-raised.

    1. Enforce ``Settings.allowed_file_types`` extension whitelist (Refs #610).
    2. Enforce ``min(Facility-Limit, globaler Cap)`` VOR jeder Voll-Pufferung
       (#1268/#1363).
    3. Scan file with ClamAV (if CLAMAV_ENABLED) BEFORE encryption — infizierte
       Uploads werden mit ``ValidationError`` abgelehnt und als
       ``SECURITY_VIOLATION`` protokolliert (fail-closed, Issue #524).
    4. Verify magic-bytes MIME matches the declared ``content_type`` (Refs #610).
    5. Decompression-/Zip-Bomb-Guards (#1268/#1310).
    6. UUID-Dateiname + Facility-Unterverzeichnis + Fernet-Encrypt auf Disk
       (Chunks an die Storage-ID gebunden, A4.5 / Refs #1016).
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
    try:
        encrypt_file(uploaded_file, output_path, file_id=storage_name)
    except Exception:
        # Scheitert die Verschluesselung, keine halbe ``.enc`` auf Disk lassen (#662).
        output_path.unlink(missing_ok=True)
        raise

    return StagedUpload(
        storage_name=storage_name,
        output_path=output_path,
        detected_mime=detected_mime or "",
        file_size=uploaded_file.size,
        original_name=uploaded_file.name,
        content_type=uploaded_file.content_type or "application/octet-stream",
    )


def commit_staged_upload(event, field_template, staged, user, *, supersedes=None, sort_order=None):
    """Persistiere eine per :func:`prepare_encrypted_upload` gestagte Datei als
    ``EventAttachment`` — reiner DB-Write (Refs #1345).

    Sicher innerhalb der Aufrufer-``transaction.atomic()`` aufzurufen: hier faellt
    kein ClamAV-Scan und keine Verschluesselung mehr an, nur der schnelle Insert.

    Modi:
    * ``supersedes=None`` → **add**: Neue Versionskette mit frischer ``entry_id``.
    * ``supersedes=<attachment>`` → **replace**: Übernimmt ``entry_id`` und
      ``sort_order`` des Vorgängers, markiert diesen als ersetzt
      (``is_current=False``, ``superseded_by=<new>``, ``superseded_at``).

    Bei einem DB-Fehler wird die gestagte ``.enc``-Datei entfernt (#662).
    Disk file stays until the event itself is deleted or anonymized
    (Refs #587/622 — Versionshistorie + Stufe-B Multi-Entry).
    """
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
            storage_filename=staged.storage_name,
            original_filename_encrypted=encrypt_field(staged.original_name),
            file_size=staged.file_size,
            mime_type=staged.content_type,
            detected_mime=staged.detected_mime or "",
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
        staged.output_path.unlink(missing_ok=True)
        raise

    return new_attachment


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

    Duenner Wrapper um :func:`prepare_encrypted_upload` + :func:`commit_staged_upload`
    (Refs #1345). Fuer Aufrufer, die Scan/Encrypt und DB-Write NICHT trennen
    muessen (Seed, Update/Replace-Pfad ``apply_attachment_changes``): der
    Vertrag (Signatur, Reihenfolge der Checks, Cleanup-Garantie) bleibt
    unveraendert. Der Create-Pfad (``EventCreateView``) ruft die beiden Phasen
    dagegen getrennt, um den Scan aus der Audit-Lock-Transaktion zu ziehen.
    """
    staged = prepare_encrypted_upload(facility, uploaded_file, user, event=event)
    return commit_staged_upload(event, field_template, staged, user, supersedes=supersedes, sort_order=sort_order)


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
