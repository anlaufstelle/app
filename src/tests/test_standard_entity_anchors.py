"""Entity services enforce document type defaults and required selections."""

import pytest
from django.core.exceptions import ValidationError

from core.models import DocumentType, DocumentTypeField, FieldTemplate
from core.packages.catalog import parse_catalog_package
from core.packages.loader import load_package
from core.services.case.cases import create_case, update_case
from core.services.case.episodes import create_episode, episode_basis_token, update_episode
from core.services.case.workitems import create_workitem, update_workitem
from core.services.client import create_client, update_client
from core.services.dynamic_data import build_dynamic_data_context, count_filled_dynamic_values
from core.services.file_vault import is_encrypted_value, safe_decrypt


@pytest.fixture(params=["client", "case", "episode", "workitem"])
def entity(request, facility, staff_user, case_open):
    kind = request.param

    def create(**data):
        if kind == "client":
            return create_client(facility, staff_user, pseudonym="Anchor client", **data)
        if kind == "case":
            return create_case(facility, staff_user, case_open.client, title="Anchor case", **data)
        if kind == "episode":
            return create_episode(case_open, staff_user, title="Anchor episode", **data)
        return create_workitem(facility, staff_user, title="Anchor task", **data)

    def update(record, **data):
        if kind == "client":
            return update_client(record, staff_user, **data)
        if kind == "case":
            return update_case(record, staff_user, **data)
        if kind == "episode":
            return update_episode(record, staff_user, basis_token=episode_basis_token(record), **data)
        return update_workitem(record, staff_user, expected_updated_at=record.updated_at, **data)

    return kind, create, update


def make_type(facility, kind, name="Standard", **kwargs):
    return DocumentType.objects.create(facility=facility, entity_type=kind, name=name, **kwargs)


@pytest.mark.django_db
def test_no_active_type_allows_empty_data_but_never_untyped_payload(entity, facility):
    kind, create, update = entity
    make_type(facility, kind, is_active=False)
    record = create()
    assert record.document_type is None
    assert record.data_json == {}
    with pytest.raises(ValidationError):
        update(record, data_json={"secret": "Untyped text"})
    with pytest.raises(ValidationError):
        create(data_json={"secret": "Untyped text"})


@pytest.mark.django_db
@pytest.mark.parametrize("explicit_none", [False, True])
def test_single_active_type_is_selected_on_create(entity, facility, explicit_none):
    kind, create, _update = entity
    expected = make_type(facility, kind)
    make_type(facility, kind, name="Inactive", is_active=False)
    make_type(facility, "event", name="Other entity")
    record = create(**({"document_type": None} if explicit_none else {}))
    record.refresh_from_db()
    assert record.document_type == expected


@pytest.mark.django_db
@pytest.mark.parametrize("operation", ["create", "update"])
def test_multiple_active_types_require_explicit_selection(entity, facility, operation):
    kind, create, update = entity
    record = create() if operation == "update" else None
    make_type(facility, kind)
    selected = make_type(facility, kind, name="Alternative")
    with pytest.raises(ValidationError) as error:
        create() if record is None else update(record, data_json={})
    assert "document_type" in error.value.message_dict
    assert "Dokumentationstyp" in str(error.value)
    result = create(document_type=selected) if record is None else update(record, document_type=selected)
    result.refresh_from_db()
    assert result.document_type == selected


@pytest.mark.django_db
def test_update_uses_single_default_for_previously_untyped_record(entity, facility):
    kind, create, update = entity
    record = create()
    expected = make_type(facility, kind)
    update(record, data_json={})
    record.refresh_from_db()
    assert record.document_type == expected


@pytest.mark.django_db
@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("invalid", ["entity", "facility"])
def test_services_reject_foreign_type(entity, facility, second_facility, operation, invalid):
    kind, create, update = entity
    record = create() if operation == "update" else None
    wrong = make_type(second_facility if invalid == "facility" else facility, "event" if invalid == "entity" else kind)
    with pytest.raises(ValidationError):
        create(document_type=wrong) if record is None else update(record, document_type=wrong)


