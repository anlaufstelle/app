"""Review targets obey the detail page's visibility boundary (Refs #2516)."""

import uuid
from html.parser import HTMLParser

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from core.models import Case, DeletionRequest
from tests._authz_expectations import ROLES
from tests._factories import make_client, make_document_type, make_event

pytestmark = pytest.mark.django_db

ROLE_FIXTURES = {
    "facility_admin": "admin_user",
    "lead": "lead_user",
    "staff": "staff_user",
    "assistant": "assistant_user",
    "super_admin": "super_admin_user",
}
TARGET_TYPES = ("event", "client", "case", "merge")


class TargetLinks(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.links = []
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("data-testid") in {"review-target-link", "review-merge-target-link"}:
            self.links.append(attrs)


@pytest.fixture
def review_targets(facility, staff_user, sample_event, client_identified, case_open):
    sample_event.document_type.name = "REVIEW-EVENT-SECRET"
    sample_event.document_type.save(update_fields=["name"])
    client_identified.pseudonym = "REVIEW-PERSON-SECRET"
    client_identified.save(update_fields=["pseudonym"])
    case_open.title = "REVIEW-CASE-SECRET"
    case_open.save(update_fields=["title"])
    target = make_client(
        facility=facility,
        created_by=staff_user,
        pseudonym="MERGE-TARGET-SECRET",
        contact_stage="qualified",
    )
    return {"event": sample_event, "client": client_identified, "case": case_open, "merge": target}


def make_request(facility, staff_user, targets, kind, **overrides):
    return DeletionRequest.objects.create(
        facility=facility,
        requested_by=staff_user,
        target_type={"event": "Event", "client": "Client", "case": "Case", "merge": "Client"}[kind],
        target_id=targets["client" if kind == "merge" else kind].pk,
        merge_into_id=targets["merge"].pk if kind == "merge" else None,
        reason="Review reason",
        **overrides,
    )


def login_confirmer(client, request, role):
    user = request.getfixturevalue(ROLE_FIXTURES[role])
    user.can_confirm_deletion = True
    user.save(update_fields=["can_confirm_deletion"])
    client.force_login(user)
    return user


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("kind", TARGET_TYPES)
def test_review_links_only_to_accessible_targets(client, request, role, kind, facility, staff_user, review_targets):
    login_confirmer(client, request, role)
    dr = make_request(facility, staff_user, review_targets, kind)
    response = client.get(reverse("core:deletion_review", args=[dr.pk]))
    if role == "super_admin":
        assert response.status_code in (403, 404)
        return
    assert response.status_code == 200
    links = TargetLinks(response.content.decode()).links
    if kind == "case" and role == "assistant":
        assert links == []
        assert review_targets["case"].title not in response.content.decode()
        return
    source_kind = "client" if kind == "merge" else kind
    expected = [reverse(f"core:{source_kind}_detail", args=[review_targets[source_kind].pk])]
    if kind == "merge":
        expected.append(reverse("core:client_detail", args=[review_targets["merge"].pk]))
    assert [link["href"] for link in links] == expected
    for link in links:
        assert link["target"] == "_blank"
        assert "noopener" in link["rel"].split()
        assert client.get(link["href"]).status_code == 200
    assert client.get(reverse("core:deletion_review", args=[dr.pk])).status_code == 200


@pytest.mark.parametrize("role", ("facility_admin", "lead", "staff", "assistant"))
@pytest.mark.parametrize("sensitivity", ("normal", "elevated", "high", "unknown"))
def test_event_review_matches_detail_visibility(
    client, request, role, sensitivity, facility, staff_user, review_targets
):
    login_confirmer(client, request, role)
    event = review_targets["event"]
    event.document_type.sensitivity = sensitivity
    event.document_type.name = "SECRET-EVENT-TYPE"
    event.document_type.save(update_fields=["sensitivity", "name"])
    event.occurred_at = timezone.now().replace(year=1997, month=2, day=3, hour=14, minute=17)
    event.save(update_fields=["occurred_at"])
    dr = make_request(facility, staff_user, review_targets, "event")
    response = client.get(reverse("core:deletion_review", args=[dr.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    visible = client.get(reverse("core:event_detail", args=[event.pk])).status_code == 200
    if visible:
        assert "SECRET-EVENT-TYPE" in body
        assert len(TargetLinks(body).links) == 1
    else:
        assert TargetLinks(body).links == []
        assert "SECRET-EVENT-TYPE" not in body
        assert event.client.pseudonym not in body
        assert "1997" not in body
        assert str(event.pk) not in body
        assert 'data-testid="event-review-not-visible"' in body
        assert 'name="action" value="approve"' in body or dr.requested_by_id == response.wsgi_request.user.pk


@pytest.mark.parametrize("role", ("facility_admin", "lead", "staff", "assistant"))
@pytest.mark.parametrize("kind", TARGET_TYPES)
@pytest.mark.parametrize("state", ("deleted", "missing", "foreign"))
def test_unavailable_targets_stay_neutral(
    client, request, role, kind, state, facility, staff_user, review_targets, second_facility, second_facility_user
):
    login_confirmer(client, request, role)
    dr = make_request(facility, staff_user, review_targets, kind)
    source_kind = "client" if kind == "merge" else kind
    target = review_targets[source_kind]
    if state == "missing":
        dr.target_id = uuid.uuid4()
        dr.save(update_fields=["target_id"])
    elif state == "foreign":
        person = make_client(
            facility=second_facility,
            created_by=second_facility_user,
            pseudonym="FOREIGN-PERSON-SECRET",
            contact_stage="qualified",
        )
        if source_kind == "event":
            document_type = make_document_type(facility=second_facility, name="FOREIGN-EVENT-SECRET")
            target = make_event(
                facility=second_facility,
                client=person,
                document_type=document_type,
                created_by=second_facility_user,
                occurred_at=timezone.now(),
                data_json={},
            )
        elif source_kind == "case":
            target = Case.objects.create(
                facility=second_facility, client=person, created_by=second_facility_user, title="FOREIGN-CASE-SECRET"
            )
        else:
            target = person
        dr.target_id = target.pk
        dr.save(update_fields=["target_id"])
    else:
        target.is_deleted = True
        target.save(update_fields=["is_deleted"])
    response = client.get(reverse("core:deletion_review", args=[dr.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    source_url = reverse(f"core:{source_kind}_detail", args=[target.pk])
    assert source_url not in body
    assert (
        'data-testid="review-target-not-visible"' in body
        or 'data-testid="event-review-not-visible"' in body
        or 'data-testid="case-review-not-visible"' in body
    )
    if source_kind == "event":
        assert target.document_type.name not in body
    elif source_kind == "case":
        assert target.title not in body
    else:
        assert target.pseudonym not in body


@pytest.mark.parametrize("state", ("deleted", "foreign"))
def test_merge_destination_is_resolved_independently(
    client, lead_user, facility, staff_user, review_targets, second_facility, state
):
    target = review_targets["merge"]
    if state == "foreign":
        target.facility = second_facility
        target.save(update_fields=["facility"])
    if state == "deleted":
        target.is_deleted = True
        target.save(update_fields=["is_deleted"])
    dr = make_request(facility, staff_user, review_targets, "merge")
    client.force_login(lead_user)
    response = client.get(reverse("core:deletion_review", args=[dr.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert target.pseudonym not in body
    assert reverse("core:client_detail", args=[target.pk]) not in body
    assert len(TargetLinks(body).links) == 1
    assert 'data-testid="review-merge-target-not-visible"' in body
    assert 'data-testid="merge-source-events"' not in body
    assert 'data-testid="merge-target-events"' not in body


@pytest.mark.parametrize("client_state", ("deleted", "inactive"))
def test_case_target_obeys_live_client_boundary(client, lead_user, facility, staff_user, review_targets, client_state):
    person = review_targets["client"]
    field = "is_deleted" if client_state == "deleted" else "is_active"
    setattr(person, field, client_state != "inactive")
    person.save(update_fields=[field])
    dr = make_request(facility, staff_user, review_targets, "case")
    client.force_login(lead_user)
    response = client.get(reverse("core:deletion_review", args=[dr.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert review_targets["case"].title not in body
    assert TargetLinks(body).links == []


@pytest.mark.parametrize("kind", TARGET_TYPES)
def test_withdrawn_request_keeps_visible_links_and_notice(
    client, lead_user, facility, staff_user, review_targets, kind
):
    dr = make_request(facility, staff_user, review_targets, kind, status=DeletionRequest.Status.WITHDRAWN)
    client.force_login(lead_user)
    response = client.get(reverse("core:deletion_review", args=[dr.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert len(TargetLinks(body).links) == (2 if kind == "merge" else 1)
    assert 'data-testid="deletion-withdrawn-notice"' in body
    assert 'name="action" value="approve"' not in body


def test_visibility_error_never_exposes_event(client, lead_user, facility, staff_user, review_targets, monkeypatch):
    def deny_visibility(user):
        raise PermissionDenied("Visibility unavailable")

    monkeypatch.setattr("core.services.compliance.allowed_sensitivities_for_user", deny_visibility)
    dr = make_request(facility, staff_user, review_targets, "event")
    client.force_login(lead_user)
    response = client.get(reverse("core:deletion_review", args=[dr.pk]))
    assert response.status_code == 403
    assert review_targets["event"].document_type.name not in response.content.decode()
