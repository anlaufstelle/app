"""Person rankings respect revocation without changing statistical aggregates."""

import json
from datetime import datetime, time, timedelta

import pytest
from django.core.serializers.json import DjangoJSONEncoder
from django.urls import reverse
from django.utils import timezone

from core.models import StatisticsSnapshot, User
from core.services.client import approve_client_deletion, request_client_deletion, restore_client
from core.services.dashboard import get_statistics, get_statistics_hybrid
from tests._factories import make_client, make_document_type, make_event

pytestmark = pytest.mark.django_db

AGGREGATE_KEYS = (
    "total_contacts",
    "unique_clients",
    "by_contact_stage",
    "persons_by_contact_stage",
    "by_document_type",
    "by_age_cluster",
)


@pytest.mark.parametrize("state", ["trash", "inactive"])
@pytest.mark.parametrize("role", ["lead", "facility_admin"])
@pytest.mark.parametrize("filtered", [False, True], ids=["hybrid", "document-type"])
@pytest.mark.parametrize("partial", [False, True], ids=["full", "htmx"])
def test_statistics_person_ranking_hides_revoked_people(
    client, facility, staff_user, lead_user, state, role, filtered, partial
):
    reader = User.objects.create_user(username=f"ranking-{role}", role=role, facility=facility)
    person = make_client(facility=facility, pseudonym="Revoked ranking person", created_by=staff_user)
    peer = make_client(facility=facility, pseudonym="Visible ranking peer", created_by=staff_user)
    document_type = make_document_type(facility=facility, name="Ranking contact", sensitivity="normal")
    for owner in [person, person, person, peer]:
        make_event(
            facility=facility,
            client=owner,
            document_type=document_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
    client.force_login(reader)
    url = reverse("core:statistics")
    params = {"doktyp": str(document_type.pk)} if filtered else {}
    headers = {"HTTP_HX_REQUEST": "true"} if partial else {}
    baseline = client.get(url, params, **headers)
    assert baseline.status_code == 200
    assert person.pseudonym in baseline.content.decode()
    assert baseline.context["stats"]["top_clients"] == [
        {"pseudonym": person.pseudonym, "count": 3},
        {"pseudonym": peer.pseudonym, "count": 1},
    ]
    assert baseline.context["stats"]["total_contacts"] == 4
    assert baseline.context["stats"]["unique_clients"] == 2
    aggregates = {key: baseline.context["stats"][key] for key in AGGREGATE_KEYS}

    if state == "trash":
        pending = request_client_deletion(person, staff_user, "Consent withdrawn")
        response = client.get(url, params, **headers)
        assert response.status_code == 200
        assert response.context["stats"]["top_clients"] == baseline.context["stats"]["top_clients"]
        assert person.pseudonym in response.content.decode()
        approve_client_deletion(pending, lead_user)
    else:
        person.is_active = False
        person.save(update_fields=["is_active"])

    assert client.get(reverse("core:client_detail", kwargs={"pk": person.pk})).status_code == 404
    response = client.get(url, params, **headers)
    assert response.status_code == 200
    assert {key: response.context["stats"][key] for key in AGGREGATE_KEYS} == aggregates
    assert person.pseudonym not in response.content.decode()
    assert response.context["stats"]["top_clients"] == [{"pseudonym": peer.pseudonym, "count": 1}]
    assert peer.pseudonym in response.content.decode()

    if state == "trash":
        restore_client(person, lead_user)
    else:
        person.is_active = True
        person.save(update_fields=["is_active"])
    restored = client.get(url, params, **headers)
    assert restored.status_code == 200
    assert person.pseudonym in restored.content.decode()
    assert restored.context["stats"]["top_clients"] == baseline.context["stats"]["top_clients"]
    assert {key: restored.context["stats"][key] for key in AGGREGATE_KEYS} == aggregates


@pytest.mark.parametrize("state", ["trash", "inactive"])
def test_statistics_ranking_promotes_visible_person_before_limit(facility, staff_user, state):
    document_type = make_document_type(facility=facility, name="Ranking limit contact")
    for index in range(5):
        hidden = make_client(
            facility=facility,
            pseudonym=f"Hidden ranking person {index}",
            created_by=staff_user,
            **{"is_deleted": True} if state == "trash" else {"is_active": False},
        )
        for _ in range(2):
            make_event(
                facility=facility,
                client=hidden,
                document_type=document_type,
                occurred_at=timezone.now(),
                data_json={},
                created_by=staff_user,
            )
    peer = make_client(facility=facility, pseudonym="Visible ranking peer", created_by=staff_user)
    make_event(
        facility=facility,
        client=peer,
        document_type=document_type,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )
    today = timezone.localdate()
    stats = get_statistics(facility, today, today)
    assert stats["total_contacts"] == 11
    assert stats["unique_clients"] == 6
    assert stats["top_clients"] == [{"pseudonym": peer.pseudonym, "count": 1}]


@pytest.mark.parametrize("state", ["trash", "inactive"])
def test_snapshot_statistics_refresh_person_ranking_without_changing_snapshot(facility, staff_user, lead_user, state):
    date_to = timezone.localdate().replace(day=1) - timedelta(days=1)
    date_from = date_to.replace(day=1)
    occurred_at = timezone.make_aware(datetime.combine(date_from, time(12)))
    person = make_client(facility=facility, pseudonym="Revoked snapshot person", created_by=staff_user)
    peer = make_client(facility=facility, pseudonym="Visible snapshot peer", created_by=staff_user)
    document_type = make_document_type(facility=facility, name="Snapshot ranking contact")
    for owner in [person, person, person, peer]:
        make_event(
            facility=facility,
            client=owner,
            document_type=document_type,
            occurred_at=occurred_at,
            data_json={},
            created_by=staff_user,
        )
    snapshot_data = get_statistics(facility, date_from, date_to)
    snapshot_data.pop("top_clients")
    snapshot_data = json.loads(json.dumps(snapshot_data, cls=DjangoJSONEncoder))
    snapshot = StatisticsSnapshot.objects.create(
        facility=facility, year=date_from.year, month=date_from.month, data=snapshot_data
    )
    baseline = get_statistics_hybrid(facility, date_from, date_to)
    assert baseline["snapshot_as_of"] == snapshot.updated_at
    assert baseline["top_clients"][0] == {"pseudonym": person.pseudonym, "count": 3}
    if state == "trash":
        pending = request_client_deletion(person, staff_user, "Consent withdrawn")
        approve_client_deletion(pending, lead_user)
    else:
        person.is_active = False
        person.save(update_fields=["is_active"])
    stats = get_statistics_hybrid(facility, date_from, date_to)
    assert {key: stats[key] for key in AGGREGATE_KEYS} == {key: baseline[key] for key in AGGREGATE_KEYS}
    assert stats["snapshot_as_of"] == snapshot.updated_at
    assert stats["top_clients"] == [{"pseudonym": peer.pseudonym, "count": 1}]
    snapshot.refresh_from_db()
    assert snapshot.data == snapshot_data
    assert snapshot.updated_at == baseline["snapshot_as_of"]
