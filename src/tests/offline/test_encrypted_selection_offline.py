"""Encrypted selections keep their type across public offline endpoints."""

import json
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, DocumentTypeField, FieldTemplate
from core.services.events import context as event_context
from core.services.file_vault import encrypt_field, is_encrypted_value, safe_decrypt
from core.services.system import offline
from tests._factories import make_document_type, make_event


@pytest.fixture
def encrypted_offline_event(facility, client_identified, staff_user, _encryption_key):
    document_type = make_document_type(facility=facility, name="Offline selection")
    template = FieldTemplate.objects.create(
        facility=facility,
        name="Private selection",
        slug="selection",
        field_type=FieldTemplate.FieldType.MULTI_SELECT,
        is_encrypted=True,
        options_json=[
            {"slug": "a", "label": "First label"},
            {"slug": "b", "label": "Second label"},
            {"slug": "c", "label": "Inactive label", "is_active": False},
            {"slug": "comma,value", "label": "Comma, label"},
            {"slug": "quote'value", "label": "Quoted 'label'"},
        ],
    )
    DocumentTypeField.objects.create(document_type=document_type, field_template=template)
    event = make_event(
        facility=facility,
        client=client_identified,
        document_type=document_type,
        occurred_at=timezone.now(),
        data_json={template.slug: ["a", "b"]},
        created_by=staff_user,
    )
    return event, template


def _bundle(client, event):
    response = client.get(reverse("core:offline_bundle", kwargs={"pk": event.client_id}))
    assert response.status_code == 200
    return response.json()


@pytest.mark.django_db
@pytest.mark.parametrize("values", [[], ["a"], ["b", "a"], ["a", "b", "a"], ["comma,value", "quote'value"]])
def test_bundle_view_preserves_encrypted_selection_arrays(client, staff_user, encrypted_offline_event, values):
    event, template = encrypted_offline_event
    event.data_json = {template.slug: values}
    event.save()
    event.refresh_from_db()
    stored = event.data_json[template.slug]
    assert is_encrypted_value(stored) if values else stored == []
    client.force_login(staff_user)

    payload = _bundle(client, event)
    result = payload["events"][0]["data_fields"][template.slug]

    assert result == values
    assert type(result) is list
    event.refresh_from_db()
    assert event.data_json[template.slug] == stored
    audit = AuditLog.objects.get(action=AuditLog.Action.OFFLINE_BUNDLE_READ)
    audit_text = json.dumps(audit.detail)
    assert "First label" not in audit_text
    assert "quote'value" not in audit_text
    assert "__encrypted__" not in audit_text


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["staff_user", "lead_user"])
@pytest.mark.parametrize("endpoint", ["bundle", "conflict"])
def test_bundle_view_gates_attached_and_orphan_selections_before_decrypt(
    client, request, facility, encrypted_offline_event, role, endpoint
):
    event, template = encrypted_offline_event
    hidden_templates = []
    for slug in ("attached-secret", "orphan-secret"):
        hidden = FieldTemplate.objects.create(
            facility=facility,
            name=f"Restricted selection {slug}",
            slug=slug,
            field_type=FieldTemplate.FieldType.MULTI_SELECT,
            sensitivity="high",
            options_json=[{"slug": "secret", "label": "Restricted label"}],
        )
        hidden_templates.append(hidden)
    DocumentTypeField.objects.create(document_type=event.document_type, field_template=hidden_templates[0])
    event.data_json.update({field.slug: encrypt_field(["secret"]) for field in hidden_templates})
    event.data_json["unknown-secret"] = encrypt_field(["unknown"])
    event.save()
    event.refresh_from_db()
    client.force_login(request.getfixturevalue(role))

    module = offline if endpoint == "bundle" else event_context
    with patch.object(module, "safe_decrypt", wraps=safe_decrypt) as decrypt:
        payload = _bundle(client, event) if endpoint == "bundle" else _conflict(client, event)

    visible = {template.slug}
    if role == "lead_user":
        visible.update({"attached-secret", "orphan-secret", "unknown-secret"})
    result = payload["events"][0]["data_fields"] if endpoint == "bundle" else payload["data_json"]
    assert set(result) == visible
    assert result[template.slug] == ["a", "b"]
    assert all(type(value) is list for value in result.values())
    assert decrypt.call_count == len(visible)
    assert [call.args[0] for call in decrypt.call_args_list] == [
        marker for slug, marker in event.data_json.items() if slug in visible
    ]
    if endpoint == "bundle":
        fields = payload["document_types"][0]["fields"]
        assert {field["slug"] for field in fields} == visible.intersection({template.slug, "attached-secret"})
    else:
        assert set(payload["field_options"]) == visible.difference({"unknown-secret"})
    if role == "staff_user":
        assert "Restricted label" not in json.dumps(payload)


