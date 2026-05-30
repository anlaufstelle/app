"""Disk- und DB-Cleanup-Pfade fuer den File-Vault.

- :func:`delete_event_attachments` ist der Hot-Path-Cleanup, wenn ein
  Event gelöscht wird (Retention, Vier-Augen-Workflow): erst Dateien
  unlinken, dann DB-Records loeschen.
- :func:`cleanup_orphan_storage_files` ist der periodische Cron-Helper,
  der ``.enc``-Dateien ohne DB-Referenz findet (Race-Conditions zwischen
  ``encrypt_file`` und ``EventAttachment.create`` koennen Orphans
  hinterlassen — #662).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from django.conf import settings as django_settings

from core.models.attachment import EventAttachment
from core.services.file_vault.storage import delete_attachment_file

logger = logging.getLogger(__name__)


def delete_event_attachments(event):
    """Delete all attachments for an event (files + DB records)."""
    for attachment in event.attachments.all():
        delete_attachment_file(attachment)
    event.attachments.all().delete()


def cleanup_orphan_storage_files(min_age_seconds: int = 3600):
    """Loesche ``.enc``-Dateien ohne ``EventAttachment``-Record.

    Auch nach dem Direct-Cleanup in :func:`store_encrypted_file` bleibt
    ein Restrisiko: schlaegt eine spaetere Operation in der umgebenden
    ``transaction.atomic``-Transaktion fehl (z. B. ``EventHistory``-Save),
    rollt der DB-Record zurueck — die bereits geschriebene ``.enc``-Datei
    bleibt jedoch ohne Referenz liegen (#662).

    Dieser Helper findet solche Orphans, indem er alle ``.enc``-Dateien
    im Media-Root mit den aktuell registrierten ``storage_filename``-
    Werten der DB abgleicht. ``min_age_seconds`` schuetzt vor Race
    Conditions: eine Datei, die gerade frisch geschrieben wird, hat
    eventuell noch keinen DB-Eintrag (Default 1h ist konservativ).

    Vorgesehen fuer einen periodischen Management-Command/Cron, nicht
    fuer den Hot-Path. Returns: Anzahl der geloeschten Dateien.
    """
    media_root = Path(django_settings.MEDIA_ROOT)
    if not media_root.exists():
        return 0
    cutoff = time.time() - min_age_seconds
    known = set(EventAttachment.objects.values_list("storage_filename", flat=True))
    deleted = 0
    for enc_file in media_root.rglob("*.enc"):
        try:
            if enc_file.name in known:
                continue
            if enc_file.stat().st_mtime >= cutoff:
                continue
            enc_file.unlink()
            deleted += 1
            logger.info("cleanup_orphan_storage_files removed orphan: %s", enc_file)
        except OSError as exc:
            logger.warning("cleanup_orphan_storage_files: %s -> %s", enc_file, exc)
    return deleted
