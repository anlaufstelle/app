"""Public service contracts for crash-safe attachment key rotation."""

import io
import json
import os
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from django.test import override_settings

from core.models import Attachment, FieldTemplate
from core.services.file_vault.encryption import CHUNK_SIZE, EncryptionError, decrypt_file_stream, encrypt_file
from core.services.file_vault.storage import delete_attachment_file, get_attachment_path


@pytest.fixture
def attachment_factory(facility, client_identified, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    field = FieldTemplate.objects.create(facility=facility, name="File", field_type=FieldTemplate.FieldType.FILE)
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    settings.ENCRYPTION_KEY = ""
    settings.ENCRYPTION_KEYS = f"{new_key},{old_key}"

    def create(data=b"private attachment", *, legacy=False, key=old_key):
        import uuid

        attachment = Attachment.objects.create(
            facility=facility,
            client=client_identified,
            field_template=field,
            storage_filename=f"{uuid.uuid4()}.enc",
            original_filename_encrypted={"value": "secret filename"},
            file_size=len(data),
            mime_type="application/octet-stream",
        )
        with override_settings(ENCRYPTION_KEYS=key):
            encrypt_file(
                io.BytesIO(data),
                get_attachment_path(attachment),
                file_id=None if legacy else attachment.storage_filename,
            )
        return attachment

    return create, new_key, old_key


def rotate(**kwargs):
    from core.services.file_vault.file_rotation import rotate_files

    return rotate_files(**kwargs)


@pytest.mark.django_db
@pytest.mark.parametrize("legacy", [False, True])
def test_rotation_and_primary_only_idempotent_resume(attachment_factory, settings, legacy):
    create, new_key, _ = attachment_factory
    data = b"payload" * (CHUNK_SIZE // 2)
    attachment = create(data, legacy=legacy)
    path = get_attachment_path(attachment)
    before = path.read_bytes()
    result = rotate()
    assert result.rotated == 1
    assert result.errors == {}
    after = path.read_bytes()
    assert after != before
    with override_settings(ENCRYPTION_KEYS=new_key):
        assert b"".join(decrypt_file_stream(path, file_id=attachment.storage_filename)) == data
        assert rotate().already_primary == 1
    assert path.read_bytes() == after
    manifest = settings.MEDIA_ROOT / ".reencrypt-files.jsonl"
    records = [json.loads(line) for line in manifest.read_text().splitlines()]
    assert records[-1]["attachment_id"] == str(attachment.pk)
    assert attachment.storage_filename not in manifest.read_text()
    assert "secret filename" not in manifest.read_text()
    assert data[:100].decode() not in manifest.read_text()


@pytest.mark.django_db
@pytest.mark.parametrize("crash_at", ["before_replace", "after_replace", "before_manifest"])
def test_resume_after_interrupted_commit(attachment_factory, crash_at):
    create, new_key, _ = attachment_factory
    attachment, remaining = sorted([create(), create()], key=lambda item: item.pk)
    path = get_attachment_path(attachment)
    before = path.read_bytes()
    remaining_before = get_attachment_path(remaining).read_bytes()

    def fail(stage, attachment_id):
        if stage == crash_at:
            raise RuntimeError("simulated process interruption")

    with pytest.raises(RuntimeError, match="simulated"):
        rotate(checkpoint=fail)
    interrupted = path.read_bytes()
    assert (interrupted == before) == (crash_at == "before_replace")
    assert get_attachment_path(remaining).read_bytes() == remaining_before
    result = rotate()
    assert result.rotated == (2 if crash_at == "before_replace" else 1)
    assert result.already_primary == (crash_at != "before_replace")
    with override_settings(ENCRYPTION_KEYS=new_key):
        assert b"".join(decrypt_file_stream(path, file_id=attachment.storage_filename)) == b"private attachment"
        assert (
            b"".join(decrypt_file_stream(get_attachment_path(remaining), file_id=remaining.storage_filename))
            == b"private attachment"
        )
    if crash_at != "before_replace":
        assert path.read_bytes() == interrupted
    assert not list(path.parent.glob(".rotate-*.tmp"))


@pytest.mark.django_db
def test_manifest_is_not_integrity_evidence(attachment_factory, settings):
    create, new_key, old_key = attachment_factory
    attachment = create()
    assert rotate().rotated == 1
    path = get_attachment_path(attachment)
    with override_settings(ENCRYPTION_KEYS=old_key):
        encrypt_file(io.BytesIO(b"restored backup"), path, file_id=attachment.storage_filename)
    assert rotate().rotated == 1
    primary_bytes = path.read_bytes()
    path.write_bytes(primary_bytes[:-20])
    result = rotate()
    assert str(attachment.pk) in result.errors
    assert path.read_bytes() == primary_bytes[:-20]
    with override_settings(ENCRYPTION_KEYS=new_key):
        with pytest.raises(EncryptionError):
            list(decrypt_file_stream(path, file_id=attachment.storage_filename))


@pytest.mark.django_db
def test_missing_old_key_preserves_file_and_continues(attachment_factory):
    create, new_key, _ = attachment_factory
    missing = create(key=Fernet.generate_key().decode())
    readable = create()
    before = get_attachment_path(missing).read_bytes()
    result = rotate()
    assert str(missing.pk) in result.errors
    assert result.rotated == 1
    assert get_attachment_path(missing).read_bytes() == before
    with override_settings(ENCRYPTION_KEYS=new_key):
        assert b"".join(decrypt_file_stream(get_attachment_path(readable), file_id=readable.storage_filename))


@pytest.mark.django_db
def test_parallel_rotation_rejected_even_with_different_manifest(attachment_factory, tmp_path):
    create, _, _ = attachment_factory
    create()

    def nested(stage, attachment_id):
        if stage == "before_replace":
            from core.services.file_vault.file_rotation import FileRotationLockedError

            with pytest.raises(FileRotationLockedError):
                rotate(manifest_path=tmp_path / "alternative.jsonl")

    assert rotate(checkpoint=nested).rotated == 1


@pytest.mark.django_db
def test_deleted_file_is_not_recreated(attachment_factory):
    create, _, _ = attachment_factory
    attachment = create()

    def delete(stage, attachment_id):
        if stage == "before_replace":
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(delete_attachment_file, attachment).result(timeout=5)

    result = rotate(checkpoint=delete)
    assert result.deleted == 1
    assert not get_attachment_path(attachment).exists()


@pytest.mark.django_db
def test_deleted_row_is_not_recreated(attachment_factory):
    create, _, _ = attachment_factory
    attachment = create()

    def delete(stage, attachment_id):
        if stage == "before_replace":
            delete_attachment_file(attachment)
            Attachment.objects.filter(pk=attachment_id).delete()

    assert rotate(checkpoint=delete).deleted == 1
    assert not get_attachment_path(attachment).exists()


@pytest.mark.django_db
def test_streams_fifty_mib_without_plaintext_temporary_file(attachment_factory):
    create, new_key, _ = attachment_factory
    attachment = create(b"x" * (50 * 1024 * 1024))
    path = get_attachment_path(attachment)

    def inspect_staging(stage, attachment_id):
        if stage == "before_replace":
            staging = path.parent / f".rotate-{attachment_id}.tmp"
            with staging.open("rb") as stream:
                assert stream.read(1) == b"\x02"
                stream.seek(0)
                sizes = map(
                    len,
                    decrypt_file_stream(stream, file_id=attachment.storage_filename, fernet=Fernet(new_key.encode())),
                )
                assert sum(sizes) == 50 * 1024 * 1024

    tracemalloc.start()
    try:
        result = rotate(checkpoint=inspect_staging)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result.rotated == 1
    print(f"Rotation Python allocation peak: {peak} bytes")
    assert peak < 5 * 1024 * 1024
    with override_settings(ENCRYPTION_KEYS=new_key):
        chunks = list(map(len, decrypt_file_stream(path, file_id=attachment.storage_filename)))
    assert sum(chunks) == 50 * 1024 * 1024
    assert max(chunks) == CHUNK_SIZE


@pytest.mark.django_db
@pytest.mark.parametrize("suffix", [b"unexpected trailing bytes", b"\x00"])
def test_decoder_rejects_trailing_data(attachment_factory, suffix):
    create, _, _ = attachment_factory
    attachment = create()
    path = get_attachment_path(attachment)
    with path.open("ab") as output:
        output.write(suffix)
    with pytest.raises(EncryptionError, match="Trailing"):
        list(decrypt_file_stream(path, file_id=attachment.storage_filename))


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("crash_at", ["before_replace", "after_replace", "before_manifest"])
def test_process_death_recovers_without_rewriting_primary(attachment_factory, crash_at, settings):
    import subprocess
    import sys
    from pathlib import Path

    from django.db import connection

    create, new_key, _ = attachment_factory
    attachment = create()
    path = get_attachment_path(attachment)
    before = path.read_bytes()
    # This test needs committed rows; the child reads them on its own connection.
    assert connection.in_atomic_block is False
    child_script = """
import os
import django
from pathlib import Path
from django.conf import settings
settings.DATABASES['default']['NAME'] = os.environ['ROTATION_TEST_DB']
settings.MEDIA_ROOT = os.environ['ROTATION_TEST_MEDIA']
settings.ENCRYPTION_KEYS = os.environ['ROTATION_TEST_KEYS']
django.setup()
from core.services.file_vault.file_rotation import rotate_files
def checkpoint(stage, attachment_id):
    if stage == os.environ['ROTATION_TEST_CRASH']:
        if stage == 'before_manifest':
            with (Path(settings.MEDIA_ROOT) / '.reencrypt-files.jsonl').open('ab') as stream:
                stream.write(b'{"attachment_id":')
                stream.flush()
                os.fsync(stream.fileno())
        os._exit(72)
rotate_files(checkpoint=checkpoint)
"""
    environment = {
        **os.environ,
        "PYTHONPATH": str(Path.cwd() / "src"),
        "DJANGO_SETTINGS_MODULE": "anlaufstelle.settings.test",
        "ROTATION_TEST_DB": connection.settings_dict["NAME"],
        "ROTATION_TEST_MEDIA": str(settings.MEDIA_ROOT),
        "ROTATION_TEST_KEYS": settings.ENCRYPTION_KEYS,
        "ROTATION_TEST_CRASH": crash_at,
    }
    child = subprocess.run([sys.executable, "-c", child_script], env=environment, capture_output=True, timeout=20)
    assert child.returncode == 72, child.stderr.decode()
    interrupted = path.read_bytes()
    assert (interrupted == before) == (crash_at == "before_replace")
    assert rotate().errors == {}
    if crash_at != "before_replace":
        assert path.read_bytes() == interrupted
    with override_settings(ENCRYPTION_KEYS=new_key):
        assert b"".join(decrypt_file_stream(path, file_id=attachment.storage_filename)) == b"private attachment"
    assert not list(path.parent.glob(".rotate-*.tmp"))
    manifest = settings.MEDIA_ROOT / ".reencrypt-files.jsonl"
    assert all(json.loads(line)["state"] == "completed" for line in manifest.read_text().splitlines())


@pytest.mark.django_db
def test_file_and_directory_fsynced_before_manifest(attachment_factory):
    import stat

    create, _, _ = attachment_factory
    create()
    events = []
    original_fsync = os.fsync
    original_replace = os.replace

    def fsync(fd):
        events.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        original_fsync(fd)

    def replace(source, target):
        events.append("replace")
        original_replace(source, target)

    def checkpoint(stage, attachment_id):
        if stage == "before_manifest":
            assert events[-3:] == ["file", "replace", "directory"]

    with patch("os.fsync", side_effect=fsync), patch("os.replace", side_effect=replace):
        assert rotate(checkpoint=checkpoint).rotated == 1
    assert events[-1] == "directory"


@pytest.mark.django_db
def test_delete_during_atomic_replace_waits_and_wins(attachment_factory):
    create, _, _ = attachment_factory
    attachment = create()
    started, deleted = Event(), Event()
    original_replace = os.replace

    def delete():
        started.set()
        delete_attachment_file(attachment)
        deleted.set()

    with ThreadPoolExecutor(max_workers=1) as executor:

        def replace(source, target):
            if target == get_attachment_path(attachment):
                future = executor.submit(delete)
                assert started.wait(timeout=5)
                assert not deleted.wait(timeout=0.1)
                original_replace(source, target)
                return future
            return original_replace(source, target)

        with patch("os.replace", side_effect=replace):
            assert rotate().rotated == 1
    assert deleted.is_set()
    assert not get_attachment_path(attachment).exists()


@pytest.mark.django_db
def test_manifest_durability_failure_stops_and_resume_uses_file_state(attachment_factory, settings):
    create, _, _ = attachment_factory
    attachments = [create(), create()]
    originals = {item.pk: get_attachment_path(item).read_bytes() for item in attachments}
    original_fsync = os.fsync
    manifest_path = settings.MEDIA_ROOT / ".reencrypt-files.jsonl"

    def fail_manifest(fd):
        if os.fstat(fd).st_ino == manifest_path.stat().st_ino:
            raise OSError("simulated manifest durability failure")
        original_fsync(fd)

    with patch("os.fsync", side_effect=fail_manifest):
        with pytest.raises(RuntimeError, match="Manifest"):
            rotate()
    changed = [item for item in attachments if get_attachment_path(item).read_bytes() != originals[item.pk]]
    assert len(changed) == 1
    interrupted = get_attachment_path(changed[0]).read_bytes()
    result = rotate()
    assert result.rotated == 1
    assert result.already_primary == 1
    assert get_attachment_path(changed[0]).read_bytes() == interrupted
