"""Person and case text is stored exclusively in typed dynamic fields."""

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.models import Case, Client, DocumentType, DocumentTypeField, FieldTemplate, ImportJob
from core.services.client.rectification import rectifiable_fields, request_rectification
from core.services.import_engine import available_targets

pytestmark = pytest.mark.django_db


@pytest.fixture
def fernet_key(settings):
    settings.ENCRYPTION_KEYS = Fernet.generate_key().decode()
    return settings.ENCRYPTION_KEYS


@pytest.fixture(params=["client", "case"])
def typed_text(request, facility):
    entity = request.param
    document_type = DocumentType.objects.create(facility=facility, name=f"{entity} record", entity_type=entity)
    field = FieldTemplate.objects.create(
        facility=facility,
        name=f"{entity} text",
        field_type="textarea",
        sensitivity="elevated",
        is_encrypted=True,
    )
    DocumentTypeField.objects.create(document_type=document_type, field_template=field)
    return entity, document_type, field


def test_create_form_renders_required_default_and_text(client, staff_user, typed_text):
    entity, document_type, field = typed_text
    client.force_login(staff_user)
    response = client.get(reverse(f"core:{entity}_create"))
    assert response.status_code == 200
    form = response.context["form"]
    assert form["document_type"].value() == document_type.pk
    assert form.fields["document_type"].required
    assert field.slug in response.context["data_form"].fields
    assert ("notes" if entity == "client" else "description") not in form.fields


def test_create_and_detail_roundtrip(client, staff_user, client_identified, typed_text, fernet_key):
    entity, document_type, field = typed_text
    client.force_login(staff_user)
    payload = {"document_type": str(document_type.pk), field.slug: "Confidential text"}
    if entity == "client":
        payload.update(pseudonym="New record", contact_stage="identified", age_cluster="unknown")
        model, query = Client, {"pseudonym": "New record"}
    else:
        payload.update(title="New record", client=str(client_identified.pk))
        model, query = Case, {"title": "New record"}
    response = client.post(reverse(f"core:{entity}_create"), payload)
    assert response.status_code == 302
    record = model.objects.get(**query)
    record.refresh_from_db()
    assert record.data_json[field.slug] != "Confidential text"
    detail = client.get(reverse(f"core:{entity}_detail", args=[record.pk]))
    assert "Confidential text" in detail.content.decode()


def test_rectification_excludes_untyped_notes(client_identified, staff_user):
    paths = [path for path, _label in rectifiable_fields(client_identified, staff_user)]
    assert paths == ["pseudonym", "age_cluster"]
    with pytest.raises(ValidationError):
        request_rectification(client_identified, staff_user, "notes", "Text", "Correction")
    with pytest.raises(ValidationError):
        request_rectification(client_identified, staff_user, "data_json.client-notes", "Text", "Correction")


def test_import_has_no_untyped_notes_target(facility):
    job = ImportJob(facility=facility, target_type="client")
    assert "notes" not in [target.key for target in available_targets(job)]
