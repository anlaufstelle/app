"""Configuration pages use the episode and task carriers and editor partials."""

import pytest
from django.urls import reverse

from tests._dynamic_test_data import assign_details

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["episode", "workitem"])
def typed_record(request):
    record = request.getfixturevalue("episode" if request.param == "episode" else "sample_workitem")
    assign_details(record, "Configured detail")
    record.save(update_fields=["document_type", "data_json"])
    return record


def test_detail_counts_the_own_carrier(client, admin_user, typed_record):
    client.force_login(admin_user)
    response = client.get(reverse("core:config_doctype_detail", kwargs={"pk": typed_record.document_type_id}))
    assert response.status_code == 200
    assert response.context["usage_count"] == 1
    label = "Episoden" if typed_record._meta.model_name == "episode" else "Aufgaben"
    assert label in response.content.decode()


def test_preview_uses_the_matching_partial(client, admin_user, typed_record):
    client.force_login(admin_user)
    response = client.get(reverse("core:config_doctype_detail", kwargs={"pk": typed_record.document_type_id}))
    assert response.status_code == 200
    endpoint = reverse(f"core:{typed_record._meta.model_name}_fields_partial")
    assert f'hx-get="{endpoint}?document_type={typed_record.document_type_id}"' in response.content.decode()
    preview = client.get(endpoint, {"document_type": str(typed_record.document_type_id)}, HTTP_HX_REQUEST="true")
    assert preview.status_code == 200
    assert f'name="test-details-{typed_record._meta.model_name}"' in preview.content.decode()


def test_delete_dialog_counts_the_own_carrier_and_blocks_submission(client, admin_user, typed_record):
    client.force_login(admin_user)
    typed_record.soft_delete(user=admin_user)
    response = client.get(reverse("core:config_doctype_delete", kwargs={"pk": typed_record.document_type_id}))
    assert response.status_code == 200
    kind = typed_record._meta.model_name
    assert response.context[f"{kind}_count"] == 1
    body = response.content.decode()
    assert f'data-testid="doctype-delete-blocker-{kind}"' in body
    button = body.split('data-testid="doctype-delete-confirm"', 1)[0].rsplit("<button", 1)[1]
    assert 'disabled aria-disabled="true"' in button
