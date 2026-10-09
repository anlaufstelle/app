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


@pytest.mark.django_db
def test_parallel_rotation_is_a_command_error(rotation_files, maintenance):
    from core.services.file_vault.file_locks import vault_lock

    create, _, _ = rotation_files
    attachment = create()
    path = get_attachment_path(attachment)
    before = path.read_bytes()
    with vault_lock(".reencrypt-files.lock", blocking=False):
        with pytest.raises(CommandError, match="already running"):
            run_command()
    assert path.read_bytes() == before


@pytest.mark.django_db
def test_manifest_durability_failure_is_a_command_error(rotation_files, maintenance, monkeypatch):
    create, _, _ = rotation_files
    create()
    fsync = os.fsync

    def fail_manifest(descriptor):
        if os.readlink(f"/proc/self/fd/{descriptor}").endswith(".reencrypt-files.jsonl"):
            raise OSError("simulated durability failure")
        fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_manifest)
    with pytest.raises(CommandError, match="Manifest progress could not be persisted"):
        run_command()
    monkeypatch.setattr(os, "fsync", fsync)
    output, _ = run_command()
    assert "already_primary=1" in output


@pytest.mark.django_db
def test_invalid_manifest_is_a_command_error_before_rotation(rotation_files, maintenance, settings):
    create, _, _ = rotation_files
    attachment = create()
    path = get_attachment_path(attachment)
    before = path.read_bytes()
    manifest = settings.MEDIA_ROOT / ".reencrypt-files.jsonl"
    manifest.write_bytes(b"invalid record\n")
    with pytest.raises(CommandError, match="Invalid manifest record"):
        run_command()
    assert path.read_bytes() == before
    assert manifest.read_bytes() == b"invalid record\n"


@pytest.mark.django_db
def test_per_file_rotation_errors_fail_loud_and_preserve_unreadable_file(rotation_files, maintenance, settings):
    create, primary, _ = rotation_files
    failed, valid = create(), create(key=primary)
    failed_path = get_attachment_path(failed)
    before = failed_path.read_bytes()
    output, errors = io.StringIO(), io.StringIO()
    with override_settings(ENCRYPTION_KEYS=primary):
        with pytest.raises(CommandError, match="File re-encryption failed for 1 attachment"):
            call_command("reencrypt_files", stdout=output, stderr=errors)
    assert str(failed.pk) in errors.getvalue()
    assert "Chunk decryption failed" in errors.getvalue()
    assert failed.storage_filename not in errors.getvalue()
    assert "private filename" not in errors.getvalue()
    assert "checked=2" in output.getvalue()
    assert "already_primary=1" in output.getvalue()
    assert failed_path.read_bytes() == before
    assert get_attachment_path(valid).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("verify", [False, True])
def test_missing_referenced_file_is_a_command_error(rotation_files, maintenance, verify):
    create, primary, _ = rotation_files
    attachment = create(key=primary)
    get_attachment_path(attachment).unlink()
    errors = io.StringIO()
    with pytest.raises(CommandError, match="failed for 1 attachment"):
        call_command("reencrypt_files", *(["--verify"] if verify else []), stderr=errors)
    assert str(attachment.pk) in errors.getvalue()
    assert "FileNotFoundError" in errors.getvalue()
    assert attachment.storage_filename not in errors.getvalue()


@pytest.mark.django_db
@pytest.mark.parametrize("verify", [False, True])
def test_missing_key_is_a_command_error_before_file_changes(rotation_files, maintenance, settings, verify):
    create, _, _ = rotation_files
    attachment = create()
    path = get_attachment_path(attachment)
    before = path.read_bytes()
    files = set(settings.MEDIA_ROOT.rglob("*"))
    with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=""):
        with pytest.raises(CommandError, match="encryption key|ENCRYPTION_KEY"):
            run_command(*(["--verify"] if verify else []))
    assert path.read_bytes() == before
    assert set(settings.MEDIA_ROOT.rglob("*")) == files


@pytest.mark.django_db
@pytest.mark.parametrize("mode", ["rotation", "dry_run", "verify", "filtered"])
def test_audit_summary_records_counts_duration_and_options(rotation_files, maintenance, mode, monkeypatch):
    from core.models import AuditLog

    create, primary, _ = rotation_files
    attachment = create(key=primary if mode == "verify" else rotation_files[2])
    args = {
        "rotation": [],
        "dry_run": ["--dry-run"],
        "verify": ["--verify"],
        "filtered": ["--facility", str(attachment.facility_id)],
    }[mode]
    action = "file_reencryption_completed"
    before = AuditLog.objects.filter(action=action).count()
    with monkeypatch.context() as timer:
        timer.setattr("core.management.commands.reencrypt_files.monotonic", iter([10.0, 12.5]).__next__, raising=False)
        run_command(*args, "--batch-size", "1")
    entries = AuditLog.objects.filter(action=action)
    assert entries.count() == before + 1
    entry = entries.latest("timestamp")
    assert entry.facility_id is None
    assert entry.user_id is None
    assert entry.target_id == ""
    assert entry.detail == {
        "file_reencryption": {
            "duration_seconds": 2.5,
            "checked": 1,
            "rotated": int(mode in {"rotation", "filtered"}),
            "already_primary": int(mode == "verify"),
            "deleted": 0,
            "would_rotate": int(mode == "dry_run"),
            "error_count": 0,
            "facility_id": str(attachment.facility_id) if mode == "filtered" else None,
            "dry_run": mode == "dry_run",
            "verify": mode == "verify",
            "batch_size": 1,
            "status": "completed",
        }
    }
    assert attachment.storage_filename not in str(entry.detail)
    assert "private filename" not in str(entry.detail)
    assert "private payload" not in str(entry.detail)
    assert str(attachment.pk) not in str(entry.detail)


