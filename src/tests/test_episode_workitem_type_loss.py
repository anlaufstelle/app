"""HTTP type changes must not silently discard stored dynamic values."""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, DocumentType, DocumentTypeField, Episode, WorkItem
from core.packages.catalog import parse_catalog_package
from core.packages.loader import load_package
from core.services.case.episodes import episode_basis_token
from core.services.case.workitems import workitem_form_state_token
from core.services.file_vault import is_encrypted_value, safe_decrypt
from tests._factories import make_document_type


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
@pytest.mark.parametrize("kind", ["episode", "workitem"])
@pytest.mark.parametrize(
    "stored_value",
    [
        "Stored protected text",
        "",
        "   ",
        None,
        pytest.param({"__encrypted__": True, "value": "invalid"}, id="unreadable-token"),
    ],
)
@pytest.mark.parametrize("shared_field", [False, True])
def test_type_change_rejects_implicit_loss_and_allows_lossless_changes(
    client, facility, staff_user, case_open, kind, stored_value, shared_field
):
    client.force_login(staff_user)
    load_package(facility, parse_catalog_package("default"))
    original_type = DocumentType.objects.for_facility(facility).get(entity_type=kind)
    field = original_type.fields.get().field_template
    target_type = make_document_type(facility=facility, entity_type=kind, name="Alternative")
    if shared_field:
        DocumentTypeField.objects.create(document_type=target_type, field_template=field)
    model = Episode if kind == "episode" else WorkItem
    create_kwargs = (
        {"case": case_open, "started_at": timezone.localdate()} if kind == "episode" else {"facility": facility}
    )
    record = model.objects.create(
        created_by=staff_user,
        title="Before type change",
        document_type=original_type,
        data_json={field.slug: stored_value},
        **create_kwargs,
    )
    record.refresh_from_db()
    original_data = dict(record.data_json)
    audit_count = AuditLog.objects.count()
    payload = {"title": "After type change", "document_type": str(target_type.pk)}
    if kind == "episode":
        payload |= {"started_at": record.started_at.isoformat(), "basis_token": episode_basis_token(record)}
        url_kwargs = {"case_pk": case_open.pk, "pk": record.pk}
    else:
        payload |= {
            "item_type": "task",
            "priority": "normal",
            "expected_updated_at": record.updated_at.isoformat(),
            "expected_form_state": workitem_form_state_token(record),
        }
        url_kwargs = {"pk": record.pk}
    response = client.post(reverse(f"core:{kind}_update", kwargs=url_kwargs), payload)
    record.refresh_from_db()
    has_value = stored_value is not None and (not isinstance(stored_value, str) or bool(stored_value.strip()))
    if has_value and not shared_field:
        assert response.status_code == 200
        assert response.context["form"].has_error("document_type")
        assert record.document_type == original_type
        assert record.title == "Before type change"
        assert record.data_json == original_data
        assert is_encrypted_value(record.data_json[field.slug])
        if isinstance(stored_value, str):
            assert safe_decrypt(record.data_json[field.slug]) == stored_value
        assert AuditLog.objects.count() == audit_count
    else:
        assert response.status_code == 302
        assert record.document_type == target_type
        assert record.title == "After type change"
        if shared_field and has_value:
            assert record.data_json == original_data
