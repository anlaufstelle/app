"""Stable filesystem locks shared by attachment replacement and deletion."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings


def fsync_directory(directory: Path) -> None:
    """Persist directory entries after replacement or deletion."""
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def vault_lock(name: str, *, blocking: bool = True) -> Iterator[None]:
    """Lock a persistent inode in the media root, never unlinking it.

    Database row locks must precede this lock. It must not cover database
    queries, so a deleting transaction cannot deadlock with a rotating one.
    """
    root = Path(settings.MEDIA_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(root / name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield
    finally:
        # Closing also releases the lock on exceptions and process death.
        os.close(descriptor)


@contextmanager
def attachment_mutation_lock() -> Iterator[None]:
    """Serialize the short filesystem commit window with physical deletion."""
    with vault_lock(".file-vault-mutation.lock"):
        yield
