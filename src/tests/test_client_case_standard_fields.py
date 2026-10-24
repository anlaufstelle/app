"""Person and case text is stored exclusively in typed dynamic fields."""

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.models import Case, Client, DocumentType, DocumentTypeField, FieldTemplate, ImportJob
from core.services.client.export import export_client_data, render_client_export_html
from core.services.client.rectification import rectifiable_fields, request_rectification
from core.services.import_engine import available_targets
from core.services.system.facility_export import build_facility_export
from core.services.system.offline import build_client_offline_bundle

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


@pytest.fixture
def text_record(typed_text, facility, staff_user, client_identified, fernet_key):
    entity, document_type, field = typed_text
    if entity == "client":
        record = client_identified
    else:
        record = Case.objects.create(facility=facility, client=client_identified, title="Case", created_by=staff_user)
    record.document_type = document_type
    record.data_json = {field.slug: "Exported confidential text"}
    record.save()
    record.refresh_from_db()
    return entity, record, field


def test_subject_export_has_typed_text_and_no_legacy_keys(text_record, client_identified, facility, lead_user):
    entity, record, field = text_record
    data = export_client_data(client_identified, facility, lead_user)
    entry = data["client"] if entity == "client" else data["cases"][0]
    assert ("notes" if entity == "client" else "description") not in entry
    assert any(
        item["slug"] == field.slug and item["value"] == "Exported confidential text" for item in entry["dynamic_data"]
    )
    html = render_client_export_html(client_identified, facility, lead_user)
    assert "Exported confidential text" in html


def test_facility_export_schema_and_typed_text(text_record, facility):
    entity, record, field = text_record
    data = build_facility_export(facility)
    assert data["schema_version"] == 2
    entry = data["entities"]["clients" if entity == "client" else "cases"][0]
    assert ("notes" if entity == "client" else "description") not in entry
    assert entry["data_json"][field.slug] == "Exported confidential text"


def test_offline_schema_and_role_filtered_text(text_record, facility, client_identified, staff_user, assistant_user):
    entity, record, field = text_record
    for user in (staff_user, assistant_user):
        data = build_client_offline_bundle(user, facility, client_identified)
        assert data["schema_version"] == 3
        entry = data["client"] if entity == "client" else data["cases"][0]
        assert ("notes" if entity == "client" else "description") not in entry
        assert entry["data_fields"] == ({field.slug: "Exported confidential text"} if user == staff_user else {})


@pytest.mark.parametrize("model,legacy_field", [(Client, "notes"), (Case, "description")])
def test_models_have_no_legacy_text_column(model, legacy_field):
    assert legacy_field not in {field.name for field in model._meta.get_fields()}


def test_offline_normal_fields_follow_entity_access(text_record, facility, client_identified, assistant_user):
    entity, record, field = text_record
    field.sensitivity = "normal"
    field.save(update_fields=["sensitivity"])
    bundle = build_client_offline_bundle(assistant_user, facility, client_identified)
    entry = bundle["client"] if entity == "client" else bundle["cases"][0]
    assert entry["data_fields"] == ({field.slug: "Exported confidential text"} if entity == "client" else {})


def test_offline_person_standard_label_uses_translation(facility, client_identified, staff_user, monkeypatch):
    from core.packages import catalog
    from tests._standard_text import set_record_text

    set_record_text(client_identified, "Text")
    client_identified.save(update_fields=["document_type", "data_json"])
    monkeypatch.setattr(catalog.translation, "gettext", lambda text: "Translated notes" if text == "Notizen" else text)
    bundle = build_client_offline_bundle(staff_user, facility, client_identified)
    assert bundle["client"]["field_metadata"][0]["name"] == "Translated notes"
