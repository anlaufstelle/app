"""Only lead and facility admin reach the free-text review (list, target, service)."""

import uuid
from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from core import nav
from core.models.workitem import DeletionRequest
from core.services.free_text_review import get_entry, list_page, mark_review, revoke_review
from tests.test_free_text_review import populate

pytestmark = pytest.mark.django_db

ALLOWED = ["lead_user", "admin_user"]
DENIED = ["assistant_user", "staff_user", "super_admin_user"]


def _target_url(event):
    return reverse(
        "core:free_text_review_target",
        kwargs={"target_type": "Event", "target_id": event.pk, "field_key": "notiz"},
    )


@pytest.mark.parametrize("role", ALLOWED)
def test_lead_and_admin_reach_list_and_target(request, client, role):
    event, _field = populate(request, "Event")
    client.force_login(request.getfixturevalue(role))

    assert client.get(reverse("core:free_text_review_list")).status_code == 200
    assert client.get(_target_url(event)).status_code == 200


@pytest.mark.parametrize("role", DENIED)
def test_roles_below_lead_get_403_on_every_route_and_method(request, client, role):
    event, _field = populate(request, "Event")
    client.force_login(request.getfixturevalue(role))
    list_url = reverse("core:free_text_review_list")
    target_url = _target_url(event)

    assert client.get(list_url).status_code == 403
    assert client.get(list_url, HTTP_HX_REQUEST="true").status_code == 403
    assert client.get(target_url).status_code == 403
    assert client.get(target_url, HTTP_HX_REQUEST="true").status_code == 403
    for action in ("revoke", "mark"):
        response = client.post(target_url, {"action": action})
        assert response.status_code == 403
        assert b"Private test content" not in response.content


@pytest.mark.parametrize("role", DENIED)
def test_roles_below_lead_cannot_use_the_service(request, facility, role):
    event, _field = populate(request, "Event")
    user = request.getfixturevalue(role)
    until = timezone.localdate() + timedelta(days=7)

    with pytest.raises(PermissionDenied):
        list_page(facility, user, "Event")
    with pytest.raises(PermissionDenied):
        get_entry(facility, user, "Event", event.pk, "notiz")
    with pytest.raises(PermissionDenied):
        mark_review(facility, user, "Event", event.pk, "notiz", until, "x")
    with pytest.raises(PermissionDenied):
        revoke_review(facility, user, "Event", event.pk, "notiz")


@pytest.mark.parametrize("role", ["assistant_user", "staff_user"])
def test_foreign_facility_lead_gets_404_and_lower_roles_get_403(
    request, client, second_facility, foreign_event, role, lead_user
):
    """A target of another facility never leaks: lead sees 404, lower roles are stopped by the gate."""
    foreign_event.data_json = {"notiz": "Private test content"}
    foreign_event.save(update_fields=["data_json"])
    url = _target_url(foreign_event)

    client.force_login(lead_user)
    assert client.get(url).status_code == 404

    client.force_login(request.getfixturevalue(role))
    response = client.get(url)
    assert response.status_code == 403
    assert b"Private test content" not in response.content


@pytest.mark.parametrize("role", ALLOWED)
def test_hub_shows_free_text_review_tab_after_retention_before_audit(request, client, role):
    client.force_login(request.getfixturevalue(role))
    content = client.get(reverse("core:datenschutz_uebersicht")).content.decode()

    assert 'data-testid="subnav-tab-free-text-review"' in content
    retention = content.index('data-testid="subnav-tab-retention"')
    review = content.index('data-testid="subnav-tab-free-text-review"')
    assert retention < review
    if role == "admin_user":
        assert review < content.index('data-testid="subnav-tab-audit"')


@pytest.mark.parametrize("role", ALLOWED)
def test_list_page_keeps_the_privacy_tab_row_with_active_review_tab(request, client, role):
    client.force_login(request.getfixturevalue(role))

    content = client.get(reverse("core:free_text_review_list")).content.decode()

    assert 'data-testid="subnav-datenschutz"' in content
    marker = content.index('data-testid="subnav-tab-free-text-review"')
    anchor = content[content.rindex("<a", 0, marker) : content.index(">", marker)]
    assert 'aria-current="page"' in anchor


@pytest.mark.parametrize("role", ["lead_user", "admin_user", "staff_user", "assistant_user", "super_admin_user"])
def test_no_sidebar_or_more_menu_entry_for_anyone(request, client, role):
    user = request.getfixturevalue(role)
    client.force_login(user)
    target = "core:system_dashboard" if role == "super_admin_user" else "core:workitem_inbox"
    content = client.get(reverse(target)).content.decode()

    assert 'data-testid="nav-free-text-review"' not in content
    assert 'href="/free-text-review/"' not in content


@pytest.mark.parametrize("role", ["assistant_user", "staff_user"])
def test_lower_roles_without_own_request_see_no_privacy_hub(request, client, role):
    user = request.getfixturevalue(role)
    assert not nav.section_is_visible(nav.DATENSCHUTZ, user)
    client.force_login(user)
    response = client.get(reverse("core:datenschutz_uebersicht"))
    assert response.status_code in (302, 403)
    assert b"/free-text-review/" not in response.content


def test_hub_explainer_names_the_review_tab(client, lead_user):
    client.force_login(lead_user)
    content = client.get(reverse("core:datenschutz_uebersicht")).content.decode()

    assert "Datenminimierung" in content
    assert "Freitext-Review" in content


def test_staff_with_own_pending_deletion_request_sees_no_review_tab(client, facility, staff_user):
    """The deletion-request exception opens the privacy hub, never the review."""
    DeletionRequest.objects.create(
        facility=facility,
        target_type=DeletionRequest.TargetType.CLIENT,
        target_id=uuid.uuid4(),
        reason="Own request",
        requested_by=staff_user,
        status=DeletionRequest.Status.PENDING,
    )
    client.force_login(staff_user)

    hub = client.get(reverse("core:datenschutz_uebersicht"))
    assert hub.status_code == 302  # straight to the single visible tab, no overview with the explainer
    tab_page = client.get(hub.url).content.decode()
    assert 'data-testid="subnav-tab-free-text-review"' not in tab_page
    assert "/free-text-review/" not in tab_page
    assert client.get(reverse("core:free_text_review_list")).status_code == 403


def test_confirmation_right_without_lead_role_does_not_open_the_review(client, staff_user):
    staff_user.can_confirm_deletion = True
    staff_user.save(update_fields=["can_confirm_deletion"])
    client.force_login(staff_user)

    assert client.get(reverse("core:free_text_review_list")).status_code == 403


@pytest.mark.parametrize("role", ALLOWED)
def test_field_with_unknown_sensitivity_is_not_listed(request, facility, sample_event, doc_type_contact, role):
    """An unknown sensitivity ranks above every role maximum, so even lead and admin never see the field."""
    from core.models import DocumentTypeField, FieldTemplate

    field = FieldTemplate.objects.create(facility=facility, name="Odd", field_type="text")
    DocumentTypeField.objects.create(document_type=doc_type_contact, field_template=field)
    sample_event.data_json = {"notiz": "Visible", "odd": "Hidden"}
    sample_event.save()
    user = request.getfixturevalue(role)
    assert {entry.field_key for entry in list_page(facility, user, "Event").entries} == {"notiz", "odd"}

    FieldTemplate.objects.filter(pk=field.pk).update(sensitivity="bogus")

    assert {entry.field_key for entry in list_page(facility, user, "Event").entries} == {"notiz"}
