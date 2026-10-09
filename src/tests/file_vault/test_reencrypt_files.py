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
