"""Pins for locale-independent cursors, empty trees and best-effort owner cleanup.

Source: mutation delta of the release-0.99.8 checkpoint, 3 mutants.
Groups: UTF-8 cursor decoding (1), empty existing tree completion (1), default unlink failure handling (1).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.db import connection

from core.models import Attachment, FieldTemplate
from core.services.file_vault import encrypt_field
from core.services.file_vault.cleanup import cleanup_orphan_storage_files, delete_attachments_for
from core.services.file_vault.storage import get_attachment_path

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

STATE_NAME = ".cleanup-orphan-storage-files-state.json"


def test_unicode_cursor_continues_under_an_ascii_locale(settings, tmp_path):
    """Public cleanup decodes raw UTF-8 under a real ASCII locale in a fresh interpreter."""
    settings.MEDIA_ROOT = tmp_path
    cursor = "caf\u00e9.enc"
    (tmp_path / STATE_NAME).write_text(json.dumps({"after": cursor}, ensure_ascii=False), encoding="utf-8")
    for name in ("aaa.enc", "zzz.enc"):
        (tmp_path / name).write_bytes(b"ciphertext")
    script = """
import json
import locale
import sys
import django
from django.conf import settings
settings.DATABASES["default"]["NAME"] = sys.argv[2]
settings.MEDIA_ROOT = sys.argv[1]
django.setup()
from core.services.file_vault.cleanup import cleanup_orphan_storage_files
assert locale.getencoding().lower() not in {"utf-8", "utf8"}
result = cleanup_orphan_storage_files(budget=1, dry_run=True)
print(json.dumps({"checked": result.checked_paths, "warning": result.state_warning}))
"""
    result = subprocess.run(
        [sys.executable, "-X", "utf8=0", "-c", script, str(tmp_path), connection.settings_dict["NAME"]],
        env={
            **os.environ,
            "LC_ALL": "C",
            "PYTHONCOERCECLOCALE": "0",
            "PYTHONUTF8": "0",
            "PYTHONPATH": os.pathsep.join(sys.path),
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    assert json.loads(result.stdout) == {"checked": ["zzz.enc"], "warning": None}
    assert json.loads((tmp_path / STATE_NAME).read_text(encoding="utf-8")) == {"after": cursor}


def test_unicode_cursor_ignores_the_default_text_encoding(settings, tmp_path, monkeypatch):
    """Cursor decoding never falls back to the default encoding: x__read_cleanup_cursor__mutmut_2.

    Runs in-process so mutation tooling associates it with the cursor reader; a default
    (encoding=None) read is decoded as ASCII, as under a C locale.
    """
    settings.MEDIA_ROOT = tmp_path
    cursor = "caf\u00e9.enc"
    (tmp_path / STATE_NAME).write_text(json.dumps({"after": cursor}, ensure_ascii=False), encoding="utf-8")
    for name in ("aaa.enc", "zzz.enc"):
        (tmp_path / name).write_bytes(b"ciphertext")
    original_read_text = Path.read_text

    def ascii_default_read_text(self, encoding=None, errors=None, **kwargs):
        return original_read_text(self, encoding=encoding or "ascii", errors=errors, **kwargs)

    monkeypatch.setattr(Path, "read_text", ascii_default_read_text)

    result = cleanup_orphan_storage_files(budget=1, dry_run=True)

    assert list(result.checked_paths) == ["zzz.enc"]
    assert result.state_warning is None


def test_empty_existing_tree_completes_and_resets_its_cursor(settings, tmp_path):
    """An empty existing tree returns a complete report: x_cleanup_orphan_storage_files__mutmut_152."""
    settings.MEDIA_ROOT = tmp_path
    state = tmp_path / STATE_NAME
    state.write_text(json.dumps({"after": "old.enc"}), encoding="utf-8")

    result = cleanup_orphan_storage_files()

    assert result.checked_paths == ()
    assert result.deleted == result.deletion_candidates == result.errors == 0
    assert result.continued_at is None and result.next_start is None
    assert result.tree_complete and result.state_warning is None
    assert json.loads(state.read_text(encoding="utf-8")) == {"after": None}

    (tmp_path / "aaa.enc").write_bytes(b"ciphertext")
    assert cleanup_orphan_storage_files(dry_run=True).checked_paths == ("aaa.enc",)


def test_default_owner_cleanup_removes_rows_despite_unlink_failure(
    client_identified,
    staff_user,
    settings,
    tmp_path,
    monkeypatch,
):
    """Default owner cleanup tolerates a failed unlink: x_delete_attachments_for__mutmut_1."""
    settings.MEDIA_ROOT = tmp_path
    field = FieldTemplate.objects.create(
        facility=client_identified.facility,
        name="Attachment",
        field_type=FieldTemplate.FieldType.FILE,
    )
    attachment = Attachment.objects.create(
        facility=client_identified.facility,
        client=client_identified,
        field_template=field,
        storage_filename="original.enc",
        original_filename_encrypted=encrypt_field("original.pdf"),
        file_size=4,
        mime_type="application/pdf",
        created_by=staff_user,
    )
    path = get_attachment_path(attachment)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"ciphertext")
    original = Path.unlink

    def fail_target(self, *args, **kwargs):
        if self == path:
            raise OSError("Injected unlink failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_target)
    delete_attachments_for(client_identified)

    assert path.exists()
    assert not client_identified.attachments.exists()
    assert not Attachment.objects.filter(pk=attachment.pk).exists()
