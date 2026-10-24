"""Dynamic field contracts for episodes and work items."""

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.forms.config import DocumentTypeForm
from core.models import DocumentType, DocumentTypeField, Episode, FieldTemplate, WorkItem
from core.packages.loader import load_package
from core.packages.schema import parse_package
from core.services.file_vault import is_encrypted_value, safe_decrypt


@pytest.fixture(params=["episode", "workitem"])
def carrier(request, facility, case_open, staff_user, settings):
    settings.ENCRYPTION_KEYS = Fernet.generate_key().decode()
    kind = request.param
    doc_type = DocumentType.objects.create(facility=facility, name=f"Dynamic {kind}", entity_type=kind)
    field = FieldTemplate.objects.create(
        facility=facility, name="Protected detail", slug="protected-detail", field_type="textarea", is_encrypted=True
    )
    assignment = DocumentTypeField.objects.create(document_type=doc_type, field_template=field)
    model = Episode if kind == "episode" else WorkItem
    kwargs = {"case": case_open, "started_at": timezone.localdate()} if kind == "episode" else {"facility": facility}
    return kind, model, kwargs | {"created_by": staff_user, "title": "Dynamic record"}, doc_type, field, assignment


def make_record(carrier, **overrides):
    _kind, model, kwargs, doc_type, field, _assignment = carrier
    return model.objects.create(
        **(kwargs | {"document_type": doc_type, "data_json": {field.slug: "Secret detail"}} | overrides)
    )


@pytest.mark.django_db
def test_entity_choices_package_loader_and_configuration(facility):
    for kind, label in [("episode", "Episode"), ("workitem", "Aufgabe")]:
        assert kind in DocumentType.EntityType.values
        assert (kind, label) in DocumentTypeForm(facility=facility).fields["entity_type"].choices
        package = parse_package(
            {"schema_version": 1, "document_types": [{"slug": kind, "name": kind, "entity_type": kind}]}
        )
        load_package(facility, package)
        assert DocumentType.objects.for_facility(facility).get(slug=kind).entity_type == kind


@pytest.mark.django_db
def test_ciphertext_at_rest_and_partial_save(carrier):
    record = make_record(carrier)
    field = carrier[4]
    record.refresh_from_db()
    stored = record.data_json[field.slug]
    assert is_encrypted_value(stored)
    assert safe_decrypt(stored) == "Secret detail"
    record.title = "Updated title"
    record.data_json = {field.slug: "Unsaved detail"}
    record.save(update_fields=["title"])
    record.refresh_from_db()
    assert record.data_json[field.slug] == stored
    record.save()
    record.refresh_from_db()
    assert record.data_json[field.slug] == stored


@pytest.mark.django_db
@pytest.mark.parametrize("operation", ["clean", "save"])
def test_model_rejects_wrong_entity_type(carrier, operation):
    record = make_record(carrier)
    wrong = DocumentType.objects.create(facility=carrier[3].facility, name="Wrong type", entity_type="client")
    record.document_type = wrong
    with pytest.raises(ValidationError):
        getattr(record, operation)()
