"""Pins for filesystem durability input validation.

Source: mutation delta of the release-0.99.7 checkpoint, 1 mutant.
Groups: directory-only synchronization (1).
"""

import pytest

from core.services.file_vault.file_locks import fsync_directory


def test_directory_sync_rejects_regular_files(tmp_path):
    """Synchronize directory entries and reject a regular file: x_fsync_directory__mutmut_6."""
    file = tmp_path / "regular.bin"
    file.write_bytes(b"ciphertext")
    fsync_directory(tmp_path)
    with pytest.raises(NotADirectoryError):
        fsync_directory(file)
    assert file.read_bytes() == b"ciphertext"
