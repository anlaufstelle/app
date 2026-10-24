"""Build typed test details and inspect their decrypted values."""

from core.models import DocumentType, DocumentTypeField, Facility, FieldTemplate
from core.services.file_vault import safe_decrypt


def entity_details(owner, kind, value):
    """Return real schema-backed keyword arguments for an episode or task."""
    facility = owner if isinstance(owner, Facility) else owner.facility
    doc_type, _ = DocumentType.objects.for_facility(facility).get_or_create(
        slug=f"test-details-{kind}",
        defaults={"facility": facility, "entity_type": kind, "name": f"Test {kind} details"},
    )
    field, _ = FieldTemplate.objects.for_facility(facility).get_or_create(
        slug=f"test-details-{kind}",
        defaults={
            "facility": facility,
            "name": f"{kind.title()} details",
            "field_type": "textarea",
            "is_encrypted": True,
        },
    )
    DocumentTypeField.objects.get_or_create(document_type=doc_type, field_template=field)
    return {"document_type": doc_type, "data_json": {field.slug: value}}


def read_details(record):
    """Return the fixture's single detail value after decryption."""
    return safe_decrypt(next(iter(record.data_json.values()), ""))


def assign_details(record, value):
    """Prepare typed model fields for tests that deliberately save directly."""
    kind = record._meta.model_name
    for key, payload in entity_details(record, kind, value).items():
        setattr(record, key, payload)


def detail_form_values(record):
    """Flatten the record's schema and decrypted values for an editor POST."""
    values = {slug: safe_decrypt(value) for slug, value in record.data_json.items()}
    if record.document_type_id is not None:
        values["document_type"] = str(record.document_type_id)
    return values
