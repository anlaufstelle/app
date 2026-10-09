"""Streaming attachment rotation with durable progress and crash recovery.

The manifest is advisory progress, never evidence that a file is readable.
Every invocation authenticates the actual file with the primary key first.
Only ciphertext is written to the temporary file, on the source filesystem.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import struct
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, cast

from cryptography.fernet import Fernet
from django.conf import settings
from django.db import transaction

from core.models import Attachment
from core.services.file_vault.encryption import (
    EncryptionError,
    _chunk_context,
    _file_binding,
    _get_keys,
    decrypt_file_stream,
    get_fernet,
)
from core.services.file_vault.file_locks import attachment_mutation_lock, fsync_directory, vault_lock

Checkpoint = Callable[[str, str], None]


class FileRotationLockedError(Exception):
    """Another attachment rotation is already running for this installation."""


class FileRotationManifestError(RuntimeError):
    """Progress could not be persisted; the run must stop before another file."""


@dataclass
class FileRotationResult:
    """Per-run counts, with errors identified by attachment UUID only."""

    checked: int = 0
    rotated: int = 0
    already_primary: int = 0
    deleted: int = 0
    would_rotate: int = 0
    errors: dict[str, str] = field(default_factory=dict)


def _open_regular(path: Path, flags: int, mode: str) -> BinaryIO:
    descriptor = os.open(path, flags | os.O_NOFOLLOW, 0o600)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise EncryptionError("Attachment or manifest is not a regular file")
    return cast(BinaryIO, os.fdopen(descriptor, mode))


class _Manifest:
    """Append bounded records; repair only a torn final write under the run lock."""

    def __init__(self, path: Path, key_id: str) -> None:
        self.path = path
        self.key_id = key_id
        with _open_regular(path, os.O_RDWR | os.O_CREAT, "r+b") as stream:
            while True:
                start = stream.tell()
                line = stream.readline(1024)
                if not line:
                    break
                if not line.endswith(b"\n"):
                    if len(line) == 1024:
                        raise EncryptionError("Manifest record exceeds its size limit")
                    stream.truncate(start)
                    stream.flush()
                    os.fsync(stream.fileno())
                    break
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeError) as exc:
                    raise EncryptionError("Invalid manifest record") from exc
                if not isinstance(record, dict) or set(record) != {"attachment_id", "key_id", "state"}:
                    raise EncryptionError("Invalid manifest record schema")
                if record["state"] != "completed":
                    raise EncryptionError("Invalid manifest state")

    def completed(self, attachment_id: str) -> None:
        record = {"attachment_id": attachment_id, "key_id": self.key_id, "state": "completed"}
        try:
            with _open_regular(self.path, os.O_WRONLY | os.O_APPEND, "ab") as stream:
                stream.write(json.dumps(record, separators=(",", ":")).encode("ascii") + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            fsync_directory(self.path.parent)
        except OSError as exc:
            raise FileRotationManifestError("Manifest progress could not be persisted") from exc


def _snapshot(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _verify(stream: BinaryIO, file_id: str, primary: Fernet) -> None:
    stream.seek(0)
    for _chunk in decrypt_file_stream(stream, file_id=file_id, fernet=primary):
        pass


def _write_rotated(source: BinaryIO, target: BinaryIO, file_id: str, primary: Fernet) -> None:
    """Authenticate through the ring and immediately encrypt each bound chunk."""
    source.seek(1)
    count_bytes = source.read(4)
    if len(count_bytes) != 4:
        raise EncryptionError("Truncated encrypted file")
    (total,) = struct.unpack(">I", count_bytes)
    source.seek(0)
    target.write(struct.pack(">BI", 2, total))
    binding = _file_binding(file_id)
    for index, chunk in enumerate(decrypt_file_stream(source, file_id=file_id)):
        token = primary.encrypt(_chunk_context(binding, index, total) + chunk)
        target.write(struct.pack(">I", len(token)))
        target.write(token)
    target.flush()
    os.fsync(target.fileno())


def _rotate_attachment(
    attachment: Attachment,
    primary: Fernet,
    manifest: _Manifest | None,
    *,
    dry_run: bool,
    checkpoint: Checkpoint,
) -> str:
    attachment_id = str(attachment.pk)
    file_id = attachment.storage_filename
    if not file_id or Path(file_id).name != file_id or file_id in {".", ".."}:
        raise EncryptionError("Invalid attachment storage identifier")
    path = Path(settings.MEDIA_ROOT) / str(attachment.facility_id) / file_id
    temporary = path.parent / f".rotate-{attachment_id}.tmp"
    # The run lock makes a deterministic encrypted staging file safe to reclaim
    # after process death. It is never used as evidence of successful rotation.
    if not dry_run:
        temporary.unlink(missing_ok=True)
    try:
        with _open_regular(path, os.O_RDONLY, "rb") as source:
            initial = _snapshot(os.fstat(source.fileno()))
            try:
                _verify(source, file_id, primary)
                outcome = "already_primary"
            except EncryptionError:
                if dry_run:
                    source.seek(0)
                    for _chunk in decrypt_file_stream(source, file_id=file_id):
                        pass
                    outcome = "would_rotate"
                else:
                    with _open_regular(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, "wb") as target:
                        _write_rotated(source, target, file_id, primary)
                    outcome = "rotated"
            checkpoint("before_replace", attachment_id)
            # Owner deletion acquires attachment rows before unlinking. Only
            # lock this row for the short commit window, never while streaming.
            with transaction.atomic():
                current = Attachment.objects.select_for_update().filter(pk=attachment.pk).first()
                if current is None:
                    return "deleted"
                if current.storage_filename != file_id or current.facility_id != attachment.facility_id:
                    raise EncryptionError("Attachment storage reference changed during rotation")
                with attachment_mutation_lock():
                    try:
                        current_info = path.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        return "deleted"
                    if _snapshot(current_info) != initial:
                        raise EncryptionError("Attachment file changed during rotation")
                    if outcome == "rotated":
                        os.replace(temporary, path)
                        fsync_directory(path.parent)
            if outcome == "rotated":
                checkpoint("after_replace", attachment_id)
            checkpoint("before_manifest", attachment_id)
            if manifest is not None:
                manifest.completed(attachment_id)
            return outcome
    finally:
        if not dry_run:
            with suppress(FileNotFoundError):
                temporary.unlink()


def rotate_files(
    *,
    facility_id: str | None = None,
    dry_run: bool = False,
    batch_size: int = 200,
    manifest_path: Path | None = None,
    checkpoint: Checkpoint | None = None,
) -> FileRotationResult:
    """Rotate all visible attachment versions, including soft-deleted ones.

    The caller must enforce the maintenance database role (command layer).
    A facility restriction does not prove installation-wide removability of
    old keys. Batch size controls database iteration, never plaintext buffering.
    Expected file/key failures are collected; unexpected interruptions propagate.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    # Resolve keys before creating runtime files. Empty/invalid rings fail loud.
    get_fernet()
    key = _get_keys()[0]
    primary = Fernet(key.encode())
    key_id = hashlib.sha256(key.encode()).hexdigest()
    result = FileRotationResult()
    run_lock = vault_lock(".reencrypt-files.lock", blocking=False)
    try:
        run_lock.__enter__()
    except BlockingIOError as exc:
        raise FileRotationLockedError("Attachment rotation is already running") from exc
    try:
        manifest = (
            None
            if dry_run
            else _Manifest(manifest_path or Path(settings.MEDIA_ROOT) / ".reencrypt-files.jsonl", key_id)
        )
        attachments = Attachment.objects.order_by("pk")
        if facility_id is not None:
            attachments = attachments.filter(facility_id=facility_id)
        for attachment in attachments.iterator(chunk_size=batch_size):
            result.checked += 1
            try:
                outcome = _rotate_attachment(
                    attachment,
                    primary,
                    manifest,
                    dry_run=dry_run,
                    checkpoint=checkpoint or (lambda stage, attachment_id: None),
                )
            except (EncryptionError, OSError) as exc:
                # Avoid names, file contents and filesystem paths in results.
                result.errors[str(attachment.pk)] = str(exc) if isinstance(exc, EncryptionError) else type(exc).__name__
                continue
            setattr(result, outcome, getattr(result, outcome) + 1)
    finally:
        run_lock.__exit__(None, None, None)
    return result
