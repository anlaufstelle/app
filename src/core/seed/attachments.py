"""File attachments: encrypted dummy files attached to counseling events.

Owner-Generik (M24, Refs #1738 / ADR-039): Seit dem Umbau haengt ein Anhang an
einem Ereignis, einer **Person** oder einem **Fall**. Der Seed erzeugte lange
nur ereignis-eigene Anhaenge — der Live-AuthZ-Audit
(``tests/e2e/test_authz_audit.py``) fand fuer die beiden neuen Download-Routen
folglich nichts zu ernten und schrieb still SETUP-ERROR-Zellen. Deshalb legt
:func:`attach_files_to_client_and_case` je Einrichtung einen personen- und
einen fall-eigenen Beispielanhang an.
"""

import random
import struct
import zlib

from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import (
    Attachment,
    Case,
    Client,
    DocumentType,
    DocumentTypeField,
    Event,
    Facility,
    FieldTemplate,
    User,
)
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

    # Idempotenz-Guard (Refs #1784): ohne ihn zieht ein erneuter Seed-Lauf
    # (ohne --flush) sein Kontingent erneut aus den Beratungs-Events OHNE
    # Anhang -- die Auswahl unten filtert zwar "noch ohne Anhang", das gilt
    # aber je Lauf, nicht je Einrichtung/Kategorie. Ein zweiter Lauf haengte
    # so immer weitere Anhaenge an bislang unberuehrte Events. Muster wie
    # attach_files_to_client_and_case: EIN Existenz-Check je Einrichtung/
    # Kategorie statt einer Ziel-Quote je Lauf.
    if Attachment.objects.filter(facility=facility, event__document_type__system_type="counseling").exists():
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


# --- Owner-generische Beispielanhaenge (M24, Refs #1738) --------------------

#: Dokumentationstypen der beiden Beispiel-Anhaenge. Bewusst NUR im Seed
#: angelegt und nicht im Standardpaket (``core/packages/default.yaml``): das
#: Paket ist der ausgelieferte Katalog jeder Einrichtung, hier geht es um
#: Demo-/Dev-Daten. Die Werte spiegeln die Unit-Fixtures
#: ``doc_type_client_profile``/``doc_type_case_form`` (NORMAL, damit die
#: Assistenz den Personen-Download ueberhaupt sehen darf).
CLIENT_DOC_TYPE_NAME = "Personenakte"
CASE_DOC_TYPE_NAME = "Fallakte"

#: Geteilte FILE-Feldvorlage beider Typen. ``anhang`` traegt im Standardpaket
#: bewusst eine LEERE Sensitivitaet (erbt vom Typ) — mit ``scan-bescheid``
#: (``high``) waere der Download fuer Fachkraft/Assistenz 403 und die
#: Audit-Zellen der neuen Routen waeren erneut nichtssagend.
OWNER_FILE_TEMPLATE_SLUG = "anhang"

#: Fester Dummy-Inhalt statt :func:`generate_dummy_file`: Der Seed laeuft unter
#: ``random.seed(42)``: jeder zusaetzliche RNG-Zugriff verschoebe den Strom
#: aller NACHFOLGENDEN Seed-Schritte (Episoden, Ziele, Activities).
_STATIC_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
    b"0000000058 00000 n \n0000000115 00000 n \n"
    b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF"
)


def _owner_doc_type(facility: Facility, name: str, entity_type: str, field_template: FieldTemplate) -> DocumentType:
    """Dokumentationstyp mit genau der FILE-Feldvorlage (idempotent)."""
    doc_type, _created = DocumentType.objects.get_or_create(
        facility=facility,
        name=name,
        defaults={
            "category": DocumentType.Category.ADMIN,
            "entity_type": entity_type,
            "sensitivity": DocumentType.Sensitivity.NORMAL,
            "icon": "paperclip",
            "color": "gray",
        },
    )
    DocumentTypeField.objects.get_or_create(
        document_type=doc_type,
        field_template=field_template,
        defaults={"sort_order": 0},
    )
    return doc_type


def _attach_to_owner(facility: Facility, owner, field_template: FieldTemplate, user: User, filename: str) -> None:
    """Einen Anhang am Owner ablegen und den Stufe-B-Marker setzen."""
    uploaded = SimpleUploadedFile(filename, _STATIC_PDF, content_type="application/pdf")
    attachment = store_encrypted_file(facility, uploaded, field_template, owner, user)
    owner.data_json = dict(owner.data_json or {})
    owner.data_json[field_template.slug] = {
        "__files__": True,
        "entries": [{"id": str(attachment.pk), "sort": 0}],
    }
    owner.save(update_fields=["data_json"])


def attach_files_to_client_and_case(facility: Facility, users: list[User]) -> int:
    """Je einen personen- und fall-eigenen Beispielanhang anlegen (Refs #1738).

    Ohne diese Zeilen ist die owner-generische Haelfte von ADR-039 im Seed
    unsichtbar: die Datei-Uebersicht zeigt nur Ereignis-Zeilen und der
    Live-AuthZ-Audit hat fuer ``client_attachment_download`` /
    ``case_attachment_download`` nichts zu ernten.

    Idempotent ueber die Existenz-Pruefung je Owner-Art (ein zweiter Lauf legt
    nichts nach). Bewusst OHNE ``random``-Zugriff — siehe ``_STATIC_PDF``.

    Returns: Anzahl der angelegten Anhaenge (0-2).
    """
    field_template = FieldTemplate.objects.filter(facility=facility, slug=OWNER_FILE_TEMPLATE_SLUG).first()
    if field_template is None:
        return 0

    staff_users = [u for u in users if u.role in (User.Role.STAFF, User.Role.LEAD)] or users
    if not staff_users:
        return 0
    author = staff_users[0]

    created = 0
    if not Attachment.objects.filter(facility=facility, client__isnull=False).exists():
        person = Client.objects.filter(facility=facility, is_deleted=False).order_by("id").first()
        if person is not None:
            person.document_type = _owner_doc_type(
                facility, CLIENT_DOC_TYPE_NAME, DocumentType.EntityType.CLIENT, field_template
            )
            person.save(update_fields=["document_type"])
            _attach_to_owner(facility, person, field_template, author, "Personenakte.pdf")
            created += 1

    if not Attachment.objects.filter(facility=facility, case__isnull=False).exists():
        case = Case.objects.filter(facility=facility, is_deleted=False).order_by("id").first()
        if case is not None:
            case.document_type = _owner_doc_type(
                facility, CASE_DOC_TYPE_NAME, DocumentType.EntityType.CASE, field_template
            )
            case.save(update_fields=["document_type"])
            _attach_to_owner(facility, case, field_template, author, "Fallakte.pdf")
            created += 1

    return created
