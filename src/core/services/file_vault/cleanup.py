"""Disk- und DB-Cleanup-Pfade fuer den File-Vault.

- :func:`delete_attachments_for` ist der Hot-Path-Cleanup, wenn ein Owner
  (Ereignis, Person oder Fall) geloescht wird (Retention, Vier-Augen-
  Workflow, Anonymisierung): erst Dateien unlinken, dann DB-Records
  loeschen.
- :func:`cleanup_orphan_storage_files` ist der periodische Cron-Helper,
  der ``.enc``-Dateien ohne DB-Referenz findet (Race-Conditions zwischen
  ``encrypt_file`` und ``Attachment.create`` koennen Orphans
  hinterlassen — #662).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from django.conf import settings as django_settings

from core.models.attachment import Attachment
from core.models.import_job import ImportJob
from core.services.file_vault.storage import delete_attachment_file

logger = logging.getLogger(__name__)


def delete_attachments_for(owner):
    """Delete all attachments of an owner (files + DB records).

    ``owner`` ist ein ``Event``, ``Client`` oder ``Case``. Harter Rename von
    ``delete_event_attachments`` (AP2, Refs #1738 / ADR-039) — bewusst OHNE
    Kompatibilitaets-Alias: die drei Aufrufstellen sind bekannt, und ein
    Alt-Name mit Event-Semantik waere bei einem personen-/falleigenen Owner
    irrefuehrend.

    **Physischer** Loeschpfad (Invariante I-M1, ADR-039 §7): der Aufrufer MUSS
    im selben ``transaction.atomic`` die ``__files__``-Marker des Owners
    bereinigen bzw. dessen ``data_json`` leeren — sonst zeigen Marker auf
    nicht mehr existierende Zeilen.

    Bewusst OHNE einzelne ``ATTACHMENT_DELETE``-Zeilen (Refs #1960): Dieser
    physische Cleanup ist nur ein untergeordneter Schritt der bereits
    auditierten Owner-Loeschung oder -Anonymisierung. Ein Eintrag je Kette
    wuerde denselben fachlichen Vorgang N-mal als vermeintliche manuelle
    Anhang-Loeschung protokollieren.
    """
    for attachment in owner.attachments.all():
        delete_attachment_file(attachment)
    owner.attachments.all().delete()


def cleanup_orphan_storage_files(min_age_seconds: int = 3600):
    """Loesche ``.enc``-Dateien ohne ``Attachment``- UND ohne ``ImportJob``-Record.

    Auch nach dem Direct-Cleanup in :func:`store_encrypted_file` bleibt
    ein Restrisiko: schlaegt eine spaetere Operation in der umgebenden
    ``transaction.atomic``-Transaktion fehl (z. B. ``EventHistory``-Save),
    rollt der DB-Record zurueck — die bereits geschriebene ``.enc``-Datei
    bleibt jedoch ohne Referenz liegen (#662).

    Dieser Helper findet solche Orphans, indem er alle ``.enc``-Dateien
    im Media-Root mit den aktuell registrierten ``storage_filename``-
    Werten der DB abgleicht. Der Abgleich ist **owner-generisch**: gezaehlt
    werden ALLE ``Attachment``-Zeilen, gleich ob sie an einem Ereignis, einer
    Person oder einem Fall haengen (ADR-039) — und auch soft-deletete Zeilen
    (``deleted_at``), deren Datei bis zur Owner-Loeschung erhalten bleibt.
    ``min_age_seconds`` schuetzt vor Race Conditions: eine Datei, die gerade
    frisch geschrieben wird, hat eventuell noch keinen DB-Eintrag (Default 1h
    ist konservativ).

    **Zweite Referenzquelle: ``ImportJob.storage_filename`` (Refs #1060).** Der
    CSV-Import legt seinen verschluesselten Upload unter
    ``MEDIA_ROOT/<facility>/imports/`` ab — bewusst OHNE ``Attachment``-Zeile,
    denn er haengt an keinem Owner. Der Wizard laeuft ueber mehrere Requests und
    darf bis zu ``import_engine.STALE_DAYS`` offen bleiben; ohne diesen Abgleich
    raeumte der naechtliche Cron-Lauf die Quelldatei jedes offenen Laufs ab.
    Bewusst KEINE Verzeichnis-Ausnahme: ``commit_import``/``abort_import``
    leeren ``storage_filename``, echte Import-Waisen bleiben damit abraeumbar.

    Vorgesehen fuer einen periodischen Management-Command/Cron, nicht
    fuer den Hot-Path. Returns: Anzahl der geloeschten Dateien.
    """
    media_root = Path(django_settings.MEDIA_ROOT)
    if not media_root.exists():
        return 0
    cutoff = time.time() - min_age_seconds
    known = set(Attachment.objects.values_list("storage_filename", flat=True))
    # Nur OFFENE Importlaeufe schuetzen: nach commit/abort leeren die
    # Uebergaenge storage_filename selbst — traegt eine FINALE Zeile das Feld
    # trotzdem noch (Prozess-Crash zwischen Datei-Loeschung und save()), soll
    # der Sweep die referenzlose PII-Datei holen, nicht ewig verschonen.
    known |= {
        name
        for name in ImportJob.objects.exclude(storage_filename="")
        .exclude(status__in=ImportJob.FINAL_STATUSES)
        .values_list("storage_filename", flat=True)
        if name
    }
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
