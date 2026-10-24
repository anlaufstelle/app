"""Offline task metadata excludes dynamic and former fixed details."""

import pytest

from core.services.system.offline import build_client_offline_bundle
from tests.test_episode_workitem_standard_fields import edit_payload, edit_url
from tests.test_episode_workitem_standard_fields import standard_record as standard_record

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def test_offline_bundle_omits_task_details(facility, staff_user, standard_record, case_open):
    kind, record, _field = standard_record
    if kind != "workitem":
        return
    record.client = case_open.client
    record.save(update_fields=["client"])
    bundle = build_client_offline_bundle(staff_user, facility, case_open.client)
    row = next(row for row in bundle["workitems"] if row["pk"] == str(record.pk))
    assert "description" not in row
    assert "data_json" not in row


def test_offline_replay_keeps_online_task_details(client, staff_user, standard_record):
    kind, record, _field = standard_record
    if kind != "workitem":
        return
    client.force_login(staff_user)
    old_data = dict(record.data_json)
    payload = edit_payload(kind, record)
    payload.pop("document_type")
    payload["title"] = "Offline title"
    response = client.post(edit_url(kind, record), payload, HTTP_ACCEPT="application/json")
    assert response.status_code in (200, 302)
    record.refresh_from_db()
    assert record.title == "Offline title"
    assert record.data_json == old_data


def test_conflict_payload_excludes_task_details(client, staff_user, standard_record):
    kind, record, _field = standard_record
    if kind != "workitem":
        return
    client.force_login(staff_user)
    payload = edit_payload(kind, record)
    payload.pop("document_type")
    payload["expected_updated_at"] = "2000-01-01T00:00:00Z"
    response = client.post(edit_url(kind, record), payload, HTTP_ACCEPT="application/json")
    assert response.status_code == 409
    server_state = response.json()["server_state"]
    assert "description" not in server_state
    assert "data_json" not in server_state
    assert server_state["item_type"] == record.item_type
    assert server_state["priority"] == record.priority
    assert server_state["recurrence"] == record.recurrence
    assert server_state["assigned_to"] == ""