@pytest.mark.django_db
def test_existing_selection_survives_new_alternatives_and_rejects_removal(entity, facility):
    kind, create, update = entity
    selected = make_type(facility, kind)
    record = create(document_type=selected)
    make_type(facility, kind, name="Alternative")
    update(record, data_json={})
    record.refresh_from_db()
    assert record.document_type == selected
    with pytest.raises(ValidationError):
        update(record, document_type=None, data_json={})
    record.refresh_from_db()
    assert record.document_type == selected


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
def test_standard_field_role_matrix_and_type_switch(entity, facility, assistant_user, staff_user):
    kind, create, update = entity
    load_package(facility, parse_catalog_package("default"))
    standard = DocumentType.objects.for_facility(facility).get(entity_type=kind)
    field = standard.fields.get().field_template
    record = create(document_type=standard, data_json={field.slug: "Protected standard text"})
    record.refresh_from_db()
    assert is_encrypted_value(record.data_json[field.slug])
    assert count_filled_dynamic_values(record.data_json) == 1
    for user, restricted in [(assistant_user, kind != "workitem"), (staff_user, False)]:
        context = build_dynamic_data_context(standard, record.data_json, user)
        entry = context["dynamic_fields"][0]
        assert entry["restricted"] is restricted
        assert (entry["value"] == "Protected standard text") is not restricted
    alternative = make_type(facility, kind, name="Alternative")
    replacement = FieldTemplate.objects.create(
        facility=facility, name="Replacement", slug="replacement", field_type="textarea", is_encrypted=True
    )
    DocumentTypeField.objects.create(document_type=alternative, field_template=replacement)
    consent = {"confirm_data_loss": True} if kind in {"episode", "workitem"} else {}
    if kind in {"episode", "workitem"}:
        with pytest.raises(ValidationError):
            update(record, document_type=alternative, data_json={field.slug: "Old", replacement.slug: "New"})
        record.refresh_from_db()
        assert record.document_type == standard
        assert safe_decrypt(record.data_json[field.slug]) == "Protected standard text"
    update(record, document_type=alternative, data_json={field.slug: "Old", replacement.slug: "New"}, **consent)
    record.refresh_from_db()
    assert set(record.data_json) == {replacement.slug}
    assert is_encrypted_value(record.data_json[replacement.slug])
    assert safe_decrypt(record.data_json[replacement.slug]) == "New"
    DocumentType.objects.for_facility(facility).filter(entity_type=kind).update(is_active=False)
    update(record, document_type=None, data_json={}, **consent)
    record.refresh_from_db()
    assert record.document_type is None
    assert record.data_json == {}


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
@pytest.mark.parametrize("kind", ["client", "case"])
def test_rejected_type_removal_keeps_attachments(client, facility, staff_user, case_open, kind):
    from django.urls import reverse

    from tests.test_attachment_lock_order import _attach, _doc_type

    doc_type, file_field, _text_field = _doc_type(facility, entity_type=kind, name="Protected owner")
    if kind == "client":
        record = create_client(facility, staff_user, pseudonym="Protected owner", document_type=doc_type)
        payload = {
            "pseudonym": record.pseudonym,
            "contact_stage": record.contact_stage,
            "age_cluster": record.age_cluster,
        }
    else:
        record = create_case(facility, staff_user, case_open.client, title="Protected owner", document_type=doc_type)
        payload = {"title": record.title, "client": str(record.client_id), "lead_user": ""}
    _attach(facility, record, file_field, staff_user)
    before = set(record.attachments.filter(deleted_at__isnull=True).values_list("pk", flat=True))
    assert before
    client.force_login(staff_user)
    response = client.post(reverse(f"core:{kind}_update", kwargs={"pk": record.pk}), payload | {"document_type": ""})
    assert response.status_code == 200
    record.refresh_from_db()
    assert record.document_type == doc_type
    assert set(record.attachments.filter(deleted_at__isnull=True).values_list("pk", flat=True)) == before
