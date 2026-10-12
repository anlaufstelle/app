"""Pins for public attachment rotation and verification contracts.

Source: mutation delta of the release-0.99.7 checkpoint, 40 mutants.
Groups: attachment rotation (12), regular-file validation (3), streaming
rewrite (3), rotation iteration (3), verification (9), manifest records (10).
The two blocking-lock mutants already have a public regression test.
"""

import errno
import hashlib
import io
import json
import os
import stat
import struct
import uuid
from contextlib import suppress

import pytest
from cryptography.fernet import Fernet
from django.db.models.query import QuerySet
from django.test import override_settings

from core.models import Attachment, Client, FieldTemplate
from core.services.file_vault import file_rotation
from core.services.file_vault.encryption import EncryptionError, encrypt_file


@pytest.fixture
def vault_files(facility, client_identified, settings, tmp_path):
    """Create real attachment rows and bound ciphertext under an isolated media root."""
    settings.MEDIA_ROOT = tmp_path
    primary = Fernet.generate_key().decode()
    old = Fernet.generate_key().decode()
    settings.ENCRYPTION_KEY = ""
    settings.ENCRYPTION_KEYS = f"{primary},{old}"

    def create(*, name=None, primary_key=False, target_facility=facility, pk=None):
        owner = client_identified
        if target_facility != facility:
            owner = Client.objects.create(facility=target_facility, pseudonym="Other owner")
        field, _ = FieldTemplate.objects.get_or_create(
            facility=target_facility, name="Files", defaults={"field_type": FieldTemplate.FieldType.FILE}
        )
        attachment = Attachment.objects.create(
            pk=pk or uuid.uuid4(),
            facility=target_facility,
            client=owner,
            field_template=field,
            storage_filename=name or f"{uuid.uuid4()}.enc",
            original_filename_encrypted={"value": "private filename"},
            file_size=7,
            mime_type="application/octet-stream",
        )
        path = tmp_path / str(target_facility.pk) / attachment.storage_filename
        with override_settings(ENCRYPTION_KEYS=primary if primary_key else old):
            encrypt_file(io.BytesIO(b"payload"), path, file_id=attachment.storage_filename)
        return attachment, path

    return create, primary


@pytest.mark.django_db
@pytest.mark.parametrize("service", [file_rotation.rotate_files, file_rotation.verify_files], ids=["rotate", "verify"])
@pytest.mark.parametrize("identifier", ["../outside.enc", "", ".."], ids=["traversal", "empty", "parent"])
def test_invalid_storage_identifiers_are_rejected(vault_files, settings, service, identifier):
    """Reject unsafe references before opening them, preserving external ciphertext.

    x__rotate_attachment__mutmut_4, x__rotate_attachment__mutmut_5,
    x__rotate_attachment__mutmut_12, x_verify_files__mutmut_29,
    x_verify_files__mutmut_30, x_verify_files__mutmut_37.
    """
    create, primary = vault_files
    attachment, path = create()
    Attachment.objects.filter(pk=attachment.pk).update(storage_filename=identifier)
    outside = settings.MEDIA_ROOT / "outside.enc"
    with override_settings(ENCRYPTION_KEYS=primary):
        encrypt_file(io.BytesIO(b"outside"), outside, file_id="../outside.enc")
    before = outside.read_bytes()
    result = service()
    assert result.checked == 1
    assert result.errors == {str(attachment.pk): "Invalid attachment storage identifier"}
    assert outside.read_bytes() == before
    assert path.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("service", [file_rotation.rotate_files, file_rotation.verify_files], ids=["rotate", "verify"])
@pytest.mark.parametrize("name", ["XX.XX", "XX..XX"], ids=["dot", "double-dot"])
def test_regular_non_uuid_names_remain_readable(vault_files, service, name):
    """Accept regular bound filenames containing dots.

    x__rotate_attachment__mutmut_10, x__rotate_attachment__mutmut_11,
    x_verify_files__mutmut_35, x_verify_files__mutmut_36.
    """
    create, _ = vault_files
    create(name=name, primary_key=service is file_rotation.verify_files)
    result = service()
    assert result.checked == 1
    assert result.errors == {}
    assert result.rotated + result.already_primary == 1


