"""Offline task fields provide unique accessible validation placeholders."""

import uuid

import pytest
from django.urls import reverse

from tests.test_workitem_filter_contract import Controls


@pytest.mark.django_db
@pytest.mark.parametrize("shell", [True, False])
def test_offline_task_error_placeholders(client, staff_user, shell):
    client.force_login(staff_user)
    url = (
        reverse("core:offline_workitem_shell") if shell else reverse("core:offline_client_detail", args=[uuid.uuid4()])
    )
    response = client.get(url)
    assert response.status_code == 200
    controls = Controls(response.content.decode())
    ids = [attrs["id"] for _, attrs in controls.elements if attrs.get("id")]
    root = next(
        attrs for _, attrs in controls.elements if attrs.get("x-data") in {"offlineWorkItemCreate", "offlineClientView"}
    )
    assert root["data-wi-title-required"]
    assert root["data-wi-title-too-long"]
    assert root["data-wi-reminder-after-due"]
    fields = [attrs for tag, attrs in controls.elements if tag == "input" and attrs.get("data-wi-field")]
    assert len(fields) == (3 if shell else 6)
    for field in fields:
        assert ids.count(field["id"]) == 1
        assert field["@input"] == "clearWorkItemErrors"
        assert field["data-testid"] == "offline-wi-input-" + field["data-wi-field"]
        error_id = field["data-wi-error-id"]
        assert ids.count(error_id) == 1
        error = next(attrs for _, attrs in controls.elements if attrs.get("id") == error_id)
        assert error["role"] == "alert"
        assert "hidden" in error
