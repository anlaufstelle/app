"""Explicit test setup for the encrypted person and case standard fields."""

from core.models import DocumentType, DocumentTypeField, FieldTemplate
from core.services.file_vault import safe_decrypt


def text_fields(facility, value, *, entity="client", document_type=None, data_json=None):
    """Return typed data suitable for a model constructor or write service."""
    slug = "client-notes" if entity == "client" else "standard-description"
    if document_type is None:
        document_type, _ = DocumentType.objects.for_facility(facility).get_or_create(
            slug=f"test-{entity}-text",
            defaults={"facility": facility, "name": f"Test {entity} text", "entity_type": entity},
        )
    field, _ = FieldTemplate.objects.for_facility(facility).get_or_create(
        slug=slug,
        defaults={
            "facility": facility,
            "name": "Notizen" if entity == "client" else "Beschreibung",
            "field_type": "textarea",
            "is_encrypted": True,
            "sensitivity": "elevated",
        },
    )
    DocumentTypeField.objects.get_or_create(document_type=document_type, field_template=field)
    return {"document_type": document_type, "data_json": {**(data_json or {}), slug: value}}


def set_record_text(record, value):
    """Assign typed text without saving, preserving other dynamic values."""
    fields = text_fields(
        record.facility,
        value,
        entity=record._meta.model_name,
        document_type=record.document_type,
        data_json=record.data_json,
    )
    record.document_type = fields["document_type"]
    record.data_json = fields["data_json"]


def record_text(record):
    """Read text after refresh without hiding an unexpected encrypted value."""
    slug = "client-notes" if record._meta.model_name == "client" else "standard-description"
    return safe_decrypt(record.data_json.get(slug, ""))


def text_path(record):
    """Prepare a typed rectification target and return its public field path."""
    set_record_text(record, record_text(record))
    record.save(update_fields=["document_type", "data_json"])
    return "data_json.client-notes"


def job_text_target(job):
    """Assign the person text schema before validating a CSV mapping."""
    fields = text_fields(job.facility, "", document_type=job.document_type)
    job.document_type = fields["document_type"]
    job.save(update_fields=["document_type"])
    return "data_json.client-notes"
