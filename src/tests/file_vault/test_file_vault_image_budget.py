"""Image budgets reject oversized frame collections before storage."""

import io
import zlib
from unittest.mock import patch

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image, PngImagePlugin, WebPImagePlugin

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
    if fmt in {"PNG", "WEBP"}:
        exif = Image.Exif()
        exif[270] = "Private metadata"
        kwargs["exif"] = exif
        xmp = b'<x:xmpmeta xmlns:x="adobe:ns:meta/">Private metadata</x:xmpmeta>'
        if fmt == "WEBP":
            kwargs["xmp"] = xmp
        else:
            pnginfo = PngImagePlugin.PngInfo()
            pnginfo.add_itxt("XML:com.adobe.xmp", xmp.decode())
            pnginfo.add_text("Description", "Private metadata")
            kwargs["pnginfo"] = pnginfo
    frames[0].save(buffer, format=fmt, save_all=True, append_images=frames[1:], **kwargs)
    for frame in frames:
        frame.close()
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
    assert b"Private metadata" in original
    if fmt in {"PNG", "WEBP"}:
        with Image.open(io.BytesIO(original)) as image:
            assert image.getexif()[270] == "Private metadata"
            if fmt == "WEBP":
                assert b"Private metadata" in image.info["xmp"]
            else:
                assert "Private metadata" in image.text["XML:com.adobe.xmp"]
                assert image.text["Description"] == "Private metadata"
    attachment = store_encrypted_file(owner.facility, upload, field, owner, staff_user)
    stored = b"".join(get_decrypted_file_stream(attachment))
    with Image.open(io.BytesIO(stored)) as image:
        assert image.n_frames == 3
        if fmt in {"PNG", "WEBP"}:
            for index in range(image.n_frames):
                image.seek(index)
                assert not image.getexif()
                assert "xmp" not in image.info
                if fmt == "PNG":
                    assert not image.text
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


@pytest.mark.parametrize("budget", ["pixels", "frames", "bytes"])
def test_webp_rejects_before_native_canvas_allocation(image_owner, staff_user, settings, monkeypatch, budget):
    owner, field = image_owner
    upload = image_upload("WEBP", sizes=((10, 10), (10, 10)))
    if budget == "pixels":
        settings.FILE_VAULT_MAX_IMAGE_PIXELS = 150
    elif budget == "frames":
        monkeypatch.setattr(policy, "_MAX_IMAGE_FRAMES", 1)
    else:
        # The native constructor would reserve two 400-byte RGBA canvases.
        monkeypatch.setattr(policy, "_MAX_IMAGE_DECODED_BYTES", 400)
    with (
        patch.object(
            WebPImagePlugin._webp,
            "WebPAnimDecoder",
            side_effect=AssertionError("Native canvas allocation reached before image-budget rejection"),
        ) as decoder,
        pytest.raises(ValidationError, match="Pixel|Frames|Speicherbudget"),
    ):
        store_encrypted_file(owner.facility, upload, field, owner, staff_user)
    decoder.assert_not_called()
    assert upload.tell() == 0
    assert not Attachment.objects.filter(event=owner).exists()


@pytest.mark.parametrize("fmt", ["PNG", "GIF"])
def test_disposal_buffer_rejected_before_image_open(image_owner, staff_user, monkeypatch, fmt):
    owner, field = image_owner
    frames = [Image.new("RGBA", (10, 10), color) for color in ("red", "blue")]
    buffer = io.BytesIO()
    frames[0].save(buffer, format=fmt, save_all=True, append_images=frames[1:], disposal=2 if fmt == "GIF" else 1)
    for frame in frames:
        frame.close()
    extension = fmt.lower()
    upload = SimpleUploadedFile(f"disposal.{extension}", buffer.getvalue(), content_type=f"image/{extension}")
    monkeypatch.setattr(policy, "_MAX_IMAGE_DECODED_BYTES", 400)
    with (
        patch.object(
            Image.core, "fill", side_effect=AssertionError("Disposal buffer allocated before rejection")
        ) as fill,
        pytest.raises(ValidationError, match="Speicherbudget"),
    ):
        store_encrypted_file(owner.facility, upload, field, owner, staff_user)
    fill.assert_not_called()
    assert upload.tell() == 0


