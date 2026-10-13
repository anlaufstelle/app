"""Person-owned pages, bundles and safety-banner fragments honor revocation."""

from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import User
from core.services.client import approve_client_deletion, request_client_deletion, restore_client
from core.services.system import build_client_offline_bundle
from tests._factories import make_client, make_document_type, make_event

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["trash", "inactive"])
def subject(request, facility, staff_user, lead_user):
    person = make_client(facility=facility, pseudonym="PRIVATE_OWNER_MARKER", created_by=staff_user)

    def revoke():
        if request.param == "trash":
            pending = request_client_deletion(person, staff_user, "Consent withdrawn")
            approve_client_deletion(pending, lead_user)
        else:
            person.is_active = False
            person.save(update_fields=["is_active"])

    def restore():
        if request.param == "trash":
            restore_client(person, lead_user)
        else:
            person.is_active = True
            person.save(update_fields=["is_active"])

    return person, revoke, restore


@pytest.mark.parametrize("role", ["assistant", "staff", "lead", "facility_admin"])
@pytest.mark.parametrize("surface", ["zeitstrom", "zeitstrom_bans_partial"])
def test_ban_banner_excludes_revoked_people(client, facility, subject, role, surface, staff_user):
    person, revoke, restore = subject
    reader = User.objects.create_user(username=f"banner-reader-{role}", role=role, facility=facility)
    peer = make_client(facility=facility, pseudonym="Visible ban owner", created_by=staff_user)
    doc = make_document_type(facility=facility, name="Safety ban", system_type="ban", sensitivity="normal")
    for owner in [person, peer]:
        make_event(
            facility=facility,
            client=owner,
            document_type=doc,
            occurred_at=timezone.now(),
            data_json={"aktiv": True, "grund": "Safety reason"},
            created_by=staff_user,
        )
    client.force_login(reader)
    url = reverse(f"core:{surface}")
    assert person.pseudonym in client.get(url).content.decode()
    revoke()
    response = client.get(url)
    assert response.status_code == 200
    assert person.pseudonym not in response.content.decode()
    assert peer.pseudonym in response.content.decode()
    restore()
    assert person.pseudonym in client.get(url).content.decode()


@pytest.mark.parametrize(
    ("surface", "method"),
    [("client_detail", "get")]
    + [
        (surface, method)
        for surface in [
            "client_delete_request",
            "client_merge",
            "merge_target",
            "client_stage_downgrade",
            "client_legal_hold",
        ]
        for method in ["get", "post"]
    ],
)
def test_normal_person_loaders_exclude_revoked_people(
    client, facility, lead_user, subject, surface, method, foreign_client
):
    person, revoke, restore = subject
    peer = make_client(facility=facility, pseudonym="Live merge source", created_by=lead_user)
    client.force_login(lead_user)
    route = "client_merge" if surface == "merge_target" else surface
    kwargs = {"pk": peer.pk if surface == "merge_target" else person.pk}
    url = reverse(f"core:{route}", kwargs=kwargs)
    data = {"target": str(person.pk)} if surface == "merge_target" else {}
    # Use the read form as the positive probe; an empty POST never writes.
    before = client.get(url, data)
    assert before.status_code == 200
    assert person.pseudonym in before.content.decode()
    assert client.get(reverse(f"core:{route}", kwargs={"pk": foreign_client.pk})).status_code == 404
    revoke()
    response = getattr(client, method)(url, data)
    assert response.status_code == 404
    assert person.pseudonym not in response.content.decode()
    restore()
    assert client.get(url, data).status_code == 200


@pytest.mark.parametrize("role", ["assistant", "staff", "lead", "facility_admin"])
@pytest.mark.parametrize("validator", [None, "*", '"previous-bundle"'])
def test_inactive_bundle_is_denied_before_serialization(client, facility, staff_user, subject, role, validator):
    person, revoke, restore = subject
    reader = User.objects.create_user(username=f"bundle-reader-{role}", role=role, facility=facility)
    client.force_login(reader)
    url = reverse("core:offline_bundle", kwargs={"pk": person.pk})
    assert client.get(url).status_code == 200
    revoke()
    headers = {"HTTP_IF_NONE_MATCH": validator} if validator else {}
    with patch("core.views.offline.build_client_offline_bundle", wraps=build_client_offline_bundle) as builder:
        response = client.get(url, **headers)
        assert response.status_code == 404
        builder.assert_not_called()
    restore()
    assert client.get(url).status_code == 200


def test_bundle_service_rechecks_the_live_owner(facility, staff_user, subject):
    person, revoke, restore = subject
    assert build_client_offline_bundle(staff_user, facility, person)["client"]["pk"] == str(person.pk)
    revoke()
    from django.http import Http404

    with pytest.raises(Http404):
        build_client_offline_bundle(staff_user, facility, person)
    restore()
    assert build_client_offline_bundle(staff_user, facility, person)["client"]["pk"] == str(person.pk)