@pytest.mark.django_db
@pytest.mark.parametrize("change", ["reference", "symlink", "content"], ids=["reference", "symlink", "content"])
def test_changes_before_replace_are_reported_without_completion(vault_files, settings, change):
    """Report the exact race cause and omit completion for changed attachments.

    x__rotate_attachment__mutmut_87, x__rotate_attachment__mutmut_90,
    x__rotate_attachment__mutmut_96, x__rotate_attachment__mutmut_101.
    """
    create, _ = vault_files
    attachment, path = create()
    original = path.read_bytes()

    def change_before_replace(stage, attachment_id):
        if stage != "before_replace":
            return
        assert attachment_id == str(attachment.pk)
        if change == "reference":
            Attachment.objects.filter(pk=attachment.pk).update(storage_filename="replacement.enc")
        elif change == "symlink":
            path.unlink()
            path.symlink_to(path.parent / "missing.enc")
        else:
            path.write_bytes(original + b"changed")

    result = file_rotation.rotate_files(checkpoint=change_before_replace)
    message = (
        "Attachment storage reference changed during rotation"
        if change == "reference"
        else "Attachment file changed during rotation"
    )
    assert result.errors == {str(attachment.pk): message}
    assert result.deleted == result.rotated == 0
    assert (settings.MEDIA_ROOT / ".reencrypt-files.jsonl").read_bytes() == b""
    assert not list(path.parent.glob(".rotate-*.tmp"))
    if change == "reference":
        assert path.read_bytes() == original
    elif change == "symlink":
        assert path.is_symlink()
    else:
        assert path.read_bytes() == original + b"changed"


@pytest.mark.django_db
def test_verification_includes_the_selected_nonempty_facility(vault_files, facility, other_facility):
    """Verify the selected old-key file and exclude an earlier foreign row.

    x_verify_files__mutmut_23.
    """
    create, _ = vault_files
    foreign, _ = create(target_facility=other_facility, pk=uuid.UUID(int=1))
    selected, _ = create(pk=uuid.UUID(int=2))
    result = file_rotation.verify_files(facility_id=str(facility.pk))
    assert result.checked == 1
    assert set(result.errors) == {str(selected.pk)}
    assert str(foreign.pk) not in result.errors
    assert result.already_primary == 0


@pytest.mark.django_db
def test_completion_keeps_checkpoint_identity_key_fingerprint_and_private_modes(vault_files, settings):
    """Persist compact completion with its key fingerprint and UUID; files stay private.

    x__open_regular__mutmut_7, x__open_regular__mutmut_9,
    x__rotate_attachment__mutmut_117, x__rotate_attachment__mutmut_123,
    xǁ_Manifestǁ__init____mutmut_2, xǁ_Manifestǁcompleted__mutmut_23,
    xǁ_Manifestǁcompleted__mutmut_25.
    """
    create, primary = vault_files
    attachment, path = create()
    manifest = settings.MEDIA_ROOT / ".reencrypt-files.jsonl"
    events = []

    def checkpoint(stage, attachment_id):
        events.append((stage, attachment_id))
        if stage == "before_replace":
            staging = path.parent / f".rotate-{attachment.pk}.tmp"
            assert stat.S_IMODE(staging.stat().st_mode) == 0o600
        if stage == "before_manifest":
            assert manifest.read_bytes() == b""

    previous_umask = os.umask(0o022)
    try:
        result = file_rotation.rotate_files(checkpoint=checkpoint)
    finally:
        os.umask(previous_umask)
    assert result.rotated == 1
    assert result.errors == {}
    assert events == [(stage, str(attachment.pk)) for stage in ("before_replace", "after_replace", "before_manifest")]
    record = {
        "attachment_id": str(attachment.pk),
        "key_id": hashlib.sha256(primary.encode()).hexdigest(),
        "state": "completed",
    }
    assert manifest.read_bytes() == json.dumps(record, separators=(",", ":")).encode() + b"\n"
    assert stat.S_IMODE(manifest.stat().st_mode) == 0o600


@pytest.mark.django_db
def test_interruption_cleans_staging_but_dry_run_preserves_it(vault_files):
    """Clean staging immediately after interruption and leave it untouched in dry runs.

    x__rotate_attachment__mutmut_130.
    """
    create, _ = vault_files
    attachment, path = create()
    staging = path.parent / f".rotate-{attachment.pk}.tmp"
    original = path.read_bytes()

    def interrupt(stage, attachment_id):
        if stage == "before_replace":
            assert staging.exists()
            raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError, match="^interrupted$"):
        file_rotation.rotate_files(checkpoint=interrupt)
    assert not staging.exists()
    assert path.read_bytes() == original
    staging.write_bytes(b"preexisting ciphertext")
    result = file_rotation.rotate_files(dry_run=True)
    assert result.would_rotate == 1
    assert result.errors == {}
    assert staging.read_bytes() == b"preexisting ciphertext"


