"""Failed edits must rebuild conditions from the currently permitted fields."""

import json
import re
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.models import DocumentTypeField, FieldCondition, FieldTemplate
from core.services.conditions import evaluate_conditions
from tests._factories import make_document_type
from tests.test_edit_error_preservation import hidden_values

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["client", "case", "event", "documentation"])
def conditional_edit(request, client, staff_user, facility):
    kind = request.param
    owner_kind = "event" if kind == "documentation" else kind
    target = request.getfixturevalue(
        {"client": "client_identified", "case": "case_open", "event": "sample_event"}[owner_kind]
    )
    document_type = make_document_type(facility=facility, name="Conditional edit type", entity_type=owner_kind)
    target.document_type = document_type
    if kind == "documentation":
        target.client = None
    target.save()
    client.force_login(staff_user)
    url = reverse(f"core:{owner_kind}_update", args=[target.pk])
    payload = {"document_type": str(document_type.pk)}
    if owner_kind == "client":
        payload.update(
            pseudonym=target.pseudonym,
            contact_stage=target.contact_stage,
            age_cluster=target.age_cluster,
            notes="Own permitted person draft",
        )
    elif owner_kind == "case":
        payload.update(title=target.title, description="Own permitted case draft", lead_user="")
    return owner_kind, target, document_type, url, payload


def add_condition(document_type, facility, prefix, threshold):
    source = FieldTemplate.objects.create(facility=facility, name=f"{prefix} source", field_type="number")
    destination = FieldTemplate.objects.create(
        facility=facility, name=f"{prefix} destination", field_type="textarea", is_required=True
    )
    source_link = DocumentTypeField.objects.create(document_type=document_type, field_template=source)
    destination_link = DocumentTypeField.objects.create(document_type=document_type, field_template=destination)
    FieldCondition.objects.create(
        target_field=destination_link,
        depends_on_field=source_link,
        operator="greater_than",
        value=threshold,
    )
    return source, destination


@pytest.mark.parametrize("failure", ["service", "conflict"])
@pytest.mark.parametrize("restricted_endpoint", ["source", "destination"])
def test_policy_change_removes_condition_metadata_but_preserves_readable_conditions(
    client, conditional_edit, facility, failure, restricted_endpoint
):
    owner_kind, target, document_type, url, payload = conditional_edit
    source, destination = add_condition(document_type, facility, "Confidential condition", 826173)
    readable_source, readable_destination = add_condition(document_type, facility, "Readable condition", 12)
    loaded = client.get(url)
    assert loaded.status_code == 200
    assert source.slug in loaded.content.decode()
    payload.update(hidden_values(loaded))
    payload.update(
        {
            source.slug: "900000",
            destination.slug: "Confidential conditional draft",
            readable_source.slug: "20",
            readable_destination.slug: "Own readable conditional draft",
        }
    )
    restricted = source if restricted_endpoint == "source" else destination
    original_data = target.data_json.copy()

    def reject_after_policy_change(*args, **kwargs):
        restricted.sensitivity = "high"
        restricted.save(update_fields=["sensitivity"])
        raise ValidationError("Save rejected", code="version_conflict" if failure == "conflict" else "invalid")

    module = {"client": "clients", "case": "cases", "event": "events"}[owner_kind]
    with patch(f"core.views.{module}.stage_attachment_uploads", side_effect=reject_after_policy_change) as staging:
        response = client.post(url, payload)
    staging.assert_called_once()
    assert response.status_code == 200
    assert response.context["has_edit_conflict"] == (failure == "conflict")
    assert restricted.slug not in response.context["data_form"].fields
    html = response.content.decode()
    assert restricted.slug not in html
    assert restricted.name not in html
    assert "826173" not in html
    assert "Own readable conditional draft" in html
    if restricted_endpoint == "destination":
        assert "Confidential conditional draft" not in html
    else:
        assert "900000" not in html
        assert "Confidential conditional draft" in html
    script = re.search(r'<script id="field-conditions" type="application/json">(.*?)</script>', html, re.DOTALL)
    assert script is not None
    conditions = json.loads(script.group(1))
    assert len(conditions["conditions"]) == 1
    condition = conditions["conditions"][0]
    assert condition["source"] == readable_source.slug
    assert condition["target"] == readable_destination.slug
    assert condition["value"] == 12
    fields = {slug: spec["type"] for slug, spec in conditions["fields"].items()}
    assert readable_destination.slug in evaluate_conditions(
        fields, conditions["conditions"], {readable_source.slug: 20}
    )
    assert readable_destination.slug not in evaluate_conditions(
        fields, conditions["conditions"], {readable_source.slug: 5}
    )
    target.refresh_from_db()
    assert target.data_json == original_data


@pytest.mark.parametrize("conditional_edit", ["client", "case"], indirect=True)
@pytest.mark.parametrize("failure", ["service", "conflict"])
def test_locked_document_type_clears_all_condition_metadata(client, conditional_edit, facility, failure):
    owner_kind, target, document_type, url, payload = conditional_edit
    source, destination = add_condition(document_type, facility, "Hidden type condition", 918263)
    loaded = client.get(url)
    assert loaded.status_code == 200
    payload.update(hidden_values(loaded))
    payload.update({source.slug: "950000", destination.slug: "Newly forbidden draft"})

    def reject_after_type_change(*args, **kwargs):
        for field in (source, destination):
            field.sensitivity = "high"
            field.save(update_fields=["sensitivity"])
        document_type.sensitivity = "high"
        document_type.save(update_fields=["sensitivity"])
        raise ValidationError("Save rejected", code="version_conflict" if failure == "conflict" else "invalid")

    module = {"client": "clients", "case": "cases"}[owner_kind]
    with patch(f"core.views.{module}.stage_attachment_uploads", side_effect=reject_after_type_change) as staging:
        response = client.post(url, payload)
    staging.assert_called_once()
    assert response.status_code == 200
    assert not response.context["data_form"].fields
    html = response.content.decode()
    for forbidden in (source.slug, destination.slug, "918263", "Newly forbidden draft", 'id="field-conditions"'):
        assert forbidden not in html
