"""Public bundle boundaries for trashed people and hidden documentation."""

from unittest.mock import patch
from uuid import uuid4

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import User
from core.services.client import approve_client_deletion, request_client_deletion, restore_client
from core.services.system import build_client_offline_bundle
from tests._factories import make_document_type, make_event

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("role", ["assistant", "staff", "lead", "facility_admin"])
@pytest.mark.parametrize("validator", [None, "*", '"previous-bundle"'])
def test_bundle_denies_approved_deletion_before_serialization(
    client, facility, client_identified, staff_user, lead_user, role, validator
):
    reader = User.objects.create_user(username=f"bundle-{role}", role=role, facility=facility)
    client.force_login(reader)
    url = reverse("core:offline_bundle", kwargs={"pk": client_identified.pk})
    assert client.get(url).status_code == 200
    pending = request_client_deletion(client_identified, staff_user, "Consent withdrawn")
    assert client.get(url).status_code == 200
    approve_client_deletion(pending, lead_user)
    headers = {"HTTP_IF_NONE_MATCH": validator} if validator else {}
    with patch("core.views.offline.build_client_offline_bundle", wraps=build_client_offline_bundle) as builder:
        response = client.get(url, **headers)
        assert response.status_code == 404
        builder.assert_not_called()
    restore_client(client_identified, lead_user)
    assert client.get(url).status_code == 200


@pytest.mark.parametrize("role", ["assistant", "staff", "lead", "facility_admin"])
def test_bundle_denies_anonymized_nontrash_people(client, facility, client_identified, role):
    reader = User.objects.create_user(username=f"anonymized-{role}", role=role, facility=facility)
    client_identified.is_active = False
    client_identified.anonymized_at = timezone.now()
    client_identified.save(update_fields=["is_active", "anonymized_at"])
    client.force_login(reader)
    response = client.get(reverse("core:offline_bundle", kwargs={"pk": client_identified.pk}))
    assert response.status_code == 404
    assert client_identified.pseudonym not in response.content.decode()


@pytest.mark.parametrize("role", ["assistant", "staff", "lead", "facility_admin"])
def test_bundle_unknown_and_foreign_people_remain_missing(client, client_identified, second_facility_user, role):
    second_facility_user.role = role
    second_facility_user.save(update_fields=["role"])
    client.force_login(second_facility_user)
    for pk in [client_identified.pk, uuid4()]:
        assert client.get(reverse("core:offline_bundle", kwargs={"pk": pk})).status_code == 404


@pytest.mark.parametrize(
    ("role", "visible"),
    [
        ("assistant", ["normal"]),
        ("staff", ["normal", "elevated"]),
        ("lead", ["normal", "elevated", "high"]),
        ("facility_admin", ["normal", "elevated", "high"]),
    ],
)
def test_bundle_events_exclude_deleted_and_role_hidden_documentation(
    client, facility, client_identified, staff_user, role, visible
):
    reader = User.objects.create_user(username=f"events-{role}", role=role, facility=facility)
    expected = set()
    for sensitivity in ["normal", "elevated", "high"]:
        doc_type = make_document_type(facility=facility, name=sensitivity, sensitivity=sensitivity)
        for deleted in [False, True]:
            event = make_event(
                facility=facility,
                client=client_identified,
                document_type=doc_type,
                occurred_at=timezone.now(),
                data_json={},
                created_by=staff_user,
                is_deleted=deleted,
            )
            if not deleted and sensitivity in visible:
                expected.add(str(event.pk))
    client.force_login(reader)
    response = client.get(reverse("core:offline_bundle", kwargs={"pk": client_identified.pk}))
    assert response.status_code == 200
    assert {event["pk"] for event in response.json()["events"]} == expected
