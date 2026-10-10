"""Encrypted field values retain their type across entity persistence paths."""

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection
from django.test import override_settings
from django.utils import timezone

from core.models import Case, Client, DocumentType, DocumentTypeField, Event, FieldTemplate
from core.services.file_vault import decrypt_field, encrypt_dynamic_data, generate_key, is_encrypted_value


@pytest.mark.django_db
@pytest.mark.parametrize("entity_type", ["client", "case", "event"])
@pytest.mark.parametrize("path", ["save", "bulk", "rotation"])
@pytest.mark.parametrize(
    "value",
    [
        pytest.param(["a", "b", "c"], id="selection"),
        pytest.param(["ä", 'quote"', "line\n", "slash\\", "", "ä"], id="special-selection"),
        pytest.param('private text ä\n"\\', id="special-text"),
        pytest.param("true", id="boolean-like-text"),
        pytest.param("123", id="number-like-text"),
        pytest.param('["a", "b"]', id="json-list-text"),
        pytest.param('{"type": "list", "value": ["a"]}', id="json-object-text"),
        pytest.param("['a', 'b']", id="legacy-repr-text"),
        pytest.param([], id="empty-selection"),
        pytest.param("", id="empty-text"),
        pytest.param(None, id="null"),
    ],
)
def test_encrypted_values_survive_persistence_paths(entity_type, path, value, facility, client_identified, staff_user):
    old_key, new_key = generate_key(), generate_key()
    legacy = isinstance(value, list) and any(item not in {"a", "b", "c"} for item in value)
    document_type = DocumentType.objects.create(
        facility=facility,
        name="Typed fields",
        entity_type=entity_type,
        sensitivity=DocumentType.Sensitivity.HIGH,
    )
    for slug, encrypted in (("private", True), ("public", False)):
        template = FieldTemplate.objects.create(
            facility=facility,
            name=f"Selection {slug}",
            slug=slug,
            field_type=FieldTemplate.FieldType.MULTI_SELECT
            if isinstance(value, list)
            else FieldTemplate.FieldType.TEXT,
            is_encrypted=encrypted,
            options_json=[{"slug": slug, "label": slug} for slug in ("a", "b", "c")] if isinstance(value, list) else [],
        )
        DocumentTypeField.objects.create(document_type=document_type, field_template=template)
    kwargs = {
        "facility": facility,
        "created_by": staff_user,
        "document_type": document_type,
        "data_json": {"private": value, "public": value},
    }
    if entity_type == "client":
        obj = Client(pseudonym="Typed field person", **kwargs)
    elif entity_type == "case":
        obj = Case(client=client_identified, title="Typed field case", **kwargs)
    else:
        obj = Event(client=client_identified, occurred_at=timezone.now(), **kwargs)

    with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=old_key):
        if legacy:
            # Invalid slugs can only be historical values, never a new selection.
            # Inject plaintext history, then exercise real encryption on save/bulk/rotation.
            original_data = obj.data_json.copy()
            obj.data_json = {}
            if path == "bulk":
                type(obj).objects.bulk_create([obj])
            else:
                obj.save()
            type(obj).objects.filter(pk=obj.pk).update(data_json=original_data)
            obj.refresh_from_db()
        if path == "bulk":
            original_data = obj.data_json.copy()
            obj.data_json = encrypt_dynamic_data(document_type, obj.data_json)
            assert original_data == {"private": value, "public": value}
            if legacy:
                # Existing legacy selections use the protected bulk update path.
                type(obj).objects.bulk_update([obj], ["data_json"])
            else:
                type(obj).objects.bulk_create([obj])
        else:
            obj.save()
            first_stored = obj.data_json["private"]
            obj.save()
            assert obj.data_json["private"] == first_stored
    if path == "rotation":
        with override_settings(ENCRYPTION_KEYS=f"{new_key},{old_key}", ENCRYPTION_KEY=""):
            call_command("reencrypt_fields", stdout=StringIO())

    obj.refresh_from_db()
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT data_json FROM {obj._meta.db_table} WHERE id = %s", [obj.pk])
        raw = cursor.fetchone()[0]
    persisted = json.loads(raw) if isinstance(raw, str) else raw
    assert persisted == obj.data_json
    assert persisted["public"] == value
    stored = persisted["private"]
    if value:
        assert is_encrypted_value(stored)
        assert set(stored) == {"__encrypted__", "value"}
        assert json.dumps(stored) != json.dumps(value)
    else:
        assert stored == value
    with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=new_key if path == "rotation" else old_key):
        result = decrypt_field(stored)
    assert result == value
    assert type(result) is type(value)