@pytest.mark.django_db
@pytest.mark.parametrize("values", [[], ["a"], ["b", "a"], ["a", "b", "a"], ["a", "c"], ["comma,value", "quote'value"]])
def test_offline_replay_preserves_encrypted_selection_arrays(client, staff_user, encrypted_offline_event, values):
    event, template = encrypted_offline_event
    event.data_json = {template.slug: values}
    event.save()
    event.refresh_from_db()
    client.force_login(staff_user)
    body = urlencode(
        [(template.slug, value) for value in values] + [("expected_updated_at", event.updated_at.isoformat())]
    )

    response = client.post(
        reverse("core:event_update", kwargs={"pk": event.pk}),
        body,
        content_type="application/x-www-form-urlencoded",
        HTTP_ACCEPT="application/json",
        HTTP_X_OFFLINE_REPLAY="2",
    )

    assert response.status_code == 200
    assert response.json() == {"status": "saved", "pk": str(event.pk)}
    assert response["X-Offline-Replay-Result"] == "saved"
    event.refresh_from_db()
    stored = event.data_json[template.slug]
    assert is_encrypted_value(stored) if values else stored == []
    result = safe_decrypt(stored)
    assert result == values
    assert type(result) is list
    assert _bundle(client, event)["events"][0]["data_fields"][template.slug] == values


def _conflict(client, event, values=None):
    response = client.post(
        reverse("core:event_update", kwargs={"pk": event.pk}),
        {"selection": values if values is not None else ["a", "b"], "expected_updated_at": "2000-01-01T00:00:00+00:00"},
        HTTP_ACCEPT="application/json",
        HTTP_X_OFFLINE_REPLAY="2",
    )
    assert response.status_code == 409
    assert response.json()["error"] == "conflict"
    return response.json()["server_state"]


@pytest.mark.django_db
@pytest.mark.parametrize("orphan", [False, True])
def test_conflict_view_preserves_selection_and_supplies_inactive_labels(
    client, staff_user, encrypted_offline_event, orphan
):
    event, template = encrypted_offline_event
    event.data_json = {template.slug: ["a", "c", "a"]}
    event.save()
    event.refresh_from_db()
    marker = event.data_json[template.slug]
    if orphan:
        DocumentTypeField.objects.filter(document_type=event.document_type, field_template=template).delete()
    client.force_login(staff_user)

    state = _conflict(client, event)

    assert state["data_json"][template.slug] == ["a", "c", "a"]
    assert type(state["data_json"][template.slug]) is list
    assert state["field_options"][template.slug] == [
        {"value": option["slug"], "label": option["label"]} for option in template.options_json
    ]
    event.refresh_from_db()
    assert event.data_json[template.slug] == marker


@pytest.mark.django_db
@pytest.mark.parametrize("choice", ["local", "server"])
def test_selection_conflict_resolution_replays_arrays(client, staff_user, encrypted_offline_event, choice):
    event, template = encrypted_offline_event
    client.force_login(staff_user)
    local_values = ["b", "a", "b"]
    state = _conflict(client, event, local_values)
    selected = local_values if choice == "local" else state["data_json"][template.slug]
    response = client.post(
        reverse("core:event_update", kwargs={"pk": event.pk}),
        {template.slug: selected, "expected_updated_at": state["updated_at"]},
        HTTP_ACCEPT="application/json",
        HTTP_X_OFFLINE_REPLAY="2",
    )
    assert response.status_code == 200
    assert response["X-Offline-Replay-Result"] == "saved"
    event.refresh_from_db()
    assert is_encrypted_value(event.data_json[template.slug])
    result = safe_decrypt(event.data_json[template.slug])
    assert result == selected
    assert type(result) is list
