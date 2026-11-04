"""Invalid ``assigned_to`` filter values must not crash the inbox (HTTP 500)."""

import pytest
from django.urls import reverse

from core.models import WorkItem

pytestmark = pytest.mark.django_db

INVALID_VALUES = ["abc", "1;DROP TABLE", "<script>", "a" * 500, "1.5", "-1", "²", "١٢"]


@pytest.fixture
def inbox_items(facility, staff_user, lead_user):
    return {
        "mine": WorkItem.objects.create(facility=facility, created_by=staff_user, title="Mine", assigned_to=staff_user),
        "other": WorkItem.objects.create(
            facility=facility, created_by=staff_user, title="Other", assigned_to=lead_user
        ),
        "free": WorkItem.objects.create(facility=facility, created_by=staff_user, title="Free"),
    }


def _titles(response):
    return {item.title for item in response.context["open_items"]}


@pytest.mark.parametrize("value", INVALID_VALUES)
def test_inbox_get_rejects_invalid_assigned_filter_with_400(client, staff_user, inbox_items, value):
    client.force_login(staff_user)
    response = client.get(reverse("core:workitem_inbox"), {"assigned_to": value})
    assert response.status_code == 400
    # No fallback listing of any tasks.
    for title in ("Mine", "Other", "Free"):
        assert title.encode() not in response.content


def test_inbox_htmx_get_rejects_invalid_assigned_filter_with_400(client, staff_user, inbox_items):
    client.force_login(staff_user)
    response = client.get(reverse("core:workitem_inbox"), {"assigned_to": "abc"}, HTTP_HX_REQUEST="true")
    assert response.status_code == 400


def test_inbox_valid_assigned_filters_keep_semantics(client, staff_user, lead_user, inbox_items):
    client.force_login(staff_user)
    url = reverse("core:workitem_inbox")

    default_scope = client.get(url)
    assert default_scope.status_code == 200
    assert _titles(default_scope) == {"Mine", "Free"}

    explicit_default = client.get(url, {"assigned_to": "mine_team"})
    assert _titles(explicit_default) == {"Mine", "Free"}

    only_me = client.get(url, {"assigned_to": "me"})
    assert _titles(only_me) == {"Mine"}

    everyone = client.get(url, {"assigned_to": ""})
    assert _titles(everyone) == {"Mine", "Other", "Free"}

    by_id = client.get(url, {"assigned_to": str(lead_user.pk)})
    assert _titles(by_id) == {"Other"}


def test_inbox_huge_numeric_assigned_filter_still_renders(client, staff_user, inbox_items):
    client.force_login(staff_user)
    response = client.get(reverse("core:workitem_inbox"), {"assigned_to": "9" * 40})
    assert response.status_code == 200
    assert _titles(response) == set()


@pytest.mark.parametrize("value", ["abc", "a" * 500, "1;DROP TABLE"])
def test_status_update_oob_path_survives_invalid_assigned_filter(client, staff_user, facility, value):
    item = WorkItem.objects.create(facility=facility, created_by=staff_user, title="Finish me", status="open")
    client.force_login(staff_user)
    response = client.post(
        reverse("core:workitem_status_update", args=[item.pk]),
        {"status": "done", "hide": "1", "recent_done_target": "1", "assigned_to": value},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert "afterbegin:#recent-done-items" not in response.content.decode()
    item.refresh_from_db()
    assert item.status == "done"


def test_status_update_oob_path_valid_assigned_filter_still_inserts(client, staff_user, facility):
    item = WorkItem.objects.create(
        facility=facility, created_by=staff_user, title="Finish me", status="open", assigned_to=staff_user
    )
    client.force_login(staff_user)
    for value in ("me", str(staff_user.pk), "mine_team", ""):
        WorkItem.objects.filter(pk=item.pk).update(status="open", completed_at=None)
        response = client.post(
            reverse("core:workitem_status_update", args=[item.pk]),
            {"status": "done", "hide": "1", "recent_done_target": "1", "assigned_to": value},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert "afterbegin:#recent-done-items" in response.content.decode(), value
