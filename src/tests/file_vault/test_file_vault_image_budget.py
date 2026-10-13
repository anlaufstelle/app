"""Image budgets reject oversized frame collections before storage."""

import io
from unittest.mock import patch

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from core.models import AuditLog, DocumentTypeField, Settings
from core.models.attachment import Attachment
from core.services.file_vault import get_decrypted_file_stream, policy, store_encrypted_file
from tests.test_attachment_purge import _file_template
from tests.test_sensitivity_transitions import make_record

pytestmark = pytest.mark.django_db


@pytest.fixture
def image_owner(facility, staff_user):
    Settings.objects.update_or_create(
        facility=facility, defaults={"allowed_file_types": "tif,tiff,gif,png,webp", "max_file_size_mb": 10}
    )
    owner, _ = make_record(facility, staff_user, "event")
    field = _file_template(facility, "Uploaded image")
    DocumentTypeField.objects.create(document_type=owner.document_type, field_template=field)
    return owner, field


def image_upload(fmt, *, sizes=((10, 10), (10, 10), (10, 10)), mode="RGB"):
    frames = [Image.new(mode, size, index * 40) for index, size in enumerate(sizes)]
    buffer = io.BytesIO()
    kwargs = {"comment": b"Private metadata"} if fmt == "GIF" else {}
    if fmt == "TIFF":
        kwargs["tiffinfo"] = {270: "Private metadata"}
    frames[0].save(buffer, format=fmt, save_all=True, append_images=frames[1:], **kwargs)
    extension = {"TIFF": "tiff", "GIF": "gif", "PNG": "png", "WEBP": "webp"}[fmt]
    return SimpleUploadedFile(f"image.{extension}", buffer.getvalue(), content_type=f"image/{extension}")


@pytest.mark.parametrize("fmt", ["TIFF", "GIF", "PNG", "WEBP"])
@pytest.mark.parametrize("budget", ["pixels", "frames", "bytes"])
def test_upload_rejects_total_image_budget_before_copying_or_encrypting(
    image_owner, staff_user, settings, monkeypatch, fmt, budget
):
    owner, field = image_owner
    upload = image_upload(fmt)
    if budget == "pixels":
        settings.FILE_VAULT_MAX_IMAGE_PIXELS = 250
    elif budget == "frames":
        monkeypatch.setattr(policy, "_MAX_IMAGE_FRAMES", 2, raising=False)
    else:
        monkeypatch.setattr(policy, "_MAX_IMAGE_DECODED_BYTES", 2500, raising=False)
    with (
        patch("core.services.file_vault.storage.encrypt_file") as encrypt,
        patch("core.services.file_vault.policy._cleaned_image") as clean,
        patch("PIL.ImageFile.ImageFile.load", side_effect=AssertionError("Image decoded before budget rejection")),
        pytest.raises(ValidationError, match="Bild|Frames|Speicherbudget"),
    ):
        store_encrypted_file(owner.facility, upload, field, owner, staff_user)
    encrypt.assert_not_called()
    clean.assert_not_called()
    assert upload.tell() == 0
    assert not Attachment.objects.filter(event=owner).exists()
    audit = AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION).latest("pk")
    assert (
        audit.detail["reason"]
        == {"pixels": "image_too_large", "frames": "image_too_many_frames", "bytes": "image_memory_budget"}[budget]
    )


@pytest.mark.parametrize("fmt", ["TIFF", "GIF", "PNG", "WEBP"])
def test_small_multiframe_upload_preserves_frames_and_metadata_policy(image_owner, staff_user, fmt):
    owner, field = image_owner
    upload = image_upload(fmt)
    original = upload.read()
    upload.seek(0)
    attachment = store_encrypted_file(owner.facility, upload, field, owner, staff_user)
    stored = b"".join(get_decrypted_file_stream(attachment))
    with Image.open(io.BytesIO(stored)) as image:
        assert image.n_frames == 3
    if fmt == "GIF":
        assert stored == original
    else:
        assert b"Private metadata" not in stored


def test_tiff_budget_counts_different_page_sizes(image_owner, staff_user, settings):
    owner, field = image_owner
    settings.FILE_VAULT_MAX_IMAGE_PIXELS = 450
    upload = image_upload("TIFF", sizes=((5, 5), (20, 20), (10, 10)))
    with pytest.raises(ValidationError, match="Pixel"):
        store_encrypted_file(owner.facility, upload, field, owner, staff_user)
    assert upload.tell() == 0


def test_gif_budget_accounts_for_larger_later_frames(image_owner, staff_user, settings):
    owner, field = image_owner
    # Pillow crops the middle frame to 5x5; the final frame expands to 10x10.
    # Each frame fits the cap, but 25 + 25 + 100 pixels exceed it.
    settings.FILE_VAULT_MAX_IMAGE_PIXELS = 100
    upload = image_upload("GIF", sizes=((5, 5), (20, 20), (10, 10)))
    with pytest.raises(ValidationError, match="Pixel"):
        store_encrypted_file(owner.facility, upload, field, owner, staff_user)
    assert upload.tell() == 0


@pytest.mark.parametrize("frame_count", [100, 101])
def test_fixed_frame_limit_keeps_all_allowed_tiff_pages(image_owner, staff_user, frame_count):
    owner, field = image_owner
    upload = image_upload("TIFF", sizes=((2, 2),) * frame_count)
    if frame_count == 101:
        with pytest.raises(ValidationError, match="Maximum: 100"):
            store_encrypted_file(owner.facility, upload, field, owner, staff_user)
        assert upload.tell() == 0
    else:
        attachment = store_encrypted_file(owner.facility, upload, field, owner, staff_user)
        stored = b"".join(get_decrypted_file_stream(attachment))
        with Image.open(io.BytesIO(stored)) as image:
            assert image.n_frames == frame_count


def test_event_upload_shows_image_budget_rejection_and_retains_values(image_owner, staff_user, client, settings):
    owner, field = image_owner
    settings.FILE_VAULT_MAX_IMAGE_PIXELS = 250
    previous_data = dict(owner.data_json)
    client.force_login(staff_user)
    response = client.post(
        reverse("core:event_update", kwargs={"pk": owner.pk}),
        {
            "document_type": str(owner.document_type_id),
            "occurred_at": owner.occurred_at.strftime("%Y-%m-%dT%H:%M"),
            **previous_data,
            field.slug: image_upload("TIFF"),
        },
    )
    assert response.status_code == 302
    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("insgesamt zu viele Pixel" in message for message in messages), messages
    owner.refresh_from_db()
    assert owner.data_json == previous_data
    assert not owner.attachments.exists()
    assert AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION, detail__reason="image_too_large").exists()
