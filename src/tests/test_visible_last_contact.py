"""Contact metadata must follow event visibility across public read paths."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import User
from tests._factories import make_client, make_document_type, make_event

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("surface", ["list", "bundle", "autocomplete"])
@pytest.mark.parametrize("role", ["assistant", "staff", "lead", "facility_admin"])
@pytest.mark.parametrize("only_high", [False, True], ids=["mixed", "only-high"])
def test_last_contact_uses_visible_live_events(
    client, facility, client_identified, staff_user, second_facility, role, surface, only_high
):
    reader = User.objects.create_user(username=f"contact-{role}", role=role, facility=facility)
    now = timezone.now()
    events = {}
    for sensitivity, age in [("normal", 120), ("elevated", 5), ("high", 1)]:
        if only_high and sensitivity != "high":
            continue
        doc_type = make_document_type(facility=facility, name=sensitivity, sensitivity=sensitivity)
        events[sensitivity] = make_event(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=now - timedelta(days=age),
            data_json={},
            created_by=staff_user,
        )
        make_event(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=now,
            data_json={},
            created_by=staff_user,
            is_deleted=True,
        )
    normal = make_document_type(facility=facility, name="Peer contact", sensitivity="normal")
    peer = make_client(facility=facility, pseudonym="Peer", created_by=staff_user)
    peer_time = now - timedelta(days=3)
    make_event(
        facility=facility,
        client=peer,
        document_type=normal,
        occurred_at=peer_time,
        data_json={},
        created_by=staff_user,
    )
    empty_a = make_client(facility=facility, pseudonym="Empty A", created_by=staff_user)
    empty_b = make_client(facility=facility, pseudonym="Empty B", created_by=staff_user)
    foreign = make_client(facility=second_facility, pseudonym="Foreign", created_by=None)
    foreign_type = make_document_type(facility=second_facility, name="Foreign contact")
    make_event(
        facility=second_facility,
        client=foreign,
        document_type=foreign_type,
        occurred_at=now,
        data_json={},
        created_by=None,
    )
    visible_sensitivity = {"assistant": "normal", "staff": "elevated"}.get(role, "high")
    expected = events.get(visible_sensitivity)
    expected_time = expected.occurred_at if expected else None
    client.force_login(reader)
    if surface == "list":
        response = client.get(reverse("core:client_list"))
        assert response.status_code == 200
        people = {person.pk: person for person in response.context["clients"]}
        assert foreign.pk not in people
        assert people[client_identified.pk].last_contact == expected_time
    elif surface == "bundle":
        response = client.get(reverse("core:offline_bundle", kwargs={"pk": client_identified.pk}))
        assert response.status_code == 200
        assert response.json()["client"]["last_contact"] == (expected_time.isoformat() if expected_time else None)
        if role == "assistant" and not only_high:
            assert response.json()["events"] == []
    else:
        response = client.get(reverse("core:client_autocomplete"))
        assert response.status_code == 200
        people = [person["id"] for person in response.json()]
        expected_order = (
            [peer, client_identified]
            if expected_time is None or expected_time < peer_time
            else [client_identified, peer]
        )
        empty = sorted(
            [empty_a, empty_b] + ([client_identified] if expected_time is None else []), key=lambda p: p.pseudonym
        )
        if expected_time is None:
            expected_order = [peer]
        assert people == [str(person.pk) for person in expected_order + empty]