@pytest.mark.django_db
@pytest.mark.parametrize("header", [struct.pack(">BI", 2, 2**31), b"\x02\x00"], ids=["unsigned-count", "truncated"])
def test_malformed_headers_are_collected_and_rotation_continues(vault_files, header):
    """Collect malformed-file errors while processing the later valid attachment.

    x__write_rotated__mutmut_18, x__write_rotated__mutmut_29, x__write_rotated__mutmut_8.
    """
    create, _ = vault_files
    broken, path = create(pk=uuid.UUID(int=1))
    create(pk=uuid.UUID(int=2))
    path.write_bytes(header)
    result = file_rotation.rotate_files()
    expected = (
        "Implausible chunk count in header: 2147483648 exceeds 0 possible chunks in 5 bytes"
        if len(header) == 5
        else "Truncated encrypted file"
    )
    assert result.errors == {str(broken.pk): expected}
    assert result.checked == 2
    assert result.rotated == 1
    assert path.read_bytes() == header


@pytest.mark.django_db
@pytest.mark.parametrize("service", [file_rotation.rotate_files, file_rotation.verify_files], ids=["rotate", "verify"])
@pytest.mark.parametrize("batch_size", [None, 1], ids=["default", "explicit"])
def test_iteration_preserves_the_requested_batch_size(vault_files, monkeypatch, service, batch_size):
    """Forward the service batch limit to attachment iteration without fixing query counts.

    x_rotate_files__mutmut_2, x_rotate_files__mutmut_44,
    x_verify_files__mutmut_1, x_verify_files__mutmut_24.
    """
    create, _ = vault_files
    create(primary_key=True)
    observed = []
    original_iterator = QuerySet.iterator

    def iterator(queryset, *args, **kwargs):
        if queryset.model is Attachment:
            observed.append(kwargs.get("chunk_size", args[0] if args else None))
        return original_iterator(queryset, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "iterator", iterator)
    result = service(**({} if batch_size is None else {"batch_size": batch_size}))
    assert result.checked == 1
    assert result.errors == {}
    assert observed
    assert all(size == (200 if batch_size is None else batch_size) for size in observed)


@pytest.mark.parametrize("service", [file_rotation.rotate_files, file_rotation.verify_files], ids=["rotate", "verify"])
def test_invalid_batch_size_has_an_actionable_message(service):
    """Reject zero batch sizes with the public minimum-size message.

    x_rotate_files__mutmut_5, x_verify_files__mutmut_4.
    """
    with pytest.raises(ValueError) as error:
        service(batch_size=0)
    assert str(error.value) == "batch_size must be at least 1"


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["unterminated", "valid-oversized", "schema", "state"])
def test_manifest_validation_preserves_the_rejected_record(vault_files, tmp_path, kind):
    """Reject oversized and invalid manifest records with their precise diagnostic.

    xǁ_Manifestǁ__init____mutmut_15, xǁ_Manifestǁ__init____mutmut_16,
    xǁ_Manifestǁ__init____mutmut_23, xǁ_Manifestǁ__init____mutmut_24,
    xǁ_Manifestǁ__init____mutmut_37, xǁ_Manifestǁ__init____mutmut_47,
    xǁ_Manifestǁ__init____mutmut_56.
    """
    manifest = tmp_path / "progress.jsonl"
    record = {"attachment_id": "", "key_id": "key", "state": "completed"}
    if kind == "unterminated":
        content = b"x" * 1024
        message = "Manifest record exceeds its size limit"
    elif kind == "valid-oversized":
        base = json.dumps(record).encode() + b"\n"
        record["attachment_id"] = "x" * (1025 - len(base))
        content = json.dumps(record).encode() + b"\n"
        assert len(content) == 1025
        message = "Manifest record exceeds its size limit"
    elif kind == "schema":
        content = b'{"state":"completed"}\n'
        message = "Invalid manifest record schema"
    else:
        record["state"] = "pending"
        content = json.dumps(record).encode() + b"\n"
        message = "Invalid manifest state"
    manifest.write_bytes(content)
    with pytest.raises(EncryptionError) as error:
        file_rotation.rotate_files(manifest_path=manifest)
    assert str(error.value) == message
    assert manifest.read_bytes() == content


@pytest.mark.django_db
def test_directory_attachment_is_rejected_and_its_descriptor_closed(vault_files, monkeypatch):
    """Reject a directory as an attachment and close its descriptor on validation failure.

    x__open_regular__mutmut_13.
    """
    create, _ = vault_files
    attachment, directory = create()
    directory.unlink()
    directory.mkdir()
    descriptors = []
    original_open = os.open

    def record_open(path, *args, **kwargs):
        descriptor = original_open(path, *args, **kwargs)
        if path == directory:
            descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(os, "open", record_open)
    try:
        result = file_rotation.verify_files()
        assert result.errors == {str(attachment.pk): "Attachment or manifest is not a regular file"}
        assert descriptors
        for descriptor in descriptors:
            with pytest.raises(OSError) as closed:
                os.fstat(descriptor)
            assert closed.value.errno == errno.EBADF
    finally:
        for descriptor in descriptors:
            with suppress(OSError):
                os.close(descriptor)
