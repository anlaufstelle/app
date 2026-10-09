"""Encrypted selections keep their type across public offline endpoints."""

import json
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, DocumentTypeField, FieldTemplate
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
def test_bundle_view_gates_attached_and_orphan_selections_before_decrypt(
    client, request, facility, encrypted_offline_event, role
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

    with patch.object(offline, "safe_decrypt", wraps=safe_decrypt) as decrypt:
        payload = _bundle(client, event)

    visible = {template.slug}
    if role == "lead_user":
        visible.update({"attached-secret", "orphan-secret", "unknown-secret"})
    result = payload["events"][0]["data_fields"]
    assert set(result) == visible
    assert result[template.slug] == ["a", "b"]
    assert all(type(value) is list for value in result.values())
    assert decrypt.call_count == len(visible)
    assert [call.args[0] for call in decrypt.call_args_list] == [
        marker for slug, marker in event.data_json.items() if slug in visible
    ]
    fields = payload["document_types"][0]["fields"]
    assert {field["slug"] for field in fields} == visible.intersection({template.slug, "attached-secret"})
