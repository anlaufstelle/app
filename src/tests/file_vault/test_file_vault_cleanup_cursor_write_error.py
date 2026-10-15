import json
import os
import time
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core.management.commands import cleanup_orphan_storage_files as command_module
from core.services.file_vault import cleanup as cleanup_module

STATE_FILENAME = ".cleanup-orphan-storage-files-state.json"


def test_cursor_write_failure_reports_completed_counts_and_preserves_state(
    monkeypatch: pytest.MonkeyPatch, settings, tmp_path: Path
) -> None:
    settings.MEDIA_ROOT = str(tmp_path)
    first_file = tmp_path / "a.enc"
    first_file.write_bytes(b"ciphertext")
    old_mtime = time.time() - 7200
    os.utime(first_file, (old_mtime, old_mtime))
    second_file = tmp_path / "b.enc"
    second_file.write_bytes(b"ciphertext")
    os.utime(second_file, (old_mtime, old_mtime))

    state_path = tmp_path / STATE_FILENAME
    state_before = json.dumps({"after": None}).encode() + b"\n"
    state_path.write_bytes(state_before)

    monkeypatch.setattr(command_module, "_has_rls_bypass_context", lambda: True)
    monkeypatch.setattr(cleanup_module.Attachment.objects, "values_list", lambda *args, **kwargs: ())
    import_jobs = MagicMock()
    import_jobs.exclude.return_value.exclude.return_value.values_list.return_value = ()
    monkeypatch.setattr(cleanup_module.ImportJob, "objects", import_jobs)

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(cleanup_module.os, "replace", fail_replace)
    output = StringIO()

    with pytest.raises(CommandError) as exc_info:
        call_command(
            "cleanup_orphan_storage_files",
            "--budget=1",
            "--min-age-seconds=0",
            stdout=output,
        )

    assert str(state_path) in str(exc_info.value)
    assert "disk full" in str(exc_info.value)
    assert "Geprüft: 1" in output.getvalue()
    assert "gelöscht: 1" in output.getvalue()
    assert state_path.read_bytes() == state_before
    assert list(tmp_path.glob(f".{STATE_FILENAME}.*.tmp")) == []
