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

import json
import logging
import os
import tempfile
import time
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path

from django.conf import settings as django_settings

from core.models.attachment import Attachment
from core.models.import_job import ImportJob
from core.services.file_vault.file_locks import attachment_mutation_lock, fsync_directory
from core.services.file_vault.storage import delete_attachment_file

logger = logging.getLogger(__name__)

DEFAULT_ORPHAN_CLEANUP_BUDGET = 10_000
_CLEANUP_STATE_FILENAME = ".cleanup-orphan-storage-files-state.json"


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
    from core.services.attachments.crud import (  # noqa: PLC0415 — Zyklus vermeiden
        lock_attachment_rows_in_facility_order,
    )

    # Refs #2260: einrichtungsweite Sperr-Reihenfolge. Die Loesch-/Anonymisie-
    # rungskaskaden halten ihre Owner-Zeilen schon und auditieren erst danach —
    # die Richtung stimmt also. Offen war die Ordnung INNERHALB der Anhangs-
    # zeilen: ``DELETE`` sperrt sie in Planreihenfolge. Die vorgezogene,
    # nach ``pk`` geordnete Sperre nimmt alle Zeilen des Owners in fester
    # Ordnung, bevor die erste Datei faellt.
    #
    # ``deletes_rows=True``: Die Loeschmenge ist owner-eigen, die
    # per ``SET_NULL`` MITGESCHRIEBENEN Zeilen sind es nicht: nach einem
    # Zusammenfuehren bleibt die ersetzte Vorgeschichte beim abgebenden
    # Datensatz, waehrend der Kopf hier faellt. Jene Zeile kaeme sonst erst
    # durch die Kaskade des ``delete()`` an die Reihe — ausserhalb der
    # geordneten Vorsperre und damit in absteigender Folge, sobald sie die
    # kleinere PK hat. ``lock_owner_rows`` bleibt aus: die Owner-Zeilen haelt
    # die umgebende Loesch-/Anonymisierungskaskade bereits.
    lock_attachment_rows_in_facility_order(owner, None, deletes_rows=True)
    for attachment in owner.attachments.all():
        delete_attachment_file(attachment)
    owner.attachments.all().delete()


class OrphanCleanupResult(int):
    """Bilanz eines Aufraeumlaufs mit kompatiblem ganzzahligem Loeschwert.

    Als ``int`` behaelt das Ergebnis fuer bestehende Aufrufer Vergleiche,
    Wahrheitswert und Arithmetik der bisherigen Loeschzahl bei. Die benannten
    Attribute liefern dem Management-Command die vollstaendige Laufbilanz.
    """

    checked_paths: tuple[str, ...]
    deleted: int
    deletion_candidates: int
    skipped_referenced: int
    skipped_young: int
    errors: int
    continued_at: str | None
    next_start: str | None
    tree_complete: bool
    state_warning: str | None
    dry_run: bool

    def __new__(
        cls,
        *,
        checked_paths: tuple[str, ...],
        deleted: int,
        deletion_candidates: int,
        skipped_referenced: int,
        skipped_young: int,
        errors: int,
        continued_at: str | None,
        next_start: str | None,
        tree_complete: bool,
        state_warning: str | None,
        dry_run: bool,
    ):
        result = super().__new__(cls, deleted)
        result.checked_paths = checked_paths
        result.deleted = deleted
        result.deletion_candidates = deletion_candidates
        result.skipped_referenced = skipped_referenced
        result.skipped_young = skipped_young
        result.errors = errors
        result.continued_at = continued_at
        result.next_start = next_start
        result.tree_complete = tree_complete
        result.state_warning = state_warning
        result.dry_run = dry_run
        return result

    def __init__(self, **_values: object) -> None:
        pass

    @property
    def checked(self) -> int:
        """Anzahl der in diesem Lauf fachlich geprueften ``.enc``-Dateien."""
        return len(self.checked_paths)


class CleanupCursorWriteError(Exception):
    """Carry a completed cleanup result when its cursor cannot be persisted."""

    def __init__(self, state_path: Path, result: OrphanCleanupResult, cause: OSError) -> None:
        super().__init__(str(cause))
        self.state_path = state_path
        self.result = result
        self.cause = cause


def _iter_encrypted_files(directory: Path, relative_directory: Path = Path()) -> Iterator[tuple[str, Path]]:
    """Liefere ``.enc``-Dateien in stabiler, komponentenweise sortierter Reihenfolge.

    ``Path.rglob`` garantiert keine Dateisystem-unabhaengige Reihenfolge. Die
    rekursive Sortierung ist Voraussetzung dafuer, dass ein persistierter
    relativer Pfad auch dann ein fairer Cursor bleibt, wenn seine Datei vor dem
    naechsten Lauf verschwunden ist. Verzeichnis-Symlinks werden nicht verfolgt.
    """
    try:
        with os.scandir(directory) as directory_entries:
            entries = sorted(directory_entries, key=lambda entry: entry.name)
    except OSError as exc:
        logger.warning("cleanup_orphan_storage_files: %s -> %s", directory, exc)
        return

    for entry in entries:
        relative_path = relative_directory / entry.name
        path = Path(entry.path)
        try:
            if entry.is_dir(follow_symlinks=False):
                yield from _iter_encrypted_files(path, relative_path)
            elif entry.name.endswith(".enc"):
                yield relative_path.as_posix(), path
        except OSError as exc:
            logger.warning("cleanup_orphan_storage_files: %s -> %s", path, exc)


def _cursor_key(relative_path: str) -> tuple[str, ...]:
    return Path(relative_path).parts


