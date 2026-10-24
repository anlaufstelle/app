"""Missing type keys retain compatibility with stored offline task payloads."""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import DocumentType, Episode, WorkItem
from core.packages.catalog import parse_catalog_package
from core.packages.loader import load_package
from core.services.case.episodes import episode_basis_token
from core.services.file_vault import is_encrypted_value, safe_decrypt
from tests._factories import make_document_type


@pytest.fixture(params=["workitem", "episode"])
def missing_type_endpoint(request, client, facility, staff_user, case_open):
    kind = request.param
    client.force_login(staff_user)
    load_package(facility, parse_catalog_package("default"))
    selected = DocumentType.objects.for_facility(facility).get(entity_type=kind)
    field = selected.fields.get().field_template
    if kind == "workitem":
        # Mirror emptyWorkItemValues + workItemFormData in offline-form-fields.js.
        payload = {
            "item_type": "task",
            "title": "Offline task",
            "description": "Offline description",
            "priority": "normal",
            "due_date": "",
            "remind_at": "",
            "recurrence": "none",
            "assigned_to": "",
        }
        headers = {"HTTP_ACCEPT": "application/json", "HTTP_X_OFFLINE_REPLAY": "2"}
        model, create_kwargs, url_kwargs = WorkItem, {"facility": facility}, {}
    else:
        payload = {"title": "Episode without type key", "started_at": timezone.localdate().isoformat()}
        headers = {}
        model, create_kwargs, url_kwargs = (
            Episode,
            {"case": case_open, "started_at": timezone.localdate()},
            {"case_pk": case_open.pk},
        )
    return kind, selected, field, payload, headers, model, create_kwargs, url_kwargs


@pytest.mark.django_db
@pytest.mark.parametrize("ambiguous", [False, True])
def test_missing_type_create_uses_only_unambiguous_package_default(client, facility, missing_type_endpoint, ambiguous):
    kind, selected, _field, payload, headers, model, _create_kwargs, url_kwargs = missing_type_endpoint
    if ambiguous:
        make_document_type(facility=facility, entity_type=kind, name="Alternative")
    response = client.post(reverse(f"core:{kind}_create", kwargs=url_kwargs), payload, **headers)
    if ambiguous:
        assert response.status_code == (422 if kind == "workitem" else 200)
        errors = response.json()["errors"] if kind == "workitem" else response.context["form"].errors
        assert "document_type" in errors
        assert not model.objects.filter(title=payload["title"]).exists()
    else:
        assert response.status_code == (200 if kind == "workitem" else 302)
        record = model.objects.get(title=payload["title"])
        assert record.document_type == selected
        if kind == "workitem":
            assert response["X-Offline-Replay-Result"] == "saved"
            assert response.json()["pk"] == str(record.pk)


@pytest.mark.django_db
@pytest.mark.parametrize("additional_type", [False, True])
def test_explicit_empty_type_remains_invalid(client, facility, missing_type_endpoint, additional_type):
    kind, _selected, _field, payload, headers, model, _create_kwargs, url_kwargs = missing_type_endpoint
    if additional_type:
        make_document_type(facility=facility, entity_type=kind, name="Alternative")
    response = client.post(
        reverse(f"core:{kind}_create", kwargs=url_kwargs), payload | {"document_type": ""}, **headers
    )
    assert response.status_code == (422 if kind == "workitem" else 200)
    errors = response.json()["errors"] if kind == "workitem" else response.context["form"].errors
    assert "document_type" in errors
    assert not model.objects.filter(title=payload["title"]).exists()


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
@pytest.mark.parametrize("assigned_active", [True, False])
def test_missing_type_update_keeps_assignment_and_encrypted_data(
    client, facility, staff_user, missing_type_endpoint, assigned_active
):
    kind, selected, field, payload, headers, model, create_kwargs, url_kwargs = missing_type_endpoint
    record = model.objects.create(
        created_by=staff_user,
        title="Existing record",
        document_type=selected,
        data_json={field.slug: "Stored protected text"},
        **create_kwargs,
    )
    record.refresh_from_db()
    original_data = dict(record.data_json)
    assert is_encrypted_value(original_data[field.slug])
    selected.is_active = assigned_active
    selected.save(update_fields=["is_active"])
    make_document_type(facility=facility, entity_type=kind, name="Alternative")
    if kind == "workitem":
        payload |= {"expected_updated_at": record.updated_at.isoformat()}
    else:
        payload |= {"basis_token": episode_basis_token(record)}
    response = client.post(reverse(f"core:{kind}_update", kwargs=url_kwargs | {"pk": record.pk}), payload, **headers)
    assert response.status_code == (200 if kind == "workitem" else 302)
    if kind == "workitem":
        assert response["X-Offline-Replay-Result"] == "saved"
    record.refresh_from_db()
    assert record.document_type == selected
    assert record.title == payload["title"]
    assert record.data_json == original_data
    assert safe_decrypt(record.data_json[field.slug]) == "Stored protected text"