@pytest.mark.django_db
@pytest.mark.parametrize("failure", ["file", "verify", "locked", "manifest"])
def test_failed_runs_have_one_minimized_audit_summary(rotation_files, maintenance, settings, failure):
    from contextlib import nullcontext

    from core.models import AuditLog
    from core.services.file_vault.file_locks import vault_lock

    create, primary, _ = rotation_files
    attachment = create()
    args = ["--verify"] if failure == "verify" else []
    if failure == "manifest":
        (settings.MEDIA_ROOT / ".reencrypt-files.jsonl").write_bytes(b"invalid record\n")
    if failure == "file":
        get_attachment_path(attachment).unlink()
    lock = vault_lock(".reencrypt-files.lock", blocking=False) if failure == "locked" else nullcontext()
    with lock:
        with pytest.raises(CommandError):
            run_command(*args)
    entries = AuditLog.objects.filter(action="file_reencryption_completed")
    assert entries.count() == 1
    detail = entries.get().detail["file_reencryption"]
    assert detail["error_count"] == 1
    assert detail["status"] == "failed"
    assert detail["checked"] == (1 if failure in {"file", "verify"} else None)
    assert detail["duration_seconds"] >= 0
    assert detail["verify"] == (failure == "verify")
    assert attachment.storage_filename not in str(detail)
    assert str(attachment.pk) not in str(detail)
    assert "private filename" not in str(detail)
    assert "private payload" not in str(detail)


@pytest.mark.django_db
def test_refused_role_does_not_create_a_rotation_audit(rotation_files, monkeypatch):
    from core.models import AuditLog

    rotation_files[0]()
    monkeypatch.setenv("ANLAUFSTELLE_PROCESS_ROLE", "runtime")
    with pytest.raises(CommandError, match="maintenance"):
        run_command()
    assert not AuditLog.objects.filter(action="file_reencryption_completed").exists()


@pytest.mark.django_db
def test_audit_failure_prevents_a_successful_command(rotation_files, maintenance, monkeypatch):
    from django.db import DatabaseError

    rotation_files[0]()

    def fail_audit(*args, **kwargs):
        raise DatabaseError("private database details")

    monkeypatch.setattr("core.management.commands.reencrypt_files.audit_event", fail_audit, raising=False)
    output = io.StringIO()
    with pytest.raises(CommandError, match="audit could not be written"):
        call_command("reencrypt_files", stdout=output)
    assert output.getvalue() == ""


@pytest.mark.django_db
def test_migration_records_the_new_audit_action():
    from django.db.migrations.loader import MigrationLoader

    from core.models import AuditLog

    state = MigrationLoader(connection).project_state()
    choices = state.apps.get_model("core", "AuditLog")._meta.get_field("action").choices
    assert ("file_reencryption_completed", "Datei-Umschlüsselung abgeschlossen") in choices
    assert AuditLog.Action.FILE_REENCRYPTION_COMPLETED.value == "file_reencryption_completed"


@pytest.mark.django_db(transaction=True)
def test_non_superuser_maintenance_database_role_rotates_and_audits(rotation_files, maintenance):
    from core.models import AuditLog

    create, primary, _ = rotation_files
    attachment = create()
    role = f"rot_e2_admin_{os.getpid()}"
    with connection.cursor() as cursor:
        cursor.execute(f"CREATE ROLE {role} NOSUPERUSER BYPASSRLS")
        cursor.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
        cursor.execute(f"GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO {role}")
        cursor.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}")
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(f"SET LOCAL ROLE {role}")
            output, _ = run_command()
            assert "rotated=1" in output
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}")
            cursor.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role}")
            cursor.execute(f"REVOKE ALL ON SCHEMA public FROM {role}")
            cursor.execute(f"DROP ROLE {role}")
    with override_settings(ENCRYPTION_KEYS=primary):
        assert (
            b"".join(decrypt_file_stream(get_attachment_path(attachment), file_id=attachment.storage_filename))
            == b"private payload"
        )
    assert AuditLog.objects.filter(action="file_reencryption_completed").count() == 1


@pytest.mark.django_db
def test_unexpected_interruption_records_failed_audit_without_claiming_counts(rotation_files, maintenance, monkeypatch):
    from core.models import AuditLog

    rotation_files[0]()

    def interrupt(**kwargs):
        raise RuntimeError("simulated interruption")

    monkeypatch.setattr("core.management.commands.reencrypt_files.rotate_files", interrupt)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        run_command()
    entries = AuditLog.objects.filter(action="file_reencryption_completed")
    assert entries.count() == 1
    summary = entries.get().detail["file_reencryption"]
    assert summary["status"] == "failed"
    assert summary["checked"] is None
    assert summary["rotated"] is None
    assert summary["error_count"] == 1