def _read_cleanup_cursor(state_path: Path) -> tuple[str | None, str | None]:
    """Lese den Cursor; unbrauchbarer Zustand startet sicher mit einem Hinweis neu."""
    try:
        raw_state = state_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeError) as exc:
        return None, f"Fortsetzungspunkt konnte nicht gelesen werden ({exc}); Start am Anfang."

    try:
        payload = json.loads(raw_state)
    except (json.JSONDecodeError, TypeError):
        return None, "Fortsetzungspunkt ist beschaedigt; Start am Anfang."

    if not isinstance(payload, dict) or set(payload) != {"after"}:
        return None, "Fortsetzungspunkt hat ein ungueltiges Format; Start am Anfang."
    cursor = payload["after"]
    if cursor is None:
        return None, None
    if not isinstance(cursor, str) or not cursor:
        return None, "Fortsetzungspunkt hat einen ungueltigen Pfad; Start am Anfang."
    cursor_path = Path(cursor)
    # Only ``..`` components and absolute paths can point outside the media
    # root; ``pathlib`` already drops ``.`` components from ``parts``.
    if cursor_path.is_absolute() or ".." in cursor_path.parts:
        return None, "Fortsetzungspunkt hat einen ungueltigen Pfad; Start am Anfang."
    return cursor, None


def _write_cleanup_cursor(state_path: Path, after: str | None) -> None:
    """Schreibe den Cursor atomar, damit ein Abbruch keinen Teilstand hinterlaesst."""
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=state_path.parent,
            prefix=f".{state_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump({"after": after}, temporary_file, ensure_ascii=True)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, state_path)
    except OSError:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)
        raise


def cleanup_orphan_storage_files(
    min_age_seconds: int = 3600,
    *,
    budget: int = DEFAULT_ORPHAN_CLEANUP_BUDGET,
    dry_run: bool = False,
) -> OrphanCleanupResult:
    """Pruefe ``.enc``-Dateien ohne ``Attachment``- oder offenen ``ImportJob``-Record.

    Auch nach dem Direct-Cleanup in :func:`store_encrypted_file` bleibt ein
    Restrisiko: Rollt eine spaetere DB-Operation zurueck, bleibt die bereits
    geschriebene Datei ohne Referenz liegen. Der Abgleich ist owner-generisch
    und umfasst auch soft-geloeschte Attachments. Offene Importlaeufe schuetzen
    ihre verschluesselte Quelldatei; finale Laeufe nicht. Die Referenzmenge wird
    einmal vollstaendig geladen, waehrend ``budget`` die pro Lauf geprueften
    Dateien im Medienbaum begrenzt. ``min_age_seconds`` schuetzt frische Dateien
    vor Race Conditions zwischen Dateischreiben und DB-Eintrag.

    Der letzte gepruefte relative Pfad wird zwischen periodischen Laeufen
    atomar unter ``MEDIA_ROOT`` gespeichert. Die deterministische Sortierung
    garantiert Fortschritt auch dann, wenn die Cursor-Datei inzwischen
    geloescht wurde. Am Baumende wird der Cursor zurueckgesetzt; so erreicht
    ein spaeterer Umlauf auch Dateien, die vor dem bisherigen Cursor neu
    angelegt wurden. Im Trockenlauf bleibt der persistierte Zustand unveraendert.
    """
    if budget < 1:
        raise ValueError("budget muss mindestens 1 sein")

    media_root = Path(django_settings.MEDIA_ROOT)
    if not media_root.exists():
        return OrphanCleanupResult(
            checked_paths=(),
            deleted=0,
            deletion_candidates=0,
            skipped_referenced=0,
            skipped_young=0,
            errors=0,
            continued_at=None,
            next_start=None,
            tree_complete=True,
            state_warning=None,
            dry_run=dry_run,
        )

    state_path = media_root / _CLEANUP_STATE_FILENAME
    cursor, state_warning = _read_cleanup_cursor(state_path)
    cursor_key = _cursor_key(cursor) if cursor is not None else None
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
    checked_paths: list[str] = []
    deleted = 0
    deletion_candidates = 0
    skipped_referenced = 0
    skipped_young = 0
    errors = 0
    next_start: str | None = None

    for relative_path, enc_file in _iter_encrypted_files(media_root):
        if cursor_key is not None and _cursor_key(relative_path) <= cursor_key:
            continue
        if len(checked_paths) >= budget:
            next_start = relative_path
            break

        checked_paths.append(relative_path)
        try:
            if enc_file.name in known:
                skipped_referenced += 1
                continue
            if enc_file.stat().st_mtime >= cutoff:
                skipped_young += 1
                continue
            deletion_candidates += 1
            if dry_run:
                continue
            with attachment_mutation_lock():
                enc_file.unlink()
                fsync_directory(enc_file.parent)
            deleted += 1
            logger.info("cleanup_orphan_storage_files removed orphan: %s", enc_file)
        except OSError as exc:
            errors += 1
            logger.warning("cleanup_orphan_storage_files: %s -> %s", enc_file, exc)

    tree_complete = next_start is None
    result = OrphanCleanupResult(
        checked_paths=tuple(checked_paths),
        deleted=deleted,
        deletion_candidates=deletion_candidates,
        skipped_referenced=skipped_referenced,
        skipped_young=skipped_young,
        errors=errors,
        continued_at=checked_paths[0] if checked_paths else None,
        next_start=next_start,
        tree_complete=tree_complete,
        state_warning=state_warning,
        dry_run=dry_run,
    )
    if not dry_run:
        after = None if tree_complete else checked_paths[-1]
        try:
            _write_cleanup_cursor(state_path, after)
        except OSError as exc:
            raise CleanupCursorWriteError(state_path, result, exc) from exc

    return result