@pytest.mark.parametrize("allowed", [True, False])
def test_svg_keeps_operator_controlled_passthrough(image_owner, staff_user, allowed):
    owner, field = image_owner
    Settings.objects.filter(facility=owner.facility).update(allowed_file_types="svg" if allowed else "png")
    original = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'
    upload = SimpleUploadedFile("drawing.svg", original, content_type="image/svg+xml")
    if allowed:
        with patch.object(Image, "open", side_effect=AssertionError("SVG entered the raster decoder")):
            attachment = store_encrypted_file(owner.facility, upload, field, owner, staff_user)
        assert attachment.detected_mime == "image/svg+xml"
        assert b"".join(get_decrypted_file_stream(attachment)) == original
    else:
        with pytest.raises(ValidationError):
            store_encrypted_file(owner.facility, upload, field, owner, staff_user)
        assert not Attachment.objects.filter(event=owner).exists()


def test_icon_rejected_before_embedded_pixel_decoding(image_owner, staff_user, monkeypatch):
    owner, field = image_owner
    Settings.objects.filter(facility=owner.facility).update(allowed_file_types="ico")
    buffer = io.BytesIO()
    with Image.new("RGB", (16, 16), "red") as image:
        image.save(buffer, format="ICO", sizes=[(16, 16)])
    upload = SimpleUploadedFile("icon.ico", buffer.getvalue(), content_type="image/vnd.microsoft.icon")
    monkeypatch.setattr(policy, "_MAX_IMAGE_DECODED_BYTES", 400)
    with (
        patch(
            "PIL.ImageFile.ImageFile.load", side_effect=AssertionError("Icon decoded before budget rejection")
        ) as load,
        pytest.raises(ValidationError, match="Speicherbudget"),
    ):
        store_encrypted_file(owner.facility, upload, field, owner, staff_user)
    load.assert_not_called()
    assert upload.tell() == 0


def test_apng_budget_includes_separate_default_image(image_owner, staff_user, monkeypatch):
    owner, field = image_owner
    buffer = io.BytesIO()
    with Image.new("RGB", (10, 10), "red") as default, Image.new("RGB", (10, 10), "blue") as frame:
        default.save(buffer, format="PNG", save_all=True, append_images=[frame], default_image=True)
    upload = SimpleUploadedFile("default.png", buffer.getvalue(), content_type="image/png")
    monkeypatch.setattr(policy, "_MAX_IMAGE_FRAMES", 1)
    with pytest.raises(ValidationError, match="Frames"):
        store_encrypted_file(owner.facility, upload, field, owner, staff_user)
    assert upload.tell() == 0


@pytest.mark.parametrize("fmt", ["PNG", "GIF"])
def test_small_disposal_animation_keeps_frames(image_owner, staff_user, fmt):
    owner, field = image_owner
    buffer = io.BytesIO()
    with Image.new("RGBA", (10, 10), "red") as first, Image.new("RGBA", (10, 10), "blue") as second:
        first.save(buffer, format=fmt, save_all=True, append_images=[second], disposal=2 if fmt == "GIF" else 1)
    extension = fmt.lower()
    upload = SimpleUploadedFile(f"disposal.{extension}", buffer.getvalue(), content_type=f"image/{extension}")
    attachment = store_encrypted_file(owner.facility, upload, field, owner, staff_user)
    with Image.open(io.BytesIO(b"".join(get_decrypted_file_stream(attachment)))) as image:
        assert image.n_frames == 2
        for index in range(image.n_frames):
            image.seek(index)
            image.load()
            assert image.size == (10, 10)


@pytest.mark.parametrize("frame_count", [100, 101])
def test_webp_actual_frame_count_obeys_fixed_limit(image_owner, staff_user, frame_count):
    owner, field = image_owner
    upload = image_upload("WEBP", sizes=((2, 2),) * frame_count)
    if frame_count == 101:
        with (
            patch.object(
                WebPImagePlugin._webp, "WebPAnimDecoder", side_effect=AssertionError("Decoder entered")
            ) as decoder,
            pytest.raises(ValidationError, match="Maximum: 100"),
        ):
            store_encrypted_file(owner.facility, upload, field, owner, staff_user)
        decoder.assert_not_called()
    else:
        attachment = store_encrypted_file(owner.facility, upload, field, owner, staff_user)
        with Image.open(io.BytesIO(b"".join(get_decrypted_file_stream(attachment)))) as image:
            assert image.n_frames == frame_count


