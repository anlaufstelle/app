"""Public command contracts for attachment key rotation."""

import io
import os
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from django.core.management import CommandError, call_command
from django.db import connection, transaction
from django.test import override_settings

from core.models import Attachment, FieldTemplate
from core.services.file_vault.encryption import decrypt_file_stream, encrypt_file
from core.services.file_vault.storage import get_attachment_path


@pytest.fixture
def rotation_files(facility, client_identified, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    field = FieldTemplate.objects.create(facility=facility, name="File", field_type=FieldTemplate.FieldType.FILE)
    primary = Fernet.generate_key().decode()
    old = Fernet.generate_key().decode()
    settings.ENCRYPTION_KEY = ""
    settings.ENCRYPTION_KEYS = f"{primary},{old}"

    def create(data=b"private payload", *, key=old, legacy=False, owner=client_identified):
        attachment = Attachment.objects.create(
            facility=owner.facility,
            client=owner,
            field_template=field,
            storage_filename=f"{uuid4()}.enc",
            original_filename_encrypted={"value": "private filename"},
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

    return create, primary, old


@pytest.fixture
def maintenance(monkeypatch):
    monkeypatch.setenv("ANLAUFSTELLE_PROCESS_ROLE", "maintenance")


def run_command(*args):
    output, errors = io.StringIO(), io.StringIO()
    call_command("reencrypt_files", *args, stdout=output, stderr=errors)
    return output.getvalue(), errors.getvalue()


@pytest.mark.django_db
@pytest.mark.parametrize("legacy", [False, True])
def test_command_rotates_and_is_idempotent(rotation_files, maintenance, legacy):
    create, primary, _ = rotation_files
    attachment = create(legacy=legacy)
    path = get_attachment_path(attachment)
    original = path.read_bytes()
    output, _ = run_command()
    assert "rotated=1" in output
    rotated = path.read_bytes()
    assert rotated != original
    with override_settings(ENCRYPTION_KEYS=primary):
        assert b"".join(decrypt_file_stream(path, file_id=attachment.storage_filename)) == b"private payload"
        output, _ = run_command()
    assert "already_primary=1" in output
    assert path.read_bytes() == rotated


@pytest.mark.django_db
@pytest.mark.parametrize("process_role", [None, "runtime"])
def test_runtime_role_rejected_before_files_change(rotation_files, monkeypatch, process_role, settings):
    create, _, _ = rotation_files
    attachment = create()
    path = get_attachment_path(attachment)
    before = path.read_bytes()
    files = set(settings.MEDIA_ROOT.rglob("*"))
    monkeypatch.delenv("ANLAUFSTELLE_PROCESS_ROLE", raising=False)
    if process_role:
        monkeypatch.setenv("ANLAUFSTELLE_PROCESS_ROLE", process_role)
    with pytest.raises(CommandError, match="maintenance"):
        run_command()
    assert path.read_bytes() == before
    assert set(settings.MEDIA_ROOT.rglob("*")) == files


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("super_admin", [False, True])
def test_app_database_role_rejected_even_with_super_admin(rotation_files, maintenance, super_admin, settings):
    create, _, _ = rotation_files
    attachment = create()
    path = get_attachment_path(attachment)
    before = path.read_bytes()
    files = set(settings.MEDIA_ROOT.rglob("*"))
    role = f"rot_e2_app_{os.getpid()}"
    with connection.cursor() as cursor:
        cursor.execute(f"CREATE ROLE {role} NOSUPERUSER NOBYPASSRLS")
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(f"SET LOCAL ROLE {role}")
            cursor.execute("SELECT set_config('app.is_super_admin', %s, true)", ["true" if super_admin else ""])
            with pytest.raises(CommandError, match="BYPASSRLS"):
                run_command()
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP ROLE {role}")
    assert path.read_bytes() == before
    assert set(settings.MEDIA_ROOT.rglob("*")) == files


@pytest.mark.django_db
def test_dry_run_preserves_ciphertext_and_manifest(rotation_files, maintenance, settings):
    create, _, _ = rotation_files
    attachment = create()
    path = get_attachment_path(attachment)
    before = path.read_bytes()
    manifest = settings.MEDIA_ROOT / ".reencrypt-files.jsonl"
    manifest.write_bytes(b"existing progress must remain unchanged")
    output, _ = run_command("--dry-run", "--batch-size", "1")
    assert "would_rotate=1" in output
    assert "rotated=0" in output
    assert path.read_bytes() == before
    assert manifest.read_bytes() == b"existing progress must remain unchanged"
    assert not list(settings.MEDIA_ROOT.rglob(".rotate-*.tmp"))


@pytest.mark.django_db
def test_facility_filter_does_not_prove_installation_wide_key_removal(
    rotation_files,
    maintenance,
    other_facility,
    client_identified,
    settings,
):
    create, _, _ = rotation_files
    selected = create()
    from tests._factories import make_client

    owner = make_client(facility=other_facility, pseudonym="Excluded", created_by=client_identified.created_by)
    excluded = create(owner=owner)
    moved_path = get_attachment_path(excluded)
    excluded_before = moved_path.read_bytes()
    selected_before = get_attachment_path(selected).read_bytes()
    output, errors = run_command("--facility", str(selected.facility_id), "--batch-size", "1")
    assert "checked=1" in output
    assert "rotated=1" in output
    assert get_attachment_path(selected).read_bytes() != selected_before
    assert moved_path.read_bytes() == excluded_before
    assert "does not prove installation-wide key removability" in output + errors


@pytest.mark.django_db
@pytest.mark.parametrize("option,value", [("--batch-size", "0"), ("--batch-size", "-1"), ("--facility", "invalid")])
def test_invalid_options_fail_before_files_change(rotation_files, maintenance, settings, option, value):
    create, _, _ = rotation_files
    attachment = create()
    before = get_attachment_path(attachment).read_bytes()
    files = set(settings.MEDIA_ROOT.rglob("*"))
    with pytest.raises(CommandError, match="must be at least 1" if option == "--batch-size" else "invalid UUID"):
        run_command(option, value)
    assert get_attachment_path(attachment).read_bytes() == before
    assert set(settings.MEDIA_ROOT.rglob("*")) == files


@pytest.mark.django_db
def test_verify_reads_every_referenced_version_without_mutation(rotation_files, maintenance, settings):
    from django.utils import timezone

    create, primary, _ = rotation_files
    current, deleted, superseded = [create(key=primary) for _ in range(3)]
    Attachment.objects.filter(pk=deleted.pk).update(deleted_at=timezone.now())
    Attachment.objects.filter(pk=superseded.pk).update(
        is_current=False,
        superseded_by=current,
        superseded_at=timezone.now(),
    )
    before = {item.pk: get_attachment_path(item).read_bytes() for item in [current, deleted, superseded]}
    output, _ = run_command("--verify", "--batch-size", "1")
    assert "checked=3" in output
    assert "already_primary=3" in output
    assert "rotated=0" in output
    assert {item.pk: get_attachment_path(item).read_bytes() for item in [current, deleted, superseded]} == before
    assert not (settings.MEDIA_ROOT / ".reencrypt-files.jsonl").exists()
    assert not list(settings.MEDIA_ROOT.rglob(".rotate-*.tmp"))


@pytest.mark.django_db
@pytest.mark.parametrize("version", ["current", "deleted", "superseded"])
def test_verify_rejects_old_key_for_every_version(rotation_files, maintenance, version):
    from django.utils import timezone

    create, primary, _ = rotation_files
    attachment = create()
    if version == "deleted":
        Attachment.objects.filter(pk=attachment.pk).update(deleted_at=timezone.now())
    elif version == "superseded":
        replacement = create(key=primary)
        Attachment.objects.filter(pk=attachment.pk).update(
            is_current=False,
            superseded_by=replacement,
            superseded_at=timezone.now(),
        )
    path = get_attachment_path(attachment)
    before = path.read_bytes()
    output, errors = io.StringIO(), io.StringIO()
    with pytest.raises(CommandError, match="Primary-key verification failed"):
        call_command("reencrypt_files", "--verify", stdout=output, stderr=errors)
    assert str(attachment.pk) in errors.getvalue()
    assert "Chunk decryption failed" in errors.getvalue()
    assert attachment.storage_filename not in errors.getvalue()
    assert "private filename" not in errors.getvalue()
    assert path.read_bytes() == before


@pytest.mark.django_db
@pytest.mark.parametrize("damage", ["file_binding", "reorder", "truncate", "trailing", "last_chunk", "downgrade"])
def test_verify_checks_full_chunk_binding(rotation_files, maintenance, damage):
    import struct

    from core.services.file_vault.encryption import CHUNK_SIZE

    create, primary, _ = rotation_files
    attachment = create(b"a" * CHUNK_SIZE + b"b" * CHUNK_SIZE, key=primary)
    path = get_attachment_path(attachment)
    ciphertext = path.read_bytes()
    first_length = struct.unpack(">I", ciphertext[5:9])[0]
    first, second = ciphertext[5 : 9 + first_length], ciphertext[9 + first_length :]
    if damage == "file_binding":
        with override_settings(ENCRYPTION_KEYS=primary):
            encrypt_file(io.BytesIO(b"other file"), path, file_id=f"{uuid4()}.enc")
    else:
        modified = {
            "reorder": ciphertext[:5] + second + first,
            "truncate": struct.pack(">BI", 2, 1) + first,
            "trailing": ciphertext + b"extra",
            "last_chunk": ciphertext[:-1] + bytes([ciphertext[-1] ^ 1]),
            "downgrade": b"\x01" + ciphertext[1:],
        }[damage]
        path.write_bytes(modified)
    before = path.read_bytes()
    output, errors = io.StringIO(), io.StringIO()
    with pytest.raises(CommandError, match="Primary-key verification failed"):
        call_command("reencrypt_files", "--verify", stdout=output, stderr=errors)
    assert str(attachment.pk) in errors.getvalue()
    expected = (
        "Trailing data"
        if damage == "trailing"
        else "decryption failed"
        if damage == "last_chunk"
        else "binding mismatch"
    )
    assert expected in errors.getvalue()
    assert path.read_bytes() == before


@pytest.mark.django_db
@pytest.mark.parametrize("dry_run", [False, True])
def test_filtered_verification_warns_about_installation_scope(rotation_files, maintenance, other_facility, dry_run):
    create, primary, _ = rotation_files
    attachment = create(key=primary)
    args = ["--verify", "--facility", str(other_facility.pk)]
    if dry_run:
        args.append("--dry-run")
    output, errors = run_command(*args)
    assert "checked=0" in output
    assert "does not prove installation-wide key removability" in output + errors
    assert get_attachment_path(attachment).exists()
