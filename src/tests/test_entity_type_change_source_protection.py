"""Type changes must respect the protection of the original stored values."""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Episode, FieldTemplate, WorkItem
from core.services.case.episodes import episode_basis_token
from core.services.file_vault import is_encrypted_value, safe_decrypt

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]
SECRET = "Restricted original content"


@pytest.fixture(params=["episode", "workitem", "client", "case"])
def protected_record(request, facility, staff_user, case_open, client_identified):
    kind = request.param
    source = DocumentType.objects.create(
        facility=facility, name="Restricted source", entity_type=kind, sensitivity="high"
    )
    target = DocumentType.objects.create(
        facility=facility, name="Visible target", entity_type=kind, sensitivity="elevated"
    )
    field = FieldTemplate.objects.create(
        facility=facility,
        name="Shared detail",
        slug="shared-detail",
        field_type="textarea",
        sensitivity="elevated",
        is_encrypted=True,
    )
    for document_type in (source, target):
        DocumentTypeField.objects.create(document_type=document_type, field_template=field)
    if kind in {"client", "case"}:
        record = client_identified if kind == "client" else case_open
        record.document_type = source
        record.data_json = {field.slug: SECRET}
        record.save(update_fields=["document_type", "data_json"])
    else:
        model = Episode if kind == "episode" else WorkItem
        context = (
            {"case": case_open, "started_at": timezone.localdate()} if kind == "episode" else {"facility": facility}
        )
        record = model.objects.create(
            **context,
            created_by=staff_user,
            title="Visible metadata",
            document_type=source,
            data_json={field.slug: SECRET},
        )
    record.refresh_from_db()
    assert is_encrypted_value(record.data_json[field.slug])
    return kind, record, source, target, field


def update_request(kind, record):
    kwargs = {"pk": record.pk}
    payload = {"expected_updated_at": record.updated_at.isoformat()} if kind != "episode" else {}
    if kind == "client":
        payload.update(pseudonym=record.pseudonym, contact_stage=record.contact_stage, age_cluster=record.age_cluster)
    else:
        payload["title"] = record.title
    if kind == "episode":
        kwargs["case_pk"] = record.case_id
        payload.update(started_at=record.started_at.isoformat(), basis_token=episode_basis_token(record))
    elif kind == "workitem":
        payload.update(item_type="task", priority="normal")
    return reverse(f"core:{kind}_update", kwargs=kwargs), payload


def partial_request(kind, record, document_type):
    key = kind if kind in {"client", "case"} else "record"
    return reverse(f"core:{kind}_fields_partial"), {key: str(record.pk), "document_type": str(document_type.pk)}


def test_partial_does_not_reveal_hidden_source_values(client, staff_user, protected_record):
    kind, record, _source, target, _field = protected_record
    client.force_login(staff_user)
    url, query = partial_request(kind, record, target)
    response = client.get(url, query)
    assert response.status_code == 200
    assert SECRET not in response.content.decode()


@pytest.mark.parametrize("confirmed", [False, True])
@pytest.mark.parametrize("submitted", [False, True])
def test_update_rejects_relabelling_hidden_source_values(client, staff_user, protected_record, confirmed, submitted):
    kind, record, source, target, field = protected_record
    original = dict(record.data_json)
    client.force_login(staff_user)
    url, payload = update_request(kind, record)
    payload["document_type"] = str(target.pk)
    if confirmed:
        payload["confirm_data_loss"] = "1"
    if submitted:
        payload[field.slug] = "New visible input"
    response = client.post(url, payload)
    assert response.status_code == (302 if kind in {"client", "case"} else 200)
    assert SECRET not in response.content.decode()
    record.refresh_from_db()
    assert record.document_type_id == source.pk
    assert record.data_json == original


def test_same_type_partial_hides_fields_above_the_actor_level(client, staff_user, protected_record):
    kind, record, source, _target, field = protected_record
    source.sensitivity = "elevated"
    source.save()
    field.sensitivity = "high"
    field.save()
    client.force_login(staff_user)
    url, query = partial_request(kind, record, source)
    response = client.get(url, query)
    assert response.status_code == 200
    assert SECRET not in response.content.decode()
    assert f'name="{field.slug}"' not in response.content.decode()


@pytest.mark.parametrize("protected_record", ["episode", "workitem"], indirect=True)
def test_authorized_actor_keeps_shared_values_on_type_change(client, lead_user, protected_record):
    kind, record, _source, target, field = protected_record
    client.force_login(lead_user)
    url, query = partial_request(kind, record, target)
    response = client.get(url, query)
    assert response.status_code == 200
    assert SECRET in response.content.decode()
    url, payload = update_request(kind, record)
    payload["document_type"] = str(target.pk)
    assert client.post(url, payload).status_code == 302
    record.refresh_from_db()
    assert record.document_type_id == target.pk
    assert safe_decrypt(record.data_json[field.slug]) == SECRET
    assert is_encrypted_value(record.data_json[field.slug])


def test_event_update_does_not_accept_a_document_type_change(client, lead_user, sample_event, facility):
    original_type = sample_event.document_type_id
    target = DocumentType.objects.create(facility=facility, name="Other event type")
    client.force_login(lead_user)
    response = client.post(
        reverse("core:event_update", args=[sample_event.pk]),
        {
            "document_type": str(target.pk),
            "notiz": "Updated event value",
            "dauer": "15",
            "expected_updated_at": sample_event.updated_at.isoformat(),
        },
    )
    assert response.status_code == 302
    sample_event.refresh_from_db()
    assert sample_event.document_type_id == original_type
    assert sample_event.data_json["notiz"] == "Updated event value"
