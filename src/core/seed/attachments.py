"""File attachments: encrypted dummy files attached to counseling events."""

import random
import struct
import zlib

from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import Event, Facility, FieldTemplate, User
from core.services.file_vault import store_encrypted_file


def generate_dummy_file() -> SimpleUploadedFile:
    """Generate an in-memory dummy file (PDF/JPEG/PNG)."""
    choice = random.choice(["pdf", "jpeg", "png"])

    if choice == "pdf":
        content = (
            b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
            b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
            b"0000000058 00000 n \n0000000115 00000 n \n"
            b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF"
        )
        name = f"Bescheid-{random.randint(1000, 9999)}.pdf"
        mime = "application/pdf"
    elif choice == "jpeg":
        # Minimal valid JPEG: SOI + APP0 (JFIF) + SOF0 + SOS + EOI
        content = (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
            b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
            b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342"
            b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
            b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00"
            b"\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
            b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00T\xdb\x9e\xa7(\xff\xd9"
        )
        name = f"Scan-{random.randint(1000, 9999)}.jpg"
        mime = "image/jpeg"
    else:
        # Minimal valid 1x1 white PNG
        ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        ihdr_crc = struct.pack(">I", zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF)
        raw_pixel = b"\x00\xff\xff\xff"  # filter-byte + RGB
        idat_data = zlib.compress(raw_pixel)
        idat_crc = struct.pack(">I", zlib.crc32(b"IDAT" + idat_data) & 0xFFFFFFFF)
        iend_crc = struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF)
        content = (
            b"\x89PNG\r\n\x1a\n"
            + struct.pack(">I", len(ihdr_data))
            + b"IHDR"
            + ihdr_data
            + ihdr_crc
            + struct.pack(">I", len(idat_data))
            + b"IDAT"
            + idat_data
            + idat_crc
            + struct.pack(">I", 0)
            + b"IEND"
            + iend_crc
        )
        name = f"Dokument-{random.randint(1000, 9999)}.png"
        mime = "image/png"

    return SimpleUploadedFile(name, content, content_type=mime)


def attach_files_to_counseling_events(facility: Facility, users: list[User], cfg: dict) -> int:
    """Attach dummy encrypted files to a portion of counseling events.

    Returns the number of attachments created.
    """
    ratio = cfg.get("attachment_ratio", 0)
    if ratio <= 0:
        return 0

    # Find counseling events without existing attachments
    counseling_events = list(
        Event.objects.filter(
            facility=facility,
            document_type__system_type="counseling",
        ).exclude(
            attachments__isnull=False,
        )
    )
    if not counseling_events:
        return 0

    # Find the "scan-bescheid" field template
    field_template = FieldTemplate.objects.filter(
        slug="scan-bescheid",
        document_type_fields__document_type__facility=facility,
    ).first()
    if not field_template:
        return 0

    staff_users = [u for u in users if u.role in (User.Role.STAFF, User.Role.LEAD)]
    if not staff_users:
        staff_users = users

    count = max(1, int(len(counseling_events) * ratio))
    selected = random.sample(counseling_events, min(count, len(counseling_events)))

    created = 0
    for event in selected:
        uploaded = generate_dummy_file()
        attachment = store_encrypted_file(facility, uploaded, field_template, event, random.choice(staff_users))
        event.data_json["scan-bescheid"] = {"__file__": True, "attachment_id": str(attachment.pk)}
        event.save(update_fields=["data_json"])
        created += 1

    return created