@pytest.mark.parametrize("lossless", [False, True])
@pytest.mark.parametrize("metadata", [False, True])
def test_static_webp_remains_valid(image_owner, staff_user, lossless, metadata):
    owner, field = image_owner
    buffer = io.BytesIO()
    with Image.new("RGB", (10, 10), "red") as image:
        image.save(buffer, format="WEBP", lossless=lossless, **({"xmp": b"Private metadata"} if metadata else {}))
    upload = SimpleUploadedFile("static.webp", buffer.getvalue(), content_type="image/webp")
    attachment = store_encrypted_file(owner.facility, upload, field, owner, staff_user)
    stored = b"".join(get_decrypted_file_stream(attachment))
    with Image.open(io.BytesIO(stored)) as image:
        assert image.n_frames == 1
        assert image.size == (10, 10)
        assert "xmp" not in image.info


@pytest.mark.parametrize("bitmap", [False, True])
def test_small_icon_remains_valid(image_owner, staff_user, bitmap):
    owner, field = image_owner
    Settings.objects.filter(facility=owner.facility).update(allowed_file_types="ico")
    buffer = io.BytesIO()
    with Image.new("RGB", (16, 16), "red") as image:
        image.save(buffer, format="ICO", sizes=[(16, 16)], **({"bitmap_format": "bmp"} if bitmap else {}))
    original = buffer.getvalue()
    upload = SimpleUploadedFile("icon.ico", original, content_type="image/vnd.microsoft.icon")
    attachment = store_encrypted_file(owner.facility, upload, field, owner, staff_user)
    assert b"".join(get_decrypted_file_stream(attachment)) == original


@pytest.mark.parametrize("damage", ["riff_size", "chunk_size", "missing_frames", "canvas_mismatch"])
def test_malformed_webp_is_rejected_before_native_decoder(image_owner, staff_user, damage):
    owner, field = image_owner
    upload = image_upload("WEBP")
    data = bytearray(upload.read())
    if damage == "riff_size":
        data[4:8] = (len(data) + 100).to_bytes(4, "little")
    elif damage == "chunk_size":
        data[16:20] = (len(data) + 100).to_bytes(4, "little")
    elif damage == "missing_frames":
        first_frame = data.index(b"ANMF")
        data = data[:first_frame]
        data[4:8] = (len(data) - 8).to_bytes(4, "little")
    else:
        data[24:27] = b"\x00\x00\x00"
    upload = SimpleUploadedFile("broken.webp", bytes(data), content_type="image/webp")
    with (
        patch.object(
            WebPImagePlugin._webp, "WebPAnimDecoder", side_effect=AssertionError("Decoder entered")
        ) as decoder,
        pytest.raises(ValidationError, match="nicht gelesen"),
    ):
        store_encrypted_file(owner.facility, upload, field, owner, staff_user)
    decoder.assert_not_called()
    assert upload.tell() == 0
    assert not Attachment.objects.filter(event=owner).exists()
    assert AuditLog.objects.filter(
        action=AuditLog.Action.SECURITY_VIOLATION, detail__reason="image_undecodable"
    ).exists()


def test_png_conflicting_canvas_is_rejected_before_disposal_allocation(image_owner, staff_user, monkeypatch):
    owner, field = image_owner
    buffer = io.BytesIO()
    with Image.new("RGBA", (10, 10), "red") as first, Image.new("RGBA", (10, 10), "blue") as second:
        first.save(buffer, format="PNG", save_all=True, append_images=[second], disposal=1)
    data = buffer.getvalue()
    # Pillow accepts a second IHDR and uses its larger canvas at opening.
    payload = (100).to_bytes(4, "big") * 2 + data[24:29]
    chunk = b"IHDR" + payload
    duplicate = len(payload).to_bytes(4, "big") + chunk + zlib.crc32(chunk).to_bytes(4, "big")
    upload = SimpleUploadedFile("conflicting.png", data[:33] + duplicate + data[33:], content_type="image/png")
    monkeypatch.setattr(policy, "_MAX_IMAGE_DECODED_BYTES", 3000)
    with (
        patch.object(
            Image.core, "fill", side_effect=AssertionError("Conflicting canvas allocated before rejection")
        ) as fill,
        pytest.raises(ValidationError, match="nicht gelesen"),
    ):
        store_encrypted_file(owner.facility, upload, field, owner, staff_user)
    fill.assert_not_called()
    assert upload.tell() == 0
    assert not Attachment.objects.filter(event=owner).exists()
